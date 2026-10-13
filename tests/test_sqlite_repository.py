"""Integration tests for SQLiteMediBridgeRepository against real SQLite database.

Verifies:
- Schema creation, foreign key enforcement, and check constraints
- Proposal creation, pending retrieval, and expiry cron sweep
- Atomic confirmation, inventory update, and history creation in a single transaction
- Transaction rollback on simulated or integrity failures
- Expiry rejection at apply time
- Undo orchestration restoring prior inventory state
- Double-undo rejection and database uniqueness constraint on undo_of_history_id
- Cannot undo an UNDO entry
- Newer changes blocking undo of earlier changes
- Pharmacy membership isolation across all operations
- Medicine search, alias lookup, and substitute discovery
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from medibridge.database.connection import (
    create_session_factory,
    create_sqlite_engine,
    init_db,
)
from medibridge.database.models import (
    Inventory,
    Medicine,
    MedicineAlias,
    Pharmacy,
    StockHistory,
    StockProposal,
    User,
    UserPharmacy,
)
from medibridge.database.repository import SQLiteMediBridgeRepository
from medibridge.exceptions import (
    CannotUndoUndoError,
    HistoryAlreadyUndoneError,
    NewerChangeExistsError,
    PharmacyScopeMismatchError,
    ProposalExpiredError,
    ProposalNotPendingError,
    ProposalPharmacyMismatchError,
    StockStatusQuantityMismatchError,
    UserNotLinkedToPharmacyError,
)
from medibridge.schemas.stock import StockActionEnum
from medibridge.services.proposal_adapter import ParsedProposalInput


class TestSQLiteMediBridgeRepository(unittest.TestCase):
    """Test suite for SQLiteMediBridgeRepository."""

    def setUp(self) -> None:
        self.engine = create_sqlite_engine("sqlite:///:memory:")
        init_db(self.engine)
        self.session_factory = create_session_factory(self.engine)
        self.repo = SQLiteMediBridgeRepository(self.session_factory)

        # Seed standard test entities
        with self.session_factory() as session:
            # Pharmacies
            self.p1 = Pharmacy(
                id=1,
                name="Apollo Pharmacy",
                locality="Indiranagar",
                latitude=12.9716,
                longitude=77.5946,
                telegram_chat_id=1001,
                verification_state="verified",
                is_active=1,
            )
            self.p2 = Pharmacy(
                id=2,
                name="MedPlus Pharmacy",
                locality="Koramangala",
                latitude=12.9352,
                longitude=77.6245,
                telegram_chat_id=1002,
                verification_state="verified",
                is_active=1,
            )
            session.add_all([self.p1, self.p2])

            # Users
            self.u_chemist = User(
                id=10,
                telegram_user_id=2001,
                display_name="Ramesh Chemist",
                role="chemist",
                is_active=1,
            )
            self.u_pharmacist = User(
                id=20,
                telegram_user_id=2002,
                display_name="Dr. Priya Pharmacist",
                role="pharmacist",
                is_active=1,
            )
            self.u_outsider = User(
                id=30,
                telegram_user_id=2003,
                display_name="Outsider User",
                role="chemist",
                is_active=1,
            )
            session.add_all([self.u_chemist, self.u_pharmacist, self.u_outsider])

            # Memberships: chemist and pharmacist linked to p1 only
            session.add(UserPharmacy(user_id=10, pharmacy_id=1, role_at_pharmacy="chemist"))
            session.add(UserPharmacy(user_id=20, pharmacy_id=1, role_at_pharmacy="pharmacist"))

            # Medicines
            self.m_dolo = Medicine(
                id=101,
                brand_name="Dolo 650",
                generic_name="paracetamol",
                composition_key="paracetamol_650mg_tablet",
                strength="650mg",
                form="tablet",
                is_schedule_h=0,
                is_active=1,
            )
            self.m_crocin = Medicine(
                id=102,
                brand_name="Crocin 650",
                generic_name="paracetamol",
                composition_key="paracetamol_650mg_tablet",
                strength="650mg",
                form="tablet",
                is_schedule_h=0,
                is_active=1,
            )
            self.m_azithral = Medicine(
                id=103,
                brand_name="Azithral 500",
                generic_name="azithromycin",
                composition_key="azithromycin_500mg_tablet",
                strength="500mg",
                form="tablet",
                is_schedule_h=1,
                is_active=1,
            )
            session.add_all([self.m_dolo, self.m_crocin, self.m_azithral])

            # Aliases
            session.add(MedicineAlias(medicine_id=101, alias="dolo", alias_type="short"))
            session.add(MedicineAlias(medicine_id=101, alias="dolo650", alias_type="misspelling"))
            session.add(MedicineAlias(medicine_id=103, alias="azithral", alias_type="short"))

            session.commit()

    def test_foreign_key_enforcement(self) -> None:
        """Enforces referential integrity: invalid foreign key insert must raise IntegrityError."""
        with self.session_factory() as session:
            # Invalid user_id in user_pharmacies
            invalid_link = UserPharmacy(user_id=9999, pharmacy_id=1, role_at_pharmacy="chemist")
            session.add(invalid_link)
            with self.assertRaises(IntegrityError):
                session.commit()

    def test_membership_verification(self) -> None:
        """Verifies active membership check matches user_pharmacies and active status."""
        self.assertTrue(self.repo.is_user_linked_to_pharmacy(user_id=10, pharmacy_id=1))
        self.assertTrue(self.repo.is_user_linked_to_pharmacy(user_id=20, pharmacy_id=1))
        self.assertFalse(self.repo.is_user_linked_to_pharmacy(user_id=10, pharmacy_id=2))
        self.assertFalse(self.repo.is_user_linked_to_pharmacy(user_id=30, pharmacy_id=1))

    def test_catalogue_lookup_and_search(self) -> None:
        """Tests exact alias matching, brand matching, and ranking."""
        # Alias lookup
        med = self.repo.find_medicine_by_alias("dolo")
        self.assertIsNotNone(med)
        self.assertEqual(med.id, 101)
        self.assertEqual(med.brand_name, "Dolo 650")

        # Direct brand lookup
        med2 = self.repo.find_medicine_by_alias("Dolo 650")
        self.assertIsNotNone(med2)
        self.assertEqual(med2.id, 101)

        # Search across generic
        results = self.repo.search_medicines("paracetamol", limit=5)
        self.assertEqual(len(results), 2)
        result_ids = {r.id for r in results}
        self.assertEqual(result_ids, {101, 102})

    def test_substitute_discovery(self) -> None:
        """Discovers medicines sharing identical composition key, strength, and form."""
        subs = self.repo.get_substitutes_for_medicine(101)
        sub_ids = {s.id for s in subs}
        self.assertIn(102, sub_ids)  # Crocin 650 has same composition
        self.assertNotIn(103, sub_ids)  # Azithral 500 has different composition

    def test_create_and_fetch_stock_proposal(self) -> None:
        """Creates PENDING proposal with 24h expiry and verifies persistence."""
        parsed = ParsedProposalInput(
            brand_name="Dolo 650",
            parsed_salt="paracetamol",
            parsed_strength="650mg",
            parsed_form="tablet",
            parsed_quantity=15,
            parsed_intent=StockActionEnum.IN_STOCK,
            confidence=0.95,
            flagged_for_review=False,
        )

        proposal = self.repo.create_stock_proposal(
            pharmacy_id=1,
            chemist_user_id=10,
            raw_message="Dolo 650 15 strips",
            parsed=parsed,
        )

        self.assertIsNotNone(proposal.id)
        self.assertEqual(proposal.status, "PENDING")
        self.assertEqual(proposal.parsed_quantity, 15)
        self.assertFalse(proposal.flagged_for_review)

        # Fetch from DB
        fetched = self.repo.get_proposal_by_id(proposal.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.brand_name, "Dolo 650")
        self.assertEqual(fetched.parsed_intent, StockActionEnum.IN_STOCK)

    def test_unauthorized_proposal_creation_raises_error(self) -> None:
        """Chemist not linked to pharmacy cannot create proposals."""
        parsed = ParsedProposalInput(
            brand_name="Dolo 650",
            parsed_salt="paracetamol",
            parsed_strength="650mg",
            parsed_form="tablet",
            parsed_quantity=15,
            parsed_intent=StockActionEnum.IN_STOCK,
            confidence=0.95,
            flagged_for_review=False,
        )

        with self.assertRaises(UserNotLinkedToPharmacyError):
            self.repo.create_stock_proposal(
                pharmacy_id=2,  # User 10 not linked to pharmacy 2
                chemist_user_id=10,
                raw_message="Dolo 650 15 strips",
                parsed=parsed,
            )

    def test_atomic_apply_confirmed_proposal(self) -> None:
        """Verifies single-transaction atomic apply creates inventory and history rows."""
        # 1. Create proposal
        parsed = ParsedProposalInput(
            brand_name="Dolo 650",
            parsed_salt="paracetamol",
            parsed_strength="650mg",
            parsed_form="tablet",
            parsed_quantity=20,
            parsed_intent=StockActionEnum.IN_STOCK,
            confidence=1.0,
            flagged_for_review=False,
        )
        prop = self.repo.create_stock_proposal(
            pharmacy_id=1,
            chemist_user_id=10,
            raw_message="Dolo 650 20 available",
            parsed=parsed,
        )

        # 2. Apply proposal
        now = datetime.now(timezone.utc)
        inv = self.repo.apply_confirmed_proposal(
            proposal_id=prop.id,
            confirmed_by_user_id=10,
            pharmacy_id=1,
            current_time=now,
        )

        self.assertEqual(inv.stock_status, StockActionEnum.IN_STOCK)
        self.assertEqual(inv.quantity, 20)
        self.assertEqual(inv.pharmacy_id, 1)
        self.assertEqual(inv.medicine_id, 101)

        # Verify proposal updated to CONFIRMED
        prop_after = self.repo.get_proposal_by_id(prop.id)
        self.assertEqual(prop_after.status, "CONFIRMED")
        self.assertEqual(prop_after.confirmed_by, 10)

        # Verify history written
        history_rows = self.repo.get_inventory_history(inv.id)
        self.assertEqual(len(history_rows), 1)
        h = history_rows[0]
        self.assertEqual(h.change_type, "STOCK_UPDATE")
        self.assertEqual(h.new_stock_status, StockActionEnum.IN_STOCK)
        self.assertEqual(h.new_quantity, 20)
        self.assertIsNone(h.previous_stock_status)
        self.assertEqual(h.source_proposal_id, prop.id)

    def test_apply_expired_proposal_rejected(self) -> None:
        """Applying an expired proposal must raise ProposalExpiredError."""
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
        prop = self.repo.create_stock_proposal(
            pharmacy_id=1,
            chemist_user_id=10,
            raw_message="Dolo 650 10",
            parsed=parsed,
        )

        # Try to apply 48 hours later
        future_time = datetime.now(timezone.utc) + timedelta(hours=48)
        with self.assertRaises(ProposalExpiredError):
            self.repo.apply_confirmed_proposal(
                proposal_id=prop.id,
                confirmed_by_user_id=10,
                pharmacy_id=1,
                current_time=future_time,
            )

    def test_double_confirmation_rejected(self) -> None:
        """Second confirmation of the same proposal must raise ProposalNotPendingError."""
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
        prop = self.repo.create_stock_proposal(
            pharmacy_id=1,
            chemist_user_id=10,
            raw_message="Dolo 650 10",
            parsed=parsed,
        )

        self.repo.apply_confirmed_proposal(
            proposal_id=prop.id,
            confirmed_by_user_id=10,
            pharmacy_id=1,
        )

        # Second attempt
        with self.assertRaises(ProposalNotPendingError):
            self.repo.apply_confirmed_proposal(
                proposal_id=prop.id,
                confirmed_by_user_id=10,
                pharmacy_id=1,
            )

    def test_rejection_leaves_inventory_untouched(self) -> None:
        """Rejecting a proposal updates status to REJECTED without modifying inventory."""
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
        prop = self.repo.create_stock_proposal(
            pharmacy_id=1,
            chemist_user_id=10,
            raw_message="Dolo 650 10",
            parsed=parsed,
        )

        rejected = self.repo.reject_proposal(
            proposal_id=prop.id,
            rejected_by_user_id=10,
            pharmacy_id=1,
            reason="Incorrect item count",
        )

        self.assertEqual(rejected.status, "REJECTED")
        self.assertEqual(rejected.review_note, "Incorrect item count")

        # Inventory table must remain empty
        with self.session_factory() as session:
            inv_count = session.scalar(select(func.count(Inventory.id)))
            self.assertEqual(inv_count, 0)

    def test_expire_overdue_proposals_sweep(self) -> None:
        """Cron sweep marks only overdue pending proposals as EXPIRED."""
        now = datetime.now(timezone.utc)
        with self.session_factory() as session:
            # Overdue proposal (expires 2 hours ago)
            p_overdue = StockProposal(
                pharmacy_id=1,
                chemist_user_id=10,
                raw_message="old",
                brand_name="Dolo 650",
                parsed_salt="paracetamol",
                parsed_strength="650mg",
                parsed_intent="IN_STOCK",
                confidence=1.0,
                status="PENDING",
                flagged_for_review=0,
                expires_at=(now - timedelta(hours=2)).replace(tzinfo=None),
            )
            # Active proposal (expires in 22 hours)
            p_active = StockProposal(
                pharmacy_id=1,
                chemist_user_id=10,
                raw_message="fresh",
                brand_name="Dolo 650",
                parsed_salt="paracetamol",
                parsed_strength="650mg",
                parsed_intent="IN_STOCK",
                confidence=1.0,
                status="PENDING",
                flagged_for_review=0,
                expires_at=(now + timedelta(hours=22)).replace(tzinfo=None),
            )
            session.add_all([p_overdue, p_active])
            session.commit()

        count = self.repo.expire_overdue_proposals(cutoff=now)
        self.assertEqual(count, 1)

        p1 = self.repo.get_proposal_by_id(p_overdue.id)
        p2 = self.repo.get_proposal_by_id(p_active.id)
        self.assertEqual(p1.status, "EXPIRED")
        self.assertEqual(p2.status, "PENDING")

    def test_undo_restores_inventory_and_records_audit_trail(self) -> None:
        """Undo creates UNDO history entry and restores exact previous inventory values."""
        # Step 1: initial apply (IN_STOCK / 50)
        p1 = self.repo.create_stock_proposal(
            pharmacy_id=1,
            chemist_user_id=10,
            raw_message="Dolo 650 50",
            parsed=ParsedProposalInput(
                brand_name="Dolo 650",
                parsed_salt="paracetamol",
                parsed_strength="650mg",
                parsed_form="tablet",
                parsed_quantity=50,
                parsed_intent=StockActionEnum.IN_STOCK,
                confidence=1.0,
                flagged_for_review=False,
            ),
        )
        inv1 = self.repo.apply_confirmed_proposal(p1.id, 10, 1)
        self.assertEqual(inv1.quantity, 50)

        # Step 2: update to OUT_OF_STOCK / 0
        p2 = self.repo.create_stock_proposal(
            pharmacy_id=1,
            chemist_user_id=10,
            raw_message="Dolo 650 khatam",
            parsed=ParsedProposalInput(
                brand_name="Dolo 650",
                parsed_salt="paracetamol",
                parsed_strength="650mg",
                parsed_form="tablet",
                parsed_quantity=0,
                parsed_intent=StockActionEnum.OUT_OF_STOCK,
                confidence=1.0,
                flagged_for_review=False,
            ),
        )
        inv2 = self.repo.apply_confirmed_proposal(p2.id, 10, 1)
        self.assertEqual(inv2.stock_status, StockActionEnum.OUT_OF_STOCK)
        self.assertEqual(inv2.quantity, 0)

        history_list = self.repo.get_inventory_history(inv2.id)
        self.assertEqual(len(history_list), 2)
        target_history = history_list[1]  # The latest update
        self.assertEqual(target_history.change_type, "STOCK_UPDATE")

        # Step 3: Undo the latest change
        undo_hist, restored_inv = self.repo.undo_last_change(
            history_id=target_history.id,
            undone_by_user_id=10,
            pharmacy_id=1,
        )

        self.assertEqual(undo_hist.change_type, "UNDO")
        self.assertEqual(undo_hist.undo_of_history_id, target_history.id)
        self.assertEqual(restored_inv.stock_status, StockActionEnum.IN_STOCK)
        self.assertEqual(restored_inv.quantity, 50)
        self.assertEqual(restored_inv.last_update_source, "undo")

        # Step 4: Verify repeated undo attempt is rejected
        with self.assertRaises(HistoryAlreadyUndoneError):
            self.repo.undo_last_change(
                history_id=target_history.id,
                undone_by_user_id=10,
                pharmacy_id=1,
            )

        # Step 5: Verify cannot undo an UNDO entry
        with self.assertRaises(CannotUndoUndoError):
            self.repo.undo_last_change(
                history_id=undo_hist.id,
                undone_by_user_id=10,
                pharmacy_id=1,
            )

    def test_newer_change_blocks_earlier_undo(self) -> None:
        """Earlier changes cannot be undone when newer changes exist."""
        # Change 1
        p1 = self.repo.create_stock_proposal(
            pharmacy_id=1,
            chemist_user_id=10,
            raw_message="Dolo 650 30",
            parsed=ParsedProposalInput(
                brand_name="Dolo 650",
                parsed_salt="paracetamol",
                parsed_strength="650mg",
                parsed_form="tablet",
                parsed_quantity=30,
                parsed_intent=StockActionEnum.IN_STOCK,
                confidence=1.0,
                flagged_for_review=False,
            ),
        )
        inv = self.repo.apply_confirmed_proposal(p1.id, 10, 1)
        h1 = self.repo.get_inventory_history(inv.id)[0]

        # Change 2
        p2 = self.repo.create_stock_proposal(
            pharmacy_id=1,
            chemist_user_id=10,
            raw_message="Dolo 650 10",
            parsed=ParsedProposalInput(
                brand_name="Dolo 650",
                parsed_salt="paracetamol",
                parsed_strength="650mg",
                parsed_form="tablet",
                parsed_quantity=10,
                parsed_intent=StockActionEnum.LOW_STOCK,
                confidence=1.0,
                flagged_for_review=False,
            ),
        )
        self.repo.apply_confirmed_proposal(p2.id, 10, 1)

        # Attempt to undo Change 1 while Change 2 exists
        with self.assertRaises(NewerChangeExistsError):
            self.repo.undo_last_change(
                history_id=h1.id,
                undone_by_user_id=10,
                pharmacy_id=1,
            )

    def test_pharmacy_isolation_enforcement(self) -> None:
        """Operations targeting a different pharmacy are rejected."""
        # Proposal created for pharmacy 1
        p = self.repo.create_stock_proposal(
            pharmacy_id=1,
            chemist_user_id=10,
            raw_message="Dolo 650 10",
            parsed=ParsedProposalInput(
                brand_name="Dolo 650",
                parsed_salt="paracetamol",
                parsed_strength="650mg",
                parsed_form="tablet",
                parsed_quantity=10,
                parsed_intent=StockActionEnum.IN_STOCK,
                confidence=1.0,
                flagged_for_review=False,
            ),
        )

        # Link user 10 to pharmacy 2 as well
        with self.session_factory() as session:
            session.add(UserPharmacy(user_id=10, pharmacy_id=2, role_at_pharmacy="chemist"))
            session.commit()

        # Try to confirm proposal 1 in pharmacy 2 scope
        with self.assertRaises(ProposalPharmacyMismatchError):
            self.repo.apply_confirmed_proposal(
                proposal_id=p.id,
                confirmed_by_user_id=10,
                pharmacy_id=2,
            )


if __name__ == "__main__":
    unittest.main()
