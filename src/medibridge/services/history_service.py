"""Audit history and stock change undo orchestration service.

Implements the append-only audit trail and undo constraints defined in docs/CONTRACT.md:
- Immutability: History rows are NEVER deleted or modified.
- Single undo: Target row cannot be undone more than once (HistoryAlreadyUndoneError).
- Latest-change rule: Only the newest change on an inventory row can be undone (NewerChangeExistsError).
- Undo protection: An UNDO entry itself cannot be undone (CannotUndoUndoError).
- Pharmacy scope: Acting user and pharmacy must match history record.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Protocol
from pydantic import BaseModel, ConfigDict, Field

from medibridge.exceptions import (
    CannotUndoUndoError,
    HistoryAlreadyUndoneError,
    NewerChangeExistsError,
    PharmacyScopeMismatchError,
    UserNotLinkedToPharmacyError,
)
from medibridge.schemas.stock import StockActionEnum
from medibridge.services.confirmation_service import InventoryRecord


class StockHistoryRecord(BaseModel):
    """Represents a row from the stock_history table as defined in docs/CONTRACT.md."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="Primary key of history row.")
    inventory_id: int = Field(..., description="ID of affected inventory row.")
    pharmacy_id: int = Field(..., description="Pharmacy ID owning this stock.")
    medicine_id: int = Field(..., description="Medicine ID in catalogue.")
    changed_by: int = Field(..., description="Chemist/pharmacist user ID who made the change.")
    source_proposal_id: Optional[int] = Field(default=None, description="Proposal ID if originated from proposal.")
    change_type: str = Field(..., description="'STOCK_UPDATE' or 'UNDO'.")
    undo_of_history_id: Optional[int] = Field(default=None, description="FK to target history row if change_type is UNDO.")
    previous_stock_status: Optional[StockActionEnum] = Field(default=None, description="Stock status before change.")
    new_stock_status: StockActionEnum = Field(..., description="Stock status after change.")
    previous_quantity: Optional[int] = Field(default=None, ge=0, description="Quantity before change.")
    new_quantity: Optional[int] = Field(default=None, ge=0, description="Quantity after change.")
    previous_last_confirmed_at: Optional[datetime] = Field(default=None, description="Previous last_confirmed_at timestamp.")
    change_reason: Optional[str] = Field(default=None, description="Reason for change / undo reference.")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UndoEvaluationResult(BaseModel):
    """Result of checking whether an undo operation can be safely performed."""

    model_config = ConfigDict(extra="forbid")

    can_undo: bool
    history_id: int
    reason: Optional[str] = None


class UndoExecutionResult(BaseModel):
    """Result of successfully orchestrating an undo operation."""

    model_config = ConfigDict(extra="forbid")

    success: bool = True
    undone_history_id: int
    undo_history_record: StockHistoryRecord
    restored_inventory: InventoryRecord


class HistoryRepositoryProtocol(Protocol):
    """Repository protocol covering audit history reads and undo execution."""

    def is_user_linked_to_pharmacy(self, user_id: int, pharmacy_id: int) -> bool:
        """Check if user has an active membership at the pharmacy."""
        ...

    def get_history_by_id(self, history_id: int) -> Optional[StockHistoryRecord]:
        """Fetch a single history row by primary key."""
        ...

    def get_inventory_history(self, inventory_id: int, limit: int = 50) -> list[StockHistoryRecord]:
        """Chronological audit trail for a specific inventory row (oldest first)."""
        ...

    def get_pharmacy_history(self, pharmacy_id: int, limit: int = 50) -> list[StockHistoryRecord]:
        """All history rows for a pharmacy."""
        ...

    def get_user_history(self, user_id: int, limit: int = 50) -> list[StockHistoryRecord]:
        """All history rows created by a user."""
        ...

    def has_undo_for_history(self, history_id: int) -> bool:
        """Check if any history row has undo_of_history_id == history_id."""
        ...

    def has_newer_history_for_inventory(self, inventory_id: int, history_id: int) -> bool:
        """Check if any history row exists with same inventory_id and id > history_id."""
        ...

    def undo_last_change(
        self,
        history_id: int,
        undone_by_user_id: int,
        pharmacy_id: int,
    ) -> tuple[StockHistoryRecord, InventoryRecord]:
        """Single transaction:
        1. Create undo history row (UNDO, undo_of_history_id=history_id).
        2. Restore inventory row to previous values.
        3. Returns (undo_history_row, restored_inventory_row).
        """
        ...


