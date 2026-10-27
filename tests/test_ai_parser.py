"""Unit tests for MediBridge deterministic offline AI message parser.

Verifies natural-language stock interpretation across English, Hindi, and Hinglish,
including negation, uncertainty rejection, future delivery exclusion, strength vs
quantity disambiguation, multi-medicine segment parsing, and malformed inputs.
"""

from datetime import datetime, timezone
import unittest

from medibridge.schemas.stock import StockActionEnum, StockUpdateProposal
from medibridge.services.ai_parser import parse_stock_message


class TestAIParser(unittest.TestCase):
    """Test suite covering the deterministic stock message parser."""

    def test_in_stock_english(self):
        """Test 1: Standard English in-stock message with unit."""
        proposal = parse_stock_message("Dolo 650 20 strips available")
        self.assertIsNotNone(proposal)
        assert proposal is not None  # type narrowing
        self.assertEqual(len(proposal.items), 1)
        item = proposal.items[0]
        self.assertEqual(item.medicine_name, "Dolo 650")
        self.assertEqual(item.quantity, 20)
        self.assertEqual(item.status, StockActionEnum.IN_STOCK)
        self.assertEqual(item.dosage_form, "Strip")
        self.assertIsNone(item.strength)
        self.assertIsInstance(proposal.proposed_at, datetime)

    def test_in_stock_hindi_hinglish(self):
        """Test 2: Hindi/Hinglish in-stock messages with colloquial phrasing."""
        proposal = parse_stock_message("Dolo 650 20 strips hai")
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertEqual(proposal.items[0].medicine_name, "Dolo 650")
        self.assertEqual(proposal.items[0].quantity, 20)
        self.assertEqual(proposal.items[0].status, StockActionEnum.IN_STOCK)

        proposal_patte = parse_stock_message("Crocin 15 patte uplabdh hai")
        self.assertIsNotNone(proposal_patte)
        assert proposal_patte is not None
        self.assertEqual(proposal_patte.items[0].medicine_name, "Crocin")
        self.assertEqual(proposal_patte.items[0].quantity, 15)
        self.assertEqual(proposal_patte.items[0].status, StockActionEnum.IN_STOCK)
        self.assertEqual(proposal_patte.items[0].dosage_form, "Strip")

    def test_out_of_stock_negation(self):
        """Test 3: Out-of-stock messages with negation keywords must have quantity None."""
        cases = [
            ("Crocin Advance khatam", "Crocin Advance"),
            ("Dolo 650 nahi hai", "Dolo 650"),
            ("Paracetamol available nahi hai", "Paracetamol"),
            ("Azithromycin out of stock", "Azithromycin"),
        ]
        for message, expected_med in cases:
            with self.subTest(msg=message):
                proposal = parse_stock_message(message)
                self.assertIsNotNone(proposal, f"Failed to parse out-of-stock for: {message}")
                assert proposal is not None
                self.assertEqual(len(proposal.items), 1)
                item = proposal.items[0]
                self.assertEqual(item.medicine_name, expected_med)
                self.assertEqual(item.status, StockActionEnum.OUT_OF_STOCK)
                self.assertIsNone(item.quantity)

    def test_low_stock(self):
        """Test 4: Low-stock keywords produce LOW_STOCK status with count."""
        proposal = parse_stock_message("Azithromycin 500mg low stock 3 strips")
        self.assertIsNotNone(proposal)
        assert proposal is not None
        item = proposal.items[0]
        self.assertEqual(item.medicine_name, "Azithromycin")
        self.assertEqual(item.strength, "500mg")
        self.assertEqual(item.quantity, 3)
        self.assertEqual(item.status, StockActionEnum.LOW_STOCK)

        proposal_kam = parse_stock_message("Dolo 650 kam hai only 5 left")
        self.assertIsNotNone(proposal_kam)
        assert proposal_kam is not None
        self.assertEqual(proposal_kam.items[0].medicine_name, "Dolo 650")
        self.assertEqual(proposal_kam.items[0].quantity, 5)
        self.assertEqual(proposal_kam.items[0].status, StockActionEnum.LOW_STOCK)

    def test_strength_versus_quantity_separation(self):
        """Test 5: Medicine strength (650mg, 500mg) must NEVER be mistaken for stock quantity."""
        proposal = parse_stock_message("Dolo 650mg 20 strips in stock")
        self.assertIsNotNone(proposal)
        assert proposal is not None
        item = proposal.items[0]
        self.assertEqual(item.medicine_name, "Dolo")
        self.assertEqual(item.strength, "650mg")
        self.assertEqual(item.quantity, 20)  # Quantity is 20, NOT 650!
        self.assertEqual(item.status, StockActionEnum.IN_STOCK)

        proposal_tab = parse_stock_message("Paracetamol 500mg 10 tablets available")
        self.assertIsNotNone(proposal_tab)
        assert proposal_tab is not None
        item_tab = proposal_tab.items[0]
        self.assertEqual(item_tab.medicine_name, "Paracetamol")
        self.assertEqual(item_tab.strength, "500mg")
        self.assertEqual(item_tab.quantity, 10)  # Quantity is 10, NOT 500!
        self.assertEqual(item_tab.dosage_form, "Tablet")

    def test_rejection_of_uncertainty(self):
        """Test 6: Uncertain messages must return None rather than fabricating inventory claims."""
        uncertain_messages = [
            "Crocin shayad hai",
            "Shayad Dolo available hai",
            "Dolo maybe available",
            "Calpol lagta hai khatam hai",
            "Azithromycin check karke batata hoon",
            "Dolo 650 pakka nahi pata",
        ]
        for msg in uncertain_messages:
            with self.subTest(msg=msg):
                self.assertIsNone(
                    parse_stock_message(msg),
                    f"Uncertain message was unexpectedly parsed: {msg}",
                )

    def test_rejection_of_future_availability(self):
        """Test 7: Future-oriented deliveries must return None and not treat arrival as stock."""
        future_messages = [
            "Calpol kal aayega",
            "Dolo 650 available tomorrow",
            "Crocin shaam ko aayega",
            "Paracetamol next week expected",
            "Parso naya stock aayega",
        ]
        for msg in future_messages:
            with self.subTest(msg=msg):
                self.assertIsNone(
                    parse_stock_message(msg),
                    f"Future delivery message was unexpectedly parsed: {msg}",
                )

    def test_rejection_of_conversational_and_queries(self):
        """Test 8: Customer questions and general greetings must return None."""
        conversational_messages = [
            "Bhaiya rate kya hai?",
            "Hello good morning sir",
            "Dolo 650 milega kya?",
            "Price kitna hai?",
            "Ok done thanks",
            "Dawa chahiye",
        ]
        for msg in conversational_messages:
            with self.subTest(msg=msg):
                self.assertIsNone(
                    parse_stock_message(msg),
                    f"Conversational message was unexpectedly parsed: {msg}",
                )

    def test_multi_medicine_message(self):
        """Test 9: Compound multi-medicine updates parsed into distinct proposal items."""
        proposal = parse_stock_message("Dolo 650 20 strips hai, Calpol khatam")
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertEqual(len(proposal.items), 2)

        item1 = proposal.items[0]
        self.assertEqual(item1.medicine_name, "Dolo 650")
        self.assertEqual(item1.quantity, 20)
        self.assertEqual(item1.status, StockActionEnum.IN_STOCK)

        item2 = proposal.items[1]
        self.assertEqual(item2.medicine_name, "Calpol")
        self.assertIsNone(item2.quantity)
        self.assertEqual(item2.status, StockActionEnum.OUT_OF_STOCK)

        # Conjunction separation with 'aur'
        proposal_aur = parse_stock_message(
            "Augmentin 625 5 strips in stock aur Pan 40 out of stock"
        )
        self.assertIsNotNone(proposal_aur)
        assert proposal_aur is not None
        self.assertEqual(len(proposal_aur.items), 2)
        self.assertEqual(proposal_aur.items[0].medicine_name, "Augmentin 625")
        self.assertEqual(proposal_aur.items[1].medicine_name, "Pan 40")

    def test_mixed_message_valid_and_future_or_uncertain(self):
        """Test 10: Multi-item message where one is valid and one is future/uncertain."""
        # 'Calpol kal aayega' is future-oriented, but 'Dolo 650 20 strips hai' is valid current stock
        proposal = parse_stock_message("Dolo 650 20 strips hai, Calpol kal aayega")
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertEqual(len(proposal.items), 1)
        self.assertEqual(proposal.items[0].medicine_name, "Dolo 650")
        self.assertEqual(proposal.items[0].quantity, 20)

        # 'Crocin shayad hai' is uncertain, but 'Metformin 500mg 10 strips in stock' is valid
        proposal_uncertain = parse_stock_message(
            "Crocin shayad hai, Metformin 500mg 10 strips in stock"
        )
        self.assertIsNotNone(proposal_uncertain)
        assert proposal_uncertain is not None
        self.assertEqual(len(proposal_uncertain.items), 1)
        self.assertEqual(proposal_uncertain.items[0].medicine_name, "Metformin")
        self.assertEqual(proposal_uncertain.items[0].strength, "500mg")

    def test_empty_and_whitespace_input(self):
        """Test 11: Empty or whitespace messages return None safely."""
        self.assertIsNone(parse_stock_message(""))
        self.assertIsNone(parse_stock_message("   \n\t  "))

    def test_invalid_type_raises_type_error(self):
        """Test 12: Non-string inputs raise TypeError explicitly."""
        with self.assertRaises(TypeError):
            parse_stock_message(123)  # type: ignore[arg-type]

        with self.assertRaises(TypeError):
            parse_stock_message(None)  # type: ignore[arg-type]

    def test_zero_quantity_explicit(self):
        """Test 13: Explicitly reported zero count produces OUT_OF_STOCK preserving quantity=0."""
        proposal = parse_stock_message("Dolo 0 strips hai")
        self.assertIsNotNone(proposal)
        assert proposal is not None
        self.assertEqual(len(proposal.items), 1)
        item = proposal.items[0]
        self.assertEqual(item.medicine_name, "Dolo")
        self.assertEqual(item.status, StockActionEnum.OUT_OF_STOCK)
        self.assertEqual(item.quantity, 0)
        self.assertEqual(item.dosage_form, "Strip")

    def test_no_status_phrase_returns_none(self):
        """Test 14: Medicine name with no status phrase must return None."""
        self.assertIsNone(parse_stock_message("Dolo 650"))

    def test_malformed_symbolic_segment_returns_none(self):
        """Test 15: Purely malformed or symbolic input must return None."""
        self.assertIsNone(parse_stock_message("??? 12345"))


    def test_multilingual_matrix(self):
        """Test 16: Parameterized verification of the Phase 3 multilingual test matrix."""
        # Matrix: (message, expected_deterministic, expected_med, expected_status, expected_qty, expected_form)
        matrix = [
            # English
            ("Dolo 650 20 strips available", True, "Dolo 650", StockActionEnum.IN_STOCK, 20, "Strip"),
            ("Only five boxes of Crocin remain", False, None, None, None, None),  # Eligible miss -> LLM
            ("Paracetamol stock is finished", True, "Paracetamol", StockActionEnum.OUT_OF_STOCK, None, None),
            ("Received twenty-five strips of Dolo 650", False, None, None, None, None),  # Eligible miss -> LLM
            # Hindi / Hinglish
            ("Dolo 650 ke 20 strips bache hain", True, "Dolo 650", StockActionEnum.IN_STOCK, 20, "Strip"),
            ("Crocin ke 5 dabbe available hain", True, "Crocin", StockActionEnum.IN_STOCK, 5, "Box"),
            ("Paracetamol ka stock khatam hai", True, "Paracetamol", StockActionEnum.OUT_OF_STOCK, None, None),
            ("Dolo 650 ke pachis strips aaye hain", False, None, None, None, None),  # Eligible miss -> LLM
        ]

        for msg, is_det, exp_name, exp_status, exp_qty, exp_form in matrix:
            with self.subTest(msg=msg, is_det=is_det):
                # When testing deterministic behavior (use_llm=False)
                res = parse_stock_message(msg, use_llm=False)
                if is_det:
                    self.assertIsNotNone(res, f"Expected deterministic parse for '{msg}'")
                    assert res is not None
                    self.assertEqual(len(res.items), 1)
                    item = res.items[0]
                    self.assertEqual(item.medicine_name, exp_name)
                    self.assertEqual(item.status, exp_status)
                    self.assertEqual(item.quantity, exp_qty)
                    self.assertEqual(item.dosage_form, exp_form)
                else:
                    self.assertIsNone(res, f"Expected deterministic miss (to delegate to LLM) for '{msg}'")

    def test_packaging_units_and_variations(self):
        """Test 17: Various standard packaging forms and Hindi/Hinglish colloquialisms."""
        cases = [
            ("Amoxicillin 500mg 10 capsules available", "Amoxicillin", "500mg", 10, "Capsule", StockActionEnum.IN_STOCK),
            ("Cough Syrup 2 bottles in stock", "Cough Syrup", None, 2, "Bottle", StockActionEnum.IN_STOCK),
            ("Eye Drops 3 shishi hai", "Eye Drops", None, 3, "Bottle", StockActionEnum.IN_STOCK),
            ("Insulin 4 vials present", "Insulin", None, 4, "Injection", StockActionEnum.IN_STOCK),
            ("Bandage 10 packets available", "Bandage", None, 10, "Pack", StockActionEnum.IN_STOCK),
            ("Crocin 50 tablets available", "Crocin", None, 50, "Tablet", StockActionEnum.IN_STOCK),
            ("Paracetamol 30 goliya hai", "Paracetamol", None, 30, "Tablet", StockActionEnum.IN_STOCK),
            ("Dolo 2 dabba uplabdh", "Dolo", None, 2, "Box", StockActionEnum.IN_STOCK),
        ]
        for msg, exp_name, exp_str, exp_qty, exp_form, exp_status in cases:
            with self.subTest(msg=msg):
                prop = parse_stock_message(msg, use_llm=False)
                self.assertIsNotNone(prop, f"Failed to parse packaging for: {msg}")
                assert prop is not None
                item = prop.items[0]
                self.assertEqual(item.medicine_name, exp_name)
                self.assertEqual(item.strength, exp_str)
                self.assertEqual(item.quantity, exp_qty)
                self.assertEqual(item.dosage_form, exp_form)
                self.assertEqual(item.status, exp_status)

    def test_safety_and_ambiguity_rejections(self):
        """Test 18: Strict safety rejections for uncertain, future, and ambiguous messages."""
        ineligible_cases = [
            # Uncertain claims
            "shayad 10 bache hain",
            "maybe 5 strips left",
            "Crocin lagta hai khatam hai",
            "pakka nahi pata Dolo",
            # Future deliveries
            "kal 50 strips aayenge",
            "tomorrow 20 boxes expected",
            "shaam ko naya stock aayega",
            # Patient inquiries
            "Dolo milega kya?",
            "Crocin available hai kya?",
            "Bhaiya Paracetamol hai kya",
            # Price questions
            "Dolo ka rate kya hai?",
            "Crocin price kitna hai",
            "Cost kitna hai dawa ka?",
            # Ambiguous / non-medicine inputs
            "12345 20 strips available",
            "?? 10 strips",
        ]
        for msg in ineligible_cases:
            with self.subTest(msg=msg):
                res = parse_stock_message(msg, use_llm=False)
                self.assertIsNone(res, f"Expected rejection for: '{msg}'")

    def test_out_of_stock_conflicting_quantity_cleared(self):
        """Test 19: OUT_OF_STOCK with contradictory non-zero quantity clears quantity to None."""
        cases = [
            ("Crocin 20 strips out of stock", "Crocin", "Strip"),
            ("Paracetamol 15 tablets khatam", "Paracetamol", "Tablet"),
        ]
        for msg, exp_name, exp_form in cases:
            with self.subTest(msg=msg):
                prop = parse_stock_message(msg, use_llm=False)
                self.assertIsNotNone(prop)
                assert prop is not None
                item = prop.items[0]
                self.assertEqual(item.medicine_name, exp_name)
                self.assertEqual(item.status, StockActionEnum.OUT_OF_STOCK)
                self.assertIsNone(item.quantity, "Non-zero quantity must be cleared for OUT_OF_STOCK")
                self.assertEqual(item.dosage_form, exp_form)


if __name__ == "__main__":
    unittest.main()
