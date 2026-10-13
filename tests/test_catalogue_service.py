"""Unit tests for the medicine catalogue and substitute discovery service.

Verifies:
- Exact alias lookup returns highest confidence
- Brand and generic search returns ranked results
- Disambiguation prompt formatting
- Composition-based substitute discovery
- Composition mismatch rejection during pharmacist substitution approval
"""

from __future__ import annotations

from typing import Optional
import unittest

from medibridge.exceptions import UserNotLinkedToPharmacyError
from medibridge.services.catalogue_service import (
    CatalogueMedicineDetail,
    CatalogueRepositoryProtocol,
    SearchResultItem,
    SubstituteGroupResult,
    find_substitutes,
    format_disambiguation_prompt,
    record_substitute_approval,
    search_catalogue,
)


class InMemoryCatalogueRepository:
    """In-memory test double implementing CatalogueRepositoryProtocol."""

    def __init__(self) -> None:
        self.linked_users: set[tuple[int, int]] = set()  # (user_id, pharmacy_id)
        self.medicines: dict[int, CatalogueMedicineDetail] = {}
        self.aliases: dict[str, int] = {}
        self.approvals: list[dict] = []
        self._next_approval_id: int = 1

    def link_user(self, user_id: int, pharmacy_id: int) -> None:
        self.linked_users.add((user_id, pharmacy_id))

    def is_user_linked_to_pharmacy(self, user_id: int, pharmacy_id: int) -> bool:
        return (user_id, pharmacy_id) in self.linked_users

    def add_medicine(self, med: CatalogueMedicineDetail) -> None:
        self.medicines[med.id] = med
        self.aliases[med.brand_name.strip().lower()] = med.id
        for a in med.aliases:
            self.aliases[a.strip().lower()] = med.id

    def find_medicine_by_alias(self, alias: str) -> Optional[CatalogueMedicineDetail]:
        med_id = self.aliases.get(alias.strip().lower())
        if med_id is not None:
            return self.medicines.get(med_id)
        return None

    def search_medicines(self, query: str, limit: int = 10) -> list[CatalogueMedicineDetail]:
        cleaned = query.strip().lower()
        results: list[CatalogueMedicineDetail] = []
        for m in self.medicines.values():
            if (
                cleaned in m.brand_name.lower()
                or cleaned in m.generic_name.lower()
                or any(cleaned in a.lower() for a in m.aliases)
            ):
                results.append(m)
                if len(results) >= limit:
                    break
        return results

    def get_medicine_by_id(self, medicine_id: int) -> Optional[CatalogueMedicineDetail]:
        return self.medicines.get(medicine_id)

    def get_substitutes_for_medicine(
        self,
        medicine_id: int,
        pharmacy_id: Optional[int] = None,
    ) -> list[CatalogueMedicineDetail]:
        target = self.medicines.get(medicine_id)
        if not target:
            return []
        return [
            m
            for m in self.medicines.values()
            if m.composition_key == target.composition_key
            and m.strength == target.strength
            and m.form == target.form
        ]

    def record_substitution_approval(
        self,
        pharmacy_id: int,
        requested_medicine_id: int,
        substitute_medicine_id: int,
        approving_pharmacist_id: int,
        status: str,
        request_reference: Optional[str] = None,
    ) -> dict:
        record = {
            "id": self._next_approval_id,
            "pharmacy_id": pharmacy_id,
            "requested_medicine_id": requested_medicine_id,
            "substitute_medicine_id": substitute_medicine_id,
            "approving_pharmacist_id": approving_pharmacist_id,
            "status": status,
            "request_reference": request_reference,
        }
        self.approvals.append(record)
        self._next_approval_id += 1
        return record


