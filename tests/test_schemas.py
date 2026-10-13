"""Unit tests for MediBridge Pydantic schemas.

Verifies validation rules, constraints, serialization, and edge cases for stock
proposals and patient search queries. Compatible with both pytest and unittest runners.
"""

from datetime import datetime, timezone
import unittest
from pydantic import ValidationError

from medibridge.schemas.search import (
    MedicineSearchQuery,
    PharmacySearchResultItem,
    SearchResponse,
)
from medibridge.schemas.stock import (
    ParsedMedicineStockItem,
    StockActionEnum,
    StockUpdateProposal,
)


class TestSchemas(unittest.TestCase):
    """Test suite covering MediBridge stock and search schemas."""

    def test_valid_in_stock_proposal(self):
        """Test 1: A valid in-stock item and proposal are accepted and whitespace is trimmed."""
        item = ParsedMedicineStockItem(
            medicine_name="  Dolo 650  ",
            status=StockActionEnum.IN_STOCK,
            quantity=25,
            dosage_form="Tablet",
            strength="650mg",
        )
        self.assertEqual(item.medicine_name, "Dolo 650")
        self.assertEqual(item.status, StockActionEnum.IN_STOCK)
        self.assertEqual(item.quantity, 25)
        self.assertEqual(item.dosage_form, "Tablet")
        self.assertEqual(item.strength, "650mg")

        proposal = StockUpdateProposal(
            items=[item],
            raw_message="  Dolo 650 25 strips available  ",
            notes="Parsed successfully",
        )
        self.assertEqual(len(proposal.items), 1)
        self.assertEqual(proposal.raw_message, "Dolo 650 25 strips available")
        self.assertIsNotNone(proposal.proposed_at.tzinfo)

    def test_valid_out_of_stock_proposal_with_no_quantity(self):
        """Test 2: A valid out-of-stock proposal with no quantity is accepted."""
        item = ParsedMedicineStockItem(
            medicine_name="Crocin Advance",
            status=StockActionEnum.OUT_OF_STOCK,
            quantity=None,
        )
        self.assertIsNone(item.quantity)
        self.assertEqual(item.status, StockActionEnum.OUT_OF_STOCK)
        self.assertIsNone(item.dosage_form)
        self.assertIsNone(item.strength)

        proposal = StockUpdateProposal(
            items=[item],
            raw_message="Crocin Advance out of stock",
        )
        self.assertEqual(len(proposal.items), 1)
        self.assertEqual(proposal.items[0].status, StockActionEnum.OUT_OF_STOCK)

    def test_reject_empty_or_whitespace_medicine_name(self):
        """Test 3: Medicine name must not be empty or whitespace-only."""
        with self.assertRaises(ValidationError) as ctx_empty:
            ParsedMedicineStockItem(
                medicine_name="",
                status=StockActionEnum.IN_STOCK,
                quantity=10,
            )
        self.assertIn("Medicine name must not be empty", str(ctx_empty.exception))

        with self.assertRaises(ValidationError) as ctx_ws:
            ParsedMedicineStockItem(
                medicine_name="   \t  ",
                status=StockActionEnum.IN_STOCK,
                quantity=10,
            )
        self.assertIn("Medicine name must not be empty", str(ctx_ws.exception))

    def test_reject_negative_quantity(self):
        """Test 4: Negative stock quantities must be rejected."""
        with self.assertRaises(ValidationError):
            ParsedMedicineStockItem(
                medicine_name="Paracetamol",
                status=StockActionEnum.IN_STOCK,
                quantity=-1,
            )

    def test_reject_invalid_stock_status(self):
        """Test 5: Status values outside StockActionEnum must be rejected."""
        with self.assertRaises(ValidationError):
            ParsedMedicineStockItem(
                medicine_name="Azithromycin",
                status="AVAILABLE",  # type: ignore[arg-type]
                quantity=5,
            )

    def test_json_serialization_and_deserialization(self):
        """Test 6: Proposal correctly serializes to JSON and round-trips without data loss."""
        item = ParsedMedicineStockItem(
            medicine_name="Augmentin 625",
            status=StockActionEnum.LOW_STOCK,
            quantity=3,
            dosage_form="Tablet",
            strength="625mg",
        )
        original_proposal = StockUpdateProposal(
            items=[item],
            raw_message="Augmentin 625 only 3 left",
            notes="Low stock alert",
        )

        json_data = original_proposal.model_dump_json()
        self.assertIn("Augmentin 625", json_data)
        self.assertIn("LOW_STOCK", json_data)

        restored_proposal = StockUpdateProposal.model_validate_json(json_data)
        self.assertEqual(restored_proposal.items[0].medicine_name, "Augmentin 625")
        self.assertEqual(restored_proposal.items[0].status, StockActionEnum.LOW_STOCK)
        self.assertEqual(restored_proposal.items[0].quantity, 3)
        self.assertEqual(restored_proposal.raw_message, original_proposal.raw_message)
        self.assertEqual(restored_proposal.proposed_at, original_proposal.proposed_at)

    def test_valid_patient_search_query(self):
        """Test 7: Valid patient search queries normalize whitespace and handle optional locality."""
        query = MedicineSearchQuery(
            medicine_name="  Metformin  ",
            locality="  Lajpat Nagar  ",
        )
        self.assertEqual(query.medicine_name, "Metformin")
        self.assertEqual(query.locality, "Lajpat Nagar")

        query_no_locality = MedicineSearchQuery(medicine_name="Insulin")
        self.assertEqual(query_no_locality.medicine_name, "Insulin")
        self.assertIsNone(query_no_locality.locality)

        # Empty locality string should normalize to None
        query_blank_locality = MedicineSearchQuery(
            medicine_name="Insulin",
            locality="   ",
        )
        self.assertIsNone(query_blank_locality.locality)

    def test_reject_empty_patient_search_query(self):
        """Test 8: Empty or whitespace-only search queries must be rejected."""
        with self.assertRaises(ValidationError) as ctx_empty:
            MedicineSearchQuery(medicine_name="")
        self.assertIn("Medicine search query must not be empty", str(ctx_empty.exception))

        with self.assertRaises(ValidationError) as ctx_ws:
            MedicineSearchQuery(medicine_name="   \n\t ")
        self.assertIn("Medicine search query must not be empty", str(ctx_ws.exception))

    def test_search_result_item_and_response_validation(self):
        """Test 9: PharmacySearchResultItem and SearchResponse validate timestamps and required fields."""
        now_utc = datetime.now(timezone.utc)
        item = PharmacySearchResultItem(
            pharmacy_id=1,
            pharmacy_name="Apollo Pharmacy",
            address="Shop 4, Market Complex, Sector 14",
            locality="Rohini",
            phone="+91-9876543210",
            medicine_name="Dolo 650",
            generic_name="Paracetamol",
            dosage_form="Tablet",
            strength="650mg",
            status=StockActionEnum.IN_STOCK,
            quantity=50,
            last_confirmed_at=now_utc,
        )

        query = MedicineSearchQuery(medicine_name="Dolo 650", locality="Rohini")
        response = SearchResponse(
            query=query,
            results=[item],
            total_found=1,
        )

        self.assertEqual(response.total_found, 1)
        self.assertEqual(len(response.results), 1)
        self.assertEqual(response.results[0].pharmacy_name, "Apollo Pharmacy")
        self.assertEqual(response.results[0].last_confirmed_at, now_utc)

        # Rejection of invalid item (e.g., negative quantity)
        with self.assertRaises(ValidationError):
            PharmacySearchResultItem(
                pharmacy_id=1,
                pharmacy_name="Apollo Pharmacy",
                address="Shop 4",
                locality="Rohini",
                medicine_name="Dolo 650",
                status=StockActionEnum.IN_STOCK,
                quantity=-10,  # Invalid
            )


if __name__ == "__main__":
    unittest.main()
