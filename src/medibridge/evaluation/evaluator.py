"""Repeatable evaluator for MediBridge natural-language stock message interpretation.

Measures:
- Deterministic-parser success rate and LLM fallback rate.
- Exact match accuracy on valid parseable messages.
- Per-field accuracy (medicine name, status, quantity, dosage form, strength).
- Safety abstention rate on ineligible/conversational/uncertain messages.
- Unsafe acceptance rate (must be 0.0%).
- Catalogue match accuracy, unknown/ambiguous unresolved rate, and incorrect match rate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Optional
from unittest.mock import MagicMock

from medibridge.evaluation.eval_dataset import EvalTestCase, get_evaluation_dataset
from medibridge.schemas.stock import StockActionEnum, StockUpdateProposal
from medibridge.services.ai_parser import (
    parse_stock_message,
    parse_stock_message_deterministic,
)
from medibridge.services.proposal_service import (
    CatalogueMedicine,
    ProposalServiceResult,
    ProposalServiceStatus,
    StoredProposalRecord,
    process_stock_message,
)


class MockHTTPResponse:
    """Mock HTTP response for LLM provider test calls."""

    def __init__(self, status_code: int = 200, json_data: Optional[dict] = None):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = json.dumps(self._json_data)

    def json(self) -> dict:
        return self._json_data


class EvalCatalogueRepo:
    """In-memory catalogue repository for evaluation benchmark."""

    def __init__(self) -> None:
        self.linked_users: set[tuple[int, int]] = {(10, 1)}
        self.medicines: dict[int, CatalogueMedicine] = {}
        self.aliases: dict[str, int] = {}
        self.created_proposals: list[Any] = []
        self._next_id: int = 100

        # Seed standard evaluation catalogue
        self.add_medicine(
            CatalogueMedicine(
                id=1,
                brand_name="Dolo 650",
                generic_name="paracetamol",
                strength="650mg",
                form="strip",
                is_schedule_h=False,
                aliases=["dolo", "dolo650"],
            )
        )
        self.add_medicine(
            CatalogueMedicine(
                id=2,
                brand_name="Crocin",
                generic_name="paracetamol",
                strength="500mg",
                form="box",
                is_schedule_h=False,
                aliases=["crocin advance"],
            )
        )
        self.add_medicine(
            CatalogueMedicine(
                id=3,
                brand_name="Paracetamol",
                generic_name="paracetamol",
                strength="500mg",
                form="tablet",
                is_schedule_h=False,
                aliases=["calpol"],
            )
        )
        self.add_medicine(
            CatalogueMedicine(
                id=4,
                brand_name="Azithromycin",
                generic_name="azithromycin",
                strength="500mg",
                form="strip",
                is_schedule_h=True,
                aliases=["azithral"],
            )
        )
        self.add_medicine(
            CatalogueMedicine(
                id=5,
                brand_name="Augmentin",
                generic_name="amoxicillin and clavulanate",
                strength="625mg",
                form="strip",
                is_schedule_h=True,
                aliases=["augmentin 625"],
            )
        )
        self.add_medicine(
            CatalogueMedicine(
                id=6,
                brand_name="Cough Syrup",
                generic_name="dextromethorphan",
                strength="100ml",
                form="bottle",
                is_schedule_h=False,
                aliases=["syrup"],
            )
        )
        self.add_medicine(
            CatalogueMedicine(
                id=7,
                brand_name="Eye Drops",
                generic_name="carboxymethylcellulose",
                strength="10ml",
                form="bottle",
                is_schedule_h=False,
                aliases=["refresh tears"],
            )
        )
        self.add_medicine(
            CatalogueMedicine(
                id=8,
                brand_name="Insulin",
                generic_name="human insulin",
                strength="40iu/ml",
                form="injection",
                is_schedule_h=False,
                aliases=["huminsulin"],
            )
        )
        # Ambiguous seeds for ambiguity tests
        self.add_medicine(
            CatalogueMedicine(
                id=20,
                brand_name="AmbiguousMed 250mg",
                generic_name="ambiguose",
                strength="250mg",
                form="tablet",
            )
        )
        self.add_medicine(
            CatalogueMedicine(
                id=21,
                brand_name="AmbiguousMed 500mg",
                generic_name="ambiguose",
                strength="500mg",
                form="tablet",
            )
        )

    def add_medicine(self, med: CatalogueMedicine) -> None:
        self.medicines[med.id] = med
        self.aliases[med.brand_name.strip().lower()] = med.id
        for a in med.aliases:
            self.aliases[a.strip().lower()] = med.id

    def is_user_linked_to_pharmacy(self, user_id: int, pharmacy_id: int) -> bool:
        return (user_id, pharmacy_id) in self.linked_users

    def find_medicine_by_alias(self, alias: str) -> Optional[CatalogueMedicine]:
        med_id = self.aliases.get(alias.strip().lower())
        return self.medicines.get(med_id) if med_id is not None else None

    def search_medicines(self, query: str, limit: int = 5) -> list[CatalogueMedicine]:
        q = query.strip().lower()
        res = []
        for m in self.medicines.values():
            if q in m.brand_name.lower() or q in m.generic_name.lower() or any(q in a.lower() for a in m.aliases):
                res.append(m)
                if len(res) >= limit:
                    break
        return res

    def create_stock_proposal(self, pharmacy_id: int, chemist_user_id: int, raw_message: str, parsed: Any, source_message_ref: Optional[str] = None) -> StoredProposalRecord:
        now = datetime.now(timezone.utc)
        record = StoredProposalRecord(
            id=self._next_id,
            pharmacy_id=pharmacy_id,
            chemist_user_id=chemist_user_id,
            raw_message=raw_message,
            brand_name=parsed.brand_name,
            parsed_salt=parsed.parsed_salt,
            parsed_strength=parsed.parsed_strength,
            parsed_form=parsed.parsed_form,
            parsed_quantity=parsed.parsed_quantity,
            parsed_intent=parsed.parsed_intent,
            confidence=parsed.confidence,
            status="PENDING",
            flagged_for_review=parsed.flagged_for_review,
            expires_at=now + timedelta(hours=24),
            created_at=now,
        )
        self.created_proposals.append(record)
        self._next_id += 1
        return record


@dataclass
class EvaluationReport:
    total_cases: int = 0
    valid_cases: int = 0
    invalid_cases: int = 0

    # Routing
    deterministic_successes: int = 0
    llm_fallback_invocations: int = 0

    # Parser accuracy on valid cases
    exact_matches: int = 0
    medicine_name_correct: int = 0
    status_correct: int = 0
    quantity_correct: int = 0
    dosage_form_correct: int = 0
    strength_correct: int = 0

    # Safety metrics on invalid cases
    safety_abstained_correct: int = 0
    unsafe_acceptances: int = 0

    # Catalogue grounding metrics
    catalogue_tested: int = 0
    catalogue_resolvable_cases: int = 0
    catalogue_resolved_correct: int = 0
    catalogue_unresolvable_cases: int = 0
    catalogue_unresolved_safety_correct: int = 0
    incorrect_catalogue_matches: int = 0

    # Category breakdowns
    category_results: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def deterministic_rate(self) -> float:
        return (self.deterministic_successes / self.valid_cases) if self.valid_cases else 0.0

    @property
    def llm_fallback_rate(self) -> float:
        return (self.llm_fallback_invocations / self.valid_cases) if self.valid_cases else 0.0

    @property
    def exact_match_accuracy(self) -> float:
        return (self.exact_matches / self.valid_cases) if self.valid_cases else 0.0

    @property
    def medicine_name_accuracy(self) -> float:
        return (self.medicine_name_correct / self.valid_cases) if self.valid_cases else 0.0

    @property
    def status_accuracy(self) -> float:
        return (self.status_correct / self.valid_cases) if self.valid_cases else 0.0

    @property
    def quantity_accuracy(self) -> float:
        return (self.quantity_correct / self.valid_cases) if self.valid_cases else 0.0

    @property
    def safety_abstention_rate(self) -> float:
        return (self.safety_abstained_correct / self.invalid_cases) if self.invalid_cases else 0.0

    @property
    def unsafe_acceptance_rate(self) -> float:
        return (self.unsafe_acceptances / self.invalid_cases) if self.invalid_cases else 0.0

    @property
    def catalogue_resolution_rate(self) -> float:
        return (self.catalogue_resolved_correct / self.catalogue_resolvable_cases) if self.catalogue_resolvable_cases else 0.0

    @property
    def catalogue_unresolved_safety_rate(self) -> float:
        return (self.catalogue_unresolved_safety_correct / self.catalogue_unresolvable_cases) if self.catalogue_unresolvable_cases else 0.0

    @property
    def incorrect_catalogue_match_rate(self) -> float:
        return (self.incorrect_catalogue_matches / self.catalogue_tested) if self.catalogue_tested else 0.0

    def summary_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "valid_cases": self.valid_cases,
            "invalid_cases": self.invalid_cases,
            "deterministic_success_rate": f"{self.deterministic_rate:.1%}",
            "llm_fallback_rate": f"{self.llm_fallback_rate:.1%}",
            "exact_match_accuracy": f"{self.exact_match_accuracy:.1%}",
            "medicine_name_accuracy": f"{self.medicine_name_accuracy:.1%}",
            "status_accuracy": f"{self.status_accuracy:.1%}",
            "quantity_accuracy": f"{self.quantity_accuracy:.1%}",
            "safety_abstention_rate": f"{self.safety_abstained_correct}/{self.invalid_cases} ({self.safety_abstention_rate:.1%})",
            "unsafe_acceptance_rate": f"{self.unsafe_acceptances}/{self.invalid_cases} ({self.unsafe_acceptance_rate:.1%})",
            "catalogue_tested": self.catalogue_tested,
            "catalogue_resolvable_rate": f"{self.catalogue_resolved_correct}/{self.catalogue_resolvable_cases} ({self.catalogue_resolution_rate:.1%})",
            "catalogue_unresolved_safety_rate": f"{self.catalogue_unresolved_safety_correct}/{self.catalogue_unresolvable_cases} ({self.catalogue_unresolved_safety_rate:.1%})",
            "incorrect_catalogue_match_rate": f"{self.incorrect_catalogue_matches}/{self.catalogue_tested} ({self.incorrect_catalogue_match_rate:.1%})",
        }


def run_evaluation(dataset: Optional[list[EvalTestCase]] = None) -> EvaluationReport:
    """Executes the benchmark evaluation over the labelled dataset using mocked LLM calls."""
    cases = dataset if dataset is not None else get_evaluation_dataset()
    report = EvaluationReport(total_cases=len(cases))
    repo = EvalCatalogueRepo()

    for tc in cases:
        cat_stats = report.category_results.setdefault(tc.category, {"total": 0, "correct": 0})
        cat_stats["total"] += 1

        # Check deterministic behavior first
        det_res = parse_stock_message_deterministic(tc.message)

        mock_client = None
        if tc.expected_route == "llm_fallback" and tc.mock_llm_response:
            mock_client = MagicMock()
            mock_client.post.return_value = MockHTTPResponse(
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(tc.mock_llm_response),
                            },
                            "finish_reason": "stop",
                        }
                    ]
                },
            )

        # Full pipeline parser call (mocked LLM if fallback required)
        proposal = parse_stock_message(
            tc.message,
            use_llm=True,
            api_key="gsk_eval_mock_key" if tc.expected_route == "llm_fallback" else None,
            http_client=mock_client,
        )

        # -------------------------------------------------------------
        # Evaluation Logic
        # -------------------------------------------------------------
        if tc.expected_to_parse:
            report.valid_cases += 1
            if proposal is None or not proposal.items:
                # Falsely abstained on a valid message
                continue

            item = proposal.items[0]

            # Route evaluation
            if det_res is not None and det_res.items:
                report.deterministic_successes += 1
            elif mock_client is not None and mock_client.post.called:
                report.llm_fallback_invocations += 1

            # Field-level accuracy checks
            med_match = False
            if tc.expected_medicine_name:
                med_match = tc.expected_medicine_name.lower() in item.medicine_name.lower()
                if med_match:
                    report.medicine_name_correct += 1

            status_match = False
            if tc.expected_status:
                status_match = item.status == tc.expected_status
                if status_match:
                    report.status_correct += 1

            qty_match = False
            if tc.expected_quantity is not None:
                qty_match = item.quantity == tc.expected_quantity
            else:
                qty_match = item.quantity is None
            if qty_match:
                report.quantity_correct += 1

            form_match = True
            if tc.expected_dosage_form:
                form_match = (item.dosage_form or "").lower() == tc.expected_dosage_form.lower()
                if form_match:
                    report.dosage_form_correct += 1
            elif item.dosage_form is None:
                report.dosage_form_correct += 1

            strength_match = True
            if tc.expected_strength:
                strength_match = (item.strength or "").lower() == tc.expected_strength.lower()
                if strength_match:
                    report.strength_correct += 1
            elif item.strength is None:
                report.strength_correct += 1

            # Overall exact match for the case
            if med_match and status_match and qty_match:
                report.exact_matches += 1
                cat_stats["correct"] += 1

            # Catalogue Grounding evaluation
            if tc.expected_catalogue_status in ("resolved", "unresolved"):
                report.catalogue_tested += 1
                cat_result: ProposalServiceResult = process_stock_message(
                    raw_message=tc.message,
                    pharmacy_id=1,
                    chemist_user_id=10,
                    repository=repo,
                    use_llm=True,
                    api_key="gsk_eval_mock_key" if tc.expected_route == "llm_fallback" else None,
                    http_client=mock_client,
                )
                if tc.expected_catalogue_status == "resolved":
                    report.catalogue_resolvable_cases += 1
                    if cat_result.status == ProposalServiceStatus.SUCCESS and len(cat_result.created_proposals) >= 1:
                        report.catalogue_resolved_correct += 1
                        created = cat_result.created_proposals[0]
                        if tc.expected_flagged_for_review is not None:
                            if created.flagged_for_review != tc.expected_flagged_for_review:
                                report.incorrect_catalogue_matches += 1
                    else:
                        report.incorrect_catalogue_matches += 1
                elif tc.expected_catalogue_status == "unresolved":
                    report.catalogue_unresolvable_cases += 1
                    if cat_result.status == ProposalServiceStatus.UNRESOLVED and len(cat_result.unresolved_items) >= 1:
                        report.catalogue_unresolved_safety_correct += 1
                    else:
                        report.incorrect_catalogue_matches += 1

        else:
            # Invalid or ineligible message (uncertain, future, conversational)
            report.invalid_cases += 1
            if proposal is None:
                report.safety_abstained_correct += 1
                cat_stats["correct"] += 1
            else:
                # Critical safety error: accepted an ineligible message
                report.unsafe_acceptances += 1

    return report


if __name__ == "__main__":
    rep = run_evaluation()
    print("=" * 60)
    print("MediBridge AI Stock-Message Parser Evaluation Benchmark")
    print("=" * 60)
    for k, v in rep.summary_dict().items():
        print(f"  {k:35s}: {v}")
    print("\nCategory Breakdown:")
    for cat, stats in sorted(rep.category_results.items()):
        total = stats["total"]
        corr = stats["correct"]
        pct = (corr / total * 100) if total else 0.0
        print(f"  {cat:35s}: {corr}/{total} ({pct:.1f}%)")
    print("=" * 60)
