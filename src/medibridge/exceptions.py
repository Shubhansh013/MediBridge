"""Domain exceptions for MediBridge database and service integrity.

Matches the exact exception specifications defined in docs/CONTRACT.md Section C.
"""

from __future__ import annotations

from typing import Any

try:
    import medibridge_db.exceptions as _db_exc
except (ImportError, ModuleNotFoundError):
    _db_exc = None

if _db_exc is not None:
    ProposalNotPendingError = _db_exc.ProposalNotPendingError
    ProposalExpiredError = _db_exc.ProposalExpiredError
    ProposalPharmacyMismatchError = _db_exc.ProposalPharmacyMismatchError
    InventoryNotFoundError = _db_exc.InventoryNotFoundError
    HistoryAlreadyUndoneError = _db_exc.HistoryAlreadyUndoneError
    NewerChangeExistsError = _db_exc.NewerChangeExistsError
    CannotUndoUndoError = _db_exc.CannotUndoUndoError
    StockStatusQuantityMismatchError = _db_exc.StockStatusQuantityMismatchError
    PharmacyScopeMismatchError = _db_exc.PharmacyScopeMismatchError
else:
    class ProposalNotPendingError(Exception):  # type: ignore[no-redef]
        """Raised when an operation requires a PENDING proposal but received another status."""

    class ProposalExpiredError(Exception):  # type: ignore[no-redef]
        """Raised when an operation attempts to confirm or apply an expired proposal."""

    class ProposalPharmacyMismatchError(Exception):  # type: ignore[no-redef]
        """Raised when a proposal's pharmacy ID does not match the target pharmacy scope."""

    class InventoryNotFoundError(Exception):  # type: ignore[no-redef]
        """Raised when an inventory row required for updating or history is not found."""

    class HistoryAlreadyUndoneError(Exception):  # type: ignore[no-redef]
        """Raised when attempting to undo a history row that has already been undone."""

    class NewerChangeExistsError(Exception):  # type: ignore[no-redef]
        """Raised when attempting to undo an inventory change when a newer change exists."""

    class CannotUndoUndoError(Exception):  # type: ignore[no-redef]
        """Raised when attempting to undo a history row whose change_type is UNDO."""

    class StockStatusQuantityMismatchError(Exception):  # type: ignore[no-redef]
        """Raised when stock status contradicts quantity (e.g. OUT_OF_STOCK with quantity > 0)."""

    class PharmacyScopeMismatchError(Exception):  # type: ignore[no-redef]
        """Raised when a mutated entity belongs to a different pharmacy than the request."""


class UserNotLinkedToPharmacyError(Exception):
    """Raised when acting user is not linked to the specified pharmacy in user_pharmacies."""
