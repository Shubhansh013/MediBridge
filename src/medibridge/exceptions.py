"""Domain exceptions for MediBridge database and service integrity.

Matches the exact exception specifications defined in docs/CONTRACT.md Section C.
"""

from __future__ import annotations


class ProposalNotPendingError(Exception):
    """Raised when an operation requires a PENDING proposal but received another status."""


class ProposalExpiredError(Exception):
    """Raised when an operation attempts to confirm or apply an expired proposal."""


class ProposalPharmacyMismatchError(Exception):
    """Raised when a proposal's pharmacy ID does not match the target pharmacy scope."""


class InventoryNotFoundError(Exception):
    """Raised when an inventory row required for updating or history is not found."""


class HistoryAlreadyUndoneError(Exception):
    """Raised when attempting to undo a history row that has already been undone."""


class NewerChangeExistsError(Exception):
    """Raised when attempting to undo an inventory change when a newer change exists."""


class CannotUndoUndoError(Exception):
    """Raised when attempting to undo a history row whose change_type is UNDO."""


class StockStatusQuantityMismatchError(Exception):
    """Raised when stock status contradicts quantity (e.g. OUT_OF_STOCK with quantity > 0)."""


class PharmacyScopeMismatchError(Exception):
    """Raised when a mutated entity belongs to a different pharmacy than the request."""


class UserNotLinkedToPharmacyError(Exception):
    """Raised when acting user is not linked to the specified pharmacy in user_pharmacies."""
