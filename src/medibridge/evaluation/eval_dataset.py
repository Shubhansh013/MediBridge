"""Fixed evaluation dataset for MediBridge natural-language stock message interpretation.

Covers English, Hindi, and Hinglish across:
- In-stock, low-stock, and out-of-stock messages.
- Numeric and word-based quantities.
- Common packaging variations (strips, patte, boxes, dabbe, bottles, shishi, tablets, goli, vials).
- Strength versus stock quantity disambiguation.
- Ineligible messages: uncertainty, future availability, price queries, conversational greetings.
- Unknown medicines, ambiguous catalogue candidates, and strength conflicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from medibridge.schemas.stock import StockActionEnum


@dataclass(frozen=True)
class EvalTestCase:
    id: str
    message: str
    category: str
    language: str
    expected_to_parse: bool
    expected_route: str  # "deterministic" | "llm_fallback" | "rejected"
    expected_medicine_name: Optional[str] = None
    expected_status: Optional[StockActionEnum] = None
    expected_quantity: Optional[int] = None
    expected_dosage_form: Optional[str] = None
    expected_strength: Optional[str] = None
    expected_catalogue_status: str = "not_applicable"  # "resolved" | "unresolved" | "not_applicable"
    expected_flagged_for_review: Optional[bool] = None
    mock_llm_response: Optional[dict] = None


def get_evaluation_dataset() -> list[EvalTestCase]:
    """Returns the benchmark evaluation dataset of 45 diverse, labelled test cases."""
    return [
        # ---------------------------------------------------------------------
        # 1. Standard In-Stock (Deterministic, English & Hinglish)
        # ---------------------------------------------------------------------
        EvalTestCase(
            id="in_stock_en_01",
            message="Dolo 650 20 strips available",
            category="standard_in_stock",
            language="English",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Dolo 650",
            expected_status=StockActionEnum.IN_STOCK,
            expected_quantity=20,
            expected_dosage_form="Strip",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
        ),
        EvalTestCase(
            id="in_stock_en_02",
            message="Crocin 10 boxes in stock",
            category="standard_in_stock",
            language="English",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Crocin",
            expected_status=StockActionEnum.IN_STOCK,
            expected_quantity=10,
            expected_dosage_form="Box",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
        ),
        EvalTestCase(
            id="in_stock_en_03",
            message="Paracetamol 50 tablets available",
            category="standard_in_stock",
            language="English",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Paracetamol",
            expected_status=StockActionEnum.IN_STOCK,
            expected_quantity=50,
            expected_dosage_form="Tablet",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
        ),
        EvalTestCase(
            id="in_stock_en_04",
            message="Cough Syrup 2 bottles in stock",
            category="standard_in_stock",
            language="English",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Cough Syrup",
            expected_status=StockActionEnum.IN_STOCK,
            expected_quantity=2,
            expected_dosage_form="Bottle",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
        ),
        EvalTestCase(
            id="in_stock_hi_01",
            message="Dolo 650 ke 20 strips bache hain",
            category="standard_in_stock",
            language="Hinglish",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Dolo 650",
            expected_status=StockActionEnum.IN_STOCK,
            expected_quantity=20,
            expected_dosage_form="Strip",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
        ),
        EvalTestCase(
            id="in_stock_hi_02",
            message="Crocin ke 5 dabbe available hain",
            category="standard_in_stock",
            language="Hinglish",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Crocin",
            expected_status=StockActionEnum.IN_STOCK,
            expected_quantity=5,
            expected_dosage_form="Box",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
        ),
        EvalTestCase(
            id="in_stock_hi_03",
            message="Paracetamol 15 patte uplabdh hai",
            category="standard_in_stock",
            language="Hinglish",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Paracetamol",
            expected_status=StockActionEnum.IN_STOCK,
            expected_quantity=15,
            expected_dosage_form="Strip",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
        ),
        EvalTestCase(
            id="in_stock_hi_04",
            message="Azithromycin 30 goliya hai",
            category="standard_in_stock",
            language="Hinglish",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Azithromycin",
            expected_status=StockActionEnum.IN_STOCK,
            expected_quantity=30,
            expected_dosage_form="Tablet",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=True,
        ),
        EvalTestCase(
            id="in_stock_hi_05",
            message="Eye Drops 3 shishi rakha hai",
            category="standard_in_stock",
            language="Hinglish",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Eye Drops",
            expected_status=StockActionEnum.IN_STOCK,
            expected_quantity=3,
            expected_dosage_form="Bottle",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
        ),
        EvalTestCase(
            id="in_stock_hi_06",
            message="Insulin 4 vials present",
            category="standard_in_stock",
            language="English",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Insulin",
            expected_status=StockActionEnum.IN_STOCK,
            expected_quantity=4,
            expected_dosage_form="Injection",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
        ),

        # ---------------------------------------------------------------------
        # 2. Word-Based Quantities & Phrasing (Eligible Misses -> LLM Fallback)
        # ---------------------------------------------------------------------
        EvalTestCase(
            id="word_qty_en_01",
            message="Only five boxes of Crocin remain",
            category="word_quantities",
            language="English",
            expected_to_parse=True,
            expected_route="llm_fallback",
            expected_medicine_name="Crocin",
            expected_status=StockActionEnum.LOW_STOCK,
            expected_quantity=5,
            expected_dosage_form="Box",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
            mock_llm_response={
                "items": [{"medicine_name": "Crocin", "status": "LOW_STOCK", "quantity": 5, "dosage_form": "Box"}]
            },
        ),
        EvalTestCase(
            id="word_qty_en_02",
            message="Received twenty-five strips of Dolo 650",
            category="word_quantities",
            language="English",
            expected_to_parse=True,
            expected_route="llm_fallback",
            expected_medicine_name="Dolo 650",
            expected_status=StockActionEnum.IN_STOCK,
            expected_quantity=25,
            expected_dosage_form="Strip",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
            mock_llm_response={
                "items": [{"medicine_name": "Dolo 650", "status": "IN_STOCK", "quantity": 25, "dosage_form": "Strip"}]
            },
        ),
        EvalTestCase(
            id="word_qty_hi_01",
            message="Dolo 650 ke pachis strips aaye hain",
            category="word_quantities",
            language="Hinglish",
            expected_to_parse=True,
            expected_route="llm_fallback",
            expected_medicine_name="Dolo 650",
            expected_status=StockActionEnum.IN_STOCK,
            expected_quantity=25,
            expected_dosage_form="Strip",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
            mock_llm_response={
                "items": [{"medicine_name": "Dolo 650", "status": "IN_STOCK", "quantity": 25, "dosage_form": "Strip"}]
            },
        ),
        EvalTestCase(
            id="word_qty_en_03",
            message="Calpol ten bottles available",
            category="word_quantities",
            language="English",
            expected_to_parse=True,
            expected_route="llm_fallback",
            expected_medicine_name="Calpol",
            expected_status=StockActionEnum.IN_STOCK,
            expected_quantity=10,
            expected_dosage_form="Bottle",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
            mock_llm_response={
                "items": [{"medicine_name": "Calpol", "status": "IN_STOCK", "quantity": 10, "dosage_form": "Bottle"}]
            },
        ),

        # ---------------------------------------------------------------------
        # 3. Low-Stock Semantics
        # ---------------------------------------------------------------------
        EvalTestCase(
            id="low_stock_01",
            message="Azithromycin 500mg low stock 3 strips",
            category="low_stock",
            language="English",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Azithromycin",
            expected_status=StockActionEnum.LOW_STOCK,
            expected_quantity=3,
            expected_dosage_form="Strip",
            expected_strength="500mg",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=True,
        ),
        EvalTestCase(
            id="low_stock_02",
            message="Dolo 650 kam hai only 5 left",
            category="low_stock",
            language="Hinglish",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Dolo 650",
            expected_status=StockActionEnum.LOW_STOCK,
            expected_quantity=5,
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
        ),
        EvalTestCase(
            id="low_stock_03",
            message="Crocin thoda hai sirf 2 patte",
            category="low_stock",
            language="Hinglish",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Crocin",
            expected_status=StockActionEnum.LOW_STOCK,
            expected_quantity=2,
            expected_dosage_form="Strip",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
        ),

        # ---------------------------------------------------------------------
        # 4. Out-of-Stock Negation & Semantics
        # ---------------------------------------------------------------------
        EvalTestCase(
            id="out_of_stock_01",
            message="Crocin Advance khatam",
            category="out_of_stock",
            language="Hinglish",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Crocin Advance",
            expected_status=StockActionEnum.OUT_OF_STOCK,
            expected_quantity=None,
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
        ),
        EvalTestCase(
            id="out_of_stock_02",
            message="Dolo 650 nahi hai",
            category="out_of_stock",
            language="Hinglish",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Dolo 650",
            expected_status=StockActionEnum.OUT_OF_STOCK,
            expected_quantity=None,
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
        ),
        EvalTestCase(
            id="out_of_stock_03",
            message="Paracetamol stock is finished",
            category="out_of_stock",
            language="English",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Paracetamol",
            expected_status=StockActionEnum.OUT_OF_STOCK,
            expected_quantity=None,
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
        ),
        EvalTestCase(
            id="out_of_stock_04",
            message="Paracetamol ka stock khatam hai",
            category="out_of_stock",
            language="Hinglish",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Paracetamol",
            expected_status=StockActionEnum.OUT_OF_STOCK,
            expected_quantity=None,
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
        ),
        EvalTestCase(
            id="out_of_stock_05",
            message="Azithromycin out of stock",
            category="out_of_stock",
            language="English",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Azithromycin",
            expected_status=StockActionEnum.OUT_OF_STOCK,
            expected_quantity=None,
            expected_catalogue_status="resolved",
            expected_flagged_for_review=True,
        ),
        EvalTestCase(
            id="out_of_stock_06",
            message="Dolo 650 0 strips hai",
            category="out_of_stock",
            language="Hinglish",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Dolo 650",
            expected_status=StockActionEnum.OUT_OF_STOCK,
            expected_quantity=0,
            expected_dosage_form="Strip",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
        ),

        # ---------------------------------------------------------------------
        # 5. Contradictory Counts / Cleaned Quantities
        # ---------------------------------------------------------------------
        EvalTestCase(
            id="contradictory_qty_01",
            message="Crocin 20 strips out of stock",
            category="contradictory_quantity",
            language="English",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Crocin",
            expected_status=StockActionEnum.OUT_OF_STOCK,
            expected_quantity=None,  # Contradictory non-zero count must be cleared
            expected_dosage_form="Strip",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
        ),
        EvalTestCase(
            id="contradictory_qty_02",
            message="Paracetamol 10 patte khatam",
            category="contradictory_quantity",
            language="Hinglish",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Paracetamol",
            expected_status=StockActionEnum.OUT_OF_STOCK,
            expected_quantity=None,  # Contradictory non-zero count must be cleared
            expected_dosage_form="Strip",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
        ),

        # ---------------------------------------------------------------------
        # 6. Strength vs Stock Quantity Disambiguation
        # ---------------------------------------------------------------------
        EvalTestCase(
            id="strength_vs_qty_01",
            message="Dolo 650mg 20 strips in stock",
            category="strength_vs_quantity",
            language="English",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Dolo",
            expected_status=StockActionEnum.IN_STOCK,
            expected_quantity=20,  # 20, NOT 650
            expected_dosage_form="Strip",
            expected_strength="650mg",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
        ),
        EvalTestCase(
            id="strength_vs_qty_02",
            message="Paracetamol 500mg 10 tablets available",
            category="strength_vs_quantity",
            language="English",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Paracetamol",
            expected_status=StockActionEnum.IN_STOCK,
            expected_quantity=10,  # 10, NOT 500
            expected_dosage_form="Tablet",
            expected_strength="500mg",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=False,
        ),
        EvalTestCase(
            id="strength_vs_qty_03",
            message="Augmentin 625mg 5 strips present",
            category="strength_vs_quantity",
            language="English",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Augmentin",
            expected_status=StockActionEnum.IN_STOCK,
            expected_quantity=5,  # 5, NOT 625
            expected_dosage_form="Strip",
            expected_strength="625mg",
            expected_catalogue_status="resolved",
            expected_flagged_for_review=True,  # Schedule H
        ),

        # ---------------------------------------------------------------------
        # 7. Uncertainty & Doubt (Safety Guards: MUST Abstain / Return None)
        # ---------------------------------------------------------------------
        EvalTestCase(
            id="uncertainty_01",
            message="Crocin shayad hai",
            category="uncertainty",
            language="Hinglish",
            expected_to_parse=False,
            expected_route="rejected",
        ),
        EvalTestCase(
            id="uncertainty_02",
            message="Shayad Dolo 650 bacha hai",
            category="uncertainty",
            language="Hinglish",
            expected_to_parse=False,
            expected_route="rejected",
        ),
        EvalTestCase(
            id="uncertainty_03",
            message="Dolo maybe available 10 strips",
            category="uncertainty",
            language="English",
            expected_to_parse=False,
            expected_route="rejected",
        ),
        EvalTestCase(
            id="uncertainty_04",
            message="Calpol lagta hai khatam hai",
            category="uncertainty",
            language="Hinglish",
            expected_to_parse=False,
            expected_route="rejected",
        ),
        EvalTestCase(
            id="uncertainty_05",
            message="Azithromycin check karke batata hoon",
            category="uncertainty",
            language="Hinglish",
            expected_to_parse=False,
            expected_route="rejected",
        ),
        EvalTestCase(
            id="uncertainty_06",
            message="Dolo 650 pakka nahi pata",
            category="uncertainty",
            language="Hinglish",
            expected_to_parse=False,
            expected_route="rejected",
        ),

        # ---------------------------------------------------------------------
        # 8. Future Delivery (Safety Guards: MUST Abstain / Return None)
        # ---------------------------------------------------------------------
        EvalTestCase(
            id="future_01",
            message="Calpol kal aayega",
            category="future_delivery",
            language="Hinglish",
            expected_to_parse=False,
            expected_route="rejected",
        ),
        EvalTestCase(
            id="future_02",
            message="Dolo 650 available tomorrow 50 strips",
            category="future_delivery",
            language="English",
            expected_to_parse=False,
            expected_route="rejected",
        ),
        EvalTestCase(
            id="future_03",
            message="Crocin shaam ko aayega",
            category="future_delivery",
            language="Hinglish",
            expected_to_parse=False,
            expected_route="rejected",
        ),
        EvalTestCase(
            id="future_04",
            message="Paracetamol next week expected",
            category="future_delivery",
            language="English",
            expected_to_parse=False,
            expected_route="rejected",
        ),
        EvalTestCase(
            id="future_05",
            message="Parso naya stock aayega",
            category="future_delivery",
            language="Hinglish",
            expected_to_parse=False,
            expected_route="rejected",
        ),

        # ---------------------------------------------------------------------
        # 9. Conversational, Greetings & Inquiries (Safety Guards: MUST Abstain)
        # ---------------------------------------------------------------------
        EvalTestCase(
            id="conversational_01",
            message="Bhaiya rate kya hai?",
            category="price_query",
            language="Hinglish",
            expected_to_parse=False,
            expected_route="rejected",
        ),
        EvalTestCase(
            id="conversational_02",
            message="Hello good morning sir",
            category="conversational",
            language="English",
            expected_to_parse=False,
            expected_route="rejected",
        ),
        EvalTestCase(
            id="conversational_03",
            message="Dolo 650 milega kya?",
            category="patient_inquiry",
            language="Hinglish",
            expected_to_parse=False,
            expected_route="rejected",
        ),
        EvalTestCase(
            id="conversational_04",
            message="Price kitna hai Dolo ka?",
            category="price_query",
            language="Hinglish",
            expected_to_parse=False,
            expected_route="rejected",
        ),
        EvalTestCase(
            id="conversational_05",
            message="Ok done thanks",
            category="conversational",
            language="English",
            expected_to_parse=False,
            expected_route="rejected",
        ),
        EvalTestCase(
            id="conversational_06",
            message="Dawa chahiye urgent",
            category="conversational",
            language="Hinglish",
            expected_to_parse=False,
            expected_route="rejected",
        ),

        # ---------------------------------------------------------------------
        # 10. Catalogue Resolution Safety: Unknown, Ambiguous, Strength Mismatch
        # ---------------------------------------------------------------------
        EvalTestCase(
            id="cat_safety_01",
            message="UnregisteredMedXYZ 10 strips available",
            category="catalogue_safety",
            language="English",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="UnregisteredMedXYZ",
            expected_status=StockActionEnum.IN_STOCK,
            expected_quantity=10,
            expected_dosage_form="Strip",
            expected_catalogue_status="unresolved",  # Unknown medicine -> UNRESOLVED
        ),
        EvalTestCase(
            id="cat_safety_02",
            message="AmbiguousMed 10 strips available",
            category="catalogue_safety",
            language="English",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="AmbiguousMed",
            expected_status=StockActionEnum.IN_STOCK,
            expected_quantity=10,
            expected_dosage_form="Strip",
            expected_catalogue_status="unresolved",  # Ambiguous search -> UNRESOLVED
        ),
        EvalTestCase(
            id="cat_safety_03",
            message="Dolo 650 500mg 20 strips available",
            category="catalogue_safety",
            language="English",
            expected_to_parse=True,
            expected_route="deterministic",
            expected_medicine_name="Dolo",
            expected_status=StockActionEnum.IN_STOCK,
            expected_quantity=20,
            expected_dosage_form="Strip",
            expected_strength="500mg",
            expected_catalogue_status="unresolved",  # 500mg vs catalogue 650mg -> UNRESOLVED
        ),
    ]
