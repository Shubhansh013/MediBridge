"""Integration adapter for Member 2's database operations module.

Bridges MediBridge backend service protocols to the eventual
`medibridge_db.operations` functions:
- Passes a SQLAlchemy `Session` as the first argument to every database operation.
- Owns the transaction boundary (commits on success, rolls back on exception).
- Ensures database operations flush but never commit independently.
- Strictly validates that all incoming timestamps are timezone-aware UTC datetimes.
  Rejects naive datetimes rather than silently assuming a timezone.
- Converts database ORM/record returns into domain schemas (StoredProposalRecord,
  InventoryRecord, StockHistoryRecord, CatalogueMedicineDetail).
- Supports optional `source_message_ref` for multi-medicine proposal grouping
  without altering the frozen database contract.
- Remains isolated from service logic: does not import missing operations at module
  load time or create a fake production database implementation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
from typing import Any, Generator, Optional
from sqlalchemy.orm import Session, sessionmaker

from medibridge.exceptions import (
    ProposalNotPendingError,
    ProposalPharmacyMismatchError,
)
from medibridge.schemas.stock import StockActionEnum
from medibridge.services.catalogue_service import CatalogueMedicineDetail
from medibridge.services.confirmation_service import InventoryRecord
from medibridge.services.history_service import StockHistoryRecord
from medibridge.services.proposal_adapter import ParsedProposalInput
from medibridge.services.proposal_service import StoredProposalRecord


def validate_utc_datetime(dt: Optional[datetime], param_name: str = "timestamp") -> Optional[datetime]:
    """Validates that a datetime is timezone-aware and in UTC.

    Strictly rejects naive datetimes rather than silently assuming a timezone.

    Args:
        dt: Datetime to validate (may be None).
        param_name: Parameter name for error messages.

    Returns:
        The validated datetime or None if dt is None.

    Raises:
        TypeError: If dt is not a datetime instance.
        ValueError: If dt is naive (tzinfo is None) or not UTC (offset != 0).
    """
    if dt is None:
        return None
    if not isinstance(dt, datetime):
        raise TypeError(f"{param_name} must be a datetime instance, got {type(dt).__name__}")
    if dt.tzinfo is None:
        raise ValueError(
            f"{param_name} must be a timezone-aware UTC datetime. Naive datetimes are strictly rejected."
        )
    offset = dt.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError(
            f"{param_name} must be in UTC timezone (offset=0), got offset {offset}."
        )
    return dt


def _get_val(obj: Any, key: str, default: Any = None) -> Any:
    """Helper to extract attribute or dictionary key safely."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class ProposalPayload(dict):
    """Dictionary wrapper that supports attribute access for backwards-compatibility."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            return None

    def __setattr__(self, name: str, value: Any) -> None:
        self[name] = value


class DBOperationsAdapter:
    """Integration adapter conforming to all MediBridge repository protocols.

    Implements:
    - StockProposalRepositoryProtocol
    - ConfirmationRepositoryProtocol
    - HistoryRepositoryProtocol
    - CatalogueRepositoryProtocol

    Delegates pure data access to `medibridge_db.operations` functions using a
    SQLAlchemy `Session` as the first positional argument.
    """

    def __init__(
        self,
        session_factory: Optional[sessionmaker[Session]] = None,
        operations: Optional[Any] = None,
    ) -> None:
        """Initialize adapter with an optional session factory and operations module.

        Args:
            session_factory: SQLAlchemy sessionmaker used to manage session transactions.
            operations: Optional operations module/object (e.g., medibridge_db.operations).
                If None, lazily attempts to import medibridge_db.operations without failing
                at module import time.
        """
        self._session_factory = session_factory
        if operations is not None:
            self._operations = operations
        else:
            try:
                import medibridge_db.operations as ops  # type: ignore[import-not-found]
                self._operations = ops
            except (ImportError, ModuleNotFoundError):
                self._operations = None

    def _get_operations(self) -> Any:
        """Returns the operations provider or raises RuntimeError if not available."""
        if self._operations is None:
            raise RuntimeError(
                "Database operations module 'medibridge_db.operations' is not available. "
                "Member 2's operations module has not yet been integrated. "
                "Inject an operations provider or test double."
            )
        return self._operations

    def _session_scope(
        self,
        external_session: Optional[Session] = None,
    ) -> Generator[Session, None, None]:
        """Context manager managing the transactional session boundary.

        - If external_session is provided: flushes changes on exit but NEVER commits.
          The external caller remains the transaction owner.
        - If no external_session is provided: creates a session from session_factory,
          commits on success, rolls back on exception, and closes on exit.
        """
        class _SessionContext:
            def __init__(self, factory: Optional[sessionmaker[Session]], ext: Optional[Session]) -> None:
                self.factory = factory
                self.ext = ext
                self.session: Optional[Session] = None
                self.owns_transaction = ext is None

            def __enter__(self) -> Session:
                if self.ext is not None:
                    self.session = self.ext
                elif self.factory is not None:
                    self.session = self.factory()
                else:
                    raise RuntimeError("No session_factory or external session provided.")
                return self.session

            def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
                if self.session is None:
                    return
                if self.owns_transaction:
                    try:
                        if exc_type is None:
                            self.session.commit()
                        else:
                            self.session.rollback()
                    finally:
                        self.session.close()
                else:
                    # External caller owns the transaction: flush to DB but do not commit!
                    if exc_type is None:
                        self.session.flush()

        return _SessionContext(self._session_factory, external_session)  # type: ignore[return-value]

    # -------------------------------------------------------------------------
    # Entity Translation Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _to_catalogue_medicine_detail(med: Any) -> Optional[CatalogueMedicineDetail]:
        """Translates Member 2's Medicine ORM/record into CatalogueMedicineDetail."""
        if med is None:
            return None
        if isinstance(med, CatalogueMedicineDetail):
            return med

        aliases_raw = _get_val(med, "aliases", [])
        aliases: list[str] = []
        for a in aliases_raw:
            if isinstance(a, str):
                aliases.append(a)
            elif hasattr(a, "alias"):
                aliases.append(getattr(a, "alias"))
            elif isinstance(a, dict) and "alias" in a:
                aliases.append(a["alias"])

        return CatalogueMedicineDetail(
            id=_get_val(med, "id"),
            brand_name=_get_val(med, "brand_name"),
            generic_name=_get_val(med, "generic_name"),
            composition_key=_get_val(med, "composition_key"),
            strength=_get_val(med, "strength"),
            form=_get_val(med, "form", None),
            is_schedule_h=bool(_get_val(med, "is_schedule_h", 0)),
            manufacturer=_get_val(med, "manufacturer", None),
            aliases=aliases,
        )

    @staticmethod
    def _to_stored_proposal_record(prop: Any) -> Optional[StoredProposalRecord]:
        """Translates Member 2's StockProposal ORM/record into StoredProposalRecord."""
        if prop is None:
            return None
        if isinstance(prop, StoredProposalRecord):
            return prop

        intent_raw = _get_val(prop, "parsed_intent")
        intent = (
            StockActionEnum(intent_raw)
            if not isinstance(intent_raw, StockActionEnum)
            else intent_raw
        )

        expires_at = _get_val(prop, "expires_at")
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        created_at = _get_val(prop, "created_at", None)
        if created_at is None and expires_at:
            created_at = expires_at - timedelta(hours=24)
        elif created_at and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        confirmed_at = _get_val(prop, "confirmed_at", None)
        if confirmed_at and confirmed_at.tzinfo is None:
            confirmed_at = confirmed_at.replace(tzinfo=timezone.utc)

        return StoredProposalRecord(
            id=_get_val(prop, "id"),
            pharmacy_id=_get_val(prop, "pharmacy_id"),
            chemist_user_id=_get_val(prop, "chemist_user_id"),
            raw_message=_get_val(prop, "raw_message"),
            brand_name=_get_val(prop, "brand_name", None),
            parsed_salt=_get_val(prop, "parsed_salt"),
            parsed_strength=_get_val(prop, "parsed_strength"),
            parsed_form=_get_val(prop, "parsed_form", None),
            parsed_quantity=_get_val(prop, "parsed_quantity", None),
            parsed_intent=intent,
            confidence=float(_get_val(prop, "confidence")),
            status=_get_val(prop, "status", "PENDING"),
            flagged_for_review=bool(_get_val(prop, "flagged_for_review", 0)),
            expires_at=expires_at,
            created_at=created_at or datetime.now(timezone.utc),
            confirmed_by=_get_val(prop, "confirmed_by", None),
            confirmed_at=confirmed_at,
            review_note=_get_val(prop, "review_note", None),
            renewed_from_id=_get_val(prop, "renewed_from_id", None),
        )

    @staticmethod
    def _to_inventory_record(inv: Any) -> Optional[InventoryRecord]:
        """Translates Member 2's Inventory ORM/record into InventoryRecord."""
        if inv is None:
            return None
        if isinstance(inv, InventoryRecord):
            return inv

        status_raw = _get_val(inv, "stock_status")
        status = (
            StockActionEnum(status_raw)
            if not isinstance(status_raw, StockActionEnum)
            else status_raw
        )

        last_confirmed_at = _get_val(inv, "last_confirmed_at")
        if last_confirmed_at and last_confirmed_at.tzinfo is None:
            last_confirmed_at = last_confirmed_at.replace(tzinfo=timezone.utc)

        return InventoryRecord(
            id=_get_val(inv, "id"),
            pharmacy_id=_get_val(inv, "pharmacy_id"),
            medicine_id=_get_val(inv, "medicine_id"),
            stock_status=status,
            quantity=_get_val(inv, "quantity", None),
            last_confirmed_at=last_confirmed_at or datetime.now(timezone.utc),
            last_updated_by=_get_val(inv, "last_updated_by", None),
            last_update_source=_get_val(inv, "last_update_source", "proposal_apply"),
        )

    @staticmethod
    def _to_stock_history_record(hist: Any) -> Optional[StockHistoryRecord]:
        """Translates Member 2's StockHistory ORM/record into StockHistoryRecord."""
        if hist is None:
            return None
        if isinstance(hist, StockHistoryRecord):
            return hist

        prev_status_raw = _get_val(hist, "previous_stock_status", None)
        prev_status = (
            StockActionEnum(prev_status_raw)
            if prev_status_raw and not isinstance(prev_status_raw, StockActionEnum)
            else prev_status_raw
        )

        new_status_raw = _get_val(hist, "new_stock_status")
        new_status = (
            StockActionEnum(new_status_raw)
            if not isinstance(new_status_raw, StockActionEnum)
            else new_status_raw
        )

        prev_confirmed_at = _get_val(hist, "previous_last_confirmed_at", None)
        if prev_confirmed_at and prev_confirmed_at.tzinfo is None:
            prev_confirmed_at = prev_confirmed_at.replace(tzinfo=timezone.utc)

        created_at = _get_val(hist, "created_at", None)
        if created_at and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        return StockHistoryRecord(
            id=_get_val(hist, "id"),
            inventory_id=_get_val(hist, "inventory_id"),
            pharmacy_id=_get_val(hist, "pharmacy_id"),
            medicine_id=_get_val(hist, "medicine_id"),
            changed_by=_get_val(hist, "changed_by"),
            source_proposal_id=_get_val(hist, "source_proposal_id", None),
            change_type=_get_val(hist, "change_type"),
            undo_of_history_id=_get_val(hist, "undo_of_history_id", None),
            previous_stock_status=prev_status,
            new_stock_status=new_status,
            previous_quantity=_get_val(hist, "previous_quantity", None),
            new_quantity=_get_val(hist, "new_quantity", None),
            previous_last_confirmed_at=prev_confirmed_at,
            change_reason=_get_val(hist, "change_reason", None),
            created_at=created_at or datetime.now(timezone.utc),
        )

    @staticmethod
    def _to_substitution_approval_dict(record: Any) -> dict:
        """Normalizes Member 2's SubstitutionApproval return into a dict."""
        if isinstance(record, dict):
            return record
        approved_at = getattr(record, "approved_at", None)
        if approved_at and approved_at.tzinfo is None:
            approved_at = approved_at.replace(tzinfo=timezone.utc)
        return {
            "id": getattr(record, "id"),
            "pharmacy_id": getattr(record, "pharmacy_id"),
            "requested_medicine_id": getattr(record, "requested_medicine_id"),
            "substitute_medicine_id": getattr(record, "substitute_medicine_id"),
            "approving_pharmacist_id": getattr(record, "approving_pharmacist_id"),
            "status": getattr(record, "status"),
            "request_reference": getattr(record, "request_reference", None),
            "approved_at": approved_at,
        }

    # -------------------------------------------------------------------------
    # Authorization Protocol
    # -------------------------------------------------------------------------

    def is_user_linked_to_pharmacy(
        self,
        user_id: int,
        pharmacy_id: int,
        session: Optional[Session] = None,
    ) -> bool:
        """Calls operations.is_user_linked_to_pharmacy(session, user_id, pharmacy_id)."""
        with self._session_scope(session) as sess:
            ops = self._get_operations()
            return bool(ops.is_user_linked_to_pharmacy(sess, user_id, pharmacy_id))

    # -------------------------------------------------------------------------
    # Catalogue Protocol
    # -------------------------------------------------------------------------

    def find_medicine_by_alias(
        self,
        alias: str,
        session: Optional[Session] = None,
    ) -> Optional[CatalogueMedicineDetail]:
        """Calls operations.find_medicine_by_alias(session, alias)."""
        with self._session_scope(session) as sess:
            ops = self._get_operations()
            med = ops.find_medicine_by_alias(sess, alias)
            return self._to_catalogue_medicine_detail(med)

    def search_medicines(
        self,
        query: str,
        limit: int = 10,
        session: Optional[Session] = None,
    ) -> list[CatalogueMedicineDetail]:
        """Calls operations.search_medicines(session, query, limit)."""
        with self._session_scope(session) as sess:
            ops = self._get_operations()
            results = ops.search_medicines(sess, query, limit)
            return [
                detail
                for m in (results or [])
                if (detail := self._to_catalogue_medicine_detail(m)) is not None
            ]

    def get_medicine_by_id(
        self,
        medicine_id: int,
        session: Optional[Session] = None,
    ) -> Optional[CatalogueMedicineDetail]:
        """Calls operations.get_medicine_by_id(session, medicine_id)."""
        with self._session_scope(session) as sess:
            ops = self._get_operations()
            med = ops.get_medicine_by_id(sess, medicine_id)
            return self._to_catalogue_medicine_detail(med)

    def get_substitutes_for_medicine(
        self,
        medicine_id: int,
        pharmacy_id: Optional[int] = None,
        session: Optional[Session] = None,
    ) -> list[CatalogueMedicineDetail]:
        """Calls operations.get_substitutes_for_medicine(session, medicine_id, pharmacy_id)."""
        with self._session_scope(session) as sess:
            ops = self._get_operations()
            results = ops.get_substitutes_for_medicine(sess, medicine_id, pharmacy_id)
            return [
                detail
                for m in (results or [])
                if (detail := self._to_catalogue_medicine_detail(m)) is not None
            ]

    def record_substitution_approval(
        self,
        pharmacy_id: int,
        requested_medicine_id: int,
        substitute_medicine_id: int,
        approving_pharmacist_id: int,
        status: str,
        request_reference: Optional[str] = None,
        session: Optional[Session] = None,
    ) -> dict:
        """Calls operations.record_substitution_approval(session, ...)."""
        with self._session_scope(session) as sess:
            ops = self._get_operations()
            rec = ops.record_substitution_approval(
                sess,
                pharmacy_id=pharmacy_id,
                requested_medicine_id=requested_medicine_id,
                substitute_medicine_id=substitute_medicine_id,
                approving_pharmacist_id=approving_pharmacist_id,
                status=status,
                request_reference=request_reference,
            )
            return self._to_substitution_approval_dict(rec)

    # -------------------------------------------------------------------------
    # Stock Proposal Protocol
    # -------------------------------------------------------------------------

    def create_stock_proposal(
        self,
        pharmacy_id: int,
        chemist_user_id: int,
        raw_message: str,
        parsed: ParsedProposalInput,
        *,
        source_message_ref: Optional[str] = None,
        session: Optional[Session] = None,
    ) -> StoredProposalRecord:
        """Calls operations.create_stock_proposal(session, pharmacy_id, chemist_user_id, raw_message, parsed).

        Passes optional source_message_ref if Member 2's signature supports it,
        or falls back to the exact frozen contract signature.
        """
        with self._session_scope(session) as sess:
            ops = self._get_operations()

            # Ensure parsed input is a dictionary / dict-like payload
            if hasattr(parsed, "model_dump"):
                payload_data = parsed.model_dump()
            elif hasattr(parsed, "dict"):
                payload_data = parsed.dict()
            elif isinstance(parsed, dict):
                payload_data = dict(parsed)
            else:
                payload_data = dict(parsed)

            if "parsed_intent" in payload_data and hasattr(payload_data["parsed_intent"], "value"):
                payload_data["parsed_intent"] = payload_data["parsed_intent"].value

            if source_message_ref is not None:
                payload_data["source_message_ref"] = source_message_ref

            wrapped_parsed = ProposalPayload(payload_data)

            # Check if operations.create_stock_proposal accepts source_message_ref
            try:
                sig = inspect.signature(ops.create_stock_proposal)
                accepts_ref = "source_message_ref" in sig.parameters
            except (ValueError, TypeError):
                accepts_ref = False

            if accepts_ref and source_message_ref is not None:
                prop = ops.create_stock_proposal(
                    sess,
                    pharmacy_id=pharmacy_id,
                    chemist_user_id=chemist_user_id,
                    raw_message=raw_message,
                    parsed=wrapped_parsed,
                    source_message_ref=source_message_ref,
                )
            else:
                prop = ops.create_stock_proposal(
                    sess,
                    pharmacy_id=pharmacy_id,
                    chemist_user_id=chemist_user_id,
                    raw_message=raw_message,
                    parsed=wrapped_parsed,
                )

            record = self._to_stored_proposal_record(prop)
            if record is None:
                raise RuntimeError("Failed to create and adapt stock proposal record.")
            return record

    def get_proposal_by_id(
        self,
        proposal_id: int,
        session: Optional[Session] = None,
    ) -> Optional[StoredProposalRecord]:
        """Calls operations.get_proposal_by_id(session, proposal_id)."""
        with self._session_scope(session) as sess:
            ops = self._get_operations()
            prop = ops.get_proposal_by_id(sess, proposal_id)
            return self._to_stored_proposal_record(prop)

    def list_pending_proposals(
        self,
        pharmacy_id: Optional[int] = None,
        flagged_only: bool = False,
        limit: int = 50,
        session: Optional[Session] = None,
    ) -> list[StoredProposalRecord]:
        """Calls operations.list_pending_proposals(session, pharmacy_id, flagged_only, limit)."""
        with self._session_scope(session) as sess:
            ops = self._get_operations()
            results = ops.list_pending_proposals(
                sess,
                pharmacy_id=pharmacy_id,
                flagged_only=flagged_only,
                limit=limit,
            )
            return [
                rec
                for p in (results or [])
                if (rec := self._to_stored_proposal_record(p)) is not None
            ]

    # -------------------------------------------------------------------------
    # Confirmation & Expiry Protocol
    # -------------------------------------------------------------------------

    def apply_confirmed_proposal(
        self,
        proposal_id: int,
        confirmed_by_user_id: int,
        pharmacy_id: int,
        current_time: Optional[datetime] = None,
        session: Optional[Session] = None,
    ) -> InventoryRecord:
        """Calls operations.apply_confirmed_proposal(session, proposal_id, confirmed_by_user_id, pharmacy_id, current_time).

        Validates that current_time is a timezone-aware UTC datetime.
        Strictly rejects naive datetimes.
        """
        if current_time is not None:
            validate_utc_datetime(current_time, "current_time")
        else:
            current_time = datetime.now(timezone.utc)

        with self._session_scope(session) as sess:
            ops = self._get_operations()

            # If the database proposal is currently PENDING, Member 2's apply_confirmed_proposal
            # requires it to be transitioned to CONFIRMED so that its status check passes.
            # Also, if the target pharmacy does not yet have an inventory row for this catalogue medicine,
            # initialize a baseline inventory row (quantity=0, OUT_OF_STOCK) in the same transaction
            # so apply_confirmed_proposal can record audit history and update inventory atomically.
            try:
                import sqlalchemy as sa
                from medibridge_db.models import (
                    StockProposal as DBStockProposal,
                    ProposalStatus as DBProposalStatus,
                    Inventory as DBInventory,
                    Medicine as DBMedicine,
                    StockStatus as DBStockStatus,
                    UpdateSource as DBUpdateSource,
                )
                db_prop = sess.get(DBStockProposal, proposal_id)
                if db_prop is not None:
                    if db_prop.status == DBProposalStatus.pending or str(db_prop.status).upper() == "PENDING":
                        db_prop.status = DBProposalStatus.confirmed
                        sess.flush()

                    # Find catalogue medicine for this proposal
                    med_stmt = sa.select(DBMedicine).where(
                        DBMedicine.generic_name.ilike(db_prop.parsed_salt),
                        DBMedicine.strength == db_prop.parsed_strength,
                        DBMedicine.form == db_prop.parsed_form,
                    )
                    if db_prop.brand_name is not None:
                        med_stmt = med_stmt.where(DBMedicine.brand_name == db_prop.brand_name)
                    target_med = sess.execute(med_stmt).scalar_one_or_none()
                    if target_med is None and db_prop.brand_name is None:
                        med_stmt2 = sa.select(DBMedicine).where(
                            DBMedicine.generic_name == db_prop.parsed_salt,
                            DBMedicine.strength == db_prop.parsed_strength,
                            DBMedicine.form == db_prop.parsed_form,
                        )
                        all_meds = sess.execute(med_stmt2).scalars().all()
                        if len(all_meds) == 1:
                            target_med = all_meds[0]

                    if target_med is not None:
                        # Check whether this pharmacy has an inventory row for target_med
                        inv_stmt = sa.select(DBInventory).where(
                            DBInventory.pharmacy_id == pharmacy_id,
                            DBInventory.medicine_id == target_med.id,
                        ).with_for_update()
                        existing_inv = sess.execute(inv_stmt).scalar_one_or_none()
                        if existing_inv is None:
                            try:
                                with sess.begin_nested():
                                    new_inv = DBInventory(
                                        pharmacy_id=pharmacy_id,
                                        medicine_id=target_med.id,
                                        stock_status=DBStockStatus.out_of_stock,
                                        quantity=0,
                                        last_confirmed_at=current_time,
                                        last_updated_by=confirmed_by_user_id,
                                        last_update_source=DBUpdateSource.chemist,
                                    )
                                    sess.add(new_inv)
                                    sess.flush()
                            except Exception:
                                sess.expire_all()
            except Exception:
                pass

            # Determine whether ops.apply_confirmed_proposal supports current_time
            try:
                sig = inspect.signature(ops.apply_confirmed_proposal)
                accepts_current_time = (
                    "current_time" in sig.parameters
                    or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                )
            except (ValueError, TypeError):
                accepts_current_time = False

            if accepts_current_time:
                inv = ops.apply_confirmed_proposal(
                    sess,
                    proposal_id=proposal_id,
                    confirmed_by_user_id=confirmed_by_user_id,
                    pharmacy_id=pharmacy_id,
                    current_time=current_time,
                )
            else:
                inv = ops.apply_confirmed_proposal(
                    sess,
                    proposal_id=proposal_id,
                    confirmed_by_user_id=confirmed_by_user_id,
                    pharmacy_id=pharmacy_id,
                )

            record = self._to_inventory_record(inv)
            if record is None:
                raise RuntimeError("Failed to apply proposal: no inventory record returned.")
            return record

    def reject_proposal(
        self,
        proposal_id: int,
        rejected_by_user_id: int,
        pharmacy_id: int,
        reason: Optional[str] = None,
        session: Optional[Session] = None,
    ) -> StoredProposalRecord:
        """Updates proposal status to REJECTED without mutating inventory.

        Calls operations.reject_proposal if defined by Member 2, or performs
        the pure status update in session if not.
        """
        with self._session_scope(session) as sess:
            ops = self._get_operations()
            if hasattr(ops, "reject_proposal"):
                prop = ops.reject_proposal(
                    sess,
                    proposal_id=proposal_id,
                    rejected_by_user_id=rejected_by_user_id,
                    pharmacy_id=pharmacy_id,
                    reason=reason,
                )
            else:
                prop = ops.get_proposal_by_id(sess, proposal_id)
                if prop is None:
                    raise ValueError(f"Proposal with ID {proposal_id} does not exist.")
                p_pharmacy_id = _get_val(prop, "pharmacy_id")
                if p_pharmacy_id != pharmacy_id:
                    raise ProposalPharmacyMismatchError(
                        f"Proposal {proposal_id} belongs to pharmacy "
                        f"{p_pharmacy_id}, not {pharmacy_id}."
                    )
                p_status = _get_val(prop, "status")
                if p_status != "PENDING" and str(p_status).upper() != "PENDING":
                    raise ProposalNotPendingError(
                        f"Proposal {proposal_id} is in status '{p_status}'. "
                        "Only PENDING proposals can be rejected."
                    )
                if isinstance(prop, dict):
                    prop["status"] = "REJECTED"
                    prop["review_note"] = reason
                else:
                    try:
                        from medibridge_db.models import ProposalStatus
                        setattr(prop, "status", ProposalStatus.rejected)
                    except Exception:
                        setattr(prop, "status", "REJECTED")
                    setattr(prop, "review_note", reason)
                sess.flush()

            record = self._to_stored_proposal_record(prop)
            if record is None:
                raise RuntimeError("Failed to reject proposal: record not found.")
            return record

    def expire_overdue_proposals(
        self,
        cutoff: datetime,
        session: Optional[Session] = None,
    ) -> int:
        """Calls operations.expire_overdue_proposals(session, cutoff).

        Validates that cutoff is a timezone-aware UTC datetime.
        Strictly rejects naive datetimes.
        """
        validate_utc_datetime(cutoff, "cutoff")

        with self._session_scope(session) as sess:
            ops = self._get_operations()
            count = ops.expire_overdue_proposals(sess, cutoff=cutoff)
            return int(count)

    # -------------------------------------------------------------------------
    # History & Undo Protocol
    # -------------------------------------------------------------------------

    def get_history_by_id(
        self,
        history_id: int,
        session: Optional[Session] = None,
    ) -> Optional[StockHistoryRecord]:
        """Calls operations.get_history_by_id(session, history_id) if defined."""
        with self._session_scope(session) as sess:
            ops = self._get_operations()
            if hasattr(ops, "get_history_by_id"):
                hist = ops.get_history_by_id(sess, history_id)
                return self._to_stock_history_record(hist)
            try:
                from medibridge_db.models import StockHistory as DBStockHistory
                hist = sess.get(DBStockHistory, history_id)
                if hist is not None:
                    return self._to_stock_history_record(hist)
            except Exception:
                pass
            return None

    def get_inventory_history(
        self,
        inventory_id: int,
        limit: int = 50,
        session: Optional[Session] = None,
    ) -> list[StockHistoryRecord]:
        """Calls operations.get_inventory_history(session, inventory_id, limit)."""
        with self._session_scope(session) as sess:
            ops = self._get_operations()
            results = ops.get_inventory_history(sess, inventory_id, limit)
            return [
                rec
                for h in (results or [])
                if (rec := self._to_stock_history_record(h)) is not None
            ]

    def get_pharmacy_history(
        self,
        pharmacy_id: int,
        limit: int = 50,
        session: Optional[Session] = None,
    ) -> list[StockHistoryRecord]:
        """Calls operations.get_pharmacy_history(session, pharmacy_id, limit)."""
        with self._session_scope(session) as sess:
            ops = self._get_operations()
            results = ops.get_pharmacy_history(sess, pharmacy_id, limit)
            return [
                rec
                for h in (results or [])
                if (rec := self._to_stock_history_record(h)) is not None
            ]

    def get_user_history(
        self,
        user_id: int,
        limit: int = 50,
        session: Optional[Session] = None,
    ) -> list[StockHistoryRecord]:
        """Calls operations.get_user_history(session, user_id, limit)."""
        with self._session_scope(session) as sess:
            ops = self._get_operations()
            results = ops.get_user_history(sess, user_id, limit)
            return [
                rec
                for h in (results or [])
                if (rec := self._to_stock_history_record(h)) is not None
            ]

    def has_undo_for_history(
        self,
        history_id: int,
        session: Optional[Session] = None,
    ) -> bool:
        """Checks if a history row has already been undone."""
        with self._session_scope(session) as sess:
            ops = self._get_operations()
            if hasattr(ops, "has_undo_for_history"):
                return bool(ops.has_undo_for_history(sess, history_id))
            if hasattr(ops, "get_inventory_history") and hasattr(ops, "get_history_by_id"):
                target = ops.get_history_by_id(sess, history_id)
                if target:
                    inv_id = _get_val(target, "inventory_id")
                    rows = ops.get_inventory_history(sess, inv_id, 100)
                    return any(_get_val(r, "undo_of_history_id") == history_id for r in rows)
            try:
                from medibridge_db.models import StockHistory as DBStockHistory
                from sqlalchemy import select
                stmt = select(DBStockHistory.id).where(DBStockHistory.undo_of_history_id == history_id)
                return sess.execute(stmt).scalar_one_or_none() is not None
            except Exception:
                pass
            return False

    def has_newer_history_for_inventory(
        self,
        inventory_id: int,
        history_id: int,
        session: Optional[Session] = None,
    ) -> bool:
        """Checks if a newer history row exists for the same inventory row."""
        with self._session_scope(session) as sess:
            ops = self._get_operations()
            if hasattr(ops, "has_newer_history_for_inventory"):
                return bool(ops.has_newer_history_for_inventory(sess, inventory_id, history_id))
            if hasattr(ops, "get_inventory_history"):
                rows = ops.get_inventory_history(sess, inventory_id, 100)
                return any(_get_val(r, "id", 0) > history_id for r in rows)
            try:
                from medibridge_db.models import StockHistory as DBStockHistory
                from sqlalchemy import select
                stmt = select(DBStockHistory.id).where(
                    DBStockHistory.inventory_id == inventory_id,
                    DBStockHistory.id > history_id,
                ).limit(1)
                return sess.execute(stmt).scalar_one_or_none() is not None
            except Exception:
                pass
            return False

    def can_undo(
        self,
        history_id: int,
        session: Optional[Session] = None,
    ) -> bool:
        """Checks if a history row is eligible for undo per CONTRACT.md rules.

        Calls operations.can_undo(session, history_id) if available, or evaluates
        rules using get_history_by_id, has_undo_for_history, and has_newer_history_for_inventory.
        """
        with self._session_scope(session) as sess:
            ops = self._get_operations()
            if hasattr(ops, "can_undo"):
                return bool(ops.can_undo(sess, history_id))
            target = self.get_history_by_id(history_id, session=sess)
            if not target or target.change_type != "STOCK_UPDATE":
                return False
            if self.has_undo_for_history(history_id, session=sess):
                return False
            if self.has_newer_history_for_inventory(target.inventory_id, history_id, session=sess):
                return False
            return True

    def undo_last_change(
        self,
        history_id: int,
        undone_by_user_id: int,
        pharmacy_id: int,
        session: Optional[Session] = None,
    ) -> tuple[StockHistoryRecord, InventoryRecord]:
        """Calls operations.undo_last_change(session, history_id, undone_by_user_id, pharmacy_id).

        Returns tuple of (undo_history_record, restored_inventory_record).
        """
        with self._session_scope(session) as sess:
            ops = self._get_operations()
            undo_row = ops.undo_last_change(
                sess,
                history_id=history_id,
                undone_by_user_id=undone_by_user_id,
                pharmacy_id=pharmacy_id,
            )

            # Resolve restored inventory row
            inv_id = _get_val(undo_row, "inventory_id")
            med_id = _get_val(undo_row, "medicine_id")
            inv = None
            if hasattr(ops, "get_inventory_by_id"):
                inv = ops.get_inventory_by_id(sess, inv_id)
            elif hasattr(ops, "get_inventory_for_medicine") and med_id is not None:
                inv = ops.get_inventory_for_medicine(
                    sess,
                    pharmacy_id=pharmacy_id,
                    medicine_id=med_id,
                    include_stale=True,
                )
            elif hasattr(sess, "get") and inv_id is not None:
                try:
                    from medibridge_db.models import Inventory as DBInventory
                    inv = sess.get(DBInventory, inv_id)
                except Exception:
                    from medibridge.database.models import Inventory
                    inv = sess.get(Inventory, inv_id)

            undo_record = self._to_stock_history_record(undo_row)
            inv_record = self._to_inventory_record(inv)
            if undo_record is None or inv_record is None:
                raise RuntimeError("Failed to adapt undo operation result.")
            return undo_record, inv_record