class TestCatalogueService(unittest.TestCase):
    """Test suite covering catalogue search, disambiguation, and substitutes."""

    def setUp(self) -> None:
        self.repo = InMemoryCatalogueRepository()

        # Seed Paracetamol 650mg tablets
        self.dolo_650 = CatalogueMedicineDetail(
            id=1,
            brand_name="Dolo 650",
            generic_name="paracetamol",
            composition_key="paracetamol_650mg_tablet",
            strength="650mg",
            form="tablet",
            is_schedule_h=False,
            manufacturer="Micro Labs",
            aliases=["dolo", "dolo650"],
        )
        self.calpol_650 = CatalogueMedicineDetail(
            id=2,
            brand_name="Calpol 650",
            generic_name="paracetamol",
            composition_key="paracetamol_650mg_tablet",
            strength="650mg",
            form="tablet",
            is_schedule_h=False,
            manufacturer="GSK",
            aliases=["calpol", "calpol650"],
        )
        # Seed Paracetamol 500mg tablets (different composition key / strength)
        self.crocin_500 = CatalogueMedicineDetail(
            id=3,
            brand_name="Crocin 500",
            generic_name="paracetamol",
            composition_key="paracetamol_500mg_tablet",
            strength="500mg",
            form="tablet",
            is_schedule_h=False,
            manufacturer="GSK",
            aliases=["crocin"],
        )
        # Seed Amoxicillin (Schedule H)
        self.amox_500 = CatalogueMedicineDetail(
            id=4,
            brand_name="Mox 500",
            generic_name="amoxicillin",
            composition_key="amoxicillin_500mg_capsule",
            strength="500mg",
            form="capsule",
            is_schedule_h=True,
            manufacturer="Sun Pharma",
            aliases=["mox"],
        )

        self.repo.add_medicine(self.dolo_650)
        self.repo.add_medicine(self.calpol_650)
        self.repo.add_medicine(self.crocin_500)
        self.repo.add_medicine(self.amox_500)

        # Link pharmacist 42 to pharmacy 5
        self.repo.link_user(user_id=42, pharmacy_id=5)

    def test_search_exact_alias_match(self) -> None:
        """Test 1: Query matching registered alias returns confidence 1.0."""
        results = search_catalogue(query="dolo", repository=self.repo)
        self.assertGreaterEqual(len(results), 1)
        top = results[0]
        self.assertEqual(top.medicine.brand_name, "Dolo 650")
        self.assertTrue(top.is_exact_match)
        self.assertEqual(top.confidence, 1.0)
        self.assertEqual(top.match_type, "alias")

    def test_search_empty_query_returns_empty(self) -> None:
        """Test 2: Empty or whitespace query returns empty list."""
        self.assertEqual(search_catalogue("", repository=self.repo), [])
        self.assertEqual(search_catalogue("   ", repository=self.repo), [])

    def test_format_disambiguation_prompt(self) -> None:
        """Test 3: Prompt lists multiple candidates clearly."""
        candidates = [self.dolo_650, self.calpol_650]
        prompt = format_disambiguation_prompt("paracetamol 650", candidates)
        self.assertIn("Dolo 650", prompt)
        self.assertIn("Calpol 650", prompt)
        self.assertIn("Please select one", prompt)

    def test_find_substitutes_matching_composition(self) -> None:
        """Test 4: Finding substitutes for Dolo 650 returns Calpol 650 (excludes Dolo 650 itself)."""
        result = find_substitutes(medicine_id=1, repository=self.repo)
        self.assertEqual(result.requested_medicine.brand_name, "Dolo 650")
        self.assertEqual(len(result.available_substitutes), 1)
        self.assertEqual(result.available_substitutes[0].brand_name, "Calpol 650")
        self.assertEqual(result.composition_key, "paracetamol_650mg_tablet")

    def test_find_substitutes_nonexistent_medicine_raises_value_error(self) -> None:
        """Test 5: Non-existent medicine ID raises ValueError."""
        with self.assertRaises(ValueError):
            find_substitutes(medicine_id=999, repository=self.repo)

    def test_record_substitute_approval_valid(self) -> None:
        """Test 6: Valid substitution with identical composition succeeds."""
        record = record_substitute_approval(
            pharmacy_id=5,
            requested_medicine_id=1,  # Dolo 650
            substitute_medicine_id=2,  # Calpol 650
            approving_pharmacist_id=42,
            status="APPROVED",
            repository=self.repo,
            request_reference="ORDER-12345",
        )
        self.assertEqual(record["status"], "APPROVED")
        self.assertEqual(record["request_reference"], "ORDER-12345")
        self.assertEqual(len(self.repo.approvals), 1)

    def test_record_substitute_approval_composition_mismatch_raises_value_error(self) -> None:
        """Test 7: Attempting to substitute Dolo 650 with Mox 500 raises ValueError."""
        with self.assertRaises(ValueError):
            record_substitute_approval(
                pharmacy_id=5,
                requested_medicine_id=1,  # Dolo 650 (paracetamol)
                substitute_medicine_id=4,  # Mox 500 (amoxicillin)
                approving_pharmacist_id=42,
                status="APPROVED",
                repository=self.repo,
            )

    def test_record_substitute_approval_unauthorized_user_raises_error(self) -> None:
        """Test 8: Unauthorized pharmacist cannot record substitution approval."""
        unauthorized_pharmacist_id = 999
        with self.assertRaises(UserNotLinkedToPharmacyError):
            record_substitute_approval(
                pharmacy_id=5,
                requested_medicine_id=1,
                substitute_medicine_id=2,
                approving_pharmacist_id=unauthorized_pharmacist_id,
                status="APPROVED",
                repository=self.repo,
            )


if __name__ == "__main__":
    unittest.main()
