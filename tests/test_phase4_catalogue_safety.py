"""Unit tests for Phase 4: AI Reliability, Catalogue Grounding, and Inventory Safety.

Verifies:
1. Catalogue grounding: exact matches, unknown medicines, ambiguous choices,
   preservation of canonical catalogue metadata against LLM override.
2. Quantity, strength, and status validation: 650mg vs 20 strips separation,
   zero preservation, contradictory quantity clearance.
3. Review rules and boundaries: 0.79, 0.80, 0.81, Schedule H mandatory review.
4. Proposal and inventory safety: PENDING-only creation, transactional confirmation,
   authorization checks, pharmacy scoping, expiry enforcement, rejection safety.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
import unittest
from unittest.mock import MagicMock, patch

from medibridge.exceptions import (
    ProposalExpiredError,
    ProposalNotPendingError,
    ProposalPharmacyMismatchError,
    StockStatusQuantityMismatchError,
    UserNotLinkedToPharmacyError,
)
from medibridge.schemas.stock import (
    ParsedMedicineStockItem,
    StockActionEnum,
    StockUpdateProposal,
)
from medibridge.services.ai_parser import parse_stock_message
from medibridge.services.confirmation_service import (
    ConfirmationResult,
    InventoryRecord,
    check_proposal_expiry,
    confirm_and_apply_proposal,
    reject_proposal_workflow,
)
from medibridge.services.proposal_adapter import (
    ParsedProposalInput,
    UnresolvedProposalItem,
    adapt_parsed_item,
    adapt_stock_proposal,
    compute_review_flag,
)
from medibridge.services.proposal_service import (
    CatalogueMedicine,
    ProposalServiceResult,
    ProposalServiceStatus,
    StoredProposalRecord,
    process_stock_message,
)


class InMemoryProposalAndConfirmationRepo:
    """Mock repository implementing both StockProposalRepositoryProtocol and ConfirmationRepositoryProtocol."""

    def __init__(self) -> None:
        self.linked_users: set[tuple[int, int]] = set()
        self.medicines: dict[int, CatalogueMedicine] = {}
        self.aliases: dict[str, int] = {}
        self.proposals: dict[int, StoredProposalRecord] = {}
        self.inventory: dict[tuple[int, int], InventoryRecord] = {}
        self.history: list[dict] = []
        self._next_prop_id: int = 200
        self._next_inv_id: int = 500

    def link_user(self, user_id: int, pharmacy_id: int) -> None:
        self.linked_users.add((user_id, pharmacy_id))

    def add_medicine(self, med: CatalogueMedicine) -> None:
        self.medicines[med.id] = med
        self.aliases[med.brand_name.strip().lower()] = med.id
        for a in med.aliases:
            self.aliases[a.strip().lower()] = med.id

    def is_user_linked_to_pharmacy(self, user_id: int, pharmacy_id: int) -> bool:
        return (user_id, pharmacy_id) in self.linked_users

    def find_medicine_by_alias(self, alias: str) -> Optional[CatalogueMedicine]:
        med_id = self.aliases.get(alias.strip().lower())
        return self.medicines.get(med_id) if med_id is not None else None

    def search_medicines(self, query: str, limit: int = 5) -> list[CatalogueMedicine]:
        q = query.strip().lower()
        res = []
        for m in self.medicines.values():
            if q in m.brand_name.lower() or q in m.generic_name.lower() or any(q in a.lower() for a in m.aliases):
                res.append(m)
                if len(res) >= limit:
                    break
        return res

    def create_stock_proposal(
        self,
        pharmacy_id: int,
        chemist_user_id: int,
        raw_message: str,
        parsed: ParsedProposalInput,
        source_message_ref: Optional[str] = None,
    ) -> StoredProposalRecord:
        now = datetime.now(timezone.utc)
        record = StoredProposalRecord(
            id=self._next_prop_id,
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
        self.proposals[self._next_prop_id] = record
        self._next_prop_id += 1
        return record

    def get_proposal_by_id(self, proposal_id: int) -> Optional[StoredProposalRecord]:
        return self.proposals.get(proposal_id)

    def apply_confirmed_proposal(
        self,
        proposal_id: int,
        confirmed_by_user_id: int,
        pharmacy_id: int,
        current_time: Optional[datetime] = None,
    ) -> InventoryRecord:
        prop = self.proposals[proposal_id]
        now = current_time or datetime.now(timezone.utc)
        # Update proposal state
        updated_prop = prop.model_copy(update={
            "status": "CONFIRMED",
            "confirmed_by": confirmed_by_user_id,
            "confirmed_at": now,
        })
        self.proposals[proposal_id] = updated_prop

        # Record audit history
        self.history.append({
            "proposal_id": proposal_id,
            "pharmacy_id": pharmacy_id,
            "user_id": confirmed_by_user_id,
            "action": "STOCK_UPDATE",
            "timestamp": now,
        })

        # Update inventory
        inv_key = (pharmacy_id, 1)  # mock med_id 1
        inv_record = InventoryRecord(
            id=self._next_inv_id,
            pharmacy_id=pharmacy_id,
            medicine_id=1,
            stock_status=prop.parsed_intent,
            quantity=prop.parsed_quantity,
            last_confirmed_at=now,
            last_updated_by=confirmed_by_user_id,
        )
        self.inventory[inv_key] = inv_record
        self._next_inv_id += 1
        return inv_record

    def reject_proposal(
        self,
        proposal_id: int,
        rejected_by_user_id: int,
        pharmacy_id: int,
        reason: Optional[str] = None,
    ) -> StoredProposalRecord:
        prop = self.proposals[proposal_id]
        updated_prop = prop.model_copy(update={
            "status": "REJECTED",
            "review_note": reason,
        })
        self.proposals[proposal_id] = updated_prop
        return updated_prop


class TestPhase4CatalogueGrounding(unittest.TestCase):
    """Step 2: Catalogue grounding, canonical attribute preservation, and ambiguity safety."""

    def setUp(self) -> None:
        self.repo = InMemoryProposalAndConfirmationRepo()
        self.pharmacy_id = 1
        self.chemist_id = 10
        self.repo.link_user(self.chemist_id, self.pharmacy_id)

        # Standard catalogue entry
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
        # Schedule H antibiotic
        self.repo.add_medicine(
            CatalogueMedicine(
                id=2,
                brand_name="Amoxicillin 500mg",
                generic_name="amoxicillin",
                strength="500mg",
                form="capsule",
                is_schedule_h=True,
                aliases=["amoxicillin"],
            )
        )

    def test_exact_catalogue_match_resolves_canonical_attributes(self):
        """Exact catalogue match resolves generic salt and strength from canonical catalogue record."""
        result = process_stock_message("Dolo 650 20 strips available", self.pharmacy_id, self.chemist_id, self.repo)
        self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)
        self.assertEqual(len(result.created_proposals), 1)
        prop = result.created_proposals[0]
        self.assertEqual(prop.brand_name, "Dolo 650")
        self.assertEqual(prop.parsed_salt, "paracetamol")
        self.assertEqual(prop.parsed_strength, "650mg")
        self.assertEqual(prop.confidence, 1.0)
        self.assertFalse(prop.flagged_for_review)

    def test_unknown_medicine_remains_unresolved(self):
        """Medicines absent from the catalogue remain unresolved without inventing salts or IDs."""
        result = process_stock_message("FictionalDrugXYZ 10 strips available", self.pharmacy_id, self.chemist_id, self.repo)
        self.assertEqual(result.status, ProposalServiceStatus.UNRESOLVED)
        self.assertEqual(len(result.created_proposals), 0)
        self.assertEqual(len(result.unresolved_items), 1)
        self.assertIn("not found in catalogue", result.unresolved_items[0].reason)
        self.assertIn("parsed_salt", result.unresolved_items[0].missing_fields)

    def test_ambiguous_matches_remain_unresolved_with_candidate_choices(self):
        """Multiple catalogue search results are never guessed; marked unresolved with choices listed."""
        self.repo.add_medicine(CatalogueMedicine(id=10, brand_name="Crocin 500", generic_name="paracetamol", strength="500mg"))
        self.repo.add_medicine(CatalogueMedicine(id=11, brand_name="Crocin 650", generic_name="paracetamol", strength="650mg"))

        result = process_stock_message("Crocin 15 strips in stock", self.pharmacy_id, self.chemist_id, self.repo)
        self.assertEqual(result.status, ProposalServiceStatus.UNRESOLVED)
        self.assertEqual(len(result.created_proposals), 0)
        self.assertEqual(len(result.unresolved_items), 1)
        reason = result.unresolved_items[0].reason
        self.assertIn("Multiple catalogue matches", reason)
        self.assertIn("Crocin 500", reason)
        self.assertIn("Crocin 650", reason)

    def test_llm_cannot_override_canonical_catalogue_attributes(self):
        """Catalogue metadata takes precedence; strength mismatches are not silently bound but kept UNRESOLVED."""
        # Chemist message mentions Dolo 650 with contradictory strength 500mg
        result = process_stock_message("Dolo 650 500mg 20 strips available", self.pharmacy_id, self.chemist_id, self.repo)
        self.assertEqual(result.status, ProposalServiceStatus.UNRESOLVED)
        self.assertEqual(len(result.created_proposals), 0)
        self.assertEqual(len(result.unresolved_items), 1)
        self.assertIn("Strength mismatch", result.unresolved_items[0].reason)

    def test_similar_brand_names_not_silently_treated_as_equivalent(self):
        """Dolo 500 is distinct from Dolo 650; an unregistered strength variant is not silently aliased."""
        result = process_stock_message("Dolo 500 20 strips available", self.pharmacy_id, self.chemist_id, self.repo)
        # Dolo 500 is not in catalogue, search for "Dolo 500" returns no match (or partial)
        # It must NOT be silently assigned to Dolo 650 at full confidence
        if result.status == ProposalServiceStatus.SUCCESS:
            prop = result.created_proposals[0]
            self.assertTrue(prop.flagged_for_review, "Similar brand name variant must be flagged for review")
        else:
            self.assertEqual(result.status, ProposalServiceStatus.UNRESOLVED)


class TestPhase4QuantityStrengthStatusValidation(unittest.TestCase):
    """Step 3: Strength vs stock quantity separation, word quantities, and contradictory combinations."""

    def test_strength_separated_from_stock_quantity(self):
        """Potency (650mg) is never mistaken for available stock quantity (20 strips)."""
        prop = parse_stock_message("Dolo 650mg 20 strips available", use_llm=False)
        self.assertIsNotNone(prop)
        assert prop is not None
        item = prop.items[0]
        self.assertEqual(item.strength, "650mg")
        self.assertEqual(item.quantity, 20)
        self.assertEqual(item.dosage_form, "Strip")

    def test_missing_quantity_remains_none(self):
        """Unspecified quantities remain None without fabricating values."""
        prop = parse_stock_message("Crocin available", use_llm=False)
        self.assertIsNotNone(prop)
        assert prop is not None
        item = prop.items[0]
        self.assertIsNone(item.quantity)
        self.assertEqual(item.status, StockActionEnum.IN_STOCK)

    def test_contradictory_out_of_stock_positive_quantity_cleared(self):
        """Positive count accompanying out-of-stock statement is cleared to None to prevent contradiction."""
        prop = parse_stock_message("Crocin 20 strips out of stock", use_llm=False)
        self.assertIsNotNone(prop)
        assert prop is not None
        item = prop.items[0]
        self.assertEqual(item.status, StockActionEnum.OUT_OF_STOCK)
        self.assertIsNone(item.quantity)

    def test_explicit_zero_quantity_preserved(self):
        """Explicitly reported zero count is preserved alongside OUT_OF_STOCK."""
        prop = parse_stock_message("Crocin 0 strips hai", use_llm=False)
        self.assertIsNotNone(prop)
        assert prop is not None
        item = prop.items[0]
        self.assertEqual(item.status, StockActionEnum.OUT_OF_STOCK)
        self.assertEqual(item.quantity, 0)


class TestPhase4ConfidenceAndReviewRules(unittest.TestCase):
    """Step 4: Strict verification of flagged_for_review = (confidence < 0.8) OR is_schedule_h."""

    def test_confidence_boundaries_non_schedule_h(self):
        """Boundary tests at 0.79, 0.80, and 0.81 for non-Schedule H medicine."""
        # 0.79 -> flagged
        self.assertTrue(compute_review_flag(0.79, False))
        # 0.80 -> NOT flagged
        self.assertFalse(compute_review_flag(0.80, False))
        # 0.81 -> NOT flagged
        self.assertFalse(compute_review_flag(0.81, False))

    def test_schedule_h_always_flagged(self):
        """Schedule H medicines are strictly flagged even at high confidence."""
        self.assertTrue(compute_review_flag(0.80, True))
        self.assertTrue(compute_review_flag(0.95, True))
        self.assertTrue(compute_review_flag(1.00, True))

    def test_confidence_validation_bounds(self):
        """Confidence outside [0.0, 1.0] raises ValueError."""
        with self.assertRaises(ValueError):
            compute_review_flag(-0.01, False)
        with self.assertRaises(ValueError):
            compute_review_flag(1.01, False)
        with self.assertRaises(ValueError):
            compute_review_flag(None, False)  # type: ignore[arg-type]

    def test_is_schedule_h_type_validation(self):
        """Non-boolean is_schedule_h raises TypeError."""
        with self.assertRaises(TypeError):
            compute_review_flag(0.9, "yes")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            compute_review_flag(0.9, None)  # type: ignore[arg-type]


class TestPhase4ProposalAndInventorySafety(unittest.TestCase):
    """Step 5: Safety invariants — parsing isolation, PENDING creation, authorization, transactional apply, and rejection."""

    def setUp(self) -> None:
        self.repo = InMemoryProposalAndConfirmationRepo()
        self.pharmacy_id = 5
        self.chemist_id = 42
        self.repo.link_user(self.chemist_id, self.pharmacy_id)
        self.repo.add_medicine(
            CatalogueMedicine(
                id=1,
                brand_name="Dolo 650",
                generic_name="paracetamol",
                strength="650mg",
                form="strip",
            )
        )

    def test_parsing_alone_has_zero_database_access(self):
        """parse_stock_message is a pure extraction function that never touches database tables."""
        # Invoke parser directly
        proposal = parse_stock_message("Dolo 650 20 strips available", use_llm=False)
        self.assertIsNotNone(proposal)
        self.assertIsInstance(proposal, StockUpdateProposal)
        # Repo was not even provided; verify zero proposals created
        self.assertEqual(len(self.repo.proposals), 0)
        self.assertEqual(len(self.repo.inventory), 0)

    def test_proposal_creation_creates_strictly_pending_and_leaves_inventory_clean(self):
        """process_stock_message creates only PENDING proposal and leaves inventory completely untouched."""
        result = process_stock_message("Dolo 650 20 strips available", self.pharmacy_id, self.chemist_id, self.repo)
        self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)
        self.assertEqual(len(result.created_proposals), 1)
        prop = result.created_proposals[0]
        self.assertEqual(prop.status, "PENDING")
        self.assertIsNone(prop.confirmed_by)
        self.assertIsNone(prop.confirmed_at)
        # Confirmed inventory remains completely empty
        self.assertEqual(len(self.repo.inventory), 0)
        self.assertEqual(len(self.repo.history), 0)

    def test_confirmation_checks_authorization_and_scoping(self):
        """Confirmation strictly validates user-pharmacy linkage and pharmacy scope."""
        # Create a pending proposal
        result = process_stock_message("Dolo 650 20 strips available", self.pharmacy_id, self.chemist_id, self.repo)
        prop_id = result.created_proposals[0].id

        # 1. Unlinked user cannot confirm
        unlinked_user_id = 999
        with self.assertRaises(UserNotLinkedToPharmacyError):
            confirm_and_apply_proposal(prop_id, unlinked_user_id, self.pharmacy_id, self.repo)

        # 2. Mismatched pharmacy scope cannot confirm
        other_pharmacy_id = 99
        self.repo.link_user(self.chemist_id, other_pharmacy_id)
        with self.assertRaises(ProposalPharmacyMismatchError):
            confirm_and_apply_proposal(prop_id, self.chemist_id, other_pharmacy_id, self.repo)

    def test_confirmation_checks_status_and_expiry(self):
        """Confirmation rejects non-PENDING or expired proposals."""
        result = process_stock_message("Dolo 650 20 strips available", self.pharmacy_id, self.chemist_id, self.repo)
        prop_id = result.created_proposals[0].id

        # Confirm once -> SUCCESS
        res = confirm_and_apply_proposal(prop_id, self.chemist_id, self.pharmacy_id, self.repo)
        self.assertTrue(res.success)
        self.assertEqual(res.status, "CONFIRMED")

        # Try to confirm again -> ProposalNotPendingError
        with self.assertRaises(ProposalNotPendingError):
            confirm_and_apply_proposal(prop_id, self.chemist_id, self.pharmacy_id, self.repo)

        # Create another proposal and test expiration
        res2 = process_stock_message("Dolo 650 10 strips available", self.pharmacy_id, self.chemist_id, self.repo)
        prop2_id = res2.created_proposals[0].id
        expired_time = datetime.now(timezone.utc) + timedelta(hours=25)
        with self.assertRaises(ProposalExpiredError):
            confirm_and_apply_proposal(prop2_id, self.chemist_id, self.pharmacy_id, self.repo, current_time=expired_time)

    def test_rejection_leaves_inventory_untouched(self):
        """Rejecting a proposal transitions status to REJECTED and makes zero inventory mutations."""
        result = process_stock_message("Dolo 650 20 strips available", self.pharmacy_id, self.chemist_id, self.repo)
        prop_id = result.created_proposals[0].id

        rej = reject_proposal_workflow(
            prop_id,
            self.chemist_id,
            self.pharmacy_id,
            reason="Incorrect batch",
            repository=self.repo,
        )
        self.assertTrue(rej.success)
        self.assertEqual(rej.status, "REJECTED")

        # Verify proposal record updated
        stored = self.repo.get_proposal_by_id(prop_id)
        assert stored is not None
        self.assertEqual(stored.status, "REJECTED")
        self.assertEqual(stored.review_note, "Incorrect batch")

        # Inventory remains empty
        self.assertEqual(len(self.repo.inventory), 0)
        self.assertEqual(len(self.repo.history), 0)


if __name__ == "__main__":
    unittest.main()
