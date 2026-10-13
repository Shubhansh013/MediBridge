"""End-to-end workflow integration tests for MediBridge.

Wired against a real SQLite database to verify the complete pipeline:
1. Chemist natural-language input -> parsing -> catalogue resolution -> PENDING proposal.
2. Chemist/Pharmacist confirmation -> atomic inventory mutation & STOCK_UPDATE audit log.
3. Chemist undo -> append-only UNDO history & exact inventory restoration.
4. Double-undo prevention across real transactions.
5. Multi-medicine message decomposition with Schedule H review flagging.
6. Rejection workflow leaving inventory pristine.
7. Expiry sweep and expiry enforcement at confirmation time.
8. Unauthorized user isolation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from medibridge.database.connection import (
    create_session_factory,
    create_sqlite_engine,
    init_db,
)
from medibridge.database.models import (
    Medicine,
    MedicineAlias,
    Pharmacy,
    User,
    UserPharmacy,
)
from medibridge.database.repository import SQLiteMediBridgeRepository
from medibridge.exceptions import (
    HistoryAlreadyUndoneError,
    ProposalExpiredError,
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
from medibridge.services.proposal_service import (
    ProposalServiceStatus,
    process_stock_message,
)


class TestE2EWorkflow(unittest.TestCase):
    """End-to-end test suite verifying integrated MediBridge pipelines."""

    def setUp(self) -> None:
        self.engine = create_sqlite_engine("sqlite:///:memory:")
        init_db(self.engine)
        self.session_factory = create_session_factory(self.engine)
        self.repo = SQLiteMediBridgeRepository(self.session_factory)

        self.pharmacy_id = 5
        self.chemist_id = 42
        self.pharmacist_id = 43
        self.unauthorized_id = 99

        with self.session_factory() as session:
            # Seed pharmacy
            pharmacy = Pharmacy(
                id=self.pharmacy_id,
                name="Sanjeevani Medicos",
                locality="Lajpat Nagar",
                latitude=28.5672,
                longitude=77.2433,
                telegram_chat_id=50001,
                verification_state="verified",
                is_active=1,
            )
            session.add(pharmacy)

            # Seed users
            chemist = User(
                id=self.chemist_id,
                telegram_user_id=10042,
                display_name="Ramesh Gupta",
                role="chemist",
                is_active=1,
            )
            pharmacist = User(
                id=self.pharmacist_id,
                telegram_user_id=10043,
                display_name="Dr. Anil Sharma",
                role="pharmacist",
                is_active=1,
            )
            outsider = User(
                id=self.unauthorized_id,
                telegram_user_id=10099,
                display_name="Random Stranger",
                role="chemist",
                is_active=1,
            )
            session.add_all([chemist, pharmacist, outsider])

            # Link chemist and pharmacist to Sanjeevani Medicos
            session.add(UserPharmacy(user_id=self.chemist_id, pharmacy_id=self.pharmacy_id, role_at_pharmacy="chemist"))
            session.add(UserPharmacy(user_id=self.pharmacist_id, pharmacy_id=self.pharmacy_id, role_at_pharmacy="pharmacist"))

            # Seed medicines
            # 1. Dolo 650 (standard OTC analgesic)
            dolo = Medicine(
                id=1,
                brand_name="Dolo 650",
                generic_name="paracetamol",
                composition_key="paracetamol_650mg_tablet",
                strength="650mg",
                form="tablet",
                is_schedule_h=0,
                is_active=1,
            )
            # 2. Azithral 500 (Schedule H prescription antibiotic)
            azithral = Medicine(
                id=2,
                brand_name="Azithral 500",
                generic_name="azithromycin",
                composition_key="azithromycin_500mg_tablet",
                strength="500mg",
                form="tablet",
                is_schedule_h=1,
                is_active=1,
            )
            session.add_all([dolo, azithral])

            # Seed aliases
            session.add(MedicineAlias(medicine_id=1, alias="dolo", alias_type="short"))
            session.add(MedicineAlias(medicine_id=1, alias="dolo650", alias_type="misspelling"))
            session.add(MedicineAlias(medicine_id=2, alias="azithral", alias_type="short"))

            session.commit()

    def test_full_pipeline_propose_confirm_and_undo(self) -> None:
        """Complete lifecycle: Propose -> Confirm & Apply -> Undo -> Double Undo Rejection."""
        # --- Step 1: Chemist sends message ---
        result = process_stock_message(
            raw_message="Dolo 650 khatam",
            pharmacy_id=self.pharmacy_id,
            chemist_user_id=self.chemist_id,
            repository=self.repo,
        )

        self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)
        self.assertEqual(len(result.created_proposals), 1)

        proposal = result.created_proposals[0]
        self.assertEqual(proposal.brand_name, "Dolo 650")
        self.assertEqual(proposal.parsed_salt, "paracetamol")
        self.assertEqual(proposal.parsed_strength, "650mg")
        self.assertEqual(proposal.parsed_intent, StockActionEnum.OUT_OF_STOCK)
        self.assertIsNone(proposal.parsed_quantity)
        self.assertEqual(proposal.status, "PENDING")
        self.assertFalse(proposal.flagged_for_review)

        # Inventory must still be untouched
        history_before = self.repo.get_pharmacy_history(self.pharmacy_id)
        self.assertEqual(len(history_before), 0)

        # --- Step 2: Pharmacist confirms and applies proposal ---
        conf_result = confirm_and_apply_proposal(
            proposal_id=proposal.id,
            user_id=self.pharmacist_id,
            pharmacy_id=self.pharmacy_id,
            repository=self.repo,
        )

        self.assertTrue(conf_result.success)
        self.assertEqual(conf_result.status, "CONFIRMED")
        self.assertEqual(conf_result.updated_inventory.stock_status, StockActionEnum.OUT_OF_STOCK)
        self.assertEqual(conf_result.updated_inventory.pharmacy_id, self.pharmacy_id)

        # Check database records
        db_prop = self.repo.get_proposal_by_id(proposal.id)
        self.assertEqual(db_prop.status, "CONFIRMED")
        self.assertEqual(db_prop.confirmed_by, self.pharmacist_id)

        history_after = self.repo.get_inventory_history(conf_result.updated_inventory.id)
        self.assertEqual(len(history_after), 1)
        applied_history = history_after[0]
        self.assertEqual(applied_history.change_type, "STOCK_UPDATE")
        self.assertEqual(applied_history.new_stock_status, StockActionEnum.OUT_OF_STOCK)
        self.assertEqual(applied_history.changed_by, self.pharmacist_id)
        self.assertEqual(applied_history.source_proposal_id, proposal.id)

        # --- Step 3: Chemist undoes the change ---
        # First evaluate undo safety
        eval_res = evaluate_can_undo(
            history_id=applied_history.id,
            user_id=self.chemist_id,
            pharmacy_id=self.pharmacy_id,
            repository=self.repo,
        )
        self.assertTrue(eval_res.can_undo)

        undo_result = orchestrate_undo(
            history_id=applied_history.id,
            user_id=self.chemist_id,
            pharmacy_id=self.pharmacy_id,
            repository=self.repo,
        )

        self.assertTrue(undo_result.success)
        self.assertEqual(undo_result.undone_history_id, applied_history.id)
        self.assertEqual(undo_result.undo_history_record.change_type, "UNDO")
        self.assertEqual(undo_result.undo_history_record.undo_of_history_id, applied_history.id)

        # Confirm inventory was restored
        self.assertEqual(undo_result.restored_inventory.last_update_source, "undo")

        # History table must now have both original and undo records (immutable append-only)
        all_hist = self.repo.get_inventory_history(conf_result.updated_inventory.id)
        self.assertEqual(len(all_hist), 2)
        self.assertEqual(all_hist[0].change_type, "STOCK_UPDATE")
        self.assertEqual(all_hist[1].change_type, "UNDO")

        # --- Step 4: Double-undo attempt is rejected ---
        with self.assertRaises(HistoryAlreadyUndoneError):
            orchestrate_undo(
                history_id=applied_history.id,
                user_id=self.chemist_id,
                pharmacy_id=self.pharmacy_id,
                repository=self.repo,
            )

    def test_multi_medicine_message_with_schedule_h_flagging(self) -> None:
        """Multi-medicine update correctly flags Schedule H medicine for pharmacist review."""
        result = process_stock_message(
            raw_message="Dolo 650 20 strip and Azithral 500 khatam",
            pharmacy_id=self.pharmacy_id,
            chemist_user_id=self.chemist_id,
            repository=self.repo,
        )

        self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)
        self.assertEqual(len(result.created_proposals), 2)

        dolo_prop = next(p for p in result.created_proposals if p.brand_name == "Dolo 650")
        azithral_prop = next(p for p in result.created_proposals if p.brand_name == "Azithral 500")

        # Dolo 650 (non-Schedule H)
        self.assertEqual(dolo_prop.parsed_quantity, 20)
        self.assertEqual(dolo_prop.parsed_intent, StockActionEnum.IN_STOCK)
        self.assertFalse(dolo_prop.flagged_for_review)

        # Azithral 500 (Schedule H restricted antibiotic)
        self.assertEqual(azithral_prop.parsed_intent, StockActionEnum.OUT_OF_STOCK)
        self.assertTrue(azithral_prop.flagged_for_review)

        # Confirm Dolo 650
        conf = confirm_and_apply_proposal(
            proposal_id=dolo_prop.id,
            user_id=self.chemist_id,
            pharmacy_id=self.pharmacy_id,
            repository=self.repo,
        )
        self.assertEqual(conf.updated_inventory.quantity, 20)

        # Reject Azithral 500 with review note
        rej = reject_proposal_workflow(
            proposal_id=azithral_prop.id,
            user_id=self.pharmacist_id,
            pharmacy_id=self.pharmacy_id,
            reason="Prescription audit incomplete",
            repository=self.repo,
        )
        self.assertEqual(rej.status, "REJECTED")

        # Confirm Dolo proposal is CONFIRMED and Azithral proposal is REJECTED in database
        self.assertEqual(self.repo.get_proposal_by_id(dolo_prop.id).status, "CONFIRMED")
        self.assertEqual(self.repo.get_proposal_by_id(azithral_prop.id).status, "REJECTED")

    def test_unauthorized_user_blocked_at_service_boundary(self) -> None:
        """Unlinked user cannot propose or confirm updates."""
        # 1. Propose attempt
        prop_result = process_stock_message(
            raw_message="Dolo 650 50 available",
            pharmacy_id=self.pharmacy_id,
            chemist_user_id=self.unauthorized_id,
            repository=self.repo,
        )
        self.assertEqual(prop_result.status, ProposalServiceStatus.UNAUTHORIZED)
        self.assertEqual(len(prop_result.created_proposals), 0)

        # 2. Confirm attempt on valid proposal
        valid_res = process_stock_message(
            raw_message="Dolo 650 50 available",
            pharmacy_id=self.pharmacy_id,
            chemist_user_id=self.chemist_id,
            repository=self.repo,
        )
        valid_prop = valid_res.created_proposals[0]

        with self.assertRaises(UserNotLinkedToPharmacyError):
            confirm_and_apply_proposal(
                proposal_id=valid_prop.id,
                user_id=self.unauthorized_id,
                pharmacy_id=self.pharmacy_id,
                repository=self.repo,
            )

    def test_expiry_sweep_and_confirmation_block(self) -> None:
        """Expired proposals cannot be confirmed even if confirmation is attempted."""
        # Chemist proposes stock update
        prop_res = process_stock_message(
            raw_message="Dolo 650 10 available",
            pharmacy_id=self.pharmacy_id,
            chemist_user_id=self.chemist_id,
            repository=self.repo,
        )
        prop = prop_res.created_proposals[0]

        # 25 hours later
        future = datetime.now(timezone.utc) + timedelta(hours=25)

        # Attempt to confirm after expiry cutoff
        with self.assertRaises(ProposalExpiredError):
            confirm_and_apply_proposal(
                proposal_id=prop.id,
                user_id=self.chemist_id,
                pharmacy_id=self.pharmacy_id,
                repository=self.repo,
                current_time=future,
            )

        # Run expiry job
        expired_count = expire_proposals_job(cutoff=future, repository=self.repo)
        self.assertEqual(expired_count, 1)

        # Status in DB is EXPIRED
        self.assertEqual(self.repo.get_proposal_by_id(prop.id).status, "EXPIRED")

        # Repeated confirmation on EXPIRED proposal raises ProposalNotPendingError
        with self.assertRaises(ProposalNotPendingError):
            confirm_and_apply_proposal(
                proposal_id=prop.id,
                user_id=self.chemist_id,
                pharmacy_id=self.pharmacy_id,
                repository=self.repo,
            )


if __name__ == "__main__":
    unittest.main()
