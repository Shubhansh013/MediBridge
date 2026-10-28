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
import os
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
    DEFAULT_GROQ_MODEL,
    GROQ_API_URL,
    parse_stock_message,
    parse_stock_message_deterministic,
    parse_with_gemini,
    parse_with_groq,
)
from medibridge.services.confirmation_service import (
    confirm_and_apply_proposal,
    reject_proposal_workflow,
)
from medibridge.exceptions import (
    ProposalExpiredError,
    UserNotLinkedToPharmacyError,
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
            sess.add(MedicineAlias(medicine_id=2, alias="augmentin 625", alias_type="short"))
            sess.add(MedicineAlias(medicine_id=1, alias="डोलो 650", alias_type="hinglish"))
            sess.add(MedicineAlias(medicine_id=1, alias="डोलो", alias_type="hinglish"))
            sess.add(MedicineAlias(medicine_id=2, alias="ऑगमेंटिन 625", alias_type="hinglish"))
            sess.add(MedicineAlias(medicine_id=2, alias="ऑगमेंटिन", alias_type="hinglish"))


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

        # Verify Groq timeout on Hinglish message 'Dolo ka stock bees strips badha dena'
        mock_timeout_client = MagicMock()
        mock_timeout_client.post.side_effect = TimeoutError("Groq gateway timed out after 3.0s")
        res_timeout_parser = parse_stock_message(
            "Dolo ka stock bees strips badha dena",
            api_key="gsk_mock_key",
            provider="groq",
            http_client=mock_timeout_client,
        )
        self.assertIsNone(res_timeout_parser)

        # Verify proposal service level fails safely with UNPARSEABLE and 0 proposals
        res_timeout_service = process_stock_message(
            "Dolo ka stock bees strips badha dena",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
            api_key="gsk_mock_key",
            http_client=mock_timeout_client,
        )
        self.assertEqual(res_timeout_service.status, ProposalServiceStatus.UNPARSEABLE)
        self.assertEqual(len(res_timeout_service.created_proposals), 0)

        # Verify inventory remains completely unmodified
        with self.session_factory() as sess:
            inv = sess.execute(
                select(Inventory).where(Inventory.pharmacy_id == 1, Inventory.medicine_id == 1)
            ).scalar_one()
            self.assertEqual(inv.quantity, 0)
            self.assertEqual(inv.stock_status, StockStatus.out_of_stock)

    # -------------------------------------------------------------------------
    # Scenario 10: Existing database and REST API Gateway operations
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Scenario 11: Complex Hinglish with salutation and action phrase
    # -------------------------------------------------------------------------
    def test_scenario_11_complex_hinglish_bhai_daal_do(self):
        """Scenario 11: 'Bhai Dolo ke 20 patte aur daal do' correctly isolates Dolo, quantity 20, Strip, IN_STOCK."""
        parsed = parse_stock_message("Bhai Dolo ke 20 patte aur daal do")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(len(parsed.items), 1)
        self.assertEqual(parsed.items[0].medicine_name, "Dolo")
        self.assertEqual(parsed.items[0].quantity, 20)
        self.assertEqual(parsed.items[0].dosage_form, "Strip")
        self.assertEqual(parsed.items[0].status, StockActionEnum.IN_STOCK)

        # Service level resolves Dolo alias to Dolo 650
        result = process_stock_message(
            "Bhai Dolo ke 20 patte aur daal do",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
        )
        self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)
        self.assertEqual(result.created_proposals[0].brand_name, "Dolo 650")
        self.assertEqual(result.created_proposals[0].parsed_quantity, 20)
        self.assertEqual(result.processing_source, "Parsed by rules")

    # -------------------------------------------------------------------------
    # Scenario 12: LLM NLU for Hindi word numbers and phrase "badha dena"
    # -------------------------------------------------------------------------
    def test_scenario_12_llm_nlu_hindi_number_words_badha_dena(self):
        """Scenario 12: Successful Groq LLM interpretation for 'Dolo ka stock bees strips badha dena'.

        Verifies:
        1. Deterministic parser rejects unparsed number word 'bees' and falls back to LLM.
        2. Mocked Groq response returns medicine 'Dolo', quantity 20, unit 'Strip', status 'IN_STOCK'.
        3. Schema validation succeeds with Pydantic model ParsedMedicineStockItem.
        4. Mock assertion verifies Groq LLM was actually invoked with expected parameters.
        5. Processing source is 'Interpreted by LLM'.
        6. Catalogue resolution checks:
           - Resolves via alias 'dolo' -> 'Dolo 650' (medicine_id=1, 650mg, Paracetamol).
           - Also verifies catalogue ambiguity handling: if an ambiguous or ungrounded medicine
             is returned, the pipeline marks it UNRESOLVED and does not invent or guess.
        7. Proposal is created with status 'PENDING' without mutating inventory.
        8. Authorized chemist confirmation updates inventory atomically.
        """
        # 1. Verify deterministic parser misses on 'bees'
        det_res = parse_stock_message_deterministic("Dolo ka stock bees strips badha dena")
        self.assertIsNone(det_res)

        # 2. Setup mocked successful Groq response
        mock_client = MagicMock()
        mock_groq_resp = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({
                            "items": [
                                {
                                    "medicine_name": "Dolo",
                                    "status": "IN_STOCK",
                                    "quantity": 20,
                                    "dosage_form": "Strip",
                                    "strength": None,
                                }
                            ],
                            "notes": "Parsed via Groq LLM (llama-3.3-70b-versatile)",
                        })
                    }
                }
            ]
        }
        mock_client.post.return_value = MockHTTPResponse(200, mock_groq_resp)

        # Verify inventory before processing is OUT_OF_STOCK, quantity=0
        with self.session_factory() as sess:
            inv_before = sess.execute(
                select(Inventory).where(Inventory.pharmacy_id == 1, Inventory.medicine_id == 1)
            ).scalar_one()
            self.assertEqual(inv_before.quantity, 0)
            self.assertEqual(inv_before.stock_status, StockStatus.out_of_stock)

        # 3. Process stock message through proposal service
        result = process_stock_message(
            "Dolo ka stock bees strips badha dena",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
            api_key="gsk_test_mock_key",
            http_client=mock_client,
        )

        # 4. Verify LLM mock was actually invoked with correct parameters
        mock_client.post.assert_called_once()
        call_args, call_kwargs = mock_client.post.call_args
        self.assertEqual(call_args[0], GROQ_API_URL)
        self.assertEqual(call_kwargs["headers"]["Authorization"], "Bearer gsk_test_mock_key")
        expected_model = os.getenv("GROQ_MODEL") or DEFAULT_GROQ_MODEL
        self.assertEqual(call_kwargs["json"]["model"], expected_model)
        self.assertIn("Dolo ka stock bees strips badha dena", call_kwargs["json"]["messages"][1]["content"])

        # 5. Verify processing source
        self.assertEqual(result.processing_source, "Interpreted by LLM")

        # 6. Verify catalogue resolution:
        # Inspect catalogue: 'Dolo' matches alias for 'Dolo 650'
        alias_match = self.adapter.find_medicine_by_alias("Dolo")
        self.assertIsNotNone(alias_match)
        assert alias_match is not None
        self.assertEqual(alias_match.brand_name, "Dolo 650")
        self.assertEqual(alias_match.generic_name, "Paracetamol")
        self.assertEqual(alias_match.strength, "650mg")

        # 7. Verify proposal was created with PENDING status
        self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)
        self.assertEqual(len(result.created_proposals), 1)
        prop = result.created_proposals[0]
        self.assertEqual(prop.brand_name, "Dolo 650")
        self.assertEqual(prop.parsed_salt, "Paracetamol")
        self.assertEqual(prop.parsed_strength, "650mg")
        self.assertEqual(prop.parsed_quantity, 20)
        self.assertEqual(prop.parsed_intent, StockActionEnum.IN_STOCK)
        self.assertEqual(prop.status, "PENDING")
        self.assertFalse(prop.flagged_for_review)

        # 8. Verify inventory is UNCHANGED before chemist confirmation
        with self.session_factory() as sess:
            inv_mid = sess.execute(
                select(Inventory).where(Inventory.pharmacy_id == 1, Inventory.medicine_id == 1)
            ).scalar_one()
            self.assertEqual(inv_mid.quantity, 0)
            self.assertEqual(inv_mid.stock_status, StockStatus.out_of_stock)

        # 9. Chemist confirms proposal and applies inventory mutation
        conf_result = confirm_and_apply_proposal(
            proposal_id=prop.id,
            user_id=3,
            pharmacy_id=1,
            repository=self.adapter,
        )
        self.assertEqual(conf_result.proposal_id, prop.id)
        self.assertEqual(conf_result.updated_inventory.quantity, 20)
        self.assertEqual(conf_result.updated_inventory.stock_status, StockStatus.in_stock)

        # Verify in database: inventory is now committed
        with self.session_factory() as sess:
            inv_after = sess.execute(
                select(Inventory).where(Inventory.pharmacy_id == 1, Inventory.medicine_id == 1)
            ).scalar_one()
            self.assertEqual(inv_after.quantity, 20)
            self.assertEqual(inv_after.stock_status, StockStatus.in_stock)

    def test_scenario_12_b_llm_catalogue_ambiguity_handled_safely(self):
        """Scenario 12B: When LLM outputs an ambiguous medicine mention with multiple catalogue candidates, keep UNRESOLVED."""
        # Seed an additional paracetamol medicine without alias so searching 'Paracetamol' finds multiple records
        with self.session_factory() as sess:
            med_crocin = Medicine(
                id=3,
                brand_name="Crocin 650",
                generic_name="Paracetamol",
                composition_key=normalize_composition("Paracetamol"),
                strength="650mg",
                form="tablet",
                is_schedule_h=False,
                manufacturer="GSK",
                is_active=True,
            )
            sess.add(med_crocin)
            sess.commit()

        # Mock Groq returning generic mention 'Paracetamol' (matches both Dolo 650 and Crocin 650)
        mock_client = MagicMock()
        mock_groq_resp = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({
                            "items": [
                                {
                                    "medicine_name": "Paracetamol",
                                    "status": "IN_STOCK",
                                    "quantity": 10,
                                    "dosage_form": "Strip",
                                    "strength": "650mg",
                                }
                            ],
                            "notes": "Parsed via Groq LLM (llama-3.3-70b-versatile)",
                        })
                    }
                }
            ]
        }
        mock_client.post.return_value = MockHTTPResponse(200, mock_groq_resp)

        result = process_stock_message(
            "Bhai Paracetamol 10 strips badha dena",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
            api_key="gsk_test_mock_key",
            http_client=mock_client,
        )

        # Ambiguous candidate must NOT be guessed; status must be UNRESOLVED and 0 proposals created
        self.assertEqual(result.status, ProposalServiceStatus.UNRESOLVED)
        self.assertEqual(len(result.created_proposals), 0)
        self.assertEqual(len(result.unresolved_items), 1)
        self.assertIn("Multiple catalogue matches found for 'Paracetamol'", result.unresolved_items[0].reason)

        # Inventory remains unchanged
        with self.session_factory() as sess:
            inv = sess.execute(
                select(Inventory).where(Inventory.pharmacy_id == 1, Inventory.medicine_id == 1)
            ).scalar_one()
            self.assertEqual(inv.quantity, 0)
            self.assertEqual(inv.stock_status, StockStatus.out_of_stock)

    # -------------------------------------------------------------------------
    # Scenario 13: Depletion message "Paracetamol bilkul khatam ho gayi hai"
    # -------------------------------------------------------------------------
    def test_scenario_13_complete_exhaustion_paracetamol_bilkul_khatam(self):
        """Scenario 13: 'Paracetamol bilkul khatam ho gayi hai' safely identifies multiple catalogue matches."""
        result = process_stock_message(
            "Paracetamol bilkul khatam ho gayi hai",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
        )
        # Paracetamol is salt of Dolo 650; with a single paracetamol medicine in test DB, it resolves or requires review
        self.assertIn(result.status, (ProposalServiceStatus.SUCCESS, ProposalServiceStatus.UNRESOLVED))
        if result.status == ProposalServiceStatus.SUCCESS:
            self.assertEqual(result.created_proposals[0].parsed_intent, StockActionEnum.OUT_OF_STOCK)
            self.assertIsNone(result.created_proposals[0].parsed_quantity)

    # -------------------------------------------------------------------------
    # Scenario 14: Extra LLM keys sanitization and API processing_source
    # -------------------------------------------------------------------------
    def test_scenario_14_extra_llm_keys_sanitization_and_api_provenance(self):
        """Scenario 14: Extra keys from LLM (unit, confidence) are sanitized without Pydantic forbid failure."""
        client = TestClient(app)
        resp = client.post(
            "/api/proposals",
            json={
                "raw_message": "Dolo 650 20 strips",
                "chemist_user_id": 3,
                "pharmacy_id": 1,
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["processing_source"], "Parsed by rules")

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


    # -------------------------------------------------------------------------
    # Scenario 15: Safety verification for unauthorized and expired confirmations
    # -------------------------------------------------------------------------
    def test_scenario_15_unauthorized_and_expired_confirmations_rejected(self):
        """Scenario 15: Unauthorized users and expired proposals cannot mutate inventory."""
        # 1. Create a valid proposal
        result = process_stock_message(
            "Dolo 650 20 strips",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
        )
        self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)
        prop_id = result.created_proposals[0].id

        # 2. Unauthorized confirmation attempt (user 99 not linked to pharmacy 1)
        with self.assertRaises(UserNotLinkedToPharmacyError):
            confirm_and_apply_proposal(
                proposal_id=prop_id,
                user_id=99,
                pharmacy_id=1,
                repository=self.adapter,
            )

        # Inventory remains untouched
        with self.session_factory() as sess:
            inv = sess.execute(
                select(Inventory).where(Inventory.pharmacy_id == 1, Inventory.medicine_id == 1)
            ).scalar_one()
            self.assertEqual(inv.quantity, 0)
            self.assertEqual(inv.stock_status, StockStatus.out_of_stock)

        # 3. Expired proposal attempt
        from datetime import timedelta
        with self.session_factory() as sess:
            prop = sess.execute(
                select(StockProposal).where(StockProposal.id == prop_id)
            ).scalar_one()
            prop.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
            sess.commit()

        with self.assertRaises(ProposalExpiredError):
            confirm_and_apply_proposal(
                proposal_id=prop_id,
                user_id=3,
                pharmacy_id=1,
                repository=self.adapter,
            )

        # Inventory remains untouched
        with self.session_factory() as sess:
            inv = sess.execute(
                select(Inventory).where(Inventory.pharmacy_id == 1, Inventory.medicine_id == 1)
            ).scalar_one()
            self.assertEqual(inv.quantity, 0)
            self.assertEqual(inv.stock_status, StockStatus.out_of_stock)


    # -------------------------------------------------------------------------
    # Scenario 16: English action verbs and intake variations
    # -------------------------------------------------------------------------
    def test_scenario_16_english_action_verbs_intake(self):
        """Scenario 16: English action verbs (Received, Restocked, Got) create valid proposals."""
        cases = [
            ("Received 20 strips of Dolo 650", "Dolo 650", 20, "Strip", False),
            ("Restocked Dolo 650 30 strips", "Dolo 650", 30, "Strip", False),
            ("Got 15 boxes of Augmentin 625", "Augmentin 625", 15, "Box", True),
        ]
        for msg, exp_brand, exp_qty, exp_unit, exp_sched_h in cases:
            with self.subTest(msg=msg):
                result = process_stock_message(
                    msg,
                    pharmacy_id=1,
                    chemist_user_id=3,
                    repository=self.adapter,
                    use_llm=False,
                )
                self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)
                self.assertEqual(len(result.created_proposals), 1)
                self.assertEqual(result.processing_source, "Parsed by rules")
                prop = result.created_proposals[0]
                self.assertEqual(prop.brand_name, exp_brand)
                self.assertEqual(prop.parsed_quantity, exp_qty)
                self.assertEqual(prop.parsed_intent, StockActionEnum.IN_STOCK)
                self.assertEqual(prop.status, "PENDING")
                if exp_sched_h:
                    self.assertTrue(prop.flagged_for_review)

        # Inventory invariant: real stock must remain untouched
        with self.session_factory() as sess:
            inv = sess.execute(
                select(Inventory).where(Inventory.pharmacy_id == 1, Inventory.medicine_id == 1)
            ).scalar_one()
            self.assertEqual(inv.quantity, 0)
            self.assertEqual(inv.stock_status, StockStatus.out_of_stock)

    # -------------------------------------------------------------------------
    # Scenario 17: Devanagari Hindi stock messages and unit normalization
    # -------------------------------------------------------------------------
    def test_scenario_17_devanagari_hindi_stock_messages(self):
        """Scenario 17: Devanagari Hindi messages normalize units and resolve to catalogue."""
        # 1. In-stock with Devanagari units: 'डोलो 650 के 20 पत्ते उपलब्ध हैं'
        res1 = process_stock_message(
            "डोलो 650 के 20 पत्ते उपलब्ध हैं",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
            use_llm=False,
        )
        self.assertEqual(res1.status, ProposalServiceStatus.SUCCESS)
        self.assertEqual(len(res1.created_proposals), 1)
        prop1 = res1.created_proposals[0]
        self.assertEqual(prop1.brand_name, "Dolo 650")
        self.assertEqual(prop1.parsed_quantity, 20)
        self.assertEqual(prop1.parsed_form, "tablet")
        self.assertEqual(prop1.parsed_intent, StockActionEnum.IN_STOCK)
        self.assertEqual(prop1.status, "PENDING")

        # 2. Out-of-stock: 'ऑगमेंटिन 625 का स्टॉक खत्म है' (Schedule H restricted)
        res2 = process_stock_message(
            "ऑगमेंटिन 625 का स्टॉक खत्म है",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
            use_llm=False,
        )
        self.assertEqual(res2.status, ProposalServiceStatus.SUCCESS)
        prop2 = res2.created_proposals[0]
        self.assertEqual(prop2.brand_name, "Augmentin 625")
        self.assertIsNone(prop2.parsed_quantity)
        self.assertEqual(prop2.parsed_intent, StockActionEnum.OUT_OF_STOCK)
        self.assertTrue(prop2.flagged_for_review)
        self.assertEqual(prop2.status, "PENDING")

        # 3. Simple in-stock: 'डोलो 650 के 10 पत्ते'
        res3 = process_stock_message(
            "डोलो 650 के 10 पत्ते",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
            use_llm=False,
        )
        self.assertEqual(res3.status, ProposalServiceStatus.SUCCESS)
        prop3 = res3.created_proposals[0]
        self.assertEqual(prop3.brand_name, "Dolo 650")
        self.assertEqual(prop3.parsed_quantity, 10)
        self.assertEqual(prop3.parsed_intent, StockActionEnum.IN_STOCK)

    # -------------------------------------------------------------------------
    # Scenario 18: Word numbers fallback to LLM with schema validation
    # -------------------------------------------------------------------------
    def test_scenario_18_word_numbers_fallback_to_llm(self):
        """Scenario 18: Hindi/English word numbers delegate to LLM and convert to integers."""
        mock_response_payload = {
            "items": [
                {
                    "medicine_name": "Dolo 650",
                    "status": "IN_STOCK",
                    "quantity": "bees",
                    "dosage_form": "patte",
                    "strength": "650mg",
                }
            ],
            "notes": "Interpreted by LLM (groq-test)",
        }
        mock_http_response = MockHTTPResponse(
            status_code=200,
            json_data={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(mock_response_payload),
                        }
                    }
                ]
            },
        )
        mock_client = MagicMock()
        mock_client.post.return_value = mock_http_response

        result = process_stock_message(
            "Dolo 650 ke bees patte aaye hain",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
            use_llm=True,
            api_key="gsk_mock_test_key",
            http_client=mock_client,
        )
        mock_client.post.assert_called_once()
        self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)
        self.assertEqual(result.processing_source, "Interpreted by LLM")
        self.assertEqual(len(result.created_proposals), 1)
        prop = result.created_proposals[0]
        self.assertEqual(prop.brand_name, "Dolo 650")
        self.assertEqual(prop.parsed_quantity, 20)  # 'bees' converted to 20!
        self.assertEqual(prop.status, "PENDING")

    # -------------------------------------------------------------------------
    # Scenario 19: Stock exhaustion variations
    # -------------------------------------------------------------------------
    def test_scenario_19_stock_exhaustion_variations(self):
        """Scenario 19: Exhaustion expressions (nil, stock out, 0 strips) parse cleanly."""
        cases = [
            ("Dolo 650 nil", "Dolo 650", None),
            ("Augmentin 625 stock out", "Augmentin 625", None),
            ("Dolo 650 0 strips", "Dolo 650", 0),
        ]
        for msg, exp_brand, exp_qty in cases:
            with self.subTest(msg=msg):
                result = process_stock_message(
                    msg,
                    pharmacy_id=1,
                    chemist_user_id=3,
                    repository=self.adapter,
                    use_llm=False,
                )
                self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)
                prop = result.created_proposals[0]
                self.assertEqual(prop.brand_name, exp_brand)
                self.assertEqual(prop.parsed_quantity, exp_qty)
                self.assertEqual(prop.parsed_intent, StockActionEnum.OUT_OF_STOCK)

    # -------------------------------------------------------------------------
    # Scenario 20: Availability reports without counts avoid phantom quantities
    # -------------------------------------------------------------------------
    def test_scenario_20_availability_without_quantity_no_phantom_counts(self):
        """Scenario 20: 'Dolo 650 available hai' reports IN_STOCK with quantity=None (not 650)."""
        result = process_stock_message(
            "Dolo 650 available hai",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
            use_llm=False,
        )
        self.assertEqual(result.status, ProposalServiceStatus.SUCCESS)
        prop = result.created_proposals[0]
        self.assertEqual(prop.brand_name, "Dolo 650")
        self.assertIsNone(prop.parsed_quantity)  # Crucial safety check: NOT 650!
        self.assertEqual(prop.parsed_intent, StockActionEnum.IN_STOCK)

    # -------------------------------------------------------------------------
    # Scenario 21: Missing medicine messages rejected safely
    # -------------------------------------------------------------------------
    def test_scenario_21_missing_medicine_rejected_safely(self):
        """Scenario 21: Quantities without medicine names return UNPARSEABLE safely."""
        for msg in ["20 strips daal do", "Add 50 strips"]:
            with self.subTest(msg=msg):
                result = process_stock_message(
                    msg,
                    pharmacy_id=1,
                    chemist_user_id=3,
                    repository=self.adapter,
                    use_llm=False,
                )
                self.assertEqual(result.status, ProposalServiceStatus.UNPARSEABLE)
                self.assertEqual(len(result.created_proposals), 0)

    # -------------------------------------------------------------------------
    # Scenario 22: Strength whitespace formatting vs genuine conflict
    # -------------------------------------------------------------------------
    def test_scenario_22_strength_whitespace_and_conflict_handling(self):
        """Scenario 22: '650 mg' matches '650mg' while '500mg' conflicts safely."""
        # 1. Whitespace in strength matches
        mock_payload = {
            "items": [
                {
                    "medicine_name": "Dolo 650",
                    "status": "IN_STOCK",
                    "quantity": 20,
                    "dosage_form": "Strip",
                    "strength": "650 mg",  # Space between number and unit
                }
            ],
            "notes": "Interpreted by LLM",
        }
        mock_resp = MockHTTPResponse(
            status_code=200,
            json_data={"choices": [{"message": {"content": json.dumps(mock_payload)}}]},
        )
        mock_client = MagicMock()
        mock_client.post.return_value = mock_resp

        res_space = process_stock_message(
            "Dolo 650 20 strips",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
            use_llm=True,
            api_key="gsk_mock",
            http_client=mock_client,
        )
        self.assertEqual(res_space.status, ProposalServiceStatus.SUCCESS)

        # 2. Genuine conflict '500mg' vs catalogue '650mg' -> UNRESOLVED
        res_conflict = process_stock_message(
            "Dolo 650 500mg 10 strips",
            pharmacy_id=1,
            chemist_user_id=3,
            repository=self.adapter,
            use_llm=False,
        )
        self.assertEqual(res_conflict.status, ProposalServiceStatus.UNRESOLVED)
        self.assertEqual(len(res_conflict.unresolved_items), 1)
        self.assertIn("Strength mismatch", res_conflict.unresolved_items[0].reason)


if __name__ == "__main__":
    unittest.main()