def evaluate_can_undo(
    history_id: int,
    user_id: int,
    pharmacy_id: int,
    repository: HistoryRepositoryProtocol,
) -> UndoEvaluationResult:
    """Evaluates undo eligibility rules without modifying database state.

    Args:
        history_id: Target history row ID.
        user_id: Acting user ID.
        pharmacy_id: Target pharmacy ID.
        repository: Data access layer implementing HistoryRepositoryProtocol.

    Returns:
        UndoEvaluationResult indicating whether undo is permitted and why.
    """
    if not repository.is_user_linked_to_pharmacy(user_id, pharmacy_id):
        return UndoEvaluationResult(
            can_undo=False,
            history_id=history_id,
            reason=f"User {user_id} is not linked to pharmacy {pharmacy_id}.",
        )

    history = repository.get_history_by_id(history_id)
    if history is None:
        return UndoEvaluationResult(
            can_undo=False,
            history_id=history_id,
            reason=f"History record {history_id} does not exist.",
        )

    if history.pharmacy_id != pharmacy_id:
        return UndoEvaluationResult(
            can_undo=False,
            history_id=history_id,
            reason=f"History record belongs to pharmacy {history.pharmacy_id}, not {pharmacy_id}.",
        )

    if history.change_type != "STOCK_UPDATE":
        return UndoEvaluationResult(
            can_undo=False,
            history_id=history_id,
            reason="Cannot undo an UNDO entry.",
        )

    if repository.has_undo_for_history(history_id):
        return UndoEvaluationResult(
            can_undo=False,
            history_id=history_id,
            reason=f"History record {history_id} has already been undone.",
        )

    if repository.has_newer_history_for_inventory(history.inventory_id, history_id):
        return UndoEvaluationResult(
            can_undo=False,
            history_id=history_id,
            reason=f"A newer change exists for inventory row {history.inventory_id}.",
        )

    return UndoEvaluationResult(
        can_undo=True,
        history_id=history_id,
        reason=None,
    )


def orchestrate_undo(
    history_id: int,
    user_id: int,
    pharmacy_id: int,
    repository: HistoryRepositoryProtocol,
) -> UndoExecutionResult:
    """Orchestrates an append-only undo of the latest inventory change.

    Args:
        history_id: Target history row ID to undo.
        user_id: Acting user ID (chemist/pharmacist).
        pharmacy_id: Target pharmacy ID.
        repository: Data access layer implementing HistoryRepositoryProtocol.

    Returns:
        UndoExecutionResult with new UNDO history record and restored inventory.

    Raises:
        UserNotLinkedToPharmacyError: If user is not linked to pharmacy.
        ValueError: If history record does not exist.
        PharmacyScopeMismatchError: If pharmacy_id does not match history record.
        CannotUndoUndoError: If target history record is an UNDO row.
        HistoryAlreadyUndoneError: If target history record has already been undone.
        NewerChangeExistsError: If a newer history record exists for the same inventory.
    """
    # 1. Authorization check
    if not repository.is_user_linked_to_pharmacy(user_id, pharmacy_id):
        raise UserNotLinkedToPharmacyError(
            f"User {user_id} is not linked to pharmacy {pharmacy_id}."
        )

    # 2. History existence check
    history = repository.get_history_by_id(history_id)
    if history is None:
        raise ValueError(f"History record with ID {history_id} does not exist.")

    # 3. Pharmacy scope check
    if history.pharmacy_id != pharmacy_id:
        raise PharmacyScopeMismatchError(
            f"History record {history_id} belongs to pharmacy {history.pharmacy_id}, not {pharmacy_id}."
        )

    # 4. Cannot undo an UNDO row
    if history.change_type != "STOCK_UPDATE":
        raise CannotUndoUndoError(
            f"Target history row {history_id} has change_type '{history.change_type}'. Cannot undo an UNDO."
        )

    # 5. Check if already undone
    if repository.has_undo_for_history(history_id):
        raise HistoryAlreadyUndoneError(
            f"History row {history_id} has already been undone."
        )

    # 6. Latest-change rule: no newer change for same inventory row
    if repository.has_newer_history_for_inventory(history.inventory_id, history_id):
        raise NewerChangeExistsError(
            f"Cannot undo history row {history_id}: newer changes exist on inventory item {history.inventory_id}."
        )

    # 7. Execute undo via repository
    undo_row, restored_inventory = repository.undo_last_change(
        history_id=history_id,
        undone_by_user_id=user_id,
        pharmacy_id=pharmacy_id,
    )

    return UndoExecutionResult(
        undone_history_id=history_id,
        undo_history_record=undo_row,
        restored_inventory=restored_inventory,
    )
