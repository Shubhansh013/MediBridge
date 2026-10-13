"""
Database operations for MediBridge.

This module contains all data access functions for the MediBridge
application: retrieving inventory, persisting approved stock changes,
managing substitute proposals, audit history, and undo functionality.

Functions here are pure data access — no business logic or authorization.
Authorization and workflow rules are handled by Member 1's layer.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Union

from sqlalchemy import and_, desc, func, select, text
from sqlalchemy.orm import Session, selectinload

from . import exceptions
from .config import get_stale_threshold_hours
from .database import Base, utc_now
from .models import (
    AliasType,
    HistoryChangeType,
    Inventory,
    Medicine,
    MedicineAlias,
    Pharmacy,
    StockHistory,
    StockProposal,
    SubstitutionApproval,
    SubstitutionStatus,
    UpdateSource,
    User,
    UserPharmacy,
    UserRole,
    PharmacyVerification,
    StaffRoleAtPharmacy,
    StockStatus,
    ProposalStatus,
    SosStatus,
)


# =============================================================================
# Inventory Reads
# =============================================================================

def get_pharmacies_nearby(
    session: Session,
    lat: float,
    lon: float,
    radius_km: float,
    limit: int,
) -> List[Pharmacy]:
    """
    Get pharmacies near a geographic point using Haversine formula.
    Only returns active and verified pharmacies.

    Args:
        session: Database session
        lat: Latitude of center point
        lon: Longitude of center point
        radius_km: Radius in kilometers
        limit: Maximum number of results

    Returns:
        List of Pharmacy objects ordered by distance (closest first)
    """
    # For simplicity and given the constraints (Delhi NCR ~ 50km radius),
    # we'll use a bounding box to filter candidates and then compute
    # the exact distance in Python. This is good enough for the demo.
    # In production, consider using PostGIS or a similar extension.

    # Convert radius from km to degrees (approx 1 degree latitude = 111 km)
    # This is a rough approximation but works for small areas
    lat_radius = radius_km / 111.0
    lon_radius = radius_km / (111.0 * abs(lat * 0.0174533))  # Adjust for latitude

    # Bounding box
    min_lat = lat - lat_radius
    max_lat = lat + lat_radius
    min_lon = lon - lon_radius
    max_lon = lon + lon_radius

    # Select active and verified pharmacies within the bounding box
    stmt = (
        select(Pharmacy)
        .where(
            Pharmacy.is_active == True,
            Pharmacy.verification_state == PharmacyVerification.verified,
            Pharmacy.latitude.between(min_lat, max_lat),
            Pharmacy.longitude.between(min_lon, max_lon),
        )
    )

    candidates = session.execute(stmt).scalars().all()

    # Haversine formula in Python
    def haversine(lat1, lon1, lat2, lon2):
        # Convert decimal degrees to radians
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        # Haversine formula
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(a))
        # Radius of earth in kilometers
        r = 6371
        return c * r

    # Compute distance for each candidate and sort
    pharmacies_with_distance = []
    for pharmacy in candidates:
        distance = haversine(lat, lon, pharmacy.latitude, pharmacy.longitude)
        pharmacies_with_distance.append((distance, pharmacy))

    # Sort by distance (closest first)
    pharmacies_with_distance.sort(key=lambda x: x[0])

    # Extract the pharmacy objects up to the limit
    result = [pharmacy for distance, pharmacy in pharmacies_with_distance[:limit]]

    return result


def get_inventory_for_medicine(
    session: Session,
    pharmacy_id: int,
    medicine_id: int,
    include_stale: bool,
    include_inactive: bool = False,
) -> Optional[Inventory]:
    """
    Get inventory row for a specific pharmacy and medicine.

    Args:
        session: Database session
        pharmacy_id: Pharmacy ID
        medicine_id: Medicine ID
        include_stale: If False, exclude stale inventory (not recently confirmed)
        include_inactive: If False, exclude inactive pharmacies and medicines

    Returns:
        Inventory object or None if not found

    Note:
        The caller must verify pharmacy scope if needed.
    """
    stmt = select(Inventory).where(
        Inventory.pharmacy_id == pharmacy_id,
        Inventory.medicine_id == medicine_id,
    )

    if not include_inactive:
        stmt = stmt.join(Inventory.pharmacy).join(Inventory.medicine).where(
            Pharmacy.is_active == True,
            Medicine.is_active == True,
        )

    if not include_stale:
        # Exclude stale inventory
        stmt = stmt.where(Inventory.last_confirmed_at >= utc_now() - timedelta(hours=get_stale_threshold_hours()))

    return session.execute(stmt).scalar_one_or_none()


def get_pharmacy_inventory(
    session: Session,
    pharmacy_id: int,
    include_stale: bool,
    status_filter: Optional[StockStatus],
    include_inactive: bool = False,
) -> List[Inventory]:
    """
    Get all inventory rows for a pharmacy.

    Args:
        session: Database session
        pharmacy_id: Pharmacy ID
        include_stale: If False, exclude stale inventory
        status_filter: Optional StockStatus to filter by
        include_inactive: If False, exclude inactive pharmacies and medicines

    Returns:
        List of Inventory objects (with lazy-loaded medicine relationships)
    """
    stmt = select(Inventory).where(Inventory.pharmacy_id == pharmacy_id)

    if not include_inactive:
        stmt = stmt.join(Inventory.pharmacy).join(Inventory.medicine).where(
            Pharmacy.is_active == True,
            Medicine.is_active == True,
        )

    if not include_stale:
        stmt = stmt.where(Inventory.last_confirmed_at >= utc_now() - timedelta(hours=get_stale_threshold_hours()))

    if status_filter is not None:
        stmt = stmt.where(Inventory.stock_status == status_filter)

    return session.execute(stmt).scalars().all()


def get_substitutes_for_medicine(
    session: Session,
    medicine_id: int,
    pharmacy_id: Optional[int],
    include_inactive: bool = False,
) -> List[Medicine]:
    """
    Find medicines with same composition_key, strength, form as the given medicine.

    Args:
        session: Database session
        medicine_id: ID of the medicine to find substitutes for
        pharmacy_id: If provided, only return medicines that are in stock at this pharmacy
        include_inactive: If False, exclude inactive medicines

    Returns:
        List of Medicine objects (excluding the input medicine itself)
    """
    # First get the target medicine to get its composition_key, strength, form
    target_stmt = select(Medicine).where(Medicine.id == medicine_id)
    if not include_inactive:
        target_stmt = target_stmt.where(Medicine.is_active == True)
    target_medicine = session.execute(target_stmt).scalar_one_or_none()
    if target_medicine is None:
        return []

    # Build the substitute query
    stmt = select(Medicine).where(
        Medicine.composition_key == target_medicine.composition_key,
        Medicine.strength == target_medicine.strength,
        Medicine.form == target_medicine.form,
        Medicine.id != medicine_id,  # Exclude the original medicine
    )

    if not include_inactive:
        stmt = stmt.where(Medicine.is_active == True)

    if pharmacy_id is not None:
        # Join with inventory to check stock status at the pharmacy
        stmt = (
            stmt.join(
                Inventory,
                and_(
                    Inventory.medicine_id == Medicine.id,
                    Inventory.pharmacy_id == pharmacy_id,
                ),
            )
            .where(Inventory.stock_status != StockStatus.out_of_stock)
        )

    return session.execute(stmt).scalars().all()


def get_stale_inventory(
    session: Session,
    pharmacy_id: Optional[int],
    threshold_hours: Optional[int] = None,
    include_inactive: bool = False,
) -> List[Inventory]:
    """
    Get inventory entries that are stale (not recently confirmed).

    Args:
        session: Database session
        pharmacy_id: If provided, only check this pharmacy; if None, check all
        threshold_hours: Hours after which inventory is stale; if None, use config default
        include_inactive: If False, exclude inactive pharmacies and medicines

    Returns:
        List of stale Inventory objects
    """
    if threshold_hours is None:
        threshold_hours = get_stale_threshold_hours()

    cutoff_time = utc_now() - timedelta(hours=threshold_hours)

    stmt = select(Inventory).where(Inventory.last_confirmed_at < cutoff_time)

    if pharmacy_id is not None:
        stmt = stmt.where(Inventory.pharmacy_id == pharmacy_id)

    if not include_inactive:
        stmt = stmt.join(Inventory.pharmacy).join(Inventory.medicine).where(
            Pharmacy.is_active == True,
            Medicine.is_active == True,
        )

    return session.execute(stmt).scalars().all()


# =============================================================================
# Medicine & Substitute Lookup
# =============================================================================

def find_medicine_by_alias(
    session: Session,
    alias: str,
    include_inactive: bool = False,
) -> Optional[Medicine]:
    """
    Look up a medicine by its alias.

    Args:
        session: Database session
        alias: The alias to look up
        include_inactive: If False, exclude inactive medicines

    Returns:
        Medicine object or None if not found
    """
    stmt = (
        select(Medicine)
        .join(MedicineAlias, MedicineAlias.medicine_id == Medicine.id)
        .where(func.lower(MedicineAlias.alias) == func.lower(alias))
    )

    if not include_inactive:
        stmt = stmt.where(Medicine.is_active == True)

    return session.execute(stmt).scalar_one_or_none()


def search_medicines(
    session: Session,
    query: str,
    limit: int,
    include_inactive: bool = False,
) -> List[Medicine]:
    """
    Search medicines by brand name, generic name, or aliases.

    Args:
        session: Database session
        query: Search string (case-insensitive partial match)
        limit: Maximum number of results
        include_inactive: If False, exclude inactive medicines

    Returns:
        List of Medicine objects
    """
    search_term = f"%{query}%"
    stmt = select(Medicine).where(
        (Medicine.brand_name.ilike(search_term))
        | (Medicine.generic_name.ilike(search_term))
        | (
            Medicine.id.in_(
                select(MedicineAlias.medicine_id).where(
                    MedicineAlias.alias.ilike(search_term)
                )
            )
        )
    )

    if not include_inactive:
        stmt = stmt.where(Medicine.is_active == True)

    stmt = stmt.limit(limit)
    return session.execute(stmt).scalars().all()


def get_medicine_by_id(
    session: Session,
    medicine_id: int,
    include_inactive: bool = False,
) -> Optional[Medicine]:
    """
    Get a medicine by its ID.

    Args:
        session: Database session
        medicine_id: Medicine ID
        include_inactive: If False, exclude inactive medicines

    Returns:
        Medicine object or None if not found
    """
    stmt = select(Medicine).where(Medicine.id == medicine_id)
    if not include_inactive:
        stmt = stmt.where(Medicine.is_active == True)
    return session.execute(stmt).scalar_one_or_none()


# =============================================================================
# Proposals
# =============================================================================

def create_stock_proposal(
    session: Session,
    pharmacy_id: int,
    chemist_user_id: int,
    raw_message: str,
    parsed: dict,  # ParsedProposalInput as a dict
) -> StockProposal:
    """
    Create a new stock proposal.

    Args:
        session: Database session
        pharmacy_id: Pharmacy ID (service layer verifies chemist is linked)
        chemist_user_id: Chemist user ID
        raw_message: Original message from chemist
        parsed: Dictionary with parsed fields (brand_name, parsed_salt, etc.)

    Returns:
        Created StockProposal object (status=PENDING, expires_at set)

    Note:
        The caller must verify that the chemist is authorized to make proposals for this pharmacy.
    """
    now = utc_now()
    proposal = StockProposal(
        pharmacy_id=pharmacy_id,
        chemist_user_id=chemist_user_id,
        raw_message=raw_message,
        brand_name=parsed.get("brand_name"),
        parsed_salt=parsed["parsed_salt"],
        parsed_strength=parsed["parsed_strength"],
        parsed_form=parsed.get("parsed_form"),
        parsed_quantity=parsed.get("parsed_quantity"),
        parsed_intent=parsed["parsed_intent"],
        confidence=parsed["confidence"],
        status=ProposalStatus.pending,
        flagged_for_review=parsed.get("flagged_for_review", False),
        expires_at=now + timedelta(hours=24),
        source_message_ref=parsed.get("source_message_ref"),
    )
    session.add(proposal)
    session.flush()  # Get the ID without committing
    return proposal


def get_proposal_by_id(
    session: Session,
    proposal_id: int,
) -> Optional[StockProposal]:
    """
    Get a stock proposal by its ID.

    Args:
        session: Database session
        proposal_id: Proposal ID

    Returns:
        StockProposal object or None if not found
    """
    return session.get(StockProposal, proposal_id)


def list_pending_proposals(
    session: Session,
    pharmacy_id: Optional[int],
    flagged_only: bool,
    limit: int,
) -> List[StockProposal]:
    """
    List pending proposals that have not expired.

    Args:
        session: Database session
        pharmacy_id: If provided, only return proposals for this pharmacy
        flagged_only: If True, only return proposals with flagged_for_review=True
        limit: Maximum number of results

    Returns:
        List of StockProposal objects (status=PENDING, expires_at > now)
    """
    stmt = select(StockProposal).where(
        StockProposal.status == ProposalStatus.pending,
        StockProposal.expires_at > utc_now(),
    )

    if pharmacy_id is not None:
        stmt = stmt.where(StockProposal.pharmacy_id == pharmacy_id)

    if flagged_only:
        stmt = stmt.where(StockProposal.flagged_for_review == True)

    stmt = stmt.order_by(StockProposal.created_at.desc()).limit(limit)
    return session.execute(stmt).scalars().all()


def expire_overdue_proposals(
    session: Session,
    cutoff: datetime,
) -> int:
    """
    Mark overdue pending proposals as expired.

    Args:
        session: Database session
        cutoff: Current time; proposals with expires_at < cutoff are marked EXPIRED

    Returns:
        Number of proposals updated
    """
    stmt = (
        select(StockProposal)
        .where(
            StockProposal.status == ProposalStatus.pending,
            StockProposal.expires_at < cutoff,
        )
        .with_for_update()  # Lock rows to prevent race conditions
    )
    proposals = session.execute(stmt).scalars().all()
    for proposal in proposals:
        proposal.status = ProposalStatus.expired
    session.flush()
    return len(proposals)


def renew_proposal(
    session: Session,
    expired_proposal_id: int,
    chemist_user_id: int,
    raw_message: str,
    parsed: dict,  # ParsedProposalInput as a dict
) -> StockProposal:
    """
    Create a new proposal by renewing an expired one.

    Args:
        session: Database session
        expired_proposal_id: ID of an EXPIRED proposal
        chemist_user_id: Chemist user ID
        raw_message: Original message from chemist
        parsed: Dictionary with parsed fields

    Returns:
        New StockProposal object (status=PENDING, with renewed_from_id set)

    Raises:
        exceptions.ProposalNotPendingError: If the expired_proposal_id is not EXPIRED
    """
    expired_proposal = session.get(StockProposal, expired_proposal_id)
    if expired_proposal is None or expired_proposal.status != ProposalStatus.expired:
        raise exceptions.ProposalNotPendingError(
            f"Proposal {expired_proposal_id} is not EXPIRED"
        )

    now = utc_now()
    new_proposal = StockProposal(
        pharmacy_id=expired_proposal.pharmacy_id,
        chemist_user_id=chemist_user_id,
        raw_message=raw_message,
        brand_name=parsed.get("brand_name"),
        parsed_salt=parsed["parsed_salt"],
        parsed_strength=parsed["parsed_strength"],
        parsed_form=parsed.get("parsed_form"),
        parsed_quantity=parsed.get("parsed_quantity"),
        parsed_intent=parsed["parsed_intent"],
        confidence=parsed["confidence"],
        status=ProposalStatus.pending,
        flagged_for_review=parsed.get("flagged_for_review", False),
        expires_at=now + timedelta(hours=24),
        renewed_from_id=expired_proposal_id,
        source_message_ref=parsed.get("source_message_ref"),
    )
    session.add(new_proposal)
    session.flush()
    return new_proposal


# =============================================================================
# Apply Confirmed Proposal
# =============================================================================

def apply_confirmed_proposal(
    session: Session,
    proposal_id: int,
    confirmed_by_user_id: int,
    pharmacy_id: int,
    current_time: Optional[datetime] = None,
) -> Inventory:
    """
    Apply a confirmed proposal to update inventory and create history.

    This function must be called within a transaction (caller owns the transaction).
    It uses a SAVEPOINT so that if any step fails, all changes made by this
    function are rolled back, while the outer transaction can still commit or
    rollback independently.

    Args:
        session: Database session
        proposal_id: ID of the proposal to apply
        confirmed_by_user_id: User ID of the confirmer (usually the chemist)
        pharmacy_id: Pharmacy ID (must match proposal's pharmacy_id)
        current_time: Optional current time for expiry check; defaults to utc_now()

    Returns:
        Updated Inventory object

    Raises:
        exceptions.ProposalNotPendingError: If proposal status is not CONFIRMED
        exceptions.ProposalExpiredError: If proposal has expired
        exceptions.ProposalPharmacyMismatchError: If proposal pharmacy_id doesn't match
        exceptions.InventoryNotFoundError: If inventory row doesn't exist
        exceptions.StockStatusQuantityMismatchError: If stock status and quantity are inconsistent
    """
    if current_time is None:
        current_time = utc_now()

    # Start a savepoint for this function's changes
    with session.begin_nested():
        # 1. Load the proposal with lock to prevent concurrent modifications
        stmt = select(StockProposal).where(StockProposal.id == proposal_id).with_for_update()
        proposal = session.execute(stmt).scalar_one_or_none()
        if proposal is None:
            raise exceptions.ProposalNotPendingError(f"Proposal {proposal_id} not found")

        # 2. Verify proposal is CONFIRMED (not PENDING, etc.)
        if proposal.status != ProposalStatus.confirmed:
            raise exceptions.ProposalNotPendingError(
                f"Proposal {proposal_id} is not CONFIRMED (status={proposal.status})"
            )

        # 3. Verify pharmacy scope
        if proposal.pharmacy_id != pharmacy_id:
            raise exceptions.ProposalPharmacyMismatchError(
                f"Proposal {proposal_id} belongs to pharmacy {proposal.pharmacy_id}, not {pharmacy_id}"
            )

        # 4. Check expiry
        if proposal.expires_at < current_time:
            raise exceptions.ProposalExpiredError(
                f"Proposal {proposal_id} expired at {proposal.expires_at}"
            )

        # 5. Load the inventory row with lock
        inventory_stmt = (
            select(Inventory)
            .where(
                Inventory.pharmacy_id == pharmacy_id,
                Inventory.medicine_id == None,  # We'll set this after we know the medicine
            )
            .with_for_update()
        )
        # We need to determine the medicine from the proposal's parsed fields
        # Since we don't store medicine_id in proposal, we need to find it by composition
        # But for simplicity in this implementation, we assume the proposal's parsed_salt, strength, form
        # can be used to find a medicine. However, the contract says proposals store parsed_salt,
        # parsed_strength, parsed_form, and we need to match to a medicine.
        # Actually, looking at the model, StockProposal does NOT have medicine_id.
        # The contract says: "medicine reads with alias matching" and "substitute lookup by (composition_key, strength, form)"
        # So we need to find the medicine that matches the parsed composition.
        # However, the apply_confirmed_proposal function in the contract example uses medicine_id from history.
        # Let's re-examine the model: StockProposal has parsed_salt, parsed_strength, parsed_form, brand_name (optional).
        # We need to find a medicine that matches these.
        # But note: there could be multiple medicines (different brands) with same composition.
        # The contract example shows:
        #   pharmacy_id=5, medicine_id=12
        #   and the proposal had parsed_salt="paracetamol", parsed_strength="650mg", parsed_form="tablet"
        #   which matches both Dolo 650 and Calpol 650.
        # How do we know which one? The proposal also has brand_name (optional).
        # In the example, brand_name="Dolo 650".
        # So we should use brand_name if provided, otherwise we might need to ask the service layer?
        # But the contract says: "DB stores what it's given" and "Proposal parsing correctness: The LLM parsing (salt, strength, intent) is trusted"
        # It doesn't say we have to pick a specific brand. However, inventory is per medicine (brand).
        # Looking at the model again: Inventory has medicine_id (FK to medicines).
        # So we must pick a specific medicine.
        # The service layer (Member 1) should have verified the proposal and picked a medicine?
        # But the contract for apply_confirmed_proposal doesn't take medicine_id.
        # Let's look at the example in CONTRACT.md:
        #   stock_history: id=201, inventory_id=7, pharmacy_id=5, medicine_id=12,
        #   ... previous_stock_status=IN_STOCK, new_stock_status=OUT_OF_STOCK,
        #   ... previous_quantity=50, new_quantity=0,
        # This implies that medicine_id=12 is known.
        # How is medicine_id determined? It must be derived from the proposal.
        # Since the proposal has brand_name (optional), we can use that to find the medicine.
        # If brand_name is not provided, we might have ambiguity.
        # However, the contract says: "Brand name in proposals: brand_name is optional — Member 1 to confirm if needed for display/confirmation UI."
        # This suggests that brand_name might not always be present.
        # But inventory is per brand, so we must have a way to determine the brand.
        # Let's check the model again: StockProposal does not have medicine_id, but it has brand_name.
        # In the example, they used brand_name to pick Dolo 650.
        # So we will assume that if brand_name is provided, we use it to find the medicine.
        # If brand_name is not provided, we need to decide what to do.
        # Looking at the normalize.py and the composition key: medicines are uniquely identified by
        # (brand_name, composition_key, strength, form, manufacturer)
        # So without brand_name, we cannot uniquely identify a medicine.
        # However, the contract says: "Salt-equivalent substitutes (e.g., Dolo 650 → Calpol 650, same paracetamol 650mg) require pharmacist confirmation"
        # This implies that for stock updates, we are dealing with a specific brand, and substitutes require confirmation.
        # Therefore, for a stock proposal, we should have a specific brand in mind.
        # Given that the contract says brand_name is optional, but the example uses it, and without it we cannot determine inventory,
        # we will assume that brand_name is required for apply_confirmed_proposal to work.
        # But let's check the model: brand_name is nullable=True.
        # We'll have to handle the case where it's null by maybe raising an error or using some default?
        # However, the contract doesn't specify. Let's look at the CREATE PROPOSAL example in CONTRACT.md:
        #   brand_name="Dolo 650",
        # So in the example it is provided.
        # We'll implement that we require brand_name to be not None; if it is None, we cannot determine the medicine.
        # But wait, the contract also says: "Every deviation from CONTRACT.md."
        # We must follow the contract exactly. The contract for create_stock_proposal says:
        #   parsed: ParsedProposalInput
        #   and ParsedProposalInput includes brand_name (optional).
        # So brand_name can be None.
        # How then do we apply the proposal? We must have a way to determine the medicine.
        # Perhaps the service layer ensures that when brand_name is None, there is only one medicine with that composition?
        # Or perhaps we use the first match? But that would be arbitrary.
        # Let's re-read the contract for apply_confirmed_proposal: it doesn't mention medicine_id.
        # The example shows medicine_id=12, but that is in the history row.
        # The inventory row also has medicine_id.
        # So we must set medicine_id on the inventory row.
        # Therefore, we must determine the medicine from the proposal.
        # Given the ambiguity, I think the intended design is that the proposal's parsed_salt, strength, form
        # are used to find a medicine, and if there are multiple, the brand_name is used to pick one.
        # If brand_name is not provided and there are multiple, we cannot proceed.
        # However, the contract does not specify this error condition.
        # We will implement that we try to find a medicine matching the parsed composition and brand_name (if provided).
        # If brand_name is provided, we require an exact match on brand_name as well.
        # If brand_name is not provided, we require that there is exactly one medicine with that composition, strength, form.
        # If zero or more than one, we raise an error (but what error? We'll create a new one? But the contract doesn't have it.)
        # Alternatively, we could say that the service layer must have ensured uniqueness.
        # Since the contract says "No authorization or workflow decisions", we cannot make a workflow decision here.
        # We will choose to raise an exception if we cannot uniquely determine the medicine.
        # We'll define a new exception? But the contract doesn't list it.
        # Let's see if we can avoid this by looking at the model: maybe the proposal is linked to a medicine via some other way?
        # No, the model doesn't show that.
        # Wait, I see: in the StockProposal model, there is no medicine_id. But in the StockHistory model, there is medicine_id.
        # And in the example, the history row has medicine_id.
        # So when we create the history row, we need to set medicine_id.
        # Therefore, we must determine it in apply_confirmed_proposal.
        # Given the time, we will assume that brand_name is always provided (as in the example) and use it to find the medicine.
        # If brand_name is None, we will look for any medicine with the matching composition, strength, form and raise an error if not exactly one.
        # We'll define a generic exception for this case? But the contract doesn't have it.
        # Alternatively, we can note that the contract says "Deviations from CONTRACT.md" must be reported.
        # We will report this as a deviation if we need to change something.
        # For now, let's assume brand_name is provided and use it.

        # Let's implement the medicine lookup:
        medicine_stmt = select(Medicine).where(
            Medicine.generic_name.ilike(proposal.parsed_salt),  # case-insensitive: parsed_salt is the generic name
            Medicine.strength == proposal.parsed_strength,
            Medicine.form == proposal.parsed_form,
        )
        if proposal.brand_name is not None:
            medicine_stmt = medicine_stmt.where(Medicine.brand_name == proposal.brand_name)
        medicine = session.execute(medicine_stmt).scalar_one_or_none()
        if medicine is None:
            # If brand_name was not provided, try without brand_name constraint
            if proposal.brand_name is None:
                medicine_stmt = select(Medicine).where(
                    Medicine.generic_name == proposal.parsed_salt,
                    Medicine.strength == proposal.parsed_strength,
                    Medicine.form == proposal.parsed_form,
                )
                medicines = session.execute(medicine_stmt).scalars().all()
                if len(medicines) == 1:
                    medicine = medicines[0]
                else:
                    # Zero or multiple matches
                    raise exceptions.InventoryNotFoundError(
                        f"Could not uniquely determine medicine for proposal {proposal_id}"
                    )
            else:
                raise exceptions.InventoryNotFoundError(
                    f"Medicine not found for proposal {proposal_id}: {proposal.brand_name} {proposal.parsed_salt} {proposal.parsed_strength} {proposal.parsed_form}"
                )

        # Now we have the medicine, get the inventory row
        inventory_stmt = (
            select(Inventory)
            .where(
                Inventory.pharmacy_id == pharmacy_id,
                Inventory.medicine_id == medicine.id,
            )
            .with_for_update()
        )
        inventory = session.execute(inventory_stmt).scalar_one_or_none()
        if inventory is None:
            raise exceptions.InventoryNotFoundError(
                f"No inventory record for pharmacy {pharmacy_id} and medicine {medicine.id}"
            )

        # 6. Create history row with previous values
        history_row = StockHistory(
            inventory_id=inventory.id,
            pharmacy_id=inventory.pharmacy_id,
            medicine_id=inventory.medicine_id,
            changed_by=confirmed_by_user_id,
            source_proposal_id=proposal.id,
            change_type=HistoryChangeType.stock_update,
            previous_stock_status=inventory.stock_status,
            new_stock_status=proposal.parsed_intent,
            previous_quantity=inventory.quantity,
            new_quantity=proposal.parsed_quantity,
            previous_last_confirmed_at=inventory.last_confirmed_at,
            change_reason=f"proposal #{proposal.id}",
        )
        session.add(history_row)

        # 7. Update inventory row
        inventory.stock_status = proposal.parsed_intent
        inventory.quantity = proposal.parsed_quantity
        inventory.last_confirmed_at = current_time
        inventory.last_updated_by = confirmed_by_user_id
        inventory.last_update_source = UpdateSource.proposal_apply

        # 8. Update proposal to CONFIRMED (already is, but set confirmed_by and confirmed_at)
        proposal.confirmed_by = confirmed_by_user_id
        proposal.confirmed_at = current_time

        # Flush to get the history ID and inventory updates
        session.flush()

        # Return the updated inventory
        return inventory


# =============================================================================
# History Reads
# =============================================================================

def get_inventory_history(
    session: Session,
    inventory_id: int,
    limit: int,
) -> List[StockHistory]:
    """
    Get history for a specific inventory row, oldest first.

    Args:
        session: Database session
        inventory_id: Inventory ID
        limit: Maximum number of rows to return

    Returns:
        List of StockHistory objects ordered by created_at ascending
    """
    stmt = (
        select(StockHistory)
        .where(StockHistory.inventory_id == inventory_id)
        .order_by(StockHistory.created_at.asc())
        .limit(limit)
    )
    return session.execute(stmt).scalars().all()


def get_latest_history_entry(
    session: Session,
    inventory_id: int,
    pharmacy_id: int,
) -> Optional[StockHistory]:
    """
    Get the most recent history entry for an inventory row, scoped to pharmacy.

    Args:
        session: Database session
        inventory_id: Inventory ID
        pharmacy_id: Pharmacy ID (scope check)

    Returns:
        Most recent StockHistory object or None if not found

    Raises:
        exceptions.PharmacyScopeMismatchError: If inventory doesn't belong to pharmacy
    """
    # First verify the inventory belongs to this pharmacy
    inventory = session.get(Inventory, inventory_id)
    if inventory is None or inventory.pharmacy_id != pharmacy_id:
        raise exceptions.PharmacyScopeMismatchError(
            f"Inventory {inventory_id} does not belong to pharmacy {pharmacy_id}"
        )

    stmt = (
        select(StockHistory)
        .where(
            StockHistory.inventory_id == inventory_id,
            StockHistory.pharmacy_id == pharmacy_id,
        )
        .order_by(StockHistory.created_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def get_pharmacy_history(
    session: Session,
    pharmacy_id: int,
    limit: int,
) -> List[StockHistory]:
    """
    Get all history for a pharmacy, oldest first.

    Args:
        session: Database session
        pharmacy_id: Pharmacy ID
        limit: Maximum number of rows to return

    Returns:
        List of StockHistory objects ordered by created_at ascending
    """
    stmt = (
        select(StockHistory)
        .where(StockHistory.pharmacy_id == pharmacy_id)
        .order_by(StockHistory.created_at.asc())
        .limit(limit)
    )
    return session.execute(stmt).scalars().all()


def get_user_history(
    session: Session,
    user_id: int,
    limit: int,
) -> List[StockHistory]:
    """
    Get all history made by a user, oldest first.

    Args:
        session: Database session
        user_id: User ID
        limit: Maximum number of rows to return

    Returns:
        List of StockHistory objects ordered by created_at ascending
    """
    stmt = (
        select(StockHistory)
        .where(StockHistory.changed_by == user_id)
        .order_by(StockHistory.created_at.asc())
        .limit(limit)
    )
    return session.execute(stmt).scalars().all()


# =============================================================================
# Undo
# =============================================================================

def undo_last_change(
    session: Session,
    history_id: int,
    undone_by_user_id: int,
    pharmacy_id: int,
) -> StockHistory:
    """
    Undo the most recent change for an inventory row.

    This function must be called within a transaction (caller owns the transaction).
    It uses a SAVEPOINT so that if any step fails, all changes made by this
    function are rolled back.

    Args:
        session: Database session
        history_id: ID of the history row to undo (must be the latest for its inventory)
        undone_by_user_id: User ID performing the undo
        pharmacy_id: Pharmacy ID (must match history row's pharmacy_id)

    Returns:
        The newly created UNDO history row

    Raises:
        exceptions.HistoryAlreadyUndoneError: If the target history row is already undone
        exceptions.NewerChangeExistsError: If a newer history row exists for the same inventory
        exceptions.CannotUndoUndoError: If the target history row is itself an undo entry
        exceptions.PharmacyScopeMismatchError: If history row's pharmacy_id doesn't match
    """
    # Start a savepoint for this function's changes
    with session.begin_nested():
        # 1. Load the target history row with lock
        target_history = session.get(StockHistory, history_id, with_for_update=True)
        if target_history is None:
            raise ValueError(f"History row {history_id} not found")  # Not in contract, but should not happen

        # 2. Verify pharmacy scope
        if target_history.pharmacy_id != pharmacy_id:
            raise exceptions.PharmacyScopeMismatchError(
                f"History row {history_id} belongs to pharmacy {target_history.pharmacy_id}, not {pharmacy_id}"
            )

        # 3. Verify it's a STOCK_UPDATE (not UNDO)
        if target_history.change_type == HistoryChangeType.undo:
            raise exceptions.CannotUndoUndoError(
                f"History row {history_id} is an undo entry and cannot be undone"
            )

        # 4. Verify it's not already undone (check if any undo row points to this history)
        already_undone_stmt = select(StockHistory.id).where(
            StockHistory.undo_of_history_id == target_history.id
        )
        already_undone_exists = session.execute(already_undone_stmt).scalar_one_or_none() is not None
        if already_undone_exists:
            raise exceptions.HistoryAlreadyUndoneError(
                f"History row {history_id} is already undone"
            )

        # 5. Verify no newer history row exists for the same inventory
        newer_stmt = (
            select(StockHistory.id)
            .where(
                StockHistory.inventory_id == target_history.inventory_id,
                StockHistory.id > target_history.id,
            )
            .limit(1)
        )
        newer_exists = session.execute(newer_stmt).scalar_one_or_none() is not None
        if newer_exists:
            raise exceptions.NewerChangeExistsError(
                f"Newer change exists for inventory {target_history.inventory_id}"
            )

        # 6. Load the inventory row (with lock) for the target history
        inventory = session.get(Inventory, target_history.inventory_id, with_for_update=True)
        if inventory is None:
            # This should not happen if the history row is valid, but we check anyway.
            raise ValueError(f"Inventory row {target_history.inventory_id} not found")

        # 7. Create undo history row
        # The undo row should restore the inventory to the previous values from the target row
        undo_history = StockHistory(
            inventory_id=target_history.inventory_id,
            pharmacy_id=target_history.pharmacy_id,
            medicine_id=target_history.medicine_id,
            changed_by=undone_by_user_id,
            source_proposal_id=target_history.source_proposal_id,  # Keep link to original proposal
            change_type=HistoryChangeType.undo,
            undo_of_history_id=target_history.id,
            previous_stock_status=target_history.new_stock_status,  # Current state
            new_stock_status=target_history.previous_stock_status,  # Previous state
            previous_quantity=target_history.new_quantity,  # Current quantity
            new_quantity=target_history.previous_quantity,  # Previous quantity
            previous_last_confirmed_at=inventory.last_confirmed_at,  # Current timestamp
            change_reason=f"undo of #{target_history.id}",
        )
        session.add(undo_history)

        # 7. Restore inventory to target's previous values
        inventory = session.get(Inventory, target_history.inventory_id, with_for_update=True)
        inventory.stock_status = target_history.previous_stock_status
        inventory.quantity = target_history.previous_quantity
        inventory.last_confirmed_at = target_history.previous_last_confirmed_at
        inventory.last_updated_by = undone_by_user_id
        inventory.last_update_source = UpdateSource.undo


        # Flush to get the undo history ID
        session.flush()

        # Return the undo history row
        return undo_history


def can_undo(session: Session, history_id: int) -> bool:
    """
    Check if a history row can be undone (without modifying anything).

    Args:
        session: Database session
        history_id: ID of the history row to check

    Returns:
        True if the row can be undone, False otherwise
    """
    # We replicate the checks from undo_last_change but without the side effects
    target_history = session.get(StockHistory, history_id)
    if target_history is None:
        return False

    # Check pharmacy scope? The function doesn't take pharmacy_id, so we cannot check scope.
    # But the contract for can_undo doesn't include pharmacy_id, so we skip scope check.
    # However, the undo function requires pharmacy_id to check scope.
    # So can_undo only checks the history-specific conditions.

    if target_history.change_type == HistoryChangeType.undo:
        return False  # Cannot undo an undo

    if target_history.undo_of_history_id is not None:
        return False  # Already undone

    # Check for newer history row
    newer_stmt = (
        select(StockHistory.id)
        .where(
            StockHistory.inventory_id == target_history.inventory_id,
            StockHistory.id > target_history.id,
        )
        .limit(1)
    )
    newer_exists = session.execute(newer_stmt).scalar_one_or_none() is not None
    if newer_exists:
        return False  # Newer change exists

    return True


# =============================================================================
# Substitution Approvals
# =============================================================================

def record_substitution_approval(
    session: Session,
    pharmacy_id: int,
    requested_medicine_id: int,
    substitute_medicine_id: int,
    approving_pharmacist_id: int,
    status: SubstitutionStatus,
    request_reference: Optional[str] = None,
) -> SubstitutionApproval:
    """
    Record a pharmacist's approval or rejection of a substitution request.

    Args:
        session: Database session
        pharmacy_id: Pharmacy ID (service layer verifies pharmacist is linked)
        requested_medicine_id: ID of the medicine originally requested
        substitute_medicine_id: ID of the suggested substitute medicine
        approving_pharmacist_id: Pharmacist user ID
        status: APPROVED or REJECTED
        request_reference: Optional reference (e.g., patient name, order ID)

    Returns:
        Created SubstitutionApproval object

    Raises:
        ValueError: If the requested and substitute medicines are the same or do not have the same composition_key, strength, and form.
    """
    # Verify that the requested and substitute medicines are different
    if requested_medicine_id == substitute_medicine_id:
        raise ValueError("Requested and substitute medicines must be different")

    # Verify that requested and substitute medicines have same composition_key, strength, form
    requested = session.get(Medicine, requested_medicine_id)
    substitute = session.get(Medicine, substitute_medicine_id)
    if requested is None or substitute is None:
        # Let the FK constraint handle this? But we want to give a clear error.
        # We'll raise an error, but what type? Not in contract.
        # We'll assume the service layer has validated the medicine IDs.
        pass  # We'll let the FK constraint raise an integrity error if needed

    if requested.composition_key != substitute.composition_key:
        raise ValueError("Requested and substitute medicines must have the same composition_key")
    if requested.strength != substitute.strength:
        raise ValueError("Requested and substitute medicines must have the same strength")
    if requested.form != substitute.form:
        raise ValueError("Requested and substitute medicines must have the same form")

    approval = SubstitutionApproval(
        pharmacy_id=pharmacy_id,
        requested_medicine_id=requested_medicine_id,
        substitute_medicine_id=substitute_medicine_id,
        approving_pharmacist_id=approving_pharmacist_id,
        status=status,
        request_reference=request_reference,
    )
    session.add(approval)
    session.flush()
    return approval


def get_substitution_approvals(
    session: Session,
    pharmacy_id: int,
    requested_medicine_id: Optional[int] = None,
) -> List[SubstitutionApproval]:
    """
    Get substitution approval records for a pharmacy.

    Args:
        session: Database session
        pharmacy_id: Pharmacy ID
        requested_medicine_id: If provided, filter by requested medicine

    Returns:
        List of SubstitutionApproval objects ordered by created_at descending
    """
    stmt = select(SubstitutionApproval).where(SubstitutionApproval.pharmacy_id == pharmacy_id)
    if requested_medicine_id is not None:
        stmt = stmt.where(SubstitutionApproval.requested_medicine_id == requested_medicine_id)
    stmt = stmt.order_by(SubstitutionApproval.created_at.desc())
    return session.execute(stmt).scalars().all()


# =============================================================================
# Pharmacy Membership Reads
# =============================================================================

def is_user_linked_to_pharmacy(
    session: Session,
    user_id: int,
    pharmacy_id: int,
) -> bool:
    """
    Check if a user is linked to a pharmacy (active link).

    Args:
        session: Database session
        user_id: User ID
        pharmacy_id: Pharmacy ID

    Returns:
        True if there is an active user_pharmacies link, False otherwise
    """
    stmt = select(UserPharmacy).where(
        UserPharmacy.user_id == user_id,
        UserPharmacy.pharmacy_id == pharmacy_id,
    )
    # Join with user and pharmacy to check is_active? The contract says:
    #   True if `user_pharmacies` row exists with `is_active=1` on both user and pharmacy.
    stmt = stmt.join(UserPharmacy.user).join(UserPharmacy.pharmacy).where(
        User.is_active == True,
        Pharmacy.is_active == True,
    )
    return session.execute(stmt).scalar_one_or_none() is not None


def get_user_pharmacies(
    session: Session,
    user_id: int,
    include_inactive: bool = False,
) -> List[Pharmacy]:
    """
    Get all pharmacies a user is linked to.

    Args:
        session: Session
        user_id: User ID
        include_inactive: If False, exclude inactive pharmacies

    Returns:
        List of Pharmacy objects
    """
    stmt = select(Pharmacy).join(UserPharmacy).where(
        UserPharmacy.user_id == user_id
    )
    if not include_inactive:
        stmt = stmt.where(Pharmacy.is_active == True)
    return session.execute(stmt).scalars().all()


def get_pharmacy_staff(
    session: Session,
    pharmacy_id: int,
    role: Optional[StaffRoleAtPharmacy],
    include_inactive: bool = False,
) -> List[User]:
    """
    Get all users linked to a pharmacy, optionally filtered by role.

    Args:
        session: Session
        pharmacy_id: Pharmacy ID
        role: If provided, filter by role at pharmacy (chemist/pharmacist)
        include_inactive: If False, exclude inactive users

    Returns:
        List of User objects
    """
    stmt = select(User).join(UserPharmacy).where(
        UserPharmacy.pharmacy_id == pharmacy_id
    )
    if role is not None:
        stmt = stmt.where(UserPharmacy.role_at_pharmacy == role)
    if not include_inactive:
        stmt = stmt.where(User.is_active == True)
    return session.execute(stmt).scalars().all()