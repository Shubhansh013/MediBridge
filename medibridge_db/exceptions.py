"""
Exception classes for MediBridge database operations.

All exceptions are raised by operations layer functions to indicate
integrity violations that the service layer (Member 1) should handle.
"""


class ProposalNotPendingError(Exception):
    """Raised when a proposal is not in PENDING state where required."""
    pass


class ProposalExpiredError(Exception):
    """Raised when a proposal has expired."""
    pass


class ProposalPharmacyMismatchError(Exception):
    """Raised when a proposal does not belong to the specified pharmacy."""
    pass


class InventoryNotFoundError(Exception):
    """Raised when an inventory record is not found."""
    pass


class HistoryAlreadyUndoneError(Exception):
    """Raised when attempting to undo a history entry that is already undone."""
    pass


class NewerChangeExistsError(Exception):
    """Raised when attempting to undo a history entry when a newer change exists."""
    pass


class CannotUndoUndoError(Exception):
    """Raised when attempting to undo an undo history entry."""
    pass


class StockStatusQuantityMismatchError(Exception):
    """Raised when stock status and quantity are inconsistent."""
    pass


class PharmacyScopeMismatchError(Exception):
    """Raised when a record's pharmacy_id does not match the specified pharmacy_id."""
    pass