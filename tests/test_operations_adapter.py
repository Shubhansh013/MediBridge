"""Tests for DBOperationsAdapter and Member 2 database operations integration.

Verifies:
- Calling operations fails gracefully with RuntimeError when medibridge_db.operations is absent
- Every database operation receives a SQLAlchemy Session as its first positional argument
- Adapter acts as transaction owner: commits on success, rolls back on exception
- External sessions: database operations flush but NEVER commit independently
- Strict rejection of naive datetimes for timestamps (cutoff, current_time)
- Strict rejection of non-UTC timezone datetimes
- Acceptance of valid timezone-aware UTC datetimes
- Forwarding of optional source_message_ref when supported, and clean fallback when not
- Accurate domain entity translation for proposals, inventory, history, and catalogue records
- Full service-layer integration with test doubles
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call
import unittest
from sqlalchemy.orm import Session, sessionmaker

from medibridge.database.connection import (
    create_session_factory,
    create_sqlite_engine,
    init_db,
)
from medibridge.database.operations_adapter import (
    DBOperationsAdapter,
    validate_utc_datetime,
)
from medibridge.exceptions import (
    ProposalNotPendingError,
    UserNotLinkedToPharmacyError,
)
from medibridge.schemas.stock import StockActionEnum
from medibridge.services.confirmation_service import (
    confirm_and_apply_proposal,
    expire_proposals_job,
    reject_proposal_workflow,
)
from medibridge.services.history_service import (
    evaluate_can_undo,
    orchestrate_undo,
)
from medibridge.services.proposal_adapter import ParsedProposalInput
from medibridge.services.proposal_service import (
    ProposalServiceStatus,
    process_stock_message,
)


class MockOperationsModule:
    """Mock test double representing Member 2's eventual medibridge_db.operations."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def is_user_linked_to_pharmacy(self, session: Session, user_id: int, pharmacy_id: int) -> bool:
        self.calls.append(("is_user_linked_to_pharmacy", (session, user_id, pharmacy_id), {}))
        return user_id == 42 and pharmacy_id == 5

    def find_medicine_by_alias(self, session: Session, alias: str) -> Optional[dict]:
        self.calls.append(("find_medicine_by_alias", (session, alias), {}))
        if alias.lower() in ("dolo", "dolo 650"):
            return {
                "id": 101,
                "brand_name": "Dolo 650",
                "generic_name": "paracetamol",
                "composition_key": "paracetamol_650mg_tablet",
                "strength": "650mg",
                "form": "tablet",
                "is_schedule_h": 0,
                "manufacturer": "Micro Labs",
                "aliases": ["dolo", "dolo 650"],
            }
        return None

    def search_medicines(self, session: Session, query: str, limit: int = 10) -> list[dict]:
        self.calls.append(("search_medicines", (session, query, limit), {}))
        if "dolo" in query.lower() or "para" in query.lower():
            return [
                {
                    "id": 101,
                    "brand_name": "Dolo 650",
                    "generic_name": "paracetamol",
                    "composition_key": "paracetamol_650mg_tablet",
                    "strength": "650mg",
                    "form": "tablet",
                    "is_schedule_h": 0,
                    "manufacturer": "Micro Labs",
                    "aliases": ["dolo"],
                }
            ]
        return []

    def get_medicine_by_id(self, session: Session, medicine_id: int) -> Optional[dict]:
        self.calls.append(("get_medicine_by_id", (session, medicine_id), {}))
        if medicine_id == 101:
            return {
                "id": 101,
                "brand_name": "Dolo 650",
                "generic_name": "paracetamol",
                "composition_key": "paracetamol_650mg_tablet",
                "strength": "650mg",
                "form": "tablet",
                "is_schedule_h": 0,
                "manufacturer": "Micro Labs",
                "aliases": ["dolo"],
            }
        return None

    def get_substitutes_for_medicine(
        self,
        session: Session,
        medicine_id: int,
        pharmacy_id: Optional[int] = None,
    ) -> list[dict]:
        self.calls.append(("get_substitutes_for_medicine", (session, medicine_id, pharmacy_id), {}))
        return [
            {
                "id": 102,
                "brand_name": "Crocin 650",
                "generic_name": "paracetamol",
                "composition_key": "paracetamol_650mg_tablet",
                "strength": "650mg",
                "form": "tablet",
                "is_schedule_h": 0,
                "manufacturer": "GSK",
                "aliases": ["crocin"],
            }
        ]

    def record_substitution_approval(
        self,
        session: Session,
        pharmacy_id: int,
        requested_medicine_id: int,
        substitute_medicine_id: int,
        approving_pharmacist_id: int,
        status: str,
        request_reference: Optional[str] = None,
    ) -> dict:
        self.calls.append((
            "record_substitution_approval",
            (session, pharmacy_id, requested_medicine_id, substitute_medicine_id, approving_pharmacist_id, status),
            {"request_reference": request_reference},
        ))
        return {
            "id": 1,
            "pharmacy_id": pharmacy_id,
            "requested_medicine_id": requested_medicine_id,
            "substitute_medicine_id": substitute_medicine_id,
            "approving_pharmacist_id": approving_pharmacist_id,
            "status": status,
            "request_reference": request_reference,
            "approved_at": datetime.now(timezone.utc),
        }

    def create_stock_proposal(
        self,
        session: Session,
        pharmacy_id: int,
        chemist_user_id: int,
        raw_message: str,
        parsed: ParsedProposalInput,
        source_message_ref: Optional[str] = None,
    ) -> dict:
        self.calls.append((
            "create_stock_proposal",
            (session, pharmacy_id, chemist_user_id, raw_message, parsed),
            {"source_message_ref": source_message_ref},
        ))
        now = datetime.now(timezone.utc)
        return {
            "id": 501,
            "pharmacy_id": pharmacy_id,
            "chemist_user_id": chemist_user_id,
            "raw_message": raw_message,
            "brand_name": parsed.brand_name,
            "parsed_salt": parsed.parsed_salt,
            "parsed_strength": parsed.parsed_strength,
            "parsed_form": parsed.parsed_form,
            "parsed_quantity": parsed.parsed_quantity,
            "parsed_intent": parsed.parsed_intent.value if hasattr(parsed.parsed_intent, "value") else str(parsed.parsed_intent),
            "confidence": parsed.confidence,
            "status": "PENDING",
            "flagged_for_review": 1 if parsed.flagged_for_review else 0,
            "expires_at": now + timedelta(hours=24),
            "created_at": now,
        }

    def get_proposal_by_id(self, session: Session, proposal_id: int) -> Optional[dict]:
        self.calls.append(("get_proposal_by_id", (session, proposal_id), {}))
        now = datetime.now(timezone.utc)
        return {
            "id": proposal_id,
            "pharmacy_id": 5,
            "chemist_user_id": 42,
            "raw_message": "Dolo 650 20 available",
            "brand_name": "Dolo 650",
            "parsed_salt": "paracetamol",
            "parsed_strength": "650mg",
            "parsed_form": "tablet",
            "parsed_quantity": 20,
            "parsed_intent": "IN_STOCK",
            "confidence": 1.0,
            "status": "PENDING",
            "flagged_for_review": 0,
            "expires_at": now + timedelta(hours=24),
            "created_at": now,
        }

    def apply_confirmed_proposal(
        self,
        session: Session,
        proposal_id: int,
        confirmed_by_user_id: int,
        pharmacy_id: int,
        current_time: Optional[datetime] = None,
    ) -> dict:
        self.calls.append((
            "apply_confirmed_proposal",
            (session, proposal_id, confirmed_by_user_id, pharmacy_id),
            {"current_time": current_time},
        ))
        now = current_time or datetime.now(timezone.utc)
        return {
            "id": 7,
            "pharmacy_id": pharmacy_id,
            "medicine_id": 101,
            "stock_status": "IN_STOCK",
            "quantity": 20,
            "last_confirmed_at": now,
            "last_updated_by": confirmed_by_user_id,
            "last_update_source": "proposal_apply",
        }

    def expire_overdue_proposals(self, session: Session, cutoff: datetime) -> int:
        self.calls.append(("expire_overdue_proposals", (session,), {"cutoff": cutoff}))
        return 3

    def get_inventory_history(self, session: Session, inventory_id: int, limit: int = 50) -> list[dict]:
        self.calls.append(("get_inventory_history", (session, inventory_id, limit), {}))
        now = datetime.now(timezone.utc)
        return [
            {
                "id": 201,
                "inventory_id": inventory_id,
                "pharmacy_id": 5,
                "medicine_id": 101,
                "changed_by": 42,
                "source_proposal_id": 501,
                "change_type": "STOCK_UPDATE",
                "undo_of_history_id": None,
                "previous_stock_status": None,
                "new_stock_status": "IN_STOCK",
                "previous_quantity": None,
                "new_quantity": 20,
                "previous_last_confirmed_at": None,
                "change_reason": "proposal #501",
                "created_at": now,
            }
        ]

    def get_history_by_id(self, session: Session, history_id: int) -> Optional[dict]:
        self.calls.append(("get_history_by_id", (session, history_id), {}))
        now = datetime.now(timezone.utc)
        return {
            "id": history_id,
            "inventory_id": 7,
            "pharmacy_id": 5,
            "medicine_id": 101,
            "changed_by": 42,
            "source_proposal_id": 501,
            "change_type": "STOCK_UPDATE",
            "undo_of_history_id": None,
            "previous_stock_status": "OUT_OF_STOCK",
            "new_stock_status": "IN_STOCK",
            "previous_quantity": 0,
            "new_quantity": 20,
            "previous_last_confirmed_at": now,
            "change_reason": "proposal #501",
            "created_at": now,
        }

    def has_undo_for_history(self, session: Session, history_id: int) -> bool:
        self.calls.append(("has_undo_for_history", (session, history_id), {}))
        return False

    def has_newer_history_for_inventory(self, session: Session, inventory_id: int, history_id: int) -> bool:
        self.calls.append(("has_newer_history_for_inventory", (session, inventory_id, history_id), {}))
        return False

    def undo_last_change(
        self,
        session: Session,
        history_id: int,
        undone_by_user_id: int,
        pharmacy_id: int,
    ) -> dict:
        self.calls.append(("undo_last_change", (session, history_id, undone_by_user_id, pharmacy_id), {}))
        now = datetime.now(timezone.utc)
        return {
            "id": 202,
            "inventory_id": 7,
            "pharmacy_id": pharmacy_id,
            "medicine_id": 101,
            "changed_by": undone_by_user_id,
            "source_proposal_id": 501,
            "change_type": "UNDO",
            "undo_of_history_id": history_id,
            "previous_stock_status": "IN_STOCK",
            "new_stock_status": "OUT_OF_STOCK",
            "previous_quantity": 20,
            "new_quantity": 0,
            "previous_last_confirmed_at": now,
            "change_reason": f"undo of #{history_id}",
            "created_at": now,
        }

    def get_inventory_by_id(self, session: Session, inventory_id: int) -> Optional[dict]:
        self.calls.append(("get_inventory_by_id", (session, inventory_id), {}))
        now = datetime.now(timezone.utc)
        return {
            "id": inventory_id,
            "pharmacy_id": 5,
            "medicine_id": 101,
            "stock_status": "OUT_OF_STOCK",
            "quantity": 0,
            "last_confirmed_at": now,
            "last_updated_by": 42,
            "last_update_source": "undo",
        }


