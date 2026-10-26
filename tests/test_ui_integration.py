"""Integration tests verifying Member 3's Telegram bot and Streamlit dashboard
against the real SQLite database and backend services layer.
"""

import os
import sqlite3
import unittest

# import bot  # Disabled: bot.py is commented out and no longer used in project
import mock_db
from medibridge.schemas.stock import StockActionEnum
from medibridge.services.proposal_service import ProposalServiceStatus
from medibridge_db.models import (
    HistoryChangeType,
    Inventory,
    Medicine,
    Pharmacy,
    ProposalStatus,
    StockHistory,
    StockStatus,
    User,
)


class TestUIIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mock_db.init_db()

    def test_mock_db_search_medicine_with_substitutes(self):
        """Verify search_medicine finds both direct matches and therapeutic substitutes."""
        results = mock_db.search_medicine("Dolo 650")
        self.assertGreater(len(results), 0)

        required_keys = {
            "pharmacy_name",
            "locality",
            "lat",
            "lng",
            "medicine_name",
            "salt",
            "is_available",
            "last_updated",
            "is_substitute",
            "substitute_note",
        }
        for item in results:
            self.assertTrue(required_keys.issubset(item.keys()))
            self.assertIsInstance(item["is_available"], bool)
            self.assertIsInstance(item["is_substitute"], bool)

        # Check that both direct brand and substitutes exist
        direct_matches = [r for r in results if not r["is_substitute"]]
        substitutes = [r for r in results if r["is_substitute"]]

        self.assertGreater(len(direct_matches), 0)
        self.assertTrue(any("Dolo" in r["medicine_name"] for r in direct_matches))

        # Paracetamol substitutes should be found (e.g. Calpol or Crocin)
        self.assertGreater(len(substitutes), 0)
        self.assertTrue(any("Paracetamol" in r["salt"] for r in substitutes))

    def test_mock_db_update_stock_audited(self):
        """Verify update_stock modifies inventory and logs to StockHistory."""
        # Mark Dolo 650 at pharmacy 1 out of stock
        ok = mock_db.update_stock(1, "Dolo 650", False)
        self.assertTrue(ok)

        with mock_db.get_session() as session:
            med = session.query(Medicine).filter_by(brand_name="Dolo 650").first()
            inv = session.query(Inventory).filter_by(pharmacy_id=1, medicine_id=med.id).first()
            self.assertEqual(inv.stock_status, StockStatus.out_of_stock)
            self.assertEqual(inv.quantity, 0)

            # Verify audit trail
            hist = (
                session.query(StockHistory)
                .filter_by(pharmacy_id=1, medicine_id=med.id)
                .order_by(StockHistory.id.desc())
                .first()
            )
            self.assertIsNotNone(hist)
            self.assertEqual(hist.change_type, HistoryChangeType.stock_update)
            self.assertEqual(hist.new_stock_status, StockStatus.out_of_stock)

    def test_dashboard_sql_queries_against_database(self):
        """Verify dashboard SQL queries run without error and return expected schema columns."""
        db_path = mock_db.DB_PATH
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Query used in medibridge_dashboard.py
        query_erp = """
            SELECT
                p.name AS "Pharmacy",
                p.locality AS "Locality",
                m.brand_name AS "Medicine Name",
                m.generic_name AS "Salt Formulation",
                CASE
                    WHEN i.stock_status = 'IN_STOCK' THEN 'In Stock'
                    WHEN i.stock_status = 'LOW_STOCK' THEN 'Low Stock'
                    ELSE 'Out of Stock'
                END AS "Status",
                coalesce(i.last_confirmed_at, i.updated_at, i.created_at) AS "Last Verified"
            FROM inventory i
            JOIN pharmacies p ON i.pharmacy_id = p.id
            JOIN medicines m ON i.medicine_id = m.id
            ORDER BY m.brand_name ASC, p.name ASC;
        """
        cursor.execute(query_erp)
        rows_erp = cursor.fetchall()
        self.assertGreater(len(rows_erp), 0)
        col_names_erp = [desc[0] for desc in cursor.description]
        self.assertEqual(
            col_names_erp,
            ["Pharmacy", "Locality", "Medicine Name", "Salt Formulation", "Status", "Last Verified"],
        )

        # Query used in dashboard.py
        query_simple = """
            SELECT
                p.name AS "Pharmacy",
                p.locality AS "Locality",
                m.brand_name AS "Medicine",
                m.generic_name AS "Salt Formulation",
                CASE
                    WHEN i.stock_status = 'IN_STOCK' THEN '?? IN STOCK'
                    WHEN i.stock_status = 'LOW_STOCK' THEN '?? LOW STOCK'
                    ELSE '?? OUT OF STOCK'
                END AS "Live Status",
                coalesce(i.last_confirmed_at, i.updated_at, i.created_at) AS "Last Verified"
            FROM inventory i
            JOIN pharmacies p ON i.pharmacy_id = p.id
            JOIN medicines m ON i.medicine_id = m.id
            ORDER BY m.brand_name ASC, p.name ASC;
        """
        cursor.execute(query_simple)
        rows_simple = cursor.fetchall()
        self.assertGreater(len(rows_simple), 0)
        col_names_simple = [desc[0] for desc in cursor.description]
        self.assertEqual(
            col_names_simple,
            ["Pharmacy", "Locality", "Medicine", "Salt Formulation", "Live Status", "Last Verified"],
        )
        conn.close()

    # Legacy bot integration tests disabled because bot.py is commented out
    # def test_bot_chemist_message_proposal_creation(self):
    #     user_id, pharmacy_id, pharmacy_name = bot.resolve_chemist_context(200001)
    #     ...
    # def test_bot_proposal_confirmation_and_undo(self):
    #     ...
    # def test_bot_proposal_rejection(self):
    #     ...
    # def test_bot_unauthorized_user_rejection(self):
    #     ...


if __name__ == "__main__":
    unittest.main()
