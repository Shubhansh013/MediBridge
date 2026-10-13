"""Unit tests for the audit history and stock change undo orchestration service.

Verifies:
- Successful undo creates an append-only UNDO history row and restores inventory
- Double undo on same history row is rejected with HistoryAlreadyUndoneError
- Attempting to undo an UNDO row is rejected with CannotUndoUndoError
- Attempting to undo an older change when a newer change exists is rejected with NewerChangeExistsError
- Pharmacy scope mismatch is rejected with PharmacyScopeMismatchError
- Unauthorized user is rejected with UserNotLinkedToPharmacyError
- Audit immutability: history count strictly increases; zero rows are ever deleted
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
import unittest

from medibridge.exceptions import (
    CannotUndoUndoError,
    HistoryAlreadyUndoneError,
    NewerChangeExistsError,
    PharmacyScopeMismatchError,
    UserNotLinkedToPharmacyError,
)
from medibridge.schemas.stock import StockActionEnum
from medibridge.services.confirmation_service import InventoryRecord
from medibridge.services.history_service import (
    HistoryRepositoryProtocol,
    StockHistoryRecord,
    UndoEvaluationResult,
    UndoExecutionResult,
    evaluate_can_undo,
    orchestrate_undo,
)


class InMemoryHistoryRepository:
    """In-memory test double implementing HistoryRepositoryProtocol."""

    def __init__(self) -> None:
        self.linked_users: set[tuple[int, int]] = set()  # (user_id, pharmacy_id)
        self.history: dict[int, StockHistoryRecord] = {}
        self.inventory: dict[int, InventoryRecord] = {}  # inventory_id -> InventoryRecord
        self._next_history_id: int = 301

    def link_user(self, user_id: int, pharmacy_id: int) -> None:
        self.linked_users.add((user_id, pharmacy_id))

    def add_history(self, record: StockHistoryRecord) -> None:
        self.history[record.id] = record

    def add_inventory(self, record: InventoryRecord) -> None:
        self.inventory[record.id] = record

    def is_user_linked_to_pharmacy(self, user_id: int, pharmacy_id: int) -> bool:
        return (user_id, pharmacy_id) in self.linked_users

    def get_history_by_id(self, history_id: int) -> Optional[StockHistoryRecord]:
        return self.history.get(history_id)

    def get_inventory_history(self, inventory_id: int, limit: int = 50) -> list[StockHistoryRecord]:
        rows = [h for h in self.history.values() if h.inventory_id == inventory_id]
        rows.sort(key=lambda x: x.id)
        return rows[:limit]

    def get_pharmacy_history(self, pharmacy_id: int, limit: int = 50) -> list[StockHistoryRecord]:
        rows = [h for h in self.history.values() if h.pharmacy_id == pharmacy_id]
        rows.sort(key=lambda x: x.id)
        return rows[:limit]

    def get_user_history(self, user_id: int, limit: int = 50) -> list[StockHistoryRecord]:
        rows = [h for h in self.history.values() if h.changed_by == user_id]
        rows.sort(key=lambda x: x.id)
        return rows[:limit]

    def has_undo_for_history(self, history_id: int) -> bool:
        return any(h.undo_of_history_id == history_id for h in self.history.values())

    def has_newer_history_for_inventory(self, inventory_id: int, history_id: int) -> bool:
        return any(h.inventory_id == inventory_id and h.id > history_id for h in self.history.values())

    def undo_last_change(
        self,
        history_id: int,
        undone_by_user_id: int,
        pharmacy_id: int,
    ) -> tuple[StockHistoryRecord, InventoryRecord]:
        target = self.history[history_id]
        current_inv = self.inventory[target.inventory_id]
        now = datetime.now(timezone.utc)

        # 1. Create append-only UNDO history row
        undo_record = StockHistoryRecord(
            id=self._next_history_id,
            inventory_id=target.inventory_id,
            pharmacy_id=pharmacy_id,
            medicine_id=target.medicine_id,
            changed_by=undone_by_user_id,
            source_proposal_id=target.source_proposal_id,
            change_type="UNDO",
            undo_of_history_id=history_id,
            previous_stock_status=current_inv.stock_status,
            new_stock_status=target.previous_stock_status or StockActionEnum.IN_STOCK,
            previous_quantity=current_inv.quantity,
            new_quantity=target.previous_quantity,
            previous_last_confirmed_at=current_inv.last_confirmed_at,
            change_reason=f"undo of #{history_id}",
            created_at=now,
        )
        self.history[self._next_history_id] = undo_record
        self._next_history_id += 1

        # 2. Restore inventory row to previous values
        restored_inv = current_inv.model_copy(
            update={
                "stock_status": target.previous_stock_status or StockActionEnum.IN_STOCK,
                "quantity": target.previous_quantity,
                "last_confirmed_at": target.previous_last_confirmed_at or now,
                "last_updated_by": undone_by_user_id,
                "last_update_source": "undo",
            }
        )
        self.inventory[target.inventory_id] = restored_inv

        return undo_record, restored_inv


class TestHistoryService(unittest.TestCase):
    """Test suite covering audit history reads and undo orchestration."""

    def setUp(self) -> None:
        self.repo = InMemoryHistoryRepository()
        self.pharmacy_id = 5
        self.chemist_id = 42
        self.repo.link_user(self.chemist_id, self.pharmacy_id)

        self.now = datetime.now(timezone.utc)
        self.inventory_id = 7
        self.medicine_id = 12

        # Inventory is currently OUT_OF_STOCK / 0
        self.initial_inv = InventoryRecord(
            id=self.inventory_id,
            pharmacy_id=self.pharmacy_id,
            medicine_id=self.medicine_id,
            stock_status=StockActionEnum.OUT_OF_STOCK,
            quantity=0,
            last_confirmed_at=self.now,
            last_updated_by=self.chemist_id,
            last_update_source="proposal_apply",
        )
        self.repo.add_inventory(self.initial_inv)

        # History row 201: STOCK_UPDATE changed from IN_STOCK/50 to OUT_OF_STOCK/0
        self.history_201 = StockHistoryRecord(
            id=201,
            inventory_id=self.inventory_id,
            pharmacy_id=self.pharmacy_id,
            medicine_id=self.medicine_id,
            changed_by=self.chemist_id,
            source_proposal_id=101,
            change_type="STOCK_UPDATE",
            undo_of_history_id=None,
            previous_stock_status=StockActionEnum.IN_STOCK,
            new_stock_status=StockActionEnum.OUT_OF_STOCK,
            previous_quantity=50,
            new_quantity=0,
            previous_last_confirmed_at=self.now,
            change_reason="proposal #101",
            created_at=self.now,
        )
        self.repo.add_history(self.history_201)

    def test_successful_undo_and_inventory_restoration(self) -> None:
        """Test 1: Undo restores inventory to previous values and writes UNDO history row."""
        # Check can_undo
        eval_result = evaluate_can_undo(
            history_id=201,
            user_id=self.chemist_id,
            pharmacy_id=self.pharmacy_id,
            repository=self.repo,
        )
        self.assertTrue(eval_result.can_undo)
        self.assertIsNone(eval_result.reason)

        # Execute undo
        undo_result = orchestrate_undo(
            history_id=201,
            user_id=self.chemist_id,
            pharmacy_id=self.pharmacy_id,
            repository=self.repo,
        )

        self.assertTrue(undo_result.success)
        self.assertEqual(undo_result.undone_history_id, 201)

        # Inventory must be restored to IN_STOCK / 50
        self.assertEqual(undo_result.restored_inventory.stock_status, StockActionEnum.IN_STOCK)
        self.assertEqual(undo_result.restored_inventory.quantity, 50)
        self.assertEqual(undo_result.restored_inventory.last_update_source, "undo")

        # Undo history row must be created with undo_of_history_id=201
        undo_row = undo_result.undo_history_record
        self.assertEqual(undo_row.change_type, "UNDO")
        self.assertEqual(undo_row.undo_of_history_id, 201)
        self.assertEqual(undo_row.previous_stock_status, StockActionEnum.OUT_OF_STOCK)
        self.assertEqual(undo_row.new_stock_status, StockActionEnum.IN_STOCK)

        # Immutability: Total history records must be 2 (original 201 + undo row)
        self.assertEqual(len(self.repo.history), 2)
        self.assertIn(201, self.repo.history)

    def test_double_undo_rejected(self) -> None:
        """Test 2: Target history row cannot be undone twice."""
        # First undo succeeds
        orchestrate_undo(
            history_id=201,
            user_id=self.chemist_id,
            pharmacy_id=self.pharmacy_id,
            repository=self.repo,
        )

        # Evaluate can_undo on same row
        eval_result = evaluate_can_undo(
            history_id=201,
            user_id=self.chemist_id,
            pharmacy_id=self.pharmacy_id,
            repository=self.repo,
        )
        self.assertFalse(eval_result.can_undo)
        self.assertIn("already been undone", eval_result.reason or "")

        # Second undo raises HistoryAlreadyUndoneError
        with self.assertRaises(HistoryAlreadyUndoneError):
            orchestrate_undo(
                history_id=201,
                user_id=self.chemist_id,
                pharmacy_id=self.pharmacy_id,
                repository=self.repo,
            )

    def test_cannot_undo_undo_row(self) -> None:
        """Test 3: An UNDO change row cannot be undone."""
        undo_res = orchestrate_undo(
            history_id=201,
            user_id=self.chemist_id,
            pharmacy_id=self.pharmacy_id,
            repository=self.repo,
        )
        undo_row_id = undo_res.undo_history_record.id

        # Evaluate can_undo on the undo row
        eval_result = evaluate_can_undo(
            history_id=undo_row_id,
            user_id=self.chemist_id,
            pharmacy_id=self.pharmacy_id,
            repository=self.repo,
        )
        self.assertFalse(eval_result.can_undo)
        self.assertIn("Cannot undo an UNDO entry", eval_result.reason or "")

        # Attempt to undo undo raises CannotUndoUndoError
        with self.assertRaises(CannotUndoUndoError):
            orchestrate_undo(
                history_id=undo_row_id,
                user_id=self.chemist_id,
                pharmacy_id=self.pharmacy_id,
                repository=self.repo,
            )

    def test_newer_change_exists_rejects_undo(self) -> None:
        """Test 4: Cannot undo an older change when a newer change exists for same inventory."""
        # Add newer change row 202 on same inventory row
        history_202 = StockHistoryRecord(
            id=202,
            inventory_id=self.inventory_id,
            pharmacy_id=self.pharmacy_id,
            medicine_id=self.medicine_id,
            changed_by=self.chemist_id,
            source_proposal_id=102,
            change_type="STOCK_UPDATE",
            undo_of_history_id=None,
            previous_stock_status=StockActionEnum.OUT_OF_STOCK,
            new_stock_status=StockActionEnum.IN_STOCK,
            previous_quantity=0,
            new_quantity=10,
            previous_last_confirmed_at=self.now,
            change_reason="proposal #102",
            created_at=self.now,
        )
        self.repo.add_history(history_202)

        # Attempting to undo older row 201 must fail
        eval_result = evaluate_can_undo(
            history_id=201,
            user_id=self.chemist_id,
            pharmacy_id=self.pharmacy_id,
            repository=self.repo,
        )
        self.assertFalse(eval_result.can_undo)
        self.assertIn("newer change exists", eval_result.reason or "")

        with self.assertRaises(NewerChangeExistsError):
            orchestrate_undo(
                history_id=201,
                user_id=self.chemist_id,
                pharmacy_id=self.pharmacy_id,
                repository=self.repo,
            )

    def test_unauthorized_user_undo_rejected(self) -> None:
        """Test 5: Unlinked user cannot evaluate or execute undo."""
        unlinked_user = 999
        eval_result = evaluate_can_undo(
            history_id=201,
            user_id=unlinked_user,
            pharmacy_id=self.pharmacy_id,
            repository=self.repo,
        )
        self.assertFalse(eval_result.can_undo)

        with self.assertRaises(UserNotLinkedToPharmacyError):
            orchestrate_undo(
                history_id=201,
                user_id=unlinked_user,
                pharmacy_id=self.pharmacy_id,
                repository=self.repo,
            )

    def test_pharmacy_mismatch_undo_rejected(self) -> None:
        """Test 6: User cannot undo a change under a different pharmacy ID."""
        other_pharmacy = 9
        self.repo.link_user(self.chemist_id, other_pharmacy)

        eval_result = evaluate_can_undo(
            history_id=201,
            user_id=self.chemist_id,
            pharmacy_id=other_pharmacy,
            repository=self.repo,
        )
        self.assertFalse(eval_result.can_undo)

        with self.assertRaises(PharmacyScopeMismatchError):
            orchestrate_undo(
                history_id=201,
                user_id=self.chemist_id,
                pharmacy_id=other_pharmacy,
                repository=self.repo,
            )

    def test_history_immutability(self) -> None:
        """Test 7: Ensure history records are never deleted."""
        initial_count = len(self.repo.history)
        self.assertEqual(initial_count, 1)

        orchestrate_undo(
            history_id=201,
            user_id=self.chemist_id,
            pharmacy_id=self.pharmacy_id,
            repository=self.repo,
        )

        # Count increases by 1; row 201 is unmodified
        self.assertEqual(len(self.repo.history), 2)
        row_201 = self.repo.get_history_by_id(201)
        self.assertIsNotNone(row_201)
        assert row_201 is not None
        self.assertEqual(row_201.change_type, "STOCK_UPDATE")
        self.assertIsNone(row_201.undo_of_history_id)


if __name__ == "__main__":
    unittest.main()
