"""SQLite repository implementation for MediBridge.

Implements all service protocols:
- StockProposalRepositoryProtocol
- ConfirmationRepositoryProtocol
- HistoryRepositoryProtocol
- CatalogueRepositoryProtocol

Enforces atomic transactions, foreign keys, and immutability invariants.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from medibridge.database.models import (
    Inventory,
    Medicine,
    MedicineAlias,
    Pharmacy,
    StockHistory,
    StockProposal,
    SubstitutionApproval,
    User,
    UserPharmacy,
)
from medibridge.exceptions import (
    CannotUndoUndoError,
    HistoryAlreadyUndoneError,
    InventoryNotFoundError,
    NewerChangeExistsError,
    PharmacyScopeMismatchError,
    ProposalExpiredError,
    ProposalNotPendingError,
    ProposalPharmacyMismatchError,
    StockStatusQuantityMismatchError,
    UserNotLinkedToPharmacyError,
)
from medibridge.schemas.stock import StockActionEnum
from medibridge.services.catalogue_service import CatalogueMedicineDetail
from medibridge.services.confirmation_service import InventoryRecord
from medibridge.services.history_service import StockHistoryRecord
from medibridge.services.proposal_adapter import ParsedProposalInput
from medibridge.services.proposal_service import StoredProposalRecord


def _to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalizes naive or aware datetimes to UTC-aware datetime objects."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class SQLiteMediBridgeRepository:
    """Unified SQLite repository satisfying all MediBridge backend protocols."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    # -------------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------------

    def _is_user_linked(self, session: Session, user_id: int, pharmacy_id: int) -> bool:
        """Internal helper to verify active user-pharmacy membership within a session."""
        link = session.scalar(
            select(UserPharmacy)
            .join(User, User.id == UserPharmacy.user_id)
            .join(Pharmacy, Pharmacy.id == UserPharmacy.pharmacy_id)
            .where(
                UserPharmacy.user_id == user_id,
                UserPharmacy.pharmacy_id == pharmacy_id,
                User.is_active == 1,
                Pharmacy.is_active == 1,
            )
        )
        return link is not None

    def _resolve_medicine(self, session: Session, proposal: StockProposal) -> Optional[Medicine]:
        """Resolves target Medicine record from proposal brand or salt details."""
        med: Optional[Medicine] = None

        if proposal.brand_name:
            brand_clean = proposal.brand_name.strip().lower()
            # 1. Exact brand and strength
            med = session.scalar(
                select(Medicine).where(
                    func.lower(Medicine.brand_name) == brand_clean,
                    Medicine.strength == proposal.parsed_strength,
                    Medicine.is_active == 1,
                )
            )
            if not med:
                # 2. Exact brand
                med = session.scalar(
                    select(Medicine).where(
                        func.lower(Medicine.brand_name) == brand_clean,
                        Medicine.is_active == 1,
                    )
                )
            if not med:
                # 3. Via alias
                alias_row = session.scalar(
                    select(MedicineAlias).where(
                        func.lower(MedicineAlias.alias) == brand_clean
                    )
                )
                if alias_row:
                    med = session.get(Medicine, alias_row.medicine_id)

        if not med and proposal.parsed_salt:
            salt_clean = proposal.parsed_salt.strip().lower()
            # 4. Salt and strength
            med = session.scalar(
                select(Medicine).where(
                    func.lower(Medicine.generic_name) == salt_clean,
                    Medicine.strength == proposal.parsed_strength,
                    Medicine.is_active == 1,
                )
            )
            if not med:
                # 5. Salt alone
                med = session.scalar(
                    select(Medicine).where(
                        func.lower(Medicine.generic_name) == salt_clean,
                        Medicine.is_active == 1,
                    )
                )

        return med

    def _row_to_proposal_record(
        self,
        row: StockProposal,
        created_at: Optional[datetime] = None,
    ) -> StoredProposalRecord:
        """Converts StockProposal ORM row to StoredProposalRecord Pydantic model."""
        ts_created = created_at or (row.expires_at - timedelta(hours=24))
        return StoredProposalRecord(
            id=row.id,
            pharmacy_id=row.pharmacy_id,
            chemist_user_id=row.chemist_user_id,
            raw_message=row.raw_message,
            brand_name=row.brand_name,
            parsed_salt=row.parsed_salt,
            parsed_strength=row.parsed_strength,
            parsed_form=row.parsed_form,
            parsed_quantity=row.parsed_quantity,
            parsed_intent=StockActionEnum(row.parsed_intent),
            confidence=row.confidence,
            status=row.status,
            flagged_for_review=bool(row.flagged_for_review),
            expires_at=_to_utc(row.expires_at),
            created_at=_to_utc(ts_created),
            confirmed_by=row.confirmed_by,
            confirmed_at=_to_utc(row.confirmed_at),
            review_note=row.review_note,
            renewed_from_id=row.renewed_from_id,
        )

    def _row_to_history_record(self, row: StockHistory) -> StockHistoryRecord:
        """Converts StockHistory ORM row to StockHistoryRecord Pydantic model."""
        prev_status = (
            StockActionEnum(row.previous_stock_status)
            if row.previous_stock_status
            else None
        )
        return StockHistoryRecord(
            id=row.id,
            inventory_id=row.inventory_id,
            pharmacy_id=row.pharmacy_id,
            medicine_id=row.medicine_id,
            changed_by=row.changed_by,
            source_proposal_id=row.source_proposal_id,
            change_type=row.change_type,
            undo_of_history_id=row.undo_of_history_id,
            previous_stock_status=prev_status,
            new_stock_status=StockActionEnum(row.new_stock_status),
            previous_quantity=row.previous_quantity,
            new_quantity=row.new_quantity,
            previous_last_confirmed_at=_to_utc(row.previous_last_confirmed_at),
            change_reason=row.change_reason,
            created_at=_to_utc(row.created_at),
        )

    # -------------------------------------------------------------------------
    # Authorization Protocol
    # -------------------------------------------------------------------------

    def is_user_linked_to_pharmacy(self, user_id: int, pharmacy_id: int) -> bool:
        """Check if user has an active membership at the specified pharmacy."""
        with self._session_factory() as session:
            return self._is_user_linked(session, user_id, pharmacy_id)

    # -------------------------------------------------------------------------
    # Catalogue Protocol
    # -------------------------------------------------------------------------

    def find_medicine_by_alias(self, alias: str) -> Optional[CatalogueMedicineDetail]:
        """Find medicine by exact alias or brand name match."""
        cleaned = alias.strip().lower()
        with self._session_factory() as session:
            # 1. Alias lookup
            alias_row = session.scalar(
                select(MedicineAlias).where(func.lower(MedicineAlias.alias) == cleaned)
            )
            med: Optional[Medicine] = None
            if alias_row:
                med = session.get(Medicine, alias_row.medicine_id)

            # 2. Exact brand name lookup
            if med is None:
                med = session.scalar(
                    select(Medicine).where(
                        func.lower(Medicine.brand_name) == cleaned,
                        Medicine.is_active == 1,
                    )
                )

            if med is None or med.is_active != 1:
                return None

            aliases = list(
                session.scalars(
                    select(MedicineAlias.alias).where(MedicineAlias.medicine_id == med.id)
                ).all()
            )

            return CatalogueMedicineDetail(
                id=med.id,
                brand_name=med.brand_name,
                generic_name=med.generic_name,
                composition_key=med.composition_key,
                strength=med.strength,
                form=med.form,
                is_schedule_h=bool(med.is_schedule_h),
                manufacturer=med.manufacturer,
                aliases=aliases,
            )

    def search_medicines(self, query: str, limit: int = 10) -> list[CatalogueMedicineDetail]:
        """Search catalogue medicines by query across brand, generic, and aliases."""
        cleaned = query.strip().lower()
        if not cleaned:
            return []

        with self._session_factory() as session:
            alias_med_ids = list(
                session.scalars(
                    select(MedicineAlias.medicine_id).where(
                        func.lower(MedicineAlias.alias).contains(cleaned)
                    )
                ).all()
            )

            meds = session.scalars(
                select(Medicine).where(
                    or_(
                        func.lower(Medicine.brand_name).contains(cleaned),
                        func.lower(Medicine.generic_name).contains(cleaned),
                        Medicine.id.in_(alias_med_ids) if alias_med_ids else False,
                    ),
                    Medicine.is_active == 1,
                )
            ).all()

            def rank_key(m: Medicine) -> tuple[int, str]:
                b = m.brand_name.strip().lower()
                if b == cleaned:
                    return (0, b)
                if b.startswith(cleaned):
                    return (1, b)
                if cleaned in b:
                    return (2, b)
                return (3, b)

            sorted_meds = sorted(meds, key=rank_key)[:limit]

            results: list[CatalogueMedicineDetail] = []
            for m in sorted_meds:
                aliases = list(
                    session.scalars(
                        select(MedicineAlias.alias).where(MedicineAlias.medicine_id == m.id)
                    ).all()
                )
                results.append(
                    CatalogueMedicineDetail(
                        id=m.id,
                        brand_name=m.brand_name,
                        generic_name=m.generic_name,
                        composition_key=m.composition_key,
                        strength=m.strength,
                        form=m.form,
                        is_schedule_h=bool(m.is_schedule_h),
                        manufacturer=m.manufacturer,
                        aliases=aliases,
                    )
                )
            return results

    def get_medicine_by_id(self, medicine_id: int) -> Optional[CatalogueMedicineDetail]:
        """Fetch medicine details by primary key."""
        with self._session_factory() as session:
            med = session.get(Medicine, medicine_id)
            if med is None or med.is_active != 1:
                return None
            aliases = list(
                session.scalars(
                    select(MedicineAlias.alias).where(MedicineAlias.medicine_id == med.id)
                ).all()
            )
            return CatalogueMedicineDetail(
                id=med.id,
                brand_name=med.brand_name,
                generic_name=med.generic_name,
                composition_key=med.composition_key,
                strength=med.strength,
                form=med.form,
                is_schedule_h=bool(med.is_schedule_h),
                manufacturer=med.manufacturer,
                aliases=aliases,
            )

    def get_substitutes_for_medicine(
        self,
        medicine_id: int,
        pharmacy_id: Optional[int] = None,
    ) -> list[CatalogueMedicineDetail]:
        """Find medicines with same composition_key, strength, and form."""
        with self._session_factory() as session:
            target = session.get(Medicine, medicine_id)
            if target is None:
                return []

            meds = session.scalars(
                select(Medicine).where(
                    Medicine.composition_key == target.composition_key,
                    Medicine.strength == target.strength,
                    Medicine.form == target.form,
                    Medicine.is_active == 1,
                )
            ).all()

            results: list[CatalogueMedicineDetail] = []
            for m in meds:
                aliases = list(
                    session.scalars(
                        select(MedicineAlias.alias).where(MedicineAlias.medicine_id == m.id)
                    ).all()
                )
                results.append(
                    CatalogueMedicineDetail(
                        id=m.id,
                        brand_name=m.brand_name,
                        generic_name=m.generic_name,
                        composition_key=m.composition_key,
                        strength=m.strength,
                        form=m.form,
                        is_schedule_h=bool(m.is_schedule_h),
                        manufacturer=m.manufacturer,
                        aliases=aliases,
                    )
                )
            return results

    def record_substitution_approval(
        self,
        pharmacy_id: int,
        requested_medicine_id: int,
        substitute_medicine_id: int,
        approving_pharmacist_id: int,
        status: str,
        request_reference: Optional[str] = None,
    ) -> dict:
        """Record per-request pharmacist substitution approval."""
        with self._session_factory() as session:
            try:
                if not self._is_user_linked(session, approving_pharmacist_id, pharmacy_id):
                    raise UserNotLinkedToPharmacyError(
                        f"User {approving_pharmacist_id} is not linked to pharmacy {pharmacy_id}."
                    )

                req_med = session.get(Medicine, requested_medicine_id)
                sub_med = session.get(Medicine, substitute_medicine_id)
                if req_med is None:
                    raise ValueError(f"Requested medicine {requested_medicine_id} does not exist.")
                if sub_med is None:
                    raise ValueError(f"Substitute medicine {substitute_medicine_id} does not exist.")

                if (
                    req_med.composition_key != sub_med.composition_key
                    or req_med.strength != sub_med.strength
                    or req_med.form != sub_med.form
                ):
                    raise ValueError(
                        f"Composition mismatch between {req_med.brand_name} and {sub_med.brand_name}."
                    )

                now = datetime.now(timezone.utc)
                approval = SubstitutionApproval(
                    pharmacy_id=pharmacy_id,
                    requested_medicine_id=requested_medicine_id,
                    substitute_medicine_id=substitute_medicine_id,
                    approving_pharmacist_id=approving_pharmacist_id,
                    status=status,
                    request_reference=request_reference,
                    approved_at=now,
                )
                session.add(approval)
                session.commit()

                return {
                    "id": approval.id,
                    "pharmacy_id": approval.pharmacy_id,
                    "requested_medicine_id": approval.requested_medicine_id,
                    "substitute_medicine_id": approval.substitute_medicine_id,
                    "approving_pharmacist_id": approval.approving_pharmacist_id,
                    "status": approval.status,
                    "request_reference": approval.request_reference,
                    "approved_at": _to_utc(approval.approved_at),
                }
            except Exception:
                session.rollback()
                raise

    # -------------------------------------------------------------------------
    # Stock Proposal Protocol
    # -------------------------------------------------------------------------

    def create_stock_proposal(
        self,
        pharmacy_id: int,
        chemist_user_id: int,
        raw_message: str,
        parsed: ParsedProposalInput,
    ) -> StoredProposalRecord:
        """Persist a new PENDING stock proposal row."""
        with self._session_factory() as session:
            try:
                if not self._is_user_linked(session, chemist_user_id, pharmacy_id):
                    raise UserNotLinkedToPharmacyError(
                        f"User {chemist_user_id} is not linked to pharmacy {pharmacy_id}."
                    )

                now = datetime.now(timezone.utc)
                expires_at = now + timedelta(hours=24)
                intent_val = (
                    parsed.parsed_intent.value
                    if hasattr(parsed.parsed_intent, "value")
                    else str(parsed.parsed_intent)
                )

                proposal = StockProposal(
                    pharmacy_id=pharmacy_id,
                    chemist_user_id=chemist_user_id,
                    raw_message=raw_message,
                    brand_name=parsed.brand_name,
                    parsed_salt=parsed.parsed_salt,
                    parsed_strength=parsed.parsed_strength,
                    parsed_form=parsed.parsed_form,
                    parsed_quantity=parsed.parsed_quantity,
                    parsed_intent=intent_val,
                    confidence=parsed.confidence,
                    status="PENDING",
                    flagged_for_review=1 if parsed.flagged_for_review else 0,
                    expires_at=expires_at,
                    renewed_from_id=None,
                    confirmed_by=None,
                    confirmed_at=None,
                    review_note=None,
                )
                session.add(proposal)
                session.commit()
                return self._row_to_proposal_record(proposal, created_at=now)
            except Exception:
                session.rollback()
                raise

    def get_proposal_by_id(self, proposal_id: int) -> Optional[StoredProposalRecord]:
        """Fetch a single stock proposal by primary key."""
        with self._session_factory() as session:
            row = session.get(StockProposal, proposal_id)
            if row is None:
                return None
            return self._row_to_proposal_record(row)

    def list_pending_proposals(
        self,
        pharmacy_id: Optional[int] = None,
        flagged_only: bool = False,
        limit: int = 50,
    ) -> list[StoredProposalRecord]:
        """List active pending proposals that have not yet expired."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        with self._session_factory() as session:
            stmt = select(StockProposal).where(
                StockProposal.status == "PENDING",
                StockProposal.expires_at > now,
            )
            if pharmacy_id is not None:
                stmt = stmt.where(StockProposal.pharmacy_id == pharmacy_id)
            if flagged_only:
                stmt = stmt.where(StockProposal.flagged_for_review == 1)

            stmt = stmt.order_by(StockProposal.id.asc()).limit(limit)
            rows = session.scalars(stmt).all()
            return [self._row_to_proposal_record(r) for r in rows]

    # -------------------------------------------------------------------------
    # Confirmation & Expiry Protocol
    # -------------------------------------------------------------------------

    def apply_confirmed_proposal(
        self,
        proposal_id: int,
        confirmed_by_user_id: int,
        pharmacy_id: int,
        current_time: Optional[datetime] = None,
    ) -> InventoryRecord:
        """Apply a confirmed proposal in a single atomic database transaction:

        1. Verify pharmacy membership.
        2. Verify proposal existence, status=PENDING, and pharmacy scope.
        3. Verify proposal has not expired.
        4. Verify stock status and quantity consistency.
        5. Upsert inventory row.
        6. Append stock_history row (STOCK_UPDATE).
        7. Update proposal status=CONFIRMED.
        """
        now = current_time or datetime.now(timezone.utc)
        with self._session_factory() as session:
            try:
                # 1. Authorization check
                if not self._is_user_linked(session, confirmed_by_user_id, pharmacy_id):
                    raise UserNotLinkedToPharmacyError(
                        f"User {confirmed_by_user_id} is not linked to pharmacy {pharmacy_id}."
                    )

                # 2. Proposal existence check
                proposal = session.get(StockProposal, proposal_id)
                if proposal is None:
                    raise ValueError(f"Proposal with ID {proposal_id} does not exist.")

                # 3. Pharmacy scope check
                if proposal.pharmacy_id != pharmacy_id:
                    raise ProposalPharmacyMismatchError(
                        f"Proposal {proposal_id} belongs to pharmacy {proposal.pharmacy_id}, not {pharmacy_id}."
                    )

                # 4. Status must be PENDING
                if proposal.status != "PENDING":
                    raise ProposalNotPendingError(
                        f"Proposal {proposal_id} is in status '{proposal.status}'. "
                        "Only PENDING proposals can be confirmed."
                    )

                # 5. Expiry check
                exp = proposal.expires_at
                now_cmp = now
                if exp.tzinfo is None and now.tzinfo is not None:
                    now_cmp = now.replace(tzinfo=None)
                elif exp.tzinfo is not None and now.tzinfo is None:
                    now_cmp = now.replace(tzinfo=timezone.utc)

                if exp < now_cmp:
                    raise ProposalExpiredError(
                        f"Proposal {proposal_id} expired at {proposal.expires_at} and cannot be confirmed."
                    )

                # 6. Intent and quantity consistency
                if (
                    proposal.parsed_intent == "OUT_OF_STOCK"
                    and proposal.parsed_quantity is not None
                    and proposal.parsed_quantity > 0
                ):
                    raise StockStatusQuantityMismatchError(
                        f"Proposal {proposal_id} specifies OUT_OF_STOCK with positive quantity {proposal.parsed_quantity}."
                    )

                # 7. Resolve medicine
                med = self._resolve_medicine(session, proposal)
                if med is None:
                    raise ValueError(
                        f"Cannot apply proposal {proposal_id}: medicine "
                        f"'{proposal.brand_name or proposal.parsed_salt}' not found in catalogue."
                    )

                # 8. Upsert inventory
                inv = session.scalar(
                    select(Inventory).where(
                        Inventory.pharmacy_id == pharmacy_id,
                        Inventory.medicine_id == med.id,
                    )
                )
                prev_status = inv.stock_status if inv else None
                prev_quantity = inv.quantity if inv else None
                prev_confirmed_at = inv.last_confirmed_at if inv else None

                now_db = now.replace(tzinfo=None) if now.tzinfo else now
                if inv is None:
                    inv = Inventory(
                        pharmacy_id=pharmacy_id,
                        medicine_id=med.id,
                        stock_status=proposal.parsed_intent,
                        quantity=proposal.parsed_quantity,
                        last_confirmed_at=now_db,
                        last_updated_by=confirmed_by_user_id,
                        last_update_source="proposal_apply",
                    )
                    session.add(inv)
                    session.flush()
                else:
                    inv.stock_status = proposal.parsed_intent
                    inv.quantity = proposal.parsed_quantity
                    inv.last_confirmed_at = now_db
                    inv.last_updated_by = confirmed_by_user_id
                    inv.last_update_source = "proposal_apply"
                    session.flush()

                # 9. Append stock_history
                history = StockHistory(
                    inventory_id=inv.id,
                    pharmacy_id=pharmacy_id,
                    medicine_id=med.id,
                    changed_by=confirmed_by_user_id,
                    source_proposal_id=proposal.id,
                    change_type="STOCK_UPDATE",
                    undo_of_history_id=None,
                    previous_stock_status=prev_status,
                    new_stock_status=proposal.parsed_intent,
                    previous_quantity=prev_quantity,
                    new_quantity=proposal.parsed_quantity,
                    previous_last_confirmed_at=prev_confirmed_at,
                    change_reason=f"proposal #{proposal.id}",
                    created_at=now_db,
                )
                session.add(history)

                # 10. Update proposal status
                proposal.status = "CONFIRMED"
                proposal.confirmed_by = confirmed_by_user_id
                proposal.confirmed_at = now_db

                session.commit()

                return InventoryRecord(
                    id=inv.id,
                    pharmacy_id=inv.pharmacy_id,
                    medicine_id=inv.medicine_id,
                    stock_status=StockActionEnum(inv.stock_status),
                    quantity=inv.quantity,
                    last_confirmed_at=_to_utc(inv.last_confirmed_at),
                    last_updated_by=inv.last_updated_by,
                    last_update_source=inv.last_update_source,
                )
            except Exception:
                session.rollback()
                raise

    def reject_proposal(
        self,
        proposal_id: int,
        rejected_by_user_id: int,
        pharmacy_id: int,
        reason: Optional[str] = None,
    ) -> StoredProposalRecord:
        """Mark proposal as REJECTED with optional review notes without inventory mutation."""
        with self._session_factory() as session:
            try:
                if not self._is_user_linked(session, rejected_by_user_id, pharmacy_id):
                    raise UserNotLinkedToPharmacyError(
                        f"User {rejected_by_user_id} is not linked to pharmacy {pharmacy_id}."
                    )

                proposal = session.get(StockProposal, proposal_id)
                if proposal is None:
                    raise ValueError(f"Proposal with ID {proposal_id} does not exist.")

                if proposal.pharmacy_id != pharmacy_id:
                    raise ProposalPharmacyMismatchError(
                        f"Proposal {proposal_id} belongs to pharmacy {proposal.pharmacy_id}, not {pharmacy_id}."
                    )

                if proposal.status != "PENDING":
                    raise ProposalNotPendingError(
                        f"Proposal {proposal_id} is in status '{proposal.status}'. "
                        "Only PENDING proposals can be rejected."
                    )

                proposal.status = "REJECTED"
                proposal.review_note = reason
                session.commit()
                return self._row_to_proposal_record(proposal)
            except Exception:
                session.rollback()
                raise

    def expire_overdue_proposals(self, cutoff: datetime) -> int:
        """Update status=EXPIRED where status=PENDING and expires_at < cutoff."""
        cutoff_cmp = cutoff.replace(tzinfo=None) if cutoff.tzinfo is not None else cutoff
        with self._session_factory() as session:
            try:
                proposals = session.scalars(
                    select(StockProposal).where(
                        StockProposal.status == "PENDING",
                        StockProposal.expires_at < cutoff_cmp,
                    )
                ).all()

                count = 0
                for p in proposals:
                    p.status = "EXPIRED"
                    count += 1

                session.commit()
                return count
            except Exception:
                session.rollback()
                raise

    # -------------------------------------------------------------------------
    # History & Undo Protocol
    # -------------------------------------------------------------------------

    def get_history_by_id(self, history_id: int) -> Optional[StockHistoryRecord]:
        """Fetch a single history row by primary key."""
        with self._session_factory() as session:
            row = session.get(StockHistory, history_id)
            if row is None:
                return None
            return self._row_to_history_record(row)

    def get_inventory_history(self, inventory_id: int, limit: int = 50) -> list[StockHistoryRecord]:
        """Chronological audit trail for an inventory row (oldest first)."""
        with self._session_factory() as session:
            rows = session.scalars(
                select(StockHistory)
                .where(StockHistory.inventory_id == inventory_id)
                .order_by(StockHistory.id.asc())
                .limit(limit)
            ).all()
            return [self._row_to_history_record(r) for r in rows]

    def get_pharmacy_history(self, pharmacy_id: int, limit: int = 50) -> list[StockHistoryRecord]:
        """All history rows for a pharmacy (oldest first)."""
        with self._session_factory() as session:
            rows = session.scalars(
                select(StockHistory)
                .where(StockHistory.pharmacy_id == pharmacy_id)
                .order_by(StockHistory.id.asc())
                .limit(limit)
            ).all()
            return [self._row_to_history_record(r) for r in rows]

    def get_user_history(self, user_id: int, limit: int = 50) -> list[StockHistoryRecord]:
        """All history rows created by a user (oldest first)."""
        with self._session_factory() as session:
            rows = session.scalars(
                select(StockHistory)
                .where(StockHistory.changed_by == user_id)
                .order_by(StockHistory.id.asc())
                .limit(limit)
            ).all()
            return [self._row_to_history_record(r) for r in rows]

    def has_undo_for_history(self, history_id: int) -> bool:
        """Check if any history row has undo_of_history_id == history_id."""
        with self._session_factory() as session:
            existing = session.scalar(
                select(StockHistory).where(StockHistory.undo_of_history_id == history_id)
            )
            return existing is not None

    def has_newer_history_for_inventory(self, inventory_id: int, history_id: int) -> bool:
        """Check if any history row exists with same inventory_id and id > history_id."""
        with self._session_factory() as session:
            newer = session.scalar(
                select(StockHistory).where(
                    StockHistory.inventory_id == inventory_id,
                    StockHistory.id > history_id,
                )
            )
            return newer is not None

    def undo_last_change(
        self,
        history_id: int,
        undone_by_user_id: int,
        pharmacy_id: int,
    ) -> tuple[StockHistoryRecord, InventoryRecord]:
        """Single atomic transaction:

        1. Verify pharmacy membership.
        2. Verify history row exists and matches pharmacy scope.
        3. Verify target row is STOCK_UPDATE (cannot undo an UNDO).
        4. Verify target row has not already been undone.
        5. Verify no newer change exists on the same inventory item.
        6. Append UNDO row to stock_history.
        7. Restore inventory row to target's previous values.
        """
        with self._session_factory() as session:
            try:
                # 1. Authorization check
                if not self._is_user_linked(session, undone_by_user_id, pharmacy_id):
                    raise UserNotLinkedToPharmacyError(
                        f"User {undone_by_user_id} is not linked to pharmacy {pharmacy_id}."
                    )

                # 2. History existence check
                target = session.get(StockHistory, history_id)
                if target is None:
                    raise ValueError(f"History record with ID {history_id} does not exist.")

                # 3. Pharmacy scope check
                if target.pharmacy_id != pharmacy_id:
                    raise PharmacyScopeMismatchError(
                        f"History record {history_id} belongs to pharmacy {target.pharmacy_id}, not {pharmacy_id}."
                    )

                # 4. Cannot undo UNDO row
                if target.change_type != "STOCK_UPDATE":
                    raise CannotUndoUndoError(
                        f"Target history row {history_id} has change_type '{target.change_type}'. Cannot undo an UNDO."
                    )

                # 5. Check if already undone
                undo_existing = session.scalar(
                    select(StockHistory).where(StockHistory.undo_of_history_id == history_id)
                )
                if undo_existing is not None:
                    raise HistoryAlreadyUndoneError(
                        f"History row {history_id} has already been undone."
                    )

                # 6. Latest-change rule
                newer_existing = session.scalar(
                    select(StockHistory).where(
                        StockHistory.inventory_id == target.inventory_id,
                        StockHistory.id > history_id,
                    )
                )
                if newer_existing is not None:
                    raise NewerChangeExistsError(
                        f"Cannot undo history row {history_id}: newer changes exist on inventory item {target.inventory_id}."
                    )

                # 7. Get inventory row
                inv = session.get(Inventory, target.inventory_id)
                if inv is None:
                    raise InventoryNotFoundError(
                        f"Inventory item {target.inventory_id} not found."
                    )

                now = datetime.now(timezone.utc)
                now_db = now.replace(tzinfo=None)

                restored_status = target.previous_stock_status or (
                    "OUT_OF_STOCK" if target.previous_quantity == 0 else "IN_STOCK"
                )
                restored_quantity = target.previous_quantity
                restored_last_confirmed_at = target.previous_last_confirmed_at or now_db

                # 8. Create UNDO row in history
                undo_row = StockHistory(
                    inventory_id=target.inventory_id,
                    pharmacy_id=pharmacy_id,
                    medicine_id=target.medicine_id,
                    changed_by=undone_by_user_id,
                    source_proposal_id=target.source_proposal_id,
                    change_type="UNDO",
                    undo_of_history_id=history_id,
                    previous_stock_status=inv.stock_status,
                    new_stock_status=restored_status,
                    previous_quantity=inv.quantity,
                    new_quantity=restored_quantity,
                    previous_last_confirmed_at=inv.last_confirmed_at,
                    change_reason=f"undo of #{history_id}",
                    created_at=now_db,
                )
                session.add(undo_row)

                # 9. Mutate inventory
                inv.stock_status = restored_status
                inv.quantity = restored_quantity
                inv.last_confirmed_at = restored_last_confirmed_at
                inv.last_updated_by = undone_by_user_id
                inv.last_update_source = "undo"

                session.commit()

                return (
                    self._row_to_history_record(undo_row),
                    InventoryRecord(
                        id=inv.id,
                        pharmacy_id=inv.pharmacy_id,
                        medicine_id=inv.medicine_id,
                        stock_status=StockActionEnum(inv.stock_status),
                        quantity=inv.quantity,
                        last_confirmed_at=_to_utc(inv.last_confirmed_at),
                        last_updated_by=inv.last_updated_by,
                        last_update_source=inv.last_update_source,
                    ),
                )
            except IntegrityError as exc:
                session.rollback()
                if "undo_of_history_id" in str(exc).lower() or "unique" in str(exc).lower():
                    raise HistoryAlreadyUndoneError(
                        f"History row {history_id} has already been undone."
                    ) from exc
                raise
            except Exception:
                session.rollback()
                raise
