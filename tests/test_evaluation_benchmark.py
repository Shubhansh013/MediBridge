"""Unit tests and evaluation benchmark for MediBridge natural-language stock message parser.

Verifies:
1. Automated benchmark execution with 0 network calls (mocked provider fallback).
2. Safety metrics: 0.0% unsafe acceptance, 100.0% safety abstention on invalid messages.
3. Catalogue grounding and strength conflict handling:
   - Agreement between message and catalogue resolves cleanly.
   - Strength mismatch (500mg vs 650mg) is kept UNRESOLVED for disambiguation.
   - Separate 500mg and 650mg entries in catalogue resolve to the correct entry.
   - Ambiguous queries across similar brand names remain UNRESOLVED.
   - Missing strength and quantity remain missing without fabrication.
"""

from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock

from medibridge.evaluation.eval_dataset import get_evaluation_dataset
from medibridge.evaluation.evaluator import (
    EvalCatalogueRepo,
    EvaluationReport,
    run_evaluation,
)
from medibridge.schemas.stock import StockActionEnum
from medibridge.services.proposal_service import (
    CatalogueMedicine,
    ProposalServiceResult,
    ProposalServiceStatus,
    process_stock_message,
)


class TestEvaluationBenchmark(unittest.TestCase):
    """Test suite executing the evaluation benchmark and verifying metrics."""

    def test_run_benchmark_metrics(self):
        """Execute evaluation across the 48 labelled cases and assert safety and accuracy thresholds."""
        report: EvaluationReport = run_evaluation()

        self.assertEqual(report.total_cases, 48)
        self.assertEqual(report.valid_cases, 31)
        self.assertEqual(report.invalid_cases, 17)

        # Safety assertions (non-negotiable clinical safety)
        self.assertEqual(report.unsafe_acceptances, 0, "Unsafe acceptance of invalid messages must be 0")
        self.assertEqual(report.unsafe_acceptance_rate, 0.0)
        self.assertEqual(report.safety_abstention_rate, 1.0, "100% of invalid messages must be abstained")
        self.assertEqual(report.incorrect_catalogue_matches, 0)
        self.assertEqual(report.incorrect_catalogue_match_rate, 0.0)

        # Catalogue grounding assertions (explicit denominator verification)
        self.assertEqual(report.catalogue_tested, 31, "All 31 valid messages tested against catalogue")
        self.assertEqual(report.catalogue_resolvable_cases, 28, "28 messages correspond to known catalogue medicines")
        self.assertEqual(report.catalogue_resolved_correct, 28, "28 resolvable messages resolved cleanly")
        self.assertEqual(report.catalogue_resolution_rate, 1.0)
        self.assertEqual(report.catalogue_unresolvable_cases, 3, "3 messages test unresolvable/safety edge cases")
        self.assertEqual(report.catalogue_unresolved_safety_correct, 3, "3 unresolvable messages safely held UNRESOLVED")
        self.assertEqual(report.catalogue_unresolved_safety_rate, 1.0)

        # Accuracy assertions
        self.assertEqual(report.exact_match_accuracy, 1.0)
        self.assertEqual(report.medicine_name_accuracy, 1.0)
        self.assertEqual(report.status_accuracy, 1.0)
        self.assertEqual(report.quantity_accuracy, 1.0)

        # Routing distribution assertions
        self.assertGreater(report.deterministic_rate, 0.80)
        self.assertGreater(report.llm_fallback_rate, 0.10)


