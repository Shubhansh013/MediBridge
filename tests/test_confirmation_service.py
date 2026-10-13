"""Unit tests for the stock proposal confirmation and rejection workflow service.

Verifies:
- Authorized chemist confirming a valid proposal updates inventory and marks proposal CONFIRMED
- Rejection marks proposal REJECTED without mutating inventory
- Unauthorized users rejected with UserNotLinkedToPharmacyError
- Pharmacy scope mismatch rejected with ProposalPharmacyMismatchError
- Non-pending proposals (already CONFIRMED, REJECTED, EXPIRED) rejected with ProposalNotPendingError
- Expired proposals rejected at apply time with ProposalExpiredError
- Stock status and quantity mismatches rejected with StockStatusQuantityMismatchError
- Expiry cron sweep marks overdue proposals EXPIRED
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
import unittest

from medibridge.exceptions import (
    InventoryNotFoundError,
    ProposalExpiredError,
    ProposalNotPendingError,
    ProposalPharmacyMismatchError,
    StockStatusQuantityMismatchError,
    UserNotLinkedToPharmacyError,
)
from medibridge.schemas.stock import StockActionEnum
from medibridge.services.confirmation_service import (
    ConfirmationRepositoryProtocol,
    ConfirmationResult,
    InventoryRecord,
    RejectionResult,
    check_proposal_expiry,
    confirm_and_apply_proposal,
    expire_proposals_job,
    reject_proposal_workflow,
)
from medibridge.services.proposal_service import StoredProposalRecord


class InMemoryConfirmationRepository:
    """In-memory test double implementing ConfirmationRepositoryProtocol."""

    def __init__(self) -> None:
        self.linked_users: set[tuple[int, int]] = set()  # (user_id, pharmacy_id)
        self.proposals: dict[int, StoredProposalRecord] = {}
        self.inventory: dict[tuple[int, int], InventoryRecord] = {}  # (pharmacy_id, medicine_id)
        self.history: list[dict] = []
        self._next_inventory_id: int = 1
        self._next_history_id: int = 1

    def link_user(self, user_id: int, pharmacy_id: int) -> None:
        self.linked_users.add((user_id, pharmacy_id))

    def add_proposal(self, proposal: StoredProposalRecord) -> None:
        self.proposals[proposal.id] = proposal

    def is_user_linked_to_pharmacy(self, user_id: int, pharmacy_id: int) -> bool:
        return (user_id, pharmacy_id) in self.linked_users

    def get_proposal_by_id(self, proposal_id: int) -> Optional[StoredProposalRecord]:
        return self.proposals.get(proposal_id)

    def apply_confirmed_proposal(
        self,
        proposal_id: int,
        confirmed_by_user_id: int,
        pharmacy_id: int,
        current_time: Optional[datetime] = None,
    ) -> InventoryRecord:
        proposal = self.proposals[proposal_id]
        now = current_time or datetime.now(timezone.utc)

        # 1. Update proposal to CONFIRMED
        updated_proposal = proposal.model_copy(
            update={
                "status": "CONFIRMED",
                "confirmed_by": confirmed_by_user_id,
                "confirmed_at": now,
            }
        )
        self.proposals[proposal_id] = updated_proposal

        # 2. Update inventory (assume medicine_id=1 for test stub)
        medicine_id = 1
        inv_key = (pharmacy_id, medicine_id)
        prev_inv = self.inventory.get(inv_key)

        new_inv_id = prev_inv.id if prev_inv else self._next_inventory_id
        if not prev_inv:
            self._next_inventory_id += 1

        inv_record = InventoryRecord(
            id=new_inv_id,
            pharmacy_id=pharmacy_id,
            medicine_id=medicine_id,
            stock_status=proposal.parsed_intent,
            quantity=proposal.parsed_quantity,
            last_confirmed_at=now,
            last_updated_by=confirmed_by_user_id,
            last_update_source="proposal_apply",
        )
        self.inventory[inv_key] = inv_record

        # 3. Create stock_history record
        self.history.append(
            {
                "id": self._next_history_id,
                "inventory_id": new_inv_id,
                "pharmacy_id": pharmacy_id,
                "medicine_id": medicine_id,
                "changed_by": confirmed_by_user_id,
                "source_proposal_id": proposal_id,
                "change_type": "STOCK_UPDATE",
                "undo_of_history_id": None,
                "previous_stock_status": prev_inv.stock_status if prev_inv else None,
                "new_stock_status": proposal.parsed_intent,
                "previous_quantity": prev_inv.quantity if prev_inv else None,
                "new_quantity": proposal.parsed_quantity,
                "created_at": now,
            }
        )
        self._next_history_id += 1

        return inv_record

    def reject_proposal(
        self,
        proposal_id: int,
        rejected_by_user_id: int,
        pharmacy_id: int,
        reason: Optional[str] = None,
    ) -> StoredProposalRecord:
        proposal = self.proposals[proposal_id]
        updated = proposal.model_copy(
            update={
                "status": "REJECTED",
                "confirmed_by": rejected_by_user_id,
                "review_note": reason,
            }
        )
        self.proposals[proposal_id] = updated
        return updated

    def expire_overdue_proposals(self, cutoff: datetime) -> int:
        count = 0
        for p_id, p in list(self.proposals.items()):
            if p.status == "PENDING" and p.expires_at < cutoff:
                self.proposals[p_id] = p.model_copy(update={"status": "EXPIRED"})
                count += 1
        return count


class TestConfirmationService(unittest.TestCase):
    """Test suite covering proposal confirmation, rejection, and expiry validation."""

    def setUp(self) -> None:
        self.repo = InMemoryConfirmationRepository()
        self.pharmacy_id = 5
        self.chemist_id = 42
        self.repo.link_user(self.chemist_id, self.pharmacy_id)

        self.now = datetime.now(timezone.utc)
        self.valid_proposal = StoredProposalRecord(
            id=101,
            pharmacy_id=self.pharmacy_id,
            chemist_user_id=self.chemist_id,
            raw_message="Dolo 650 20 strips available",
            brand_name="Dolo 650",
            parsed_salt="paracetamol",
            parsed_strength="650mg",
            parsed_form="strip",
            parsed_quantity=20,
            parsed_intent=StockActionEnum.IN_STOCK,
            confidence=1.0,
            status="PENDING",
            flagged_for_review=False,
            expires_at=self.now + timedelta(hours=24),
            created_at=self.now,
        )
        self.repo.add_proposal(self.valid_proposal)

    def test_successful_confirmation_and_apply(self) -> None:
        """Test 1: Valid confirmation applies inventory change and sets status CONFIRMED."""
        result = confirm_and_apply_proposal(
            proposal_id=101,
            user_id=self.chemist_id,
            pharmacy_id=self.pharmacy_id,
            repository=self.repo,
            current_time=self.now,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.status, "CONFIRMED")
        self.assertEqual(result.updated_inventory.stock_status, StockActionEnum.IN_STOCK)
        self.assertEqual(result.updated_inventory.quantity, 20)

        # Check proposal was marked CONFIRMED
        persisted_p = self.repo.get_proposal_by_id(101)
        self.assertIsNotNone(persisted_p)
        assert persisted_p is not None
        self.assertEqual(persisted_p.status, "CONFIRMED")
        self.assertEqual(persisted_p.confirmed_by, self.chemist_id)

        # Check history was created
        self.assertEqual(len(self.repo.history), 1)
        self.assertEqual(self.repo.history[0]["change_type"], "STOCK_UPDATE")

    def test_successful_rejection_leaves_inventory_untouched(self) -> None:
        """Test 2: Rejection updates proposal status without mutating inventory."""
        result = reject_proposal_workflow(
            proposal_id=101,
            user_id=self.chemist_id,
            pharmacy_id=self.pharmacy_id,
            reason="Incorrect stock count",
            repository=self.repo,
            current_time=self.now,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.status, "REJECTED")
        self.assertEqual(result.reason, "Incorrect stock count")

        # Check proposal was marked REJECTED
        persisted_p = self.repo.get_proposal_by_id(101)
        self.assertIsNotNone(persisted_p)
        assert persisted_p is not None
        self.assertEqual(persisted_p.status, "REJECTED")
        self.assertEqual(persisted_p.review_note, "Incorrect stock count")

        # Inventory and history must remain empty
        self.assertEqual(len(self.repo.inventory), 0)
        self.assertEqual(len(self.repo.history), 0)

    def test_unauthorized_user_confirmation_rejected(self) -> None:
        """Test 3: Unlinked user cannot confirm proposal."""
        unlinked_user = 999
        with self.assertRaises(UserNotLinkedToPharmacyError):
            confirm_and_apply_proposal(
                proposal_id=101,
                user_id=unlinked_user,
                pharmacy_id=self.pharmacy_id,
                repository=self.repo,
            )

    def test_unauthorized_user_rejection_rejected(self) -> None:
        """Test 4: Unlinked user cannot reject proposal."""
        unlinked_user = 999
        with self.assertRaises(UserNotLinkedToPharmacyError):
            reject_proposal_workflow(
                proposal_id=101,
                user_id=unlinked_user,
                pharmacy_id=self.pharmacy_id,
                repository=self.repo,
            )

    def test_nonexistent_proposal_raises_value_error(self) -> None:
        """Test 5: Confirming non-existent proposal ID raises ValueError."""
        with self.assertRaises(ValueError):
            confirm_and_apply_proposal(
                proposal_id=999,
                user_id=self.chemist_id,
                pharmacy_id=self.pharmacy_id,
                repository=self.repo,
            )

    def test_pharmacy_scope_mismatch_raises_error(self) -> None:
        """Test 6: Proposal belonging to pharmacy 5 cannot be confirmed under pharmacy 9."""
        other_pharmacy = 9
        self.repo.link_user(self.chemist_id, other_pharmacy)

        with self.assertRaises(ProposalPharmacyMismatchError):
            confirm_and_apply_proposal(
                proposal_id=101,
                user_id=self.chemist_id,
                pharmacy_id=other_pharmacy,
                repository=self.repo,
            )

    def test_repeated_confirmation_raises_proposal_not_pending_error(self) -> None:
        """Test 7: Attempting to confirm an already CONFIRMED proposal is rejected."""
        # Confirm once
        confirm_and_apply_proposal(
            proposal_id=101,
            user_id=self.chemist_id,
            pharmacy_id=self.pharmacy_id,
            repository=self.repo,
            current_time=self.now,
        )

        # Confirm second time
        with self.assertRaises(ProposalNotPendingError):
            confirm_and_apply_proposal(
                proposal_id=101,
                user_id=self.chemist_id,
                pharmacy_id=self.pharmacy_id,
                repository=self.repo,
                current_time=self.now,
            )

    def test_confirmation_of_rejected_proposal_raises_error(self) -> None:
        """Test 8: Attempting to confirm a REJECTED proposal raises ProposalNotPendingError."""
        reject_proposal_workflow(
            proposal_id=101,
            user_id=self.chemist_id,
            pharmacy_id=self.pharmacy_id,
            repository=self.repo,
            current_time=self.now,
        )

        with self.assertRaises(ProposalNotPendingError):
            confirm_and_apply_proposal(
                proposal_id=101,
                user_id=self.chemist_id,
                pharmacy_id=self.pharmacy_id,
                repository=self.repo,
                current_time=self.now,
            )

    def test_confirmation_of_expired_proposal_raises_proposal_expired_error(self) -> None:
        """Test 9: Attempting to confirm a proposal after expires_at raises ProposalExpiredError."""
        past_time = self.now + timedelta(hours=25)  # 25h later, proposal expired at 24h

        with self.assertRaises(ProposalExpiredError):
            confirm_and_apply_proposal(
                proposal_id=101,
                user_id=self.chemist_id,
                pharmacy_id=self.pharmacy_id,
                repository=self.repo,
                current_time=past_time,
            )

    def test_quantity_mismatch_raises_error(self) -> None:
        """Test 10: Contradictory proposal (OUT_OF_STOCK with positive quantity) raises error."""
        invalid_p = self.valid_proposal.model_copy(
            update={
                "id": 102,
                "parsed_intent": StockActionEnum.OUT_OF_STOCK,
                "parsed_quantity": 10,  # Invalid: OUT_OF_STOCK cannot have quantity > 0
            }
        )
        self.repo.add_proposal(invalid_p)

        with self.assertRaises(StockStatusQuantityMismatchError):
            confirm_and_apply_proposal(
                proposal_id=102,
                user_id=self.chemist_id,
                pharmacy_id=self.pharmacy_id,
                repository=self.repo,
                current_time=self.now,
            )

    def test_expire_overdue_proposals_job(self) -> None:
        """Test 11: Periodic sweep marks overdue PENDING proposals as EXPIRED."""
        # Proposal 201: overdue (expired 1h ago)
        self.repo.add_proposal(
            self.valid_proposal.model_copy(
                update={"id": 201, "expires_at": self.now - timedelta(hours=1)}
            )
        )
        # Proposal 202: overdue (expired 2h ago)
        self.repo.add_proposal(
            self.valid_proposal.model_copy(
                update={"id": 202, "expires_at": self.now - timedelta(hours=2)}
            )
        )
        # Proposal 203: future (expires in 10h)
        self.repo.add_proposal(
            self.valid_proposal.model_copy(
                update={"id": 203, "expires_at": self.now + timedelta(hours=10)}
            )
        )

        expired_count = expire_proposals_job(cutoff=self.now, repository=self.repo)
        self.assertEqual(expired_count, 2)

        self.assertEqual(self.repo.get_proposal_by_id(201).status, "EXPIRED")  # type: ignore[union-attr]
        self.assertEqual(self.repo.get_proposal_by_id(202).status, "EXPIRED")  # type: ignore[union-attr]
        self.assertEqual(self.repo.get_proposal_by_id(203).status, "PENDING")  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