class TestDBOperationsAdapter(unittest.TestCase):
    """Test suite covering DBOperationsAdapter behaviors and constraints."""

    def setUp(self) -> None:
        self.engine = create_sqlite_engine("sqlite:///:memory:")
        init_db(self.engine)
        self.session_factory = create_session_factory(self.engine)
        self.mock_ops = MockOperationsModule()
        self.adapter = DBOperationsAdapter(
            session_factory=self.session_factory,
            operations=self.mock_ops,
        )

    def test_missing_operations_raises_runtime_error(self) -> None:
        """When Member 2's operations module is absent, invoking methods raises clear RuntimeError."""
        unconfigured_adapter = DBOperationsAdapter(
            session_factory=self.session_factory,
            operations=None,
        )
        with self.assertRaises(RuntimeError) as ctx:
            unconfigured_adapter.is_user_linked_to_pharmacy(42, 5)
        self.assertIn("medibridge_db.operations", str(ctx.exception))
        self.assertIn("Member 2", str(ctx.exception))

    def test_session_passed_as_first_argument_to_all_operations(self) -> None:
        """Verifies that a SQLAlchemy Session is forwarded as the first argument to operations."""
        # 1. is_user_linked_to_pharmacy
        self.adapter.is_user_linked_to_pharmacy(42, 5)
        first_call = self.mock_ops.calls[-1]
        self.assertEqual(first_call[0], "is_user_linked_to_pharmacy")
        self.assertIsInstance(first_call[1][0], Session)

        # 2. find_medicine_by_alias
        self.adapter.find_medicine_by_alias("dolo")
        first_call = self.mock_ops.calls[-1]
        self.assertEqual(first_call[0], "find_medicine_by_alias")
        self.assertIsInstance(first_call[1][0], Session)

        # 3. create_stock_proposal
        parsed = ParsedProposalInput(
            brand_name="Dolo 650",
            parsed_salt="paracetamol",
            parsed_strength="650mg",
            parsed_form="tablet",
            parsed_quantity=10,
            parsed_intent=StockActionEnum.IN_STOCK,
            confidence=1.0,
            flagged_for_review=False,
        )
        self.adapter.create_stock_proposal(5, 42, "Dolo 650 10", parsed)
        first_call = self.mock_ops.calls[-1]
        self.assertEqual(first_call[0], "create_stock_proposal")
        self.assertIsInstance(first_call[1][0], Session)

        # 4. apply_confirmed_proposal
        self.adapter.apply_confirmed_proposal(
            proposal_id=501,
            confirmed_by_user_id=42,
            pharmacy_id=5,
            current_time=datetime.now(timezone.utc),
        )
        first_call = self.mock_ops.calls[-1]
        self.assertEqual(first_call[0], "apply_confirmed_proposal")
        self.assertIsInstance(first_call[1][0], Session)

        # 5. expire_overdue_proposals
        self.adapter.expire_overdue_proposals(cutoff=datetime.now(timezone.utc))
        first_call = self.mock_ops.calls[-1]
        self.assertEqual(first_call[0], "expire_overdue_proposals")
        self.assertIsInstance(first_call[1][0], Session)

        # 6. undo_last_change
        self.adapter.undo_last_change(history_id=201, undone_by_user_id=42, pharmacy_id=5)
        undo_calls = [c for c in self.mock_ops.calls if c[0] == "undo_last_change"]
        self.assertTrue(len(undo_calls) > 0)
        self.assertIsInstance(undo_calls[-1][1][0], Session)

    def test_transaction_owner_commits_on_success(self) -> None:
        """When adapter owns the transaction, it commits upon successful operation completion."""
        mock_session = MagicMock(spec=Session)
        mock_factory = MagicMock(return_value=mock_session)

        adapter = DBOperationsAdapter(session_factory=mock_factory, operations=self.mock_ops)
        adapter.is_user_linked_to_pharmacy(42, 5)

        mock_session.commit.assert_called_once()
        mock_session.rollback.assert_not_called()
        mock_session.close.assert_called_once()

    def test_transaction_owner_rolls_back_on_failure(self) -> None:
        """When an operation raises an exception, the transaction owner rolls back and propagates."""
        mock_session = MagicMock(spec=Session)
        mock_factory = MagicMock(return_value=mock_session)

        failing_ops = MagicMock()
        failing_ops.is_user_linked_to_pharmacy.side_effect = RuntimeError("Database query failed")

        adapter = DBOperationsAdapter(session_factory=mock_factory, operations=failing_ops)

        with self.assertRaises(RuntimeError) as ctx:
            adapter.is_user_linked_to_pharmacy(42, 5)

        self.assertIn("Database query failed", str(ctx.exception))
        mock_session.rollback.assert_called_once()
        mock_session.commit.assert_not_called()
        mock_session.close.assert_called_once()

    def test_database_operations_flush_never_commit_when_external_session(self) -> None:
        """When an external session is provided, database operations flush but NEVER commit independently."""
        mock_external_session = MagicMock(spec=Session)

        self.adapter.is_user_linked_to_pharmacy(42, 5, session=mock_external_session)

        mock_external_session.flush.assert_called_once()
        mock_external_session.commit.assert_not_called()
        mock_external_session.close.assert_not_called()

    def test_naive_timestamp_rejection_in_apply_confirmed_proposal(self) -> None:
        """Passing a naive datetime to apply_confirmed_proposal must raise ValueError."""
        naive_time = datetime(2026, 10, 13, 10, 30, 0)  # No tzinfo!

        with self.assertRaises(ValueError) as ctx:
            self.adapter.apply_confirmed_proposal(
                proposal_id=501,
                confirmed_by_user_id=42,
                pharmacy_id=5,
                current_time=naive_time,
            )

        self.assertIn("timezone-aware UTC datetime", str(ctx.exception))
        self.assertIn("Naive datetimes are strictly rejected", str(ctx.exception))

    def test_naive_timestamp_rejection_in_expire_overdue_proposals(self) -> None:
        """Passing a naive datetime cutoff to expire_overdue_proposals must raise ValueError."""
        naive_cutoff = datetime.now()  # Naive!

        with self.assertRaises(ValueError) as ctx:
            self.adapter.expire_overdue_proposals(cutoff=naive_cutoff)

        self.assertIn("timezone-aware UTC datetime", str(ctx.exception))
        self.assertIn("Naive datetimes are strictly rejected", str(ctx.exception))

    def test_non_utc_timezone_rejection(self) -> None:
        """Passing a timezone with non-zero offset (e.g. IST +05:30) must raise ValueError."""
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        ist_time = datetime(2026, 10, 13, 12, 0, 0, tzinfo=ist_tz)

        with self.assertRaises(ValueError) as ctx:
            validate_utc_datetime(ist_time, "ist_param")

        self.assertIn("must be in UTC timezone (offset=0)", str(ctx.exception))

    def test_utc_timezone_accepted(self) -> None:
        """UTC-aware datetime is accepted and returned unmodified."""
        utc_time = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)
        validated = validate_utc_datetime(utc_time, "test_param")
        self.assertEqual(validated, utc_time)

    def test_source_message_ref_forwarded_when_operation_signature_supports_it(self) -> None:
        """When Member 2's create_stock_proposal accepts source_message_ref, it is passed through."""
        parsed = ParsedProposalInput(
            brand_name="Dolo 650",
            parsed_salt="paracetamol",
            parsed_strength="650mg",
            parsed_form="tablet",
            parsed_quantity=10,
            parsed_intent=StockActionEnum.IN_STOCK,
            confidence=1.0,
            flagged_for_review=False,
        )

        record = self.adapter.create_stock_proposal(
            pharmacy_id=5,
            chemist_user_id=42,
            raw_message="Dolo 650 10",
            parsed=parsed,
            source_message_ref="msg-telegram-9999",
        )

        self.assertIsNotNone(record)
        last_call = self.mock_ops.calls[-1]
        self.assertEqual(last_call[0], "create_stock_proposal")
        self.assertEqual(last_call[2].get("source_message_ref"), "msg-telegram-9999")

    def test_source_message_ref_omitted_when_operation_uses_frozen_contract_signature(self) -> None:
        """When operation strictly has frozen 4 parameters, source_message_ref is safely omitted."""
        frozen_ops = MagicMock()
        # Define signature with only the frozen contract parameters
        def frozen_create(session, pharmacy_id, chemist_user_id, raw_message, parsed):
            now = datetime.now(timezone.utc)
            return {
                "id": 888,
                "pharmacy_id": pharmacy_id,
                "chemist_user_id": chemist_user_id,
                "raw_message": raw_message,
                "brand_name": parsed.brand_name,
                "parsed_salt": parsed.parsed_salt,
                "parsed_strength": parsed.parsed_strength,
                "parsed_intent": "IN_STOCK",
                "confidence": 1.0,
                "status": "PENDING",
                "flagged_for_review": 0,
                "expires_at": now + timedelta(hours=24),
                "created_at": now,
            }

        frozen_ops.create_stock_proposal = frozen_create
        adapter = DBOperationsAdapter(session_factory=self.session_factory, operations=frozen_ops)

        parsed = ParsedProposalInput(
            brand_name="Dolo 650",
            parsed_salt="paracetamol",
            parsed_strength="650mg",
            parsed_form="tablet",
            parsed_quantity=10,
            parsed_intent=StockActionEnum.IN_STOCK,
            confidence=1.0,
            flagged_for_review=False,
        )

        # Calling with source_message_ref should NOT raise TypeError!
        record = adapter.create_stock_proposal(
            pharmacy_id=5,
            chemist_user_id=42,
            raw_message="Dolo 650 10",
            parsed=parsed,
            source_message_ref="msg-telegram-8888",
        )
        self.assertEqual(record.id, 888)

    def test_entity_translation_proposals(self) -> None:
        """Verifies dictionary/ORM return from Member 2 adapts to StoredProposalRecord."""
        record = self.adapter.get_proposal_by_id(501)
        self.assertIsNotNone(record)
        self.assertEqual(record.id, 501)
        self.assertEqual(record.brand_name, "Dolo 650")
        self.assertEqual(record.parsed_intent, StockActionEnum.IN_STOCK)
        self.assertEqual(record.status, "PENDING")
        self.assertEqual(record.expires_at.tzinfo, timezone.utc)

    def test_entity_translation_inventory(self) -> None:
        """Verifies dictionary/ORM return from Member 2 adapts to InventoryRecord."""
        now = datetime.now(timezone.utc)
        inv = self.adapter.apply_confirmed_proposal(
            proposal_id=501,
            confirmed_by_user_id=42,
            pharmacy_id=5,
            current_time=now,
        )
        self.assertEqual(inv.id, 7)
        self.assertEqual(inv.stock_status, StockActionEnum.IN_STOCK)
        self.assertEqual(inv.quantity, 20)
        self.assertEqual(inv.last_confirmed_at.tzinfo, timezone.utc)

    def test_end_to_end_service_integration_with_adapter(self) -> None:
        """Full service workflow test: proposal -> confirmation -> undo with DBOperationsAdapter."""
        # 1. Proposal orchestration
        result = process_stock_message(
            raw_message="Dolo 650 20 available",
            pharmacy_id=5,
            chemist_user_id=42,
            repository=self.adapter,
            source_message_ref="msg-e2e-1",
        )
        self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)
        self.assertEqual(len(result.created_proposals), 1)
        self.assertEqual(result.source_message_ref, "msg-e2e-1")
        proposal = result.created_proposals[0]

        # 2. Confirmation workflow
        conf_result = confirm_and_apply_proposal(
            proposal_id=proposal.id,
            user_id=42,
            pharmacy_id=5,
            repository=self.adapter,
            current_time=datetime.now(timezone.utc),
        )
        self.assertTrue(conf_result.success)
        self.assertEqual(conf_result.status, "CONFIRMED")
        self.assertEqual(conf_result.updated_inventory.quantity, 20)

        # 3. Undo orchestration
        undo_result = orchestrate_undo(
            history_id=201,
            user_id=42,
            pharmacy_id=5,
            repository=self.adapter,
        )
        self.assertTrue(undo_result.success)
        self.assertEqual(undo_result.undo_history_record.change_type, "UNDO")
        self.assertEqual(undo_result.restored_inventory.stock_status, StockActionEnum.OUT_OF_STOCK)
        self.assertEqual(undo_result.restored_inventory.quantity, 0)

    def test_external_session_exception_does_not_flush_or_commit(self) -> None:
        """When an external session is used and an operation fails, the session does not flush or commit."""
        mock_external_session = MagicMock(spec=Session)

        failing_ops = MagicMock()
        failing_ops.is_user_linked_to_pharmacy.side_effect = RuntimeError("External DB query error")

        adapter = DBOperationsAdapter(operations=failing_ops)

        with self.assertRaises(RuntimeError) as ctx:
            adapter.is_user_linked_to_pharmacy(42, 5, session=mock_external_session)

        self.assertIn("External DB query error", str(ctx.exception))
        mock_external_session.flush.assert_not_called()
        mock_external_session.commit.assert_not_called()
        mock_external_session.rollback.assert_not_called()
        mock_external_session.close.assert_not_called()

    def test_can_undo_delegates_to_operations_or_evaluates_fallback(self) -> None:
        """Verifies that can_undo checks eligibility directly and delegates appropriately."""
        # 1. When ops has can_undo
        can_undo_res = self.adapter.can_undo(201)
        self.assertTrue(can_undo_res)

        # 2. When ops does NOT have can_undo, falls back to history inspection
        ops_without_can_undo = MagicMock()
        ops_without_can_undo.get_history_by_id.return_value = {
            "id": 201,
            "inventory_id": 7,
            "pharmacy_id": 5,
            "medicine_id": 101,
            "changed_by": 42,
            "change_type": "STOCK_UPDATE",
            "undo_of_history_id": None,
            "new_stock_status": "IN_STOCK",
        }
        ops_without_can_undo.has_undo_for_history.return_value = False
        ops_without_can_undo.has_newer_history_for_inventory.return_value = False

        adapter = DBOperationsAdapter(
            session_factory=self.session_factory,
            operations=ops_without_can_undo,
        )
        self.assertTrue(adapter.can_undo(201))

    def test_apply_confirmed_proposal_defaults_to_utc_when_none_provided(self) -> None:
        """When current_time is None, apply_confirmed_proposal supplies a timezone-aware UTC datetime."""
        inv = self.adapter.apply_confirmed_proposal(
            proposal_id=501,
            confirmed_by_user_id=42,
            pharmacy_id=5,
            current_time=None,
        )
        self.assertEqual(inv.id, 7)
        last_call = self.mock_ops.calls[-1]
        passed_time = last_call[2].get("current_time")
        self.assertIsNotNone(passed_time)
        self.assertIsNotNone(passed_time.tzinfo)
        self.assertEqual(passed_time.utcoffset().total_seconds(), 0)

    def test_apply_confirmed_proposal_forwards_current_time_when_signature_supports_it(self) -> None:
        """When operations.apply_confirmed_proposal supports current_time, it is passed explicitly."""
        custom_time = datetime(2026, 10, 13, 14, 30, 0, tzinfo=timezone.utc)
        inv = self.adapter.apply_confirmed_proposal(
            proposal_id=501,
            confirmed_by_user_id=42,
            pharmacy_id=5,
            current_time=custom_time,
        )
        self.assertEqual(inv.id, 7)
        last_call = self.mock_ops.calls[-1]
        self.assertEqual(last_call[0], "apply_confirmed_proposal")
        self.assertEqual(last_call[2].get("current_time"), custom_time)

    def test_apply_confirmed_proposal_omits_current_time_when_operation_uses_frozen_contract_signature(self) -> None:
        """When operations strictly adheres to CONTRACT.md (3 arguments + session), current_time is omitted."""
        frozen_ops = MagicMock()
        recorded_calls = []

        def frozen_apply(session, proposal_id, confirmed_by_user_id, pharmacy_id):
            recorded_calls.append((session, proposal_id, confirmed_by_user_id, pharmacy_id))
            now = datetime.now(timezone.utc)
            return {
                "id": 99,
                "pharmacy_id": pharmacy_id,
                "medicine_id": 101,
                "stock_status": "IN_STOCK",
                "quantity": 15,
                "last_confirmed_at": now,
                "last_updated_by": confirmed_by_user_id,
                "last_update_source": "proposal_apply",
            }

        frozen_ops.apply_confirmed_proposal = frozen_apply
        adapter = DBOperationsAdapter(session_factory=self.session_factory, operations=frozen_ops)

        # Call with explicit current_time: should NOT raise TypeError!
        custom_time = datetime(2026, 10, 13, 15, 0, 0, tzinfo=timezone.utc)
        inv = adapter.apply_confirmed_proposal(
            proposal_id=501,
            confirmed_by_user_id=42,
            pharmacy_id=5,
            current_time=custom_time,
        )
        self.assertEqual(inv.id, 99)
        self.assertEqual(inv.quantity, 15)
        self.assertEqual(len(recorded_calls), 1)
        self.assertEqual(recorded_calls[0][1:], (501, 42, 5))


if __name__ == "__main__":
    unittest.main()
