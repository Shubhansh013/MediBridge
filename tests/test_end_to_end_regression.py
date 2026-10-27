"""End-to-End AI/LLM Integration and Backend Foundation Regression Tests.

Verifies the 10 core integration scenarios specified in Phase 4:
1. English natural-language input: "Add 20 strips of Dolo 650"
2. Concise input: "Dolo 650 20 strips"
3. Supported Hindi/Hinglish stock message
4. A different catalogue medicine (e.g., Augmentin 625)
5. An unknown medicine not present in catalogue
6. A medicine-strength mismatch handling
7. Proposal creation without inventory mutation (inventory invariant)
8. Authorized confirmation applying the correct stock change atomically
9. LLM provider failure or timeout safe fallback
10. Existing database and REST API gateway operations
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from api.main import app
from medibridge_db.database import Base, _apply_sqlite_pragmas
from medibridge_db.models import (
    HistoryChangeType,
    Inventory,
    Medicine,
    MedicineAlias,
    Pharmacy,
    PharmacyVerification,
    ProposalStatus,
    StaffRoleAtPharmacy,
    StockHistory,
    StockProposal,
    StockStatus,
    User,
    UserPharmacy,
    UserRole,
)
from medibridge_db.normalize import normalize_composition
from medibridge.database.operations_adapter import DBOperationsAdapter
from medibridge.schemas.stock import StockActionEnum
from medibridge.services.ai_parser import (
    parse_stock_message,
    parse_with_gemini,
    parse_with_groq,
)
from medibridge.services.confirmation_service import (
    confirm_and_apply_proposal,
    reject_proposal_workflow,
)
from medibridge.services.proposal_service import (
    ProposalServiceStatus,
    process_stock_message,
)


class MockHTTPResponse:
    def __init__(self, status_code: int = 200, json_data: dict | None = None, text: str | None = None):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text if text is not None else json.dumps(self._json_data)

    def json(self):
        return self._json_data


class TestEndToEndIntegrationRegression(unittest.TestCase):
    """Controlled regression tests using an isolated in-memory test database."""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", future=True)
        event.listen(self.engine, "connect", _apply_sqlite_pragmas)
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

        # Seed isolated test records
        with self.session_factory() as sess:
            self.pharmacy = Pharmacy(
                id=1,
                name="Sanjeevani Medicos",
                locality="Lajpat Nagar",
                address="123 Ring Road",
                latitude=28.5672,
                longitude=77.2433,
                telegram_chat_id=10001,
                verification_state=PharmacyVerification.verified,
                is_active=True,
            )
            self.other_pharmacy = Pharmacy(
                id=2,
                name="Apollo Pharmacy",
                locality="South Ex",
                address="456 Main Market",
                latitude=28.5700,
                longitude=77.2200,
                telegram_chat_id=10002,
                verification_state=PharmacyVerification.verified,
                is_active=True,
            )
            sess.add_all([self.pharmacy, self.other_pharmacy])

            self.chemist = User(
                id=3,
                telegram_user_id=10003,
                display_name="Ramesh Gupta",
                role=UserRole.chemist,
                is_active=True,
            )
            self.unauthorized_user = User(
                id=99,
                telegram_user_id=10099,
                display_name="Stranger Danger",
                role=UserRole.patient,
                is_active=True,
            )
            sess.add_all([self.chemist, self.unauthorized_user])

            # Link chemist to pharmacy 1
            sess.add(
                UserPharmacy(
                    user_id=3,
                    pharmacy_id=1,
                    role_at_pharmacy=StaffRoleAtPharmacy.chemist,
                )
            )

            # Seed medicines
            self.med_dolo = Medicine(
                id=1,
                brand_name="Dolo 650",
                generic_name="Paracetamol",
                composition_key=normalize_composition("Paracetamol"),
                strength="650mg",
                form="tablet",
                is_schedule_h=False,
                manufacturer="Micro Labs",
                is_active=True,
            )
            self.med_augmentin = Medicine(
                id=2,
                brand_name="Augmentin 625",
                generic_name="Amoxicillin + Clavulanic Acid",
                composition_key=normalize_composition("Amoxicillin + Clavulanic Acid"),
                strength="500mg+125mg",
                form="tablet",
                is_schedule_h=True,
                manufacturer="GSK",
                is_active=True,
            )
            sess.add_all([self.med_dolo, self.med_augmentin])

            # Seed aliases
            sess.add(MedicineAlias(medicine_id=1, alias="dolo", alias_type="short"))
            sess.add(MedicineAlias(medicine_id=1, alias="dolo 650", alias_type="short"))
            sess.add(MedicineAlias(medicine_id=2, alias="augmentin", alias_type="short"))

            # Baseline inventory for Dolo at pharmacy 1 (OUT_OF_STOCK, quantity=0)
            self.initial_inv = Inventory(
                pharmacy_id=1,
                medicine_id=1,
                stock_status=StockStatus.out_of_stock,
                quantity=0,
                last_confirmed_at=datetime.now(timezone.utc),
            )
            sess.add(self.initial_inv)
            sess.commit()

        self.adapter = DBOperationsAdapter(session_factory=self.session_factory)

    # -------------------------------------------------------------------------
    # Scenario 1: English "Add 20 strips of Dolo 650"
    # -------------------------------------------------------------------------
    def test_scenario_1_add_20_strips_of_dolo_650(self):
        """Scenario 1: 'Add 20 strips of Dolo 650' parses cleanly to Dolo 650, quantity 20, IN_STOCK."""
        # 1. AI Parser level
        parsed = parse_stock_message("Add 20 strips of Dolo 650")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(len(parsed.items), 1)
        self.assertEqual(parsed.items[0].medicine_name, "Dolo 650")
        self.assertEqual(parsed.items[0].quantity, 20)
        self.assertEqual(parsed.items[0].dosage_form, "Strip")
        self.assertEqual(parsed.items[0].status, StockActionEnum.IN_STOCK)

        # 2. Service level with catalogue grounding
        result = process_stock_message(
            "Add 20 strips of Dolo 650",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
        )
        self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)
        self.assertEqual(len(result.created_proposals), 1)
        proposal = result.created_proposals[0]
        self.assertEqual(proposal.brand_name, "Dolo 650")
        self.assertEqual(proposal.parsed_salt, "Paracetamol")
        self.assertEqual(proposal.parsed_strength, "650mg")
        self.assertEqual(proposal.parsed_quantity, 20)
        self.assertEqual(proposal.status, "PENDING")

    # -------------------------------------------------------------------------
    # Scenario 2: Concise input "Dolo 650 20 strips"
    # -------------------------------------------------------------------------
    def test_scenario_2_concise_dolo_650_20_strips(self):
        """Scenario 2: 'Dolo 650 20 strips' parses and creates PENDING proposal."""
        result = process_stock_message(
            "Dolo 650 20 strips",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
        )
        self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)
        self.assertEqual(len(result.created_proposals), 1)
        proposal = result.created_proposals[0]
        self.assertEqual(proposal.brand_name, "Dolo 650")
        self.assertEqual(proposal.parsed_quantity, 20)
        self.assertEqual(proposal.status, "PENDING")

    # -------------------------------------------------------------------------
    # Scenario 3: Hinglish stock message
    # -------------------------------------------------------------------------
    def test_scenario_3_hinglish_stock_message(self):
        """Scenario 3: Hinglish messages 'Dolo 650 15 patte uplabdh hai' and 'daal do' create valid proposals."""
        result1 = process_stock_message(
            "Dolo 650 15 patte uplabdh hai",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
        )
        self.assertEqual(result1.status, ProposalServiceStatus.SUCCESS)
        self.assertEqual(result1.created_proposals[0].parsed_quantity, 15)

        result2 = process_stock_message(
            "Dolo 650 10 strips daal do",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
        )
        self.assertEqual(result2.status, ProposalServiceStatus.SUCCESS)
        self.assertEqual(result2.created_proposals[0].parsed_quantity, 10)

    # -------------------------------------------------------------------------
    # Scenario 4: Different catalogue medicine (Schedule H Augmentin 625)
    # -------------------------------------------------------------------------
    def test_scenario_4_different_catalogue_medicine(self):
        """Scenario 4: 'Augmentin 625 15 strips available' resolves with Schedule H review flag."""
        result = process_stock_message(
            "Augmentin 625 15 strips available",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
        )
        self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)
        proposal = result.created_proposals[0]
        self.assertEqual(proposal.brand_name, "Augmentin 625")
        self.assertEqual(proposal.parsed_salt, "Amoxicillin + Clavulanic Acid")
        self.assertEqual(proposal.parsed_strength, "500mg+125mg")
        self.assertEqual(proposal.parsed_quantity, 15)
        # Schedule H requires mandatory review flag
        self.assertTrue(proposal.flagged_for_review)

    # -------------------------------------------------------------------------
    # Scenario 5: Unknown medicine
    # -------------------------------------------------------------------------
    def test_scenario_5_unknown_medicine_fails_safely(self):
        """Scenario 5: Medicine not in catalogue does not invent salts or create ungrounded proposals."""
        result = process_stock_message(
            "FakeMedNonExistent 10 strips available",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
        )
        self.assertEqual(result.status, ProposalServiceStatus.UNRESOLVED)
        self.assertEqual(len(result.created_proposals), 0)
        self.assertEqual(len(result.unresolved_items), 1)
        self.assertIn("not found in catalogue", result.unresolved_items[0].reason)

    # -------------------------------------------------------------------------
    # Scenario 6: Strength mismatch
    # -------------------------------------------------------------------------
    def test_scenario_6_strength_mismatch_fails_safely(self):
        """Scenario 6: Chemist message mentions Dolo 650 with contradictory strength 500mg (kept UNRESOLVED)."""
        result = process_stock_message(
            "Dolo 650 500mg 20 strips available",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
        )
        self.assertEqual(result.status, ProposalServiceStatus.UNRESOLVED)
        self.assertEqual(len(result.created_proposals), 0)
        self.assertEqual(len(result.unresolved_items), 1)
        self.assertIn("Strength mismatch", result.unresolved_items[0].reason)

    # -------------------------------------------------------------------------
    # Scenario 7: Proposal creation without inventory mutation
    # -------------------------------------------------------------------------
    def test_scenario_7_proposal_creation_does_not_mutate_inventory(self):
        """Scenario 7: Creating a proposal MUST NEVER change live inventory."""
        with self.session_factory() as sess:
            inv_before = sess.execute(
                select(Inventory).where(Inventory.pharmacy_id == 1, Inventory.medicine_id == 1)
            ).scalar_one()
            self.assertEqual(inv_before.quantity, 0)
            self.assertEqual(inv_before.stock_status, StockStatus.out_of_stock)

        result = process_stock_message(
            "Add 20 strips of Dolo 650",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
        )
        self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)

        # Confirm inventory is 100% UNCHANGED
        with self.session_factory() as sess:
            inv_after = sess.execute(
                select(Inventory).where(Inventory.pharmacy_id == 1, Inventory.medicine_id == 1)
            ).scalar_one()
            self.assertEqual(inv_after.quantity, 0)
            self.assertEqual(inv_after.stock_status, StockStatus.out_of_stock)

    # -------------------------------------------------------------------------
    # Scenario 8: Authorized confirmation applying stock change atomically
    # -------------------------------------------------------------------------
    def test_scenario_8_authorized_confirmation_applies_stock_change(self):
        """Scenario 8: Only confirmation updates inventory and records audit history."""
        # 1. Create proposal
        result = process_stock_message(
            "Add 20 strips of Dolo 650",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
        )
        self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)
        prop_id = result.created_proposals[0].id

        # 2. Confirm proposal
        conf_result = confirm_and_apply_proposal(
            proposal_id=prop_id,
            user_id=3,
            pharmacy_id=1,
            repository=self.adapter,
        )
        self.assertEqual(conf_result.proposal_id, prop_id)
        self.assertEqual(conf_result.updated_inventory.quantity, 20)
        self.assertEqual(conf_result.updated_inventory.stock_status, StockStatus.in_stock)

        # 3. Verify in database
        with self.session_factory() as sess:
            db_inv = sess.execute(
                select(Inventory).where(Inventory.pharmacy_id == 1, Inventory.medicine_id == 1)
            ).scalar_one()
            self.assertEqual(db_inv.quantity, 20)
            self.assertEqual(db_inv.stock_status, StockStatus.in_stock)

            # Audit history written
            history_rows = sess.execute(
                select(StockHistory).where(StockHistory.pharmacy_id == 1, StockHistory.medicine_id == 1)
            ).scalars().all()
            self.assertGreaterEqual(len(history_rows), 1)
            latest = history_rows[-1]
            self.assertEqual(latest.new_quantity, 20)
            self.assertEqual(latest.new_stock_status, StockStatus.in_stock)

    # -------------------------------------------------------------------------
    # Scenario 9: Provider timeout and failure safe fallback
    # -------------------------------------------------------------------------
    def test_scenario_9_llm_failure_and_timeout_safe_fallback(self):
        """Scenario 9: Provider timeouts and 500 errors gracefully return None without crash."""
        mock_client = MagicMock()
        mock_client.post.return_value = MockHTTPResponse(status_code=500, json_data={"error": "server down"})

        # Eligible deterministic miss message
        ambiguous_message = "Shipment intake received: Dolo 650 count is twenty-five"

        # Gemini failure fallback
        res_gemini = parse_with_gemini(ambiguous_message, api_key="mock-key", http_client=mock_client)
        self.assertIsNone(res_gemini)

        # Groq failure fallback
        res_groq = parse_with_groq(ambiguous_message, api_key="gsk_mock_key", http_client=mock_client)
        self.assertIsNone(res_groq)

        # Safe fallback in top-level parse_stock_message
        res_top = parse_stock_message(
            ambiguous_message,
            api_key="gsk_mock_key",
            provider="groq",
            http_client=mock_client,
        )
        self.assertIsNone(res_top)

    # -------------------------------------------------------------------------
    # Scenario 10: Existing database and REST API Gateway operations
    # -------------------------------------------------------------------------
    def test_scenario_10_api_gateway_proposals_and_authorization(self):
        """Scenario 10: REST API Gateway enforces 403 on unauthorized user and 200 on authorized flow."""
        client = TestClient(app)

        # 1. Health check
        health = client.get("/api/health")
        self.assertEqual(health.status_code, 200)

        # 2. Unauthorized attempt (user 99 is not linked to pharmacy 1) -> 403 Forbidden
        unauth_resp = client.post(
            "/api/proposals",
            json={
                "raw_message": "Add 20 strips of Dolo 650",
                "chemist_user_id": 99,
                "pharmacy_id": 1,
            },
        )
        self.assertEqual(unauth_resp.status_code, 403)

        # 3. Authorized proposal creation (user 3 linked to pharmacy 1) -> 200 OK, PENDING
        create_resp = client.post(
            "/api/proposals",
            json={
                "raw_message": "Dolo 650 20 strips",
                "chemist_user_id": 3,
                "pharmacy_id": 1,
            },
        )
        self.assertEqual(create_resp.status_code, 200)
        data = create_resp.json()
        self.assertEqual(data["status"], "CREATED")
        prop_id = data["proposals"][0]["id"]

        # 4. Rejection workflow via API
        reject_resp = client.post(
            f"/api/proposals/{prop_id}/reject",
            json={
                "reject_by_user_id": 3,
                "pharmacy_id": 1,
                "rejection_note": "Regression test cancellation",
            },
        )
        self.assertEqual(reject_resp.status_code, 200)
        self.assertTrue(reject_resp.json()["success"])


if __name__ == "__main__":
    unittest.main()
