"""Stock proposal confirmation and rejection workflow service.

Implements the chemist confirmation workflow defined in docs/CONTRACT.md:
- Pure data access separation: service enforces authorization and state rules,
  then coordinates persistence with the repository.
- Single transaction apply: updates proposal status to CONFIRMED, records
  confirmed_by and confirmed_at, creates stock_history delta, and mutates inventory.
- Rejection: updates proposal status to REJECTED with optional review notes
  without mutating confirmed inventory.
- Expiry validation: rejects confirmation of expired proposals at apply time.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Protocol
from pydantic import BaseModel, ConfigDict, Field

from medibridge.exceptions import (
    InventoryNotFoundError,
    PharmacyScopeMismatchError,
    ProposalExpiredError,
    ProposalNotPendingError,
    ProposalPharmacyMismatchError,
    StockStatusQuantityMismatchError,
    UserNotLinkedToPharmacyError,
)
from medibridge.schemas.stock import StockActionEnum
from medibridge.services.proposal_adapter import ParsedProposalInput
from medibridge.services.proposal_service import StoredProposalRecord


class InventoryRecord(BaseModel):
    """Represents a row from the inventory table as defined in docs/CONTRACT.md."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="Primary key of inventory row.")
    pharmacy_id: int = Field(..., description="Pharmacy ID owning this stock.")
    medicine_id: int = Field(..., description="Medicine ID in catalogue.")
    stock_status: StockActionEnum = Field(..., description="Current stock status.")
    quantity: Optional[int] = Field(default=None, ge=0, description="Available quantity count.")
    last_confirmed_at: datetime = Field(..., description="Timestamp when confirmed by chemist.")
    last_updated_by: Optional[int] = Field(default=None, description="User ID of chemist who confirmed.")
    last_update_source: Optional[str] = Field(default="proposal_apply", description="Update source audit marker.")


class ConfirmationResult(BaseModel):
    """Result of successfully confirming and applying a proposal."""

    model_config = ConfigDict(extra="forbid")

    success: bool = True
    proposal_id: int
    status: str = "CONFIRMED"
    updated_inventory: InventoryRecord
    confirmed_by_user_id: int
    confirmed_at: datetime


class RejectionResult(BaseModel):
    """Result of rejecting a proposal."""

    model_config = ConfigDict(extra="forbid")

    success: bool = True
    proposal_id: int
    status: str = "REJECTED"
    rejected_by_user_id: int
    reason: Optional[str] = None


class ConfirmationRepositoryProtocol(Protocol):
    """Repository protocol covering proposal confirmation, apply, and rejection."""

    def is_user_linked_to_pharmacy(self, user_id: int, pharmacy_id: int) -> bool:
        """Check if user has an active membership at the pharmacy."""
        ...

    def get_proposal_by_id(self, proposal_id: int) -> Optional[StoredProposalRecord]:
        """Fetch a single stock proposal by primary key."""
        ...

    def apply_confirmed_proposal(
        self,
        proposal_id: int,
        confirmed_by_user_id: int,
        pharmacy_id: int,
        current_time: Optional[datetime] = None,
    ) -> InventoryRecord:
        """Apply a confirmed proposal in a single transaction:
        1. Verify proposal is CONFIRMED (or ready for confirmation) and not expired.
        2. Create stock_history row (STOCK_UPDATE).
        3. Update inventory row.
        4. Update proposal status=CONFIRMED, confirmed_by, confirmed_at.
        """
        ...

    def reject_proposal(
        self,
        proposal_id: int,
        rejected_by_user_id: int,
        pharmacy_id: int,
        reason: Optional[str] = None,
    ) -> StoredProposalRecord:
        """Mark proposal as REJECTED with audit note without modifying inventory."""
        ...

    def expire_overdue_proposals(self, cutoff: datetime) -> int:
        """Update status=EXPIRED where status=PENDING and expires_at < cutoff."""
        ...


def check_proposal_expiry(
    proposal: StoredProposalRecord,
    current_time: Optional[datetime] = None,
) -> bool:
    """Check if proposal has passed its expiration deadline.

    Args:
        proposal: The proposal record to check.
        current_time: Reference time, defaults to UTC now.

    Returns:
        True if the proposal is expired, False otherwise.
    """
    now = current_time or datetime.now(timezone.utc)
    # Handle timezone normalization safely
    exp = proposal.expires_at
    if exp.tzinfo is None and now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    elif exp.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return exp < now


