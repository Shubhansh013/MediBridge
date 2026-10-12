"""Unit tests for the parser-to-proposal adapter service.

Verifies conversion of Phase 2A parsed items to database ParsedProposalInput,
flagged_for_review evaluation, zero-quantity preservation, unresolved catalogue
matching, multi-medicine decomposition, and input validation.
"""

import unittest

from medibridge.schemas.stock import (
    ParsedMedicineStockItem,
    StockActionEnum,
    StockUpdateProposal,
)
from medibridge.services.ai_parser import parse_stock_message
from medibridge.services.proposal_adapter import (
    AdaptedProposalResult,
    ParsedProposalInput,
    UnresolvedProposalItem,
    adapt_parsed_item,
    adapt_stock_proposal,
    compute_review_flag,
)


class TestProposalAdapter(unittest.TestCase):
    """Test suite covering the parser-to-database proposal adapter."""

    def test_valid_conversion_single_medicine(self):
        """Test 1: Valid conversion of a single medicine with complete catalogue context."""
        item = ParsedMedicineStockItem(
            medicine_name="Dolo 650",
            status=StockActionEnum.IN_STOCK,
            quantity=20,
            dosage_form="Strip",
            strength="650mg",
        )
        adapted = adapt_parsed_item(
            item=item,
            raw_message="Dolo 650 20 strips available",
            salt="paracetamol",
            confidence=0.90,
            is_schedule_h=False,
        )

        self.assertIsInstance(adapted, ParsedProposalInput)
        assert isinstance(adapted, ParsedProposalInput)
        self.assertEqual(adapted.brand_name, "Dolo 650")
        self.assertEqual(adapted.parsed_salt, "paracetamol")
        self.assertEqual(adapted.parsed_strength, "650mg")
        self.assertEqual(adapted.parsed_form, "Strip")
        self.assertEqual(adapted.parsed_quantity, 20)
        self.assertEqual(adapted.parsed_intent, StockActionEnum.IN_STOCK)
        self.assertEqual(adapted.confidence, 0.90)
        self.assertFalse(adapted.flagged_for_review)

    def test_conversion_multiple_medicines_from_one_message(self):
        """Test 2: Multi-medicine message adapted into distinct proposal payloads."""
        raw_msg = "Dolo 650 20 strips hai, Calpol khatam"
        proposal = parse_stock_message(raw_msg)
        self.assertIsNotNone(proposal)
        assert proposal is not None

        catalogue_context = {
            "Dolo 650": {
                "salt": "paracetamol",
                "strength": "650mg",
                "form": "strip",
                "confidence": 0.95,
                "is_schedule_h": False,
            },
            "Calpol": {
                "salt": "paracetamol",
                "strength": "500mg",
                "form": "tablet",
                "confidence": 0.85,
                "is_schedule_h": False,
            },
        }

        result = adapt_stock_proposal(proposal, catalogue_matches=catalogue_context)
        self.assertIsInstance(result, AdaptedProposalResult)
        self.assertTrue(result.is_fully_resolved)
        self.assertEqual(len(result.resolved), 2)
        self.assertEqual(len(result.unresolved), 0)
        self.assertEqual(result.raw_message, raw_msg)

        item1 = result.resolved[0]
        self.assertEqual(item1.brand_name, "Dolo 650")
        self.assertEqual(item1.parsed_quantity, 20)
        self.assertEqual(item1.parsed_intent, StockActionEnum.IN_STOCK)
        self.assertFalse(item1.flagged_for_review)

        item2 = result.resolved[1]
        self.assertEqual(item2.brand_name, "Calpol")
        self.assertIsNone(item2.parsed_quantity)
        self.assertEqual(item2.parsed_intent, StockActionEnum.OUT_OF_STOCK)
        self.assertFalse(item2.flagged_for_review)

    def test_missing_required_fields_and_unresolved_matching(self):
        """Test 3: Missing salt, strength, or confidence emits UnresolvedProposalItem without guessing."""
        item = ParsedMedicineStockItem(
            medicine_name="Crocin Advance",
            status=StockActionEnum.OUT_OF_STOCK,
            quantity=None,
        )
        adapted = adapt_parsed_item(
            item=item,
            raw_message="Crocin Advance khatam",
            # No salt, strength, confidence, or schedule_h provided
        )

        self.assertIsInstance(adapted, UnresolvedProposalItem)
        assert isinstance(adapted, UnresolvedProposalItem)
        self.assertEqual(adapted.item.medicine_name, "Crocin Advance")
        self.assertEqual(adapted.raw_message, "Crocin Advance khatam")
        self.assertIn("parsed_salt", adapted.missing_fields)
        self.assertIn("parsed_strength", adapted.missing_fields)
        self.assertIn("confidence", adapted.missing_fields)
        self.assertIn("is_schedule_h", adapted.missing_fields)
        self.assertIn("Missing required contract fields", adapted.reason)

    def test_quantity_zero_and_stock_status_consistency(self):
        """Test 4: Explicit quantity 0 is preserved alongside OUT_OF_STOCK status."""
        item = ParsedMedicineStockItem(
            medicine_name="Dolo",
            status=StockActionEnum.OUT_OF_STOCK,
            quantity=0,
            dosage_form="Strip",
        )
        adapted = adapt_parsed_item(
            item=item,
            raw_message="Dolo 0 strips hai",
            salt="paracetamol",
            strength="650mg",
            confidence=0.92,
            is_schedule_h=False,
        )

        self.assertIsInstance(adapted, ParsedProposalInput)
        assert isinstance(adapted, ParsedProposalInput)
        self.assertEqual(adapted.parsed_quantity, 0)
        self.assertEqual(adapted.parsed_intent, StockActionEnum.OUT_OF_STOCK)

    def test_confidence_threshold_flagging(self):
        """Test 5: flagged_for_review evaluates confidence < 0.8 correctly across boundary values."""
        # Below 0.8 -> flagged
        self.assertTrue(compute_review_flag(confidence=0.79, is_schedule_h=False))
        self.assertTrue(compute_review_flag(confidence=0.50, is_schedule_h=False))
        self.assertTrue(compute_review_flag(confidence=0.00, is_schedule_h=False))

        # Equal to 0.8 -> not flagged (if not schedule H)
        self.assertFalse(compute_review_flag(confidence=0.80, is_schedule_h=False))

        # Above 0.8 -> not flagged (if not schedule H)
        self.assertFalse(compute_review_flag(confidence=0.81, is_schedule_h=False))
        self.assertFalse(compute_review_flag(confidence=1.00, is_schedule_h=False))

    def test_schedule_h_flagging(self):
        """Test 6: Schedule H medicines are always flagged for review regardless of confidence."""
        self.assertTrue(compute_review_flag(confidence=0.99, is_schedule_h=True))
        self.assertTrue(compute_review_flag(confidence=0.80, is_schedule_h=True))
        self.assertTrue(compute_review_flag(confidence=0.60, is_schedule_h=True))
        self.assertTrue(compute_review_flag(confidence=0.00, is_schedule_h=True))

    def test_invalid_inputs_review_flag(self):
        """Test 7: Missing or invalid parameters in compute_review_flag raise explicit errors."""
        # Missing inputs must NOT silently default
        with self.assertRaises(ValueError):
            compute_review_flag(confidence=None, is_schedule_h=False)  # type: ignore[arg-type]

        with self.assertRaises(ValueError):
            compute_review_flag(confidence=0.9, is_schedule_h=None)  # type: ignore[arg-type]

        # Out-of-bounds confidence
        with self.assertRaises(ValueError):
            compute_review_flag(confidence=-0.01, is_schedule_h=False)

        with self.assertRaises(ValueError):
            compute_review_flag(confidence=1.01, is_schedule_h=False)

        # Type mismatches
        with self.assertRaises(TypeError):
            compute_review_flag(confidence="high", is_schedule_h=False)  # type: ignore[arg-type]

        with self.assertRaises(TypeError):
            compute_review_flag(confidence=0.9, is_schedule_h="yes")  # type: ignore[arg-type]

    def test_invalid_inputs_adapter(self):
        """Test 8: Invalid inputs to adapt_parsed_item and adapt_stock_proposal raise explicit errors."""
        with self.assertRaises(TypeError):
            adapt_parsed_item(None, "some raw message")  # type: ignore[arg-type]

        item = ParsedMedicineStockItem(
            medicine_name="Dolo",
            status=StockActionEnum.IN_STOCK,
        )
        with self.assertRaises(ValueError):
            adapt_parsed_item(item, "")

        with self.assertRaises(TypeError):
            adapt_stock_proposal(None)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