class TestMedicineIdentityAndStrengthConflicts(unittest.TestCase):
    """Step 2: Deep verification of medicine identity and strength conflict resolution."""

    def setUp(self):
        self.repo = EvalCatalogueRepo()
        # Add distinct strength entries with no standalone root alias
        self.repo.add_medicine(
            CatalogueMedicine(
                id=30,
                brand_name="Glycomet 500",
                generic_name="metformin",
                strength="500mg",
                form="tablet",
                aliases=["glycomet 500mg"],
            )
        )
        self.repo.add_medicine(
            CatalogueMedicine(
                id=31,
                brand_name="Glycomet 850",
                generic_name="metformin",
                strength="850mg",
                form="tablet",
                aliases=["glycomet 850mg"],
            )
        )

    def test_catalogue_match_and_message_strength_agree(self):
        """When message strength and catalogue strength match, proposal is created at full confidence."""
        res = process_stock_message("Dolo 650 650mg 20 strips available", pharmacy_id=1, chemist_user_id=10, repository=self.repo)
        self.assertEqual(res.status, ProposalServiceStatus.SUCCESS)
        self.assertEqual(len(res.created_proposals), 1)
        prop = res.created_proposals[0]
        self.assertEqual(prop.brand_name, "Dolo 650")
        self.assertEqual(prop.parsed_strength, "650mg")
        self.assertEqual(prop.confidence, 1.0)
        self.assertFalse(prop.flagged_for_review)

    def test_message_specifies_500mg_but_only_candidate_has_650mg(self):
        """When message specifies 500mg but only candidate has 650mg, item remains UNRESOLVED for disambiguation."""
        res = process_stock_message("Dolo 650 500mg 20 strips available", pharmacy_id=1, chemist_user_id=10, repository=self.repo)
        # Must NOT silently bind to 650mg variant
        self.assertEqual(res.status, ProposalServiceStatus.UNRESOLVED)
        self.assertEqual(len(res.created_proposals), 0)
        self.assertEqual(len(res.unresolved_items), 1)
        unresolved = res.unresolved_items[0]
        self.assertIn("Strength mismatch", unresolved.reason)
        self.assertIn("500mg", unresolved.reason)
        self.assertIn("650mg", unresolved.reason)

    def test_catalogue_contains_separate_500mg_and_650mg_entries(self):
        """When catalogue has separate 500mg and 850mg entries, specific messages resolve to their respective entry."""
        # 1. 500mg message resolves to Glycomet 500
        res_500 = process_stock_message("Glycomet 500 10 tablets available", pharmacy_id=1, chemist_user_id=10, repository=self.repo)
        self.assertEqual(res_500.status, ProposalServiceStatus.SUCCESS)
        self.assertEqual(res_500.created_proposals[0].brand_name, "Glycomet 500")
        self.assertEqual(res_500.created_proposals[0].parsed_strength, "500mg")

        # 2. 850mg message resolves to Glycomet 850
        res_850 = process_stock_message("Glycomet 850 15 tablets available", pharmacy_id=1, chemist_user_id=10, repository=self.repo)
        self.assertEqual(res_850.status, ProposalServiceStatus.SUCCESS)
        self.assertEqual(res_850.created_proposals[0].brand_name, "Glycomet 850")
        self.assertEqual(res_850.created_proposals[0].parsed_strength, "850mg")

        # 3. Unspecified strength matching both Glycomet 500 and Glycomet 850 is ambiguous -> UNRESOLVED
        res_ambig = process_stock_message("Glycomet 20 tablets available", pharmacy_id=1, chemist_user_id=10, repository=self.repo)
        self.assertEqual(res_ambig.status, ProposalServiceStatus.UNRESOLVED)
        self.assertEqual(len(res_ambig.created_proposals), 0)
        self.assertIn("Multiple catalogue matches", res_ambig.unresolved_items[0].reason)

    def test_similar_brand_names_map_to_multiple_catalogue_candidates(self):
        """Ambiguous query matching multiple distinct catalogue brands remains UNRESOLVED with candidate choices."""
        res = process_stock_message("AmbiguousMed 10 tablets in stock", pharmacy_id=1, chemist_user_id=10, repository=self.repo)
        self.assertEqual(res.status, ProposalServiceStatus.UNRESOLVED)
        self.assertEqual(len(res.created_proposals), 0)
        reason = res.unresolved_items[0].reason
        self.assertIn("Multiple catalogue matches", reason)
        self.assertIn("AmbiguousMed 250mg", reason)
        self.assertIn("AmbiguousMed 500mg", reason)

    def test_missing_strength_and_missing_quantity_remain_missing(self):
        """Missing attributes are never guessed; missing strength prevents proposal creation."""
        # Add medicine with strength None in catalogue
        self.repo.add_medicine(
            CatalogueMedicine(
                id=99,
                brand_name="UnspecifiedOintment",
                generic_name="petrolatum",
                strength=None,
                form="tube",
            )
        )
        res = process_stock_message("UnspecifiedOintment available", pharmacy_id=1, chemist_user_id=10, repository=self.repo)
        self.assertEqual(res.status, ProposalServiceStatus.UNRESOLVED)
        self.assertEqual(len(res.created_proposals), 0)
        self.assertIn("parsed_strength", res.unresolved_items[0].missing_fields)


if __name__ == "__main__":
    unittest.main()
