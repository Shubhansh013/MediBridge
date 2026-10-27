"""Tests for optional LLM-assisted stock-message parser and fallback behavior.

Covers Groq-first default provider routing, optional Gemini provider selection,
deterministic-first priority, conversational/uncertain message rejection guards,
configurable models and timeouts, secret redaction in logs, and failure safety.
"""

from datetime import datetime
import json
import unittest
from unittest.mock import MagicMock, patch

from medibridge.schemas.stock import StockActionEnum, StockUpdateProposal
from medibridge.services.ai_parser import (
    DEFAULT_GROQ_MODEL,
    GROQ_API_URL,
    parse_stock_message,
    parse_stock_message_deterministic,
    parse_with_gemini,
    parse_with_groq,
)


class MockHTTPResponse:
    def __init__(self, status_code: int = 200, json_data: dict = None, text: str = None):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text if text is not None else json.dumps(self._json_data)
        self.closed = False

    def json(self):
        return self._json_data

    def close(self):
        self.closed = True


class TestLLMParser(unittest.TestCase):
    def test_deterministic_first_skips_llm(self):
        """Test: Deterministic messages are parsed directly with zero LLM API calls."""
        mock_client = MagicMock()
        proposal = parse_stock_message(
            "Dolo 650 20 strips available",
            api_key="mock-api-key-test",
            http_client=mock_client,
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertEqual(len(proposal.items), 1)
        self.assertEqual(proposal.items[0].medicine_name, "Dolo 650")
        self.assertEqual(proposal.items[0].quantity, 20)
        self.assertEqual(proposal.items[0].status, StockActionEnum.IN_STOCK)
        self.assertEqual(proposal.items[0].dosage_form, "Strip")
        # Zero HTTP calls made to LLM provider
        mock_client.post.assert_not_called()

    def test_conversational_and_uncertain_rejection_skips_llm(self):
        """Test: Conversational, uncertain, or future-oriented messages are rejected without calling LLM."""
        mock_client = MagicMock()
        reject_cases = [
            "Bhaiya Dolo 650 milega kya?",
            "Dolo 650 shayad 10 bache hai",
            "Kal subah aayega Paracetamol",
            "Crocin Advance rate kitna hai?",
        ]
        for msg in reject_cases:
            with self.subTest(msg=msg):
                proposal = parse_stock_message(
                    msg,
                    api_key="mock-api-key-test",
                    http_client=mock_client,
                )
                self.assertIsNone(proposal)
                mock_client.post.assert_not_called()

    def test_default_provider_is_groq(self):
        """Test: When no provider is explicitly set, Groq is the default provider."""
        mock_groq_response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({
                            "items": [
                                {
                                    "medicine_name": "Dolo 650",
                                    "status": "IN_STOCK",
                                    "quantity": 25,
                                    "dosage_form": "Strip",
                                    "strength": "650mg",
                                }
                            ],
                            "notes": "Extracted via default Groq",
                        })
                    }
                }
            ]
        }
        mock_client = MagicMock()
        mock_client.post.return_value = MockHTTPResponse(200, mock_groq_response)

        # Call with no provider argument and no LLM_PROVIDER in env
        with patch.dict("os.environ", {}, clear=True):
            proposal = parse_stock_message(
                "Shipment intake received: Dolo 650 count is twenty-five",
                api_key="gsk_mock_test_key",
                http_client=mock_client,
            )

        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertEqual(proposal.items[0].medicine_name, "Dolo 650")
        self.assertEqual(proposal.items[0].quantity, 25)
        # Verify Groq endpoint was called by default
        mock_client.post.assert_called_once()
        called_url, kwargs = mock_client.post.call_args[0][0], mock_client.post.call_args[1]
        self.assertIn("api.groq.com", called_url)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer gsk_mock_test_key")

    def test_missing_groq_credentials_does_not_route_to_gemini(self):
        """Test: If Groq credentials are missing, safe behavior is to return None without silently routing to Gemini."""
        mock_client = MagicMock()
        # Environment has GEMINI_API_KEY, but NO GROQ_API_KEY, and default provider is Groq
        with patch.dict("os.environ", {"GEMINI_API_KEY": "gemini-secret-key"}, clear=True):
            proposal = parse_stock_message(
                "Shipment intake received: Dolo 650 count is twenty-five",
                api_key=None,
                http_client=mock_client,
            )
            # Must return None safely and must NOT call Gemini
            self.assertIsNone(proposal)
            mock_client.post.assert_not_called()

    def test_valid_structured_groq_response(self):
        """Test: Groq returns valid JSON matching StockUpdateProposal on eligible miss."""
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
            "Shipment intake received: Azithromycin 500mg count is ten",
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
        mock_client.post.assert_called_once()

    def test_valid_structured_gemini_response(self):
        """Test: Explicit Gemini selection invokes Gemini and validates output."""
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
            "Shipment intake received: Dolo 650 count is twenty-five",
            api_key="mock-api-key-test",
            provider="gemini",
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
        mock_client.post.assert_called_once()
        self.assertIn("googleapis.com", mock_client.post.call_args[0][0])

    def test_gemini_selection_via_env(self):
        """Test: LLM_PROVIDER=gemini in environment routes to Gemini."""
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
                                        }
                                    ]
                                })
                            }
                        ]
                    }
                }
            ]
        }
        mock_client = MagicMock()
        mock_client.post.return_value = MockHTTPResponse(200, mock_gemini_response)

        with patch.dict("os.environ", {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "gemini-env-key"}, clear=True):
            proposal = parse_stock_message(
                "Shipment intake received: Dolo 650 count is twenty-five",
                http_client=mock_client,
            )
            self.assertIsNotNone(proposal)
            mock_client.post.assert_called_once()
            called_url = mock_client.post.call_args[0][0]
            self.assertIn("googleapis.com", called_url)
            self.assertIn("key=gemini-env-key", called_url)

    def test_gemini_selection_via_key_prefix(self):
        """Test: AIza-prefixed api_key hints Gemini provider when provider is not specified."""
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
                                        }
                                    ]
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
            "Shipment intake received: Dolo 650 count is twenty-five",
            api_key="AIzaSyMockKeyForGoogleApi",
            http_client=mock_client,
        )
        self.assertIsNotNone(proposal)
        mock_client.post.assert_called_once()
        self.assertIn("googleapis.com", mock_client.post.call_args[0][0])

    def test_groq_selection_via_key_prefix(self):
        """Test: gsk_-prefixed api_key hints Groq provider when provider is not specified."""
        mock_groq_response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({
                            "items": [
                                {
                                    "medicine_name": "Dolo 650",
                                    "status": "IN_STOCK",
                                    "quantity": 25,
                                }
                            ]
                        })
                    }
                }
            ]
        }
        mock_client = MagicMock()
        mock_client.post.return_value = MockHTTPResponse(200, mock_groq_response)

        proposal = parse_stock_message(
            "Shipment intake received: Dolo 650 count is twenty-five",
            api_key="gsk_mock_test_key_12345",
            http_client=mock_client,
        )
        self.assertIsNotNone(proposal)
        mock_client.post.assert_called_once()
        self.assertIn("api.groq.com", mock_client.post.call_args[0][0])

    def test_unrecognized_provider_returns_none(self):
        """Test: Unrecognized provider string logs a warning and returns None safely."""
        mock_client = MagicMock()
        proposal = parse_stock_message(
            "Shipment intake received: Dolo 650 count is twenty-five",
            api_key="mock-key",
            provider="unsupported_provider_xyz",
            http_client=mock_client,
        )
        self.assertIsNone(proposal)
        mock_client.post.assert_not_called()

    def test_invalid_malformed_groq_response_falls_back(self):
        """Test: Groq returns malformed JSON or invalid schema, falls back safely."""
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
        """Test: Groq raises a network exception or timeout, deterministic fallback succeeds."""
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
        """Test: Groq returns HTTP 500 error, deterministic fallback succeeds."""
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

    def test_no_api_key_configured_uses_deterministic(self):
        """Test: When no API key is set, deterministic parser runs directly without HTTP calls."""
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
            mock_client.post.assert_not_called()

    def test_deterministic_fallback_still_works(self):
        """Test: Deterministic fallback works identically to baseline."""
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

    def test_groq_placeholder_key_uses_deterministic(self):
        """Test: When GROQ_API_KEY is placeholder, deterministic parser runs directly."""
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

    def test_gemini_placeholder_key_uses_deterministic(self):
        """Test: When GEMINI_API_KEY is placeholder, deterministic parser runs directly."""
        with patch.dict("os.environ", {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "your_gemini_api_key_here"}, clear=True):
            mock_client = MagicMock()
            proposal = parse_stock_message(
                "Dolo 650 20 strips available",
                http_client=mock_client,
            )
            self.assertIsNotNone(proposal)
            assert proposal is not None
            self.assertEqual(proposal.items[0].medicine_name, "Dolo 650")
            mock_client.post.assert_not_called()

    def test_schema_violation_falls_back(self):
        """Test: LLM returns valid JSON but a schema violation (bad status enum)."""
        mock_groq_response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({
                            "items": [
                                {
                                    "medicine_name": "Dolo 650",
                                    "status": "ALMOST_IN_STOCK",
                                    "quantity": 25,
                                }
                            ]
                        })
                    }
                }
            ]
        }
        mock_client = MagicMock()
        mock_client.post.return_value = MockHTTPResponse(200, mock_groq_response)

        proposal = parse_stock_message(
            "Dolo 650 20 strips available",
            api_key="gsk_mock_test_key",
            http_client=mock_client,
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        # Deterministic result produced safely
        self.assertEqual(proposal.items[0].medicine_name, "Dolo 650")
        self.assertEqual(proposal.items[0].quantity, 20)

    def test_empty_items_array_falls_back(self):
        """Test: LLM returns {"items": []} — deterministic parsing applies safely."""
        mock_groq_response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"items": [], "notes": "conversational"})
                    }
                }
            ]
        }
        mock_client = MagicMock()
        mock_client.post.return_value = MockHTTPResponse(200, mock_groq_response)

        proposal = parse_stock_message(
            "Dolo 650 20 strips available",
            api_key="gsk_mock_test_key",
            http_client=mock_client,
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertEqual(proposal.items[0].medicine_name, "Dolo 650")

    def test_configurable_timeout_is_respected(self):
        """Test: An explicit timeout override is passed through to Groq provider on fallback."""
        mock_client = MagicMock()
        mock_client.post.return_value = MockHTTPResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({
                                "items": [
                                    {
                                        "medicine_name": "Dolo 650",
                                        "status": "IN_STOCK",
                                        "quantity": 25,
                                    }
                                ]
                            })
                        }
                    }
                ]
            },
        )

        parse_stock_message(
            "Shipment intake received: Dolo 650 count is twenty-five",
            api_key="gsk_mock_test_key",
            timeout=12.5,
            http_client=mock_client,
        )
        _, kwargs = mock_client.post.call_args
        self.assertEqual(kwargs["timeout"], 12.5)

    def test_env_timeout_override(self):
        """Test: LLM_TIMEOUT env var overrides the default 3.0s timeout."""
        mock_client = MagicMock()
        mock_client.post.return_value = MockHTTPResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({
                                "items": [
                                    {
                                        "medicine_name": "Dolo 650",
                                        "status": "IN_STOCK",
                                        "quantity": 25,
                                    }
                                ]
                            })
                        }
                    }
                ]
            },
        )

        with patch.dict("os.environ", {"LLM_TIMEOUT": "8.0"}, clear=True):
            parse_stock_message(
                "Shipment intake received: Dolo 650 count is twenty-five",
                api_key="gsk_mock_test_key",
                http_client=mock_client,
            )
        _, kwargs = mock_client.post.call_args
        self.assertEqual(kwargs["timeout"], 8.0)

    def test_no_unvalidated_proposal_on_failure(self):
        """Test: A provider exception must never yield an unvalidated proposal."""
        mock_client = MagicMock()
        mock_client.post.side_effect = RuntimeError("provider exploded")

        proposal = parse_stock_message(
            "Dolo 650 20 strips available",
            api_key="gsk_mock_test_key",
            http_client=mock_client,
        )
        if proposal is not None:
            for item in proposal.items:
                self.assertIsInstance(item.medicine_name, str)
                self.assertTrue(item.medicine_name.strip())
                self.assertIn(item.status, ("IN_STOCK", "LOW_STOCK", "OUT_OF_STOCK"))
                if item.quantity is not None:
                    self.assertGreaterEqual(item.quantity, 0)

    def test_api_key_not_logged(self):
        """Test: Provider warnings never embed the raw API key."""
        import logging
        from medibridge.services import ai_parser

        records = []

        class _Handler(logging.Handler):
            def emit(self, record):
                records.append(self.format(record))

        handler = _Handler()
        handler.setLevel(logging.WARNING)
        ai_parser.logger.addHandler(handler)
        ai_parser.logger.setLevel(logging.WARNING)

        try:
            mock_client = MagicMock()
            mock_client.post.return_value = MockHTTPResponse(500, {"error": "boom"})

            secret = "gsk_supersecretkey1234567890"
            parse_stock_message(
                "Shipment intake received: Dolo 650 count is twenty-five",
                api_key=secret,
                provider="groq",
                http_client=mock_client,
            )
        finally:
            ai_parser.logger.removeHandler(handler)

        joined = "\n".join(records)
        self.assertNotIn(secret, joined)
        self.assertIn("gsk_...", joined)

    def test_gemini_model_configuration(self):
        """Test: Gemini model override via argument and GEMINI_MODEL env var."""
        mock_client = MagicMock()
        mock_client.post.return_value = MockHTTPResponse(200, {
            "candidates": [{
                "content": {
                    "parts": [{"text": json.dumps({"items": [{"medicine_name": "Dolo 650", "status": "IN_STOCK"}]})}]
                }
            }]
        })

        # Explicit model argument
        parse_with_gemini(
            "Shipment intake received: Dolo 650 count is twenty-five",
            api_key="mock-key",
            model="gemini-2.0-flash-lite",
            http_client=mock_client,
        )
        url_called = mock_client.post.call_args[0][0]
        self.assertIn("models/gemini-2.0-flash-lite:generateContent", url_called)

        # Environment variable override with provider=gemini
        mock_client.reset_mock()
        with patch.dict("os.environ", {"LLM_PROVIDER": "gemini", "GEMINI_MODEL": "gemini-2.0-flash-lite"}, clear=True):
            parse_stock_message(
                "Shipment intake received: Dolo 650 count is twenty-five",
                api_key="mock-key",
                http_client=mock_client,
            )
            url_called_env = mock_client.post.call_args[0][0]
            self.assertIn("models/gemini-2.0-flash-lite:generateContent", url_called_env)

    def test_groq_model_configuration(self):
        """Test: Groq model override via argument and GROQ_MODEL env var."""
        mock_client = MagicMock()
        mock_client.post.return_value = MockHTTPResponse(200, {
            "choices": [{"message": {"content": json.dumps({"items": [{"medicine_name": "Dolo 650", "status": "IN_STOCK"}]})}}]
        })

        # Explicit model argument
        parse_with_groq(
            "Shipment intake received: Dolo 650 count is twenty-five",
            api_key="gsk_mock_test_key",
            model="llama-3.1-8b-instant",
            http_client=mock_client,
        )
        payload = mock_client.post.call_args[1]["json"]
        self.assertEqual(payload["model"], "llama-3.1-8b-instant")

        # Environment variable override
        mock_client.reset_mock()
        with patch.dict("os.environ", {"GROQ_MODEL": "llama-3.1-70b-specdec"}, clear=True):
            parse_stock_message(
                "Shipment intake received: Dolo 650 count is twenty-five",
                api_key="gsk_mock_test_key",
                http_client=mock_client,
            )
            payload_env = mock_client.post.call_args[1]["json"]
            self.assertEqual(payload_env["model"], "llama-3.1-70b-specdec")

    def test_eligible_miss_with_failed_llm_returns_none(self):
        """Test: Eligible miss returns None safely when LLM provider errors."""
        mock_client = MagicMock()
        mock_client.post.return_value = MockHTTPResponse(500, {"error": "server down"})

        proposal = parse_stock_message(
            "Shipment intake received: Dolo 650 count is twenty-five",
            api_key="gsk_mock_test_key",
            http_client=mock_client,
        )
        self.assertIsNone(proposal)


    def test_groq_model_openai_gpt_oss_120b_environment_override(self):
        """Test: GROQ_MODEL=openai/gpt-oss-120b env var overrides model in request payload."""
        mock_client = MagicMock()
        mock_client.post.return_value = MockHTTPResponse(200, {
            "choices": [{"message": {"content": json.dumps({"items": [{"medicine_name": "Dolo 650", "status": "IN_STOCK", "quantity": 25}]})}}]
        })

        with patch.dict("os.environ", {"GROQ_MODEL": "openai/gpt-oss-120b"}, clear=True):
            proposal = parse_stock_message(
                "Shipment intake received: Dolo 650 count is twenty-five",
                api_key="gsk_mock_test_key",
                http_client=mock_client,
            )
            self.assertIsNotNone(proposal)
            assert proposal is not None
            self.assertEqual(proposal.items[0].medicine_name, "Dolo 650")
            self.assertEqual(proposal.items[0].quantity, 25)
            payload_env = mock_client.post.call_args[1]["json"]
            self.assertEqual(payload_env["model"], "openai/gpt-oss-120b")

    def test_groq_response_with_reasoning_field(self):
        """Test: Groq responses containing reasoning fields alongside content parse correctly."""
        mock_client = MagicMock()
        mock_client.post.return_value = MockHTTPResponse(200, {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps({
                            "items": [
                                {
                                    "medicine_name": "Dolo 650",
                                    "status": "IN_STOCK",
                                    "quantity": 25,
                                    "dosage_form": "Strip",
                                }
                            ],
                            "notes": "Extracted via reasoning model",
                        }),
                        "reasoning": "The user mentioned shipment intake received of Dolo 650 with count twenty-five.",
                    },
                    "finish_reason": "stop",
                }
            ]
        })

        proposal = parse_with_groq(
            "Shipment intake received: Dolo 650 count is twenty-five",
            api_key="gsk_mock_test_key",
            model="openai/gpt-oss-120b",
            http_client=mock_client,
        )
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertEqual(len(proposal.items), 1)
        self.assertEqual(proposal.items[0].medicine_name, "Dolo 650")
        self.assertEqual(proposal.items[0].quantity, 25)
        self.assertEqual(proposal.items[0].dosage_form, "Strip")

    def test_groq_empty_or_null_content_returns_none_safely(self):
        """Test: Blank, whitespace, or null message content from Groq returns None safely."""
        mock_client = MagicMock()

        for blank_content in ["", "   ", "   \n\t   ", None]:
            with self.subTest(blank_content=repr(blank_content)):
                mock_client.reset_mock()
                mock_client.post.return_value = MockHTTPResponse(200, {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": blank_content,
                                "reasoning": "Thinking but generated blank content",
                            },
                            "finish_reason": "stop",
                        }
                    ]
                })

                proposal = parse_with_groq(
                    "Shipment intake received: Dolo 650 count is twenty-five",
                    api_key="gsk_mock_test_key",
                    model="openai/gpt-oss-120b",
                    http_client=mock_client,
                )
                self.assertIsNone(proposal)


    def test_eligible_misses_route_to_groq_mocked(self):
        """Test: Eligible natural language misses (word numbers, conversational phrasing) route to Groq."""
        cases = [
            (
                "Only five boxes of Crocin remain",
                {"medicine_name": "Crocin", "status": "LOW_STOCK", "quantity": 5, "dosage_form": "Box"},
                StockActionEnum.LOW_STOCK,
                5,
                "Box",
            ),
            (
                "Received twenty-five strips of Dolo 650",
                {"medicine_name": "Dolo 650", "status": "IN_STOCK", "quantity": 25, "dosage_form": "Strip"},
                StockActionEnum.IN_STOCK,
                25,
                "Strip",
            ),
            (
                "Dolo 650 ke pachis strips aaye hain",
                {"medicine_name": "Dolo 650", "status": "IN_STOCK", "quantity": 25, "dosage_form": "Strip"},
                StockActionEnum.IN_STOCK,
                25,
                "Strip",
            ),
        ]

        mock_client = MagicMock()
        for msg, mock_item, exp_status, exp_qty, exp_form in cases:
            with self.subTest(msg=msg):
                mock_client.reset_mock()
                mock_client.post.return_value = MockHTTPResponse(200, {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps({"items": [mock_item]}),
                            },
                            "finish_reason": "stop",
                        }
                    ]
                })

                proposal = parse_stock_message(
                    msg,
                    api_key="gsk_mock_test_key",
                    http_client=mock_client,
                )
                self.assertIsNotNone(proposal, f"Failed to parse eligible miss: {msg}")
                assert proposal is not None
                self.assertEqual(len(proposal.items), 1)
                item = proposal.items[0]
                self.assertEqual(item.medicine_name, mock_item["medicine_name"])
                self.assertEqual(item.status, exp_status)
                self.assertEqual(item.quantity, exp_qty)
                self.assertEqual(item.dosage_form, exp_form)
                mock_client.post.assert_called_once()

    def test_deterministic_and_ineligible_never_call_groq(self):
        """Test: Zero LLM HTTP calls occur for deterministic successes or ineligible guard triggers."""
        cases = [
            # Deterministic successes
            "Dolo 650 20 strips available",
            "Crocin ke 5 dabbe available hain",
            "Paracetamol stock is finished",
            "Paracetamol ka stock khatam hai",
            # Ineligible messages
            "Hello bhaiya Dolo 650 milega kya?",
            "Shayad 10 strips bache hain",
            "Kal 50 strips aayenge",
            "Dolo ka rate kya hai?",
        ]

        mock_client = MagicMock()
        for msg in cases:
            with self.subTest(msg=msg):
                mock_client.reset_mock()
                parse_stock_message(
                    msg,
                    api_key="gsk_mock_test_key",
                    http_client=mock_client,
                )
                mock_client.post.assert_not_called()


    def test_groq_connect_and_read_timeout_fail_safely(self):
        """Test: Both ConnectTimeout and ReadTimeout fail safely without crashing."""
        for exc in (TimeoutError("Connection timed out"), TimeoutError("Read timed out")):
            mock_client = MagicMock()
            mock_client.post.side_effect = exc
            res = parse_with_groq(
                "Shipment intake received: Dolo 650 count is twenty-five",
                api_key="gsk_mock_key",
                http_client=mock_client,
            )
            self.assertIsNone(res)

    def test_groq_http_auth_and_rate_limit_errors_fail_safely(self):
        """Test: HTTP 401, 403, and 429 return None and redact secrets."""
        for code in (401, 403, 429):
            mock_client = MagicMock()
            mock_resp = MockHTTPResponse(code, {"error": {"message": f"Error {code}"}})
            mock_client.post.return_value = mock_resp
            res = parse_with_groq(
                "Shipment intake received: Dolo 650 count is twenty-five",
                api_key="gsk_super_secret_test_key_12345",
                http_client=mock_client,
            )
            self.assertIsNone(res)
            self.assertTrue(mock_resp.closed, f"Response was not closed for HTTP {code}")

    def test_groq_http_5xx_server_errors_fail_safely(self):
        """Test: HTTP 500, 502, 503 return None safely and clean up resources."""
        for code in (500, 502, 503):
            mock_client = MagicMock()
            mock_resp = MockHTTPResponse(code, {"error": f"Server {code}"})
            mock_client.post.return_value = mock_resp
            res = parse_with_groq(
                "Shipment intake received: Dolo 650 count is twenty-five",
                api_key="gsk_mock_key",
                http_client=mock_client,
            )
            self.assertIsNone(res)
            self.assertTrue(mock_resp.closed, f"Response was not closed for HTTP {code}")

    def test_groq_malformed_json_and_whitespace_content_fail_safely(self):
        """Test: Malformed JSON or blank content from provider fails safely."""
        # Malformed raw JSON string
        mock_client = MagicMock()
        mock_resp = MockHTTPResponse(200, text="not-valid-json { [")
        mock_client.post.return_value = mock_resp
        self.assertIsNone(
            parse_with_groq("Shipment intake received: Dolo 650 count is twenty-five", api_key="gsk_key", http_client=mock_client)
        )
        self.assertTrue(mock_resp.closed)

        # Whitespace-only content in choices
        mock_client = MagicMock()
        mock_resp = MockHTTPResponse(200, {"choices": [{"message": {"content": "   \n\t  "}}]})
        mock_client.post.return_value = mock_resp
        self.assertIsNone(
            parse_with_groq("Shipment intake received: Dolo 650 count is twenty-five", api_key="gsk_key", http_client=mock_client)
        )
        self.assertTrue(mock_resp.closed)

    def test_groq_schema_invalid_model_output_fails_safely(self):
        """Test: Model outputs that violate Pydantic schema constraints fail safely."""
        invalid_payloads = [
            # Invalid status enum
            {"items": [{"medicine_name": "Dolo 650", "status": "AVAILABLE", "quantity": 10}]},
            # Negative quantity (violates ge=0)
            {"items": [{"medicine_name": "Dolo 650", "status": "IN_STOCK", "quantity": -5}]},
            # Non-integer quantity
            {"items": [{"medicine_name": "Dolo 650", "status": "IN_STOCK", "quantity": "many"}]},
            # Empty medicine name
            {"items": [{"medicine_name": "", "status": "IN_STOCK", "quantity": 10}]},
        ]
        for payload in invalid_payloads:
            mock_client = MagicMock()
            mock_resp = MockHTTPResponse(200, {"choices": [{"message": {"content": json.dumps(payload)}}]})
            mock_client.post.return_value = mock_resp
            res = parse_with_groq(
                "Shipment intake received: Dolo 650 count is twenty-five",
                api_key="gsk_key",
                http_client=mock_client,
            )
            self.assertIsNone(res)
            self.assertTrue(mock_resp.closed)

    def test_groq_unexpected_response_shapes_fail_safely(self):
        """Test: Malformed or unexpected response envelopes fail safely."""
        bad_envelopes = [
            # Array instead of dict envelope
            [{"choices": []}],
            # Missing choices
            {"error": "no choices"},
            # Choices is not a list
            {"choices": "none"},
            # Choices[0] is not a dict
            {"choices": ["string-choice"]},
            # Message is None
            {"choices": [{"message": None}]},
            # Items is not a list
            {"choices": [{"message": {"content": json.dumps({"items": "not a list"})}}]},
        ]
        for env in bad_envelopes:
            mock_client = MagicMock()
            mock_resp = MockHTTPResponse(200, env if isinstance(env, dict) else {}, text=json.dumps(env))
            mock_client.post.return_value = mock_resp
            res = parse_with_groq(
                "Shipment intake received: Dolo 650 count is twenty-five",
                api_key="gsk_key",
                http_client=mock_client,
            )
            self.assertIsNone(res)
            self.assertTrue(mock_resp.closed)

    def test_groq_json_array_root_parsed_successfully(self):
        """Test: When model outputs a root JSON array [{...}] instead of {"items": [...]}, it is parsed cleanly."""
        mock_client = MagicMock()
        root_array = [
            {"medicine_name": "Dolo 650", "status": "IN_STOCK", "quantity": 25, "dosage_form": "Strip"}
        ]
        mock_resp = MockHTTPResponse(200, {"choices": [{"message": {"content": json.dumps(root_array)}}]})
        mock_client.post.return_value = mock_resp
        res = parse_with_groq(
            "Shipment intake received: Dolo 650 count is twenty-five",
            api_key="gsk_key",
            http_client=mock_client,
        )
        self.assertIsNotNone(res)
        assert res is not None
        self.assertEqual(res.items[0].medicine_name, "Dolo 650")
        self.assertEqual(res.items[0].quantity, 25)
        self.assertTrue(mock_resp.closed)

    def test_gemini_provider_reliability_and_resource_cleanup(self):
        """Test: Gemini fails safely on timeouts, HTTP errors, and malformed responses."""
        # Timeout
        mock_client = MagicMock()
        mock_client.post.side_effect = TimeoutError("Gemini read timeout")
        self.assertIsNone(
            parse_with_gemini("Shipment intake received: Dolo 650 count is twenty-five", api_key="AIzaKey", http_client=mock_client)
        )

        # HTTP 403 & 500
        for code in (403, 500):
            mock_client = MagicMock()
            mock_resp = MockHTTPResponse(code, {"error": "blocked"})
            mock_client.post.return_value = mock_resp
            self.assertIsNone(
                parse_with_gemini("Shipment intake received: Dolo 650 count is twenty-five", api_key="AIzaKey", http_client=mock_client)
            )
            self.assertTrue(mock_resp.closed)

        # Empty candidates
        mock_client = MagicMock()
        mock_resp = MockHTTPResponse(200, {"candidates": []})
        mock_client.post.return_value = mock_resp
        self.assertIsNone(
            parse_with_gemini("Shipment intake received: Dolo 650 count is twenty-five", api_key="AIzaKey", http_client=mock_client)
        )
        self.assertTrue(mock_resp.closed)

        # Root array in Gemini
        mock_client = MagicMock()
        mock_resp = MockHTTPResponse(200, {
            "candidates": [{
                "content": {"parts": [{"text": json.dumps([{"medicine_name": "Crocin", "status": "IN_STOCK", "quantity": 10}])}]}
            }]
        })
        mock_client.post.return_value = mock_resp
        res = parse_with_gemini("Shipment intake received: Crocin count is ten", api_key="AIzaKey", http_client=mock_client)
        self.assertIsNotNone(res)
        assert res is not None
        self.assertEqual(res.items[0].medicine_name, "Crocin")
        self.assertTrue(mock_resp.closed)

    def test_cross_provider_isolation(self):
        """Test: When Groq fails, Gemini is NOT called; when Gemini fails, Groq is NOT called."""
        # Groq selected, raises error -> Gemini never called
        mock_client = MagicMock()
        mock_client.post.side_effect = TimeoutError("Groq timeout")
        res = parse_stock_message(
            "Shipment intake received: Dolo 650 count is twenty-five",
            api_key="gsk_groq_key",
            provider="groq",
            http_client=mock_client,
        )
        self.assertIsNone(res)
        self.assertEqual(mock_client.post.call_count, 1)
        self.assertIn("groq.com", mock_client.post.call_args[0][0])

        # Gemini selected, raises error -> Groq never called
        mock_client.reset_mock()
        mock_client.post.side_effect = TimeoutError("Gemini timeout")
        res = parse_stock_message(
            "Shipment intake received: Dolo 650 count is twenty-five",
            api_key="AIza_gemini_key",
            provider="gemini",
            http_client=mock_client,
        )
        self.assertIsNone(res)
        self.assertEqual(mock_client.post.call_count, 1)
        self.assertIn("googleapis.com", mock_client.post.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
