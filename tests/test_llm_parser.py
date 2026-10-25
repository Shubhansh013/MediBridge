"""Tests for optional LLM-assisted stock-message parser and fallback behavior."""

from datetime import datetime
import json
import unittest
from unittest.mock import MagicMock, patch

from medibridge.schemas.stock import StockActionEnum, StockUpdateProposal
from medibridge.services.ai_parser import (
    parse_stock_message,
    parse_stock_message_deterministic,
    parse_with_gemini,
    parse_with_groq,
)


class MockHTTPResponse:
    def __init__(self, status_code: int = 200, json_data: dict = None):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = json.dumps(self._json_data)

    def json(self):
        return self._json_data


class TestLLMParser(unittest.TestCase):
    def test_valid_structured_llm_response(self):
        """Test 1: LLM returns a valid JSON matching StockUpdateProposal."""
        mock_gemini_response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "items": [
                                        {
                                            "medicine_name": "Dolo 650",
                                            "status": "IN_STOCK",
                                            "quantity": 25,
                                            "dosage_form": "Strip",
                                            "strength": "650mg",
                                        }
                                    ],
                                    "notes": "Extracted via Gemini Flash",
                                })
                            }
                        ]
                    }
                }
            ]
        }
        mock_client = MagicMock()
        mock_client.post.return_value = MockHTTPResponse(200, mock_gemini_response)

        proposal = parse_stock_message(
            "Dolo 650 25 strips available",
            api_key="mock-api-key-test",
            http_client=mock_client,
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertEqual(len(proposal.items), 1)
        item = proposal.items[0]
        self.assertEqual(item.medicine_name, "Dolo 650")
        self.assertEqual(item.quantity, 25)
        self.assertEqual(item.status, StockActionEnum.IN_STOCK)
        self.assertEqual(item.dosage_form, "Strip")
        self.assertEqual(item.strength, "650mg")
        self.assertIn("Gemini", proposal.notes or "")

    def test_invalid_malformed_llm_response_falls_back(self):
        """Test 2: LLM returns malformed JSON or invalid schema, falls back to deterministic."""
        mock_gemini_response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Sorry, I am not able to parse this update."}
                        ]
                    }
                }
            ]
        }
        mock_client = MagicMock()
        mock_client.post.return_value = MockHTTPResponse(200, mock_gemini_response)

        # Valid deterministic message should still parse via deterministic fallback
        proposal = parse_stock_message(
            "Dolo 650 20 strips available",
            api_key="mock-api-key-test",
            http_client=mock_client,
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertEqual(proposal.items[0].medicine_name, "Dolo 650")
        self.assertEqual(proposal.items[0].quantity, 20)
        self.assertEqual(proposal.items[0].status, StockActionEnum.IN_STOCK)

    def test_provider_failure_or_timeout_falls_back(self):
        """Test 3: Provider raises a network exception or timeout, falls back safely."""
        mock_client = MagicMock()
        mock_client.post.side_effect = TimeoutError("Request timed out after 3.0s")

        proposal = parse_stock_message(
            "Crocin Advance khatam",
            api_key="mock-api-key-test",
            http_client=mock_client,
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertEqual(proposal.items[0].medicine_name, "Crocin Advance")
        self.assertEqual(proposal.items[0].status, StockActionEnum.OUT_OF_STOCK)

    def test_provider_http_error_falls_back(self):
        """Test 3B: Provider returns HTTP 500 error, falls back safely."""
        mock_client = MagicMock()
        mock_client.post.return_value = MockHTTPResponse(500, {"error": "Internal Server Error"})

        proposal = parse_stock_message(
            "Dolo 650 15 strips available",
            api_key="mock-api-key-test",
            http_client=mock_client,
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertEqual(proposal.items[0].quantity, 15)

    def test_no_api_key_configured_uses_deterministic(self):
        """Test 4: When no API key is set, deterministic parser runs directly without HTTP calls."""
        with patch.dict("os.environ", {}, clear=True):
            mock_client = MagicMock()
            proposal = parse_stock_message(
                "Dolo 650 30 strips hai",
                api_key=None,
                http_client=mock_client,
            )
            self.assertIsNotNone(proposal)
            assert proposal is not None
            self.assertEqual(proposal.items[0].medicine_name, "Dolo 650")
            self.assertEqual(proposal.items[0].quantity, 30)
            # Zero HTTP calls made
            mock_client.post.assert_not_called()

    def test_deterministic_fallback_still_works(self):
        """Test 5: Deterministic fallback works identically to baseline."""
        msg = "Calpol 650 15 patte uplabdh hai"
        det_prop = parse_stock_message_deterministic(msg)
        llm_prop = parse_stock_message(msg, use_llm=False)

        self.assertIsNotNone(det_prop)
        self.assertIsNotNone(llm_prop)
        assert det_prop is not None and llm_prop is not None
        self.assertEqual(det_prop.items[0].medicine_name, "Calpol 650")
        self.assertEqual(det_prop.items[0].quantity, 15)
        self.assertEqual(det_prop.items[0].status, StockActionEnum.IN_STOCK)
        self.assertEqual(det_prop.items[0].dosage_form, "Strip")
        self.assertEqual(det_prop.items[0].model_dump(), llm_prop.items[0].model_dump())

    def test_valid_structured_groq_response(self):
        """Test 6: Groq returns valid JSON matching StockUpdateProposal."""
        mock_groq_response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({
                            "items": [
                                {
                                    "medicine_name": "Azithromycin 500mg",
                                    "status": "IN_STOCK",
                                    "quantity": 10,
                                    "dosage_form": "Strip",
                                    "strength": "500mg",
                                }
                            ],
                            "notes": "Extracted via Groq llama-3.3-70b-versatile",
                        })
                    }
                }
            ]
        }
        mock_client = MagicMock()
        mock_client.post.return_value = MockHTTPResponse(200, mock_groq_response)

        proposal = parse_stock_message(
            "Azithromycin 500mg 10 strips available",
            api_key="gsk_mock_test_key",
            provider="groq",
            http_client=mock_client,
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertEqual(len(proposal.items), 1)
        item = proposal.items[0]
        self.assertEqual(item.medicine_name, "Azithromycin 500mg")
        self.assertEqual(item.quantity, 10)
        self.assertEqual(item.status, StockActionEnum.IN_STOCK)
        self.assertIn("Groq", proposal.notes or "")

    def test_invalid_malformed_groq_response_falls_back(self):
        """Test 7: Groq returns malformed JSON or invalid schema, falls back to deterministic."""
        mock_groq_response = {
            "choices": [
                {
                    "message": {
                        "content": "Not a JSON response from Groq"
                    }
                }
            ]
        }
        mock_client = MagicMock()
        mock_client.post.return_value = MockHTTPResponse(200, mock_groq_response)

        proposal = parse_stock_message(
            "Dolo 650 20 strips available",
            api_key="gsk_mock_test_key",
            provider="groq",
            http_client=mock_client,
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertEqual(proposal.items[0].medicine_name, "Dolo 650")
        self.assertEqual(proposal.items[0].quantity, 20)
        self.assertEqual(proposal.items[0].status, StockActionEnum.IN_STOCK)

    def test_groq_provider_failure_or_timeout_falls_back(self):
        """Test 8: Groq raises a network exception or timeout, falls back safely."""
        mock_client = MagicMock()
        mock_client.post.side_effect = TimeoutError("Groq request timed out")

        proposal = parse_stock_message(
            "Crocin Advance khatam",
            api_key="gsk_mock_test_key",
            provider="groq",
            http_client=mock_client,
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertEqual(proposal.items[0].medicine_name, "Crocin Advance")
        self.assertEqual(proposal.items[0].status, StockActionEnum.OUT_OF_STOCK)

    def test_groq_http_error_falls_back(self):
        """Test 9: Groq returns HTTP 500 or 401 error, falls back safely."""
        mock_client = MagicMock()
        mock_client.post.return_value = MockHTTPResponse(500, {"error": "Internal Server Error"})

        proposal = parse_stock_message(
            "Dolo 650 15 strips available",
            api_key="gsk_mock_test_key",
            provider="groq",
            http_client=mock_client,
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertEqual(proposal.items[0].quantity, 15)

    def test_groq_provider_selection_via_env(self):
        """Test 10: LLM_PROVIDER=groq selects Groq provider and uses GROQ_API_KEY."""
        mock_groq_response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({
                            "items": [
                                {
                                    "medicine_name": "Paracetamol 500mg",
                                    "status": "IN_STOCK",
                                    "quantity": 50,
                                }
                            ]
                        })
                    }
                }
            ]
        }
        mock_client = MagicMock()
        mock_client.post.return_value = MockHTTPResponse(200, mock_groq_response)

        with patch.dict("os.environ", {"LLM_PROVIDER": "groq", "GROQ_API_KEY": "gsk_env_test_key"}, clear=True):
            proposal = parse_stock_message(
                "Paracetamol 500mg 50 strips",
                http_client=mock_client,
            )
            self.assertIsNotNone(proposal)
            assert proposal is not None
            self.assertEqual(proposal.items[0].medicine_name, "Paracetamol 500mg")
            # Verify mock_client called Groq endpoint with Bearer auth
            mock_client.post.assert_called_once()
            args, kwargs = mock_client.post.call_args
            self.assertIn("api.groq.com", args[0])
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer gsk_env_test_key")

    def test_groq_placeholder_key_uses_deterministic(self):
        """Test 11: When GROQ_API_KEY is placeholder, deterministic parser runs directly."""
        with patch.dict("os.environ", {"LLM_PROVIDER": "groq", "GROQ_API_KEY": "your_groq_api_key_here"}, clear=True):
            mock_client = MagicMock()
            proposal = parse_stock_message(
                "Dolo 650 20 strips available",
                http_client=mock_client,
            )
            self.assertIsNotNone(proposal)
            assert proposal is not None
            self.assertEqual(proposal.items[0].medicine_name, "Dolo 650")
            mock_client.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()

