"""Unit tests for the stock-proposal orchestration service.

Verifies:
- Authorization enforcement (unauthorized chemists rejected)
- Unparseable, conversational, and uncertain message rejection
- Unknown medicines flagged as unresolved without data fabrication
- Ambiguous catalogue matches flagged as unresolved
- Missing strength flagged as unresolved
- Single and multi-medicine proposal creation
- Zero quantity preservation
- Schedule H and low-confidence review flag behavior
- Immutability of inventory (no code path writes to confirmed inventory)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
import unittest
from unittest.mock import patch

from medibridge.schemas.stock import StockActionEnum
from medibridge.services.proposal_adapter import ParsedProposalInput
from medibridge.services.proposal_service import (
    CatalogueMedicine,
    ProposalServiceResult,
    ProposalServiceStatus,
    StockProposalRepositoryProtocol,
    StoredProposalRecord,
    process_stock_message,
)


class InMemoryStockProposalRepository:
    """In-memory test double implementing StockProposalRepositoryProtocol.

    Strictly manages membership, catalogue lookups, and PENDING proposal creation.
    Deliberately contains NO inventory tables or mutation methods.
    """

    def __init__(self) -> None:
        self.linked_users: set[tuple[int, int]] = set()  # (user_id, pharmacy_id)
        self.medicines: dict[int, CatalogueMedicine] = {}
        self.aliases: dict[str, int] = {}  # alias.lower() -> medicine_id
        self.proposals: list[StoredProposalRecord] = []
        self._next_proposal_id: int = 101
        self.find_alias_calls: int = 0
        self.search_medicines_calls: int = 0
        self.create_proposal_calls: int = 0

    def link_user(self, user_id: int, pharmacy_id: int) -> None:
        """Helper to configure user-pharmacy links for tests."""
        self.linked_users.add((user_id, pharmacy_id))

    def add_medicine(self, medicine: CatalogueMedicine) -> None:
        """Helper to seed catalogue medicines for tests."""
        self.medicines[medicine.id] = medicine
        self.aliases[medicine.brand_name.strip().lower()] = medicine.id
        for alias in medicine.aliases:
            self.aliases[alias.strip().lower()] = medicine.id

    def is_user_linked_to_pharmacy(self, user_id: int, pharmacy_id: int) -> bool:
        """Check if user has an active membership at pharmacy."""
        return (user_id, pharmacy_id) in self.linked_users

    def find_medicine_by_alias(self, alias: str) -> Optional[CatalogueMedicine]:
        """Find a single medicine by exact alias match."""
        self.find_alias_calls += 1
        cleaned = alias.strip().lower()
        med_id = self.aliases.get(cleaned)
        if med_id is not None:
            return self.medicines.get(med_id)
        return None

    def search_medicines(self, query: str, limit: int = 5) -> list[CatalogueMedicine]:
        """Search catalogue medicines by query across brand, generic, and aliases."""
        self.search_medicines_calls += 1
        cleaned = query.strip().lower()
        results: list[CatalogueMedicine] = []
        for med in self.medicines.values():
            if (
                cleaned in med.brand_name.lower()
                or cleaned in med.generic_name.lower()
                or any(cleaned in a.lower() for a in med.aliases)
            ):
                results.append(med)
                if len(results) >= limit:
                    break
        return results

    def create_stock_proposal(
        self,
        pharmacy_id: int,
        chemist_user_id: int,
        raw_message: str,
        parsed: ParsedProposalInput,
    ) -> StoredProposalRecord:
        """Create and persist a PENDING stock proposal row."""
        self.create_proposal_calls += 1
        if not self.is_user_linked_to_pharmacy(chemist_user_id, pharmacy_id):
            raise ValueError(f"User {chemist_user_id} is not linked to pharmacy {pharmacy_id}.")

        now = datetime.now(timezone.utc)
        record = StoredProposalRecord(
            id=self._next_proposal_id,
            pharmacy_id=pharmacy_id,
            chemist_user_id=chemist_user_id,
            raw_message=raw_message,
            brand_name=parsed.brand_name,
            parsed_salt=parsed.parsed_salt,
            parsed_strength=parsed.parsed_strength,
            parsed_form=parsed.parsed_form,
            parsed_quantity=parsed.parsed_quantity,
            parsed_intent=parsed.parsed_intent,
            confidence=parsed.confidence,
            status="PENDING",
            flagged_for_review=parsed.flagged_for_review,
            expires_at=now + timedelta(hours=24),
            created_at=now,
        )
        self.proposals.append(record)
        self._next_proposal_id += 1
        return record


class TestStockProposalService(unittest.TestCase):
    """Test suite covering the stock-proposal orchestration service."""

    def setUp(self) -> None:
        """Set up test repository seeded with standard test medicines."""
        self.repo = InMemoryStockProposalRepository()
        self.pharmacy_id = 5
        self.chemist_id = 42

        # Link chemist 42 to pharmacy 5
        self.repo.link_user(self.chemist_id, self.pharmacy_id)

        # Seed catalogue
        self.repo.add_medicine(
            CatalogueMedicine(
                id=1,
                brand_name="Dolo 650",
                generic_name="paracetamol",
                strength="650mg",
                form="strip",
                is_schedule_h=False,
                aliases=["dolo", "dolo650"],
            )
        )
        self.repo.add_medicine(
            CatalogueMedicine(
                id=2,
                brand_name="Calpol 500",
                generic_name="paracetamol",
                strength="500mg",
                form="tablet",
                is_schedule_h=False,
                aliases=["calpol", "calpol500"],
            )
        )
        self.repo.add_medicine(
            CatalogueMedicine(
                id=3,
                brand_name="Amoxicillin 500mg",
                generic_name="amoxicillin",
                strength="500mg",
                form="capsule",
                is_schedule_h=True,  # Schedule H antibiotic
                aliases=["amoxil", "amoxicillin"],
            )
        )
        self.repo.add_medicine(
            CatalogueMedicine(
                id=4,
                brand_name="Crocin Syrup",
                generic_name="paracetamol",
                strength=None,  # Intentionally missing strength in catalogue
                form="syrup",
                is_schedule_h=False,
                aliases=["crocin"],
            )
        )

    def test_unauthorized_chemist_rejected(self) -> None:
        """Test 1: Unlinked user cannot create proposals; halts immediately with UNAUTHORIZED."""
        unauthorized_user_id = 999
        raw_msg = "Dolo 650 20 strips available"

        with patch("medibridge.services.proposal_service.parse_stock_message") as mock_parser:
            result = process_stock_message(
                raw_message=raw_msg,
                pharmacy_id=self.pharmacy_id,
                chemist_user_id=unauthorized_user_id,
                repository=self.repo,
            )

        self.assertEqual(result.status, ProposalServiceStatus.UNAUTHORIZED)
        self.assertIn("not linked", result.reason or "")
        self.assertEqual(len(result.created_proposals), 0)
        self.assertEqual(len(result.unresolved_items), 0)
        self.assertEqual(len(self.repo.proposals), 0)

        # Assert no parsing, catalogue access, or proposal creation occurred
        mock_parser.assert_not_called()
        self.assertEqual(self.repo.find_alias_calls, 0)
        self.assertEqual(self.repo.search_medicines_calls, 0)
        self.assertEqual(self.repo.create_proposal_calls, 0)

    def test_unauthorized_chemist_with_empty_or_whitespace_message(self) -> None:
        """Test 1B: Unauthorized user receives UNAUTHORIZED even on empty/whitespace input without parser/catalogue access."""
        unauthorized_user_id = 999
        empty_inputs = ["", "   ", "\t\n  "]

        for empty_msg in empty_inputs:
            with self.subTest(empty_msg=empty_msg):
                with patch("medibridge.services.proposal_service.parse_stock_message") as mock_parser:
                    result = process_stock_message(
                        raw_message=empty_msg,
                        pharmacy_id=self.pharmacy_id,
                        chemist_user_id=unauthorized_user_id,
                        repository=self.repo,
                    )

                self.assertEqual(result.status, ProposalServiceStatus.UNAUTHORIZED)
                self.assertIn("not linked", result.reason or "")
                self.assertEqual(len(result.created_proposals), 0)
                self.assertEqual(len(result.unresolved_items), 0)
                self.assertEqual(len(self.repo.proposals), 0)

                # Assert that no parsing, catalogue lookups, or proposals occurred
                mock_parser.assert_not_called()
                self.assertEqual(self.repo.find_alias_calls, 0)
                self.assertEqual(self.repo.search_medicines_calls, 0)
                self.assertEqual(self.repo.create_proposal_calls, 0)

    def test_unparseable_and_uncertain_messages(self) -> None:
        """Test 2: Authorized user submitting conversational, empty, or uncertain messages returns UNPARSEABLE."""
        cases = [
            "Hello bhaiya kya rate hai",
            "Shayad Dolo 650 available hai",
            "Kal Dolo 650 aayega",
            "",
            "   ",
        ]
        for msg in cases:
            with self.subTest(msg=msg):
                result = process_stock_message(
                    raw_message=msg,
                    pharmacy_id=self.pharmacy_id,
                    chemist_user_id=self.chemist_id,
                    repository=self.repo,
                )
                self.assertEqual(result.status, ProposalServiceStatus.UNPARSEABLE)
                self.assertEqual(len(result.created_proposals), 0)
                self.assertEqual(len(self.repo.proposals), 0)

    def test_unknown_medicine_returns_unresolved(self) -> None:
        """Test 3: Uncatalogued medicine returns UNRESOLVED without guessing salt."""
        raw_msg = "UnknownMedicineX 10 strips available"

        result = process_stock_message(
            raw_message=raw_msg,
            pharmacy_id=self.pharmacy_id,
            chemist_user_id=self.chemist_id,
            repository=self.repo,
        )

        self.assertEqual(result.status, ProposalServiceStatus.UNRESOLVED)
        self.assertEqual(len(result.created_proposals), 0)
        self.assertEqual(len(result.unresolved_items), 1)
        unresolved = result.unresolved_items[0]
        self.assertEqual(unresolved.item.medicine_name, "UnknownMedicineX")
        self.assertIn("not found in catalogue", unresolved.reason)
        self.assertIn("parsed_salt", unresolved.missing_fields)
        self.assertEqual(len(self.repo.proposals), 0)

    def test_ambiguous_catalogue_matches_returns_unresolved(self) -> None:
        """Test 4: Ambiguous catalogue search results return UNRESOLVED."""
        # Add two ambiguous medicines that match query "Paracetamol"
        self.repo.add_medicine(
            CatalogueMedicine(
                id=10,
                brand_name="Paracetamol 500mg Tablets",
                generic_name="paracetamol",
                strength="500mg",
                form="tablet",
            )
        )
        self.repo.add_medicine(
            CatalogueMedicine(
                id=11,
                brand_name="Paracetamol 650mg Tablets",
                generic_name="paracetamol",
                strength="650mg",
                form="tablet",
            )
        )

        raw_msg = "Paracetamol 10 strips available"
        result = process_stock_message(
            raw_message=raw_msg,
            pharmacy_id=self.pharmacy_id,
            chemist_user_id=self.chemist_id,
            repository=self.repo,
        )

        self.assertEqual(result.status, ProposalServiceStatus.UNRESOLVED)
        self.assertEqual(len(result.created_proposals), 0)
        self.assertEqual(len(result.unresolved_items), 1)
        unresolved = result.unresolved_items[0]
        self.assertIn("Multiple catalogue matches", unresolved.reason)
        self.assertEqual(len(self.repo.proposals), 0)

    def test_missing_strength_returns_unresolved(self) -> None:
        """Test 5: Medicine with missing strength in both message and catalogue is UNRESOLVED."""
        # "Crocin Syrup" in catalogue has strength=None
        raw_msg = "Crocin available"

        result = process_stock_message(
            raw_message=raw_msg,
            pharmacy_id=self.pharmacy_id,
            chemist_user_id=self.chemist_id,
            repository=self.repo,
        )

        self.assertEqual(result.status, ProposalServiceStatus.UNRESOLVED)
        self.assertEqual(len(result.created_proposals), 0)
        self.assertEqual(len(result.unresolved_items), 1)
        unresolved = result.unresolved_items[0]
        self.assertIn("parsed_strength", unresolved.missing_fields)
        self.assertIn("Strength not specified", unresolved.reason)
        self.assertEqual(len(self.repo.proposals), 0)

    def test_successful_single_medicine_proposal(self) -> None:
        """Test 6: Valid chemist update creates a single PENDING proposal."""
        raw_msg = "Dolo 650 20 strips available"

        result = process_stock_message(
            raw_message=raw_msg,
            pharmacy_id=self.pharmacy_id,
            chemist_user_id=self.chemist_id,
            repository=self.repo,
        )

        self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)
        self.assertEqual(len(result.created_proposals), 1)
        self.assertEqual(len(result.unresolved_items), 0)

        proposal = result.created_proposals[0]
        self.assertEqual(proposal.brand_name, "Dolo 650")
        self.assertEqual(proposal.parsed_salt, "paracetamol")
        self.assertEqual(proposal.parsed_strength, "650mg")
        self.assertEqual(proposal.parsed_quantity, 20)
        self.assertEqual(proposal.parsed_intent, StockActionEnum.IN_STOCK)
        self.assertEqual(proposal.status, "PENDING")
        self.assertEqual(proposal.confidence, 1.0)
        self.assertFalse(proposal.flagged_for_review)
        self.assertEqual(proposal.raw_message, raw_msg)
        self.assertEqual(proposal.pharmacy_id, self.pharmacy_id)
        self.assertEqual(proposal.chemist_user_id, self.chemist_id)

        # Verify persisted in repo
        self.assertEqual(len(self.repo.proposals), 1)
        self.assertEqual(self.repo.proposals[0].id, proposal.id)

    def test_multi_medicine_message_decomposition(self) -> None:
        """Test 7: Multi-medicine message creates distinct PENDING proposals sharing raw_message."""
        raw_msg = "Dolo 650 20 strips available, Calpol 500 khatam"

        result = process_stock_message(
            raw_message=raw_msg,
            pharmacy_id=self.pharmacy_id,
            chemist_user_id=self.chemist_id,
            repository=self.repo,
        )

        self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)
        self.assertEqual(len(result.created_proposals), 2)
        self.assertEqual(len(result.unresolved_items), 0)

        p1, p2 = result.created_proposals
        self.assertEqual(p1.brand_name, "Dolo 650")
        self.assertEqual(p1.parsed_quantity, 20)
        self.assertEqual(p1.parsed_intent, StockActionEnum.IN_STOCK)
        self.assertEqual(p1.status, "PENDING")

        self.assertEqual(p2.brand_name, "Calpol 500")
        self.assertIsNone(p2.parsed_quantity)
        self.assertEqual(p2.parsed_intent, StockActionEnum.OUT_OF_STOCK)
        self.assertEqual(p2.status, "PENDING")

        # Both proposals share identical raw_message, pharmacy_id, chemist_user_id
        for p in (p1, p2):
            self.assertEqual(p.raw_message, raw_msg)
            self.assertEqual(p.pharmacy_id, self.pharmacy_id)
            self.assertEqual(p.chemist_user_id, self.chemist_id)

    def test_partial_success_multi_medicine(self) -> None:
        """Test 8: One resolved medicine and one unknown medicine produce PARTIAL_SUCCESS."""
        raw_msg = "Dolo 650 20 strips available, NonExistentMed 5 bottles"

        result = process_stock_message(
            raw_message=raw_msg,
            pharmacy_id=self.pharmacy_id,
            chemist_user_id=self.chemist_id,
            repository=self.repo,
        )

        self.assertEqual(result.status, ProposalServiceStatus.PARTIAL_SUCCESS)
        self.assertEqual(len(result.created_proposals), 1)
        self.assertEqual(len(result.unresolved_items), 1)

        created = result.created_proposals[0]
        self.assertEqual(created.brand_name, "Dolo 650")
        self.assertEqual(created.status, "PENDING")

        unresolved = result.unresolved_items[0]
        self.assertEqual(unresolved.item.medicine_name, "NonExistentMed")
        self.assertIn("not found in catalogue", unresolved.reason)

    def test_preservation_of_quantity_zero(self) -> None:
        """Test 9: Explicit quantity=0 is preserved alongside OUT_OF_STOCK in created proposal."""
        raw_msg = "Dolo 650 0 strips hai"

        result = process_stock_message(
            raw_message=raw_msg,
            pharmacy_id=self.pharmacy_id,
            chemist_user_id=self.chemist_id,
            repository=self.repo,
        )

        self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)
        self.assertEqual(len(result.created_proposals), 1)
        proposal = result.created_proposals[0]
        self.assertEqual(proposal.parsed_quantity, 0)
        self.assertEqual(proposal.parsed_intent, StockActionEnum.OUT_OF_STOCK)
        self.assertEqual(proposal.status, "PENDING")

    def test_review_flag_for_schedule_h(self) -> None:
        """Test 10: Schedule H medicine creates proposal with flagged_for_review=True."""
        raw_msg = "Amoxicillin 500mg 10 strips available"

        result = process_stock_message(
            raw_message=raw_msg,
            pharmacy_id=self.pharmacy_id,
            chemist_user_id=self.chemist_id,
            repository=self.repo,
        )

        self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)
        self.assertEqual(len(result.created_proposals), 1)
        proposal = result.created_proposals[0]
        self.assertTrue(proposal.flagged_for_review)
        self.assertEqual(proposal.parsed_salt, "amoxicillin")
        self.assertEqual(proposal.status, "PENDING")

    def test_review_flag_for_fuzzy_search_match(self) -> None:
        """Test 11: Partial search match assigns confidence < 0.8 and flags for review."""
        self.repo.add_medicine(
            CatalogueMedicine(
                id=50,
                brand_name="Azithromycin 500mg Tablets",
                generic_name="azithromycin",
                strength="500mg",
                form="tablet",
                is_schedule_h=False,
                aliases=[],  # No direct alias for query
            )
        )
        # Chemist says "Azithromycin 500mg", which is a substring match of "Azithromycin 500mg Tablets"
        raw_msg = "Azithromycin 500mg 10 strips available"

        result = process_stock_message(
            raw_message=raw_msg,
            pharmacy_id=self.pharmacy_id,
            chemist_user_id=self.chemist_id,
            repository=self.repo,
        )

        self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)
        self.assertEqual(len(result.created_proposals), 1)
        proposal = result.created_proposals[0]
        # Partial match receives confidence 0.75
        self.assertEqual(proposal.confidence, 0.75)
        # Because confidence < 0.8, flagged_for_review must be True
        self.assertTrue(proposal.flagged_for_review)

    def test_inventory_never_modified_and_proposals_remain_pending(self) -> None:
        """Test 12: Verify inventory is never written and all proposals remain PENDING."""
        raw_msg = "Dolo 650 20 strips available"

        result = process_stock_message(
            raw_message=raw_msg,
            pharmacy_id=self.pharmacy_id,
            chemist_user_id=self.chemist_id,
            repository=self.repo,
        )

        # 1. Proposal state must strictly be PENDING
        self.assertEqual(len(result.created_proposals), 1)
        self.assertEqual(result.created_proposals[0].status, "PENDING")

        # 2. Test repository must not possess an inventory store or modify confirmed stock
        self.assertFalse(hasattr(self.repo, "inventory"))
        self.assertFalse(hasattr(self.repo, "apply_confirmed_proposal"))
        self.assertFalse(hasattr(self.repo, "update_inventory"))


if __name__ == "__main__":
    unittest.main()