def confirm_and_apply_proposal(
    proposal_id: int,
    user_id: int,
    pharmacy_id: int,
    repository: ConfirmationRepositoryProtocol,
    *,
    current_time: Optional[datetime] = None,
) -> ConfirmationResult:
    """Orchestrates authorization, state verification, and application of a stock proposal.

    Args:
        proposal_id: Target proposal ID.
        user_id: Acting user ID (chemist/pharmacist).
        pharmacy_id: Target pharmacy ID.
        repository: Data access layer implementing ConfirmationRepositoryProtocol.
        current_time: Optional reference time for testing expiry.

    Returns:
        ConfirmationResult containing updated inventory and status.

    Raises:
        UserNotLinkedToPharmacyError: If user is not linked to pharmacy.
        ValueError: If proposal does not exist.
        ProposalPharmacyMismatchError: If proposal does not belong to pharmacy_id.
        ProposalNotPendingError: If proposal is not currently PENDING.
        ProposalExpiredError: If proposal has expired.
        StockStatusQuantityMismatchError: If OUT_OF_STOCK has quantity > 0.
    """
    # 1. Authorization check
    if not repository.is_user_linked_to_pharmacy(user_id, pharmacy_id):
        raise UserNotLinkedToPharmacyError(
            f"User {user_id} is not linked to pharmacy {pharmacy_id}."
        )

    # 2. Proposal existence check
    proposal = repository.get_proposal_by_id(proposal_id)
    if proposal is None:
        raise ValueError(f"Proposal with ID {proposal_id} does not exist.")

    # 3. Pharmacy scope check
    if proposal.pharmacy_id != pharmacy_id:
        raise ProposalPharmacyMismatchError(
            f"Proposal {proposal_id} belongs to pharmacy {proposal.pharmacy_id}, not {pharmacy_id}."
        )

    # 4. State check (must be PENDING)
    if proposal.status != "PENDING":
        raise ProposalNotPendingError(
            f"Proposal {proposal_id} is in status '{proposal.status}'. "
            "Only PENDING proposals can be confirmed."
        )

    # 5. Expiry check
    if check_proposal_expiry(proposal, current_time=current_time):
        raise ProposalExpiredError(
            f"Proposal {proposal_id} expired at {proposal.expires_at} and cannot be confirmed."
        )

    # 6. Quantity/Status consistency check
    if (
        proposal.parsed_intent == StockActionEnum.OUT_OF_STOCK
        and proposal.parsed_quantity is not None
        and proposal.parsed_quantity > 0
    ):
        raise StockStatusQuantityMismatchError(
            f"Proposal {proposal_id} specifies OUT_OF_STOCK with positive quantity {proposal.parsed_quantity}."
        )

    # 7. Apply confirmed proposal via repository
    now = current_time or datetime.now(timezone.utc)
    updated_inventory = repository.apply_confirmed_proposal(
        proposal_id=proposal_id,
        confirmed_by_user_id=user_id,
        pharmacy_id=pharmacy_id,
        current_time=now,
    )

    return ConfirmationResult(
        proposal_id=proposal_id,
        status="CONFIRMED",
        updated_inventory=updated_inventory,
        confirmed_by_user_id=user_id,
        confirmed_at=now,
    )


def reject_proposal_workflow(
    proposal_id: int,
    user_id: int,
    pharmacy_id: int,
    *,
    reason: Optional[str] = None,
    repository: ConfirmationRepositoryProtocol,
    current_time: Optional[datetime] = None,
) -> RejectionResult:
    """Orchestrates rejection of an unconfirmed proposal without inventory mutation.

    Args:
        proposal_id: Target proposal ID.
        user_id: Acting chemist/pharmacist ID.
        pharmacy_id: Target pharmacy ID.
        reason: Optional rejection explanation.
        repository: Data access layer implementing ConfirmationRepositoryProtocol.
        current_time: Optional reference time for testing expiry.

    Returns:
        RejectionResult containing rejection confirmation.

    Raises:
        UserNotLinkedToPharmacyError: If user is not linked to pharmacy.
        ValueError: If proposal does not exist.
        ProposalPharmacyMismatchError: If proposal does not belong to pharmacy_id.
        ProposalNotPendingError: If proposal is not currently PENDING.
        ProposalExpiredError: If proposal has expired.
    """
    # 1. Authorization check
    if not repository.is_user_linked_to_pharmacy(user_id, pharmacy_id):
        raise UserNotLinkedToPharmacyError(
            f"User {user_id} is not linked to pharmacy {pharmacy_id}."
        )

    # 2. Proposal existence check
    proposal = repository.get_proposal_by_id(proposal_id)
    if proposal is None:
        raise ValueError(f"Proposal with ID {proposal_id} does not exist.")

    # 3. Pharmacy scope check
    if proposal.pharmacy_id != pharmacy_id:
        raise ProposalPharmacyMismatchError(
            f"Proposal {proposal_id} belongs to pharmacy {proposal.pharmacy_id}, not {pharmacy_id}."
        )

    # 4. State check (must be PENDING)
    if proposal.status != "PENDING":
        raise ProposalNotPendingError(
            f"Proposal {proposal_id} is in status '{proposal.status}'. "
            "Only PENDING proposals can be rejected."
        )

    # 5. Expiry check
    if check_proposal_expiry(proposal, current_time=current_time):
        raise ProposalExpiredError(
            f"Proposal {proposal_id} expired at {proposal.expires_at}."
        )

    # 6. Delegate rejection to repository (status -> REJECTED, no inventory mutation)
    repository.reject_proposal(
        proposal_id=proposal_id,
        rejected_by_user_id=user_id,
        pharmacy_id=pharmacy_id,
        reason=reason,
    )

    return RejectionResult(
        proposal_id=proposal_id,
        status="REJECTED",
        rejected_by_user_id=user_id,
        reason=reason,
    )


def expire_proposals_job(
    cutoff: Optional[datetime],
    repository: ConfirmationRepositoryProtocol,
) -> int:
    """Executes the cron sweep to mark overdue PENDING proposals as EXPIRED.

    Args:
        cutoff: Cutoff timestamp (proposals with expires_at < cutoff are expired).
        repository: Data access layer implementing ConfirmationRepositoryProtocol.

    Returns:
        Count of proposals transitioned to EXPIRED.
    """
    sweep_cutoff = cutoff or datetime.now(timezone.utc)
    return repository.expire_overdue_proposals(cutoff=sweep_cutoff)
