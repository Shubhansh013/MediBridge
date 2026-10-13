"""Deterministic offline stock message parser for MediBridge.

Parses natural-language inventory updates sent by chemists in English,
Hindi, and Hinglish into structured Pydantic StockUpdateProposal objects.

This parser operates 100% offline with zero external network dependencies,
guaranteeing fast and deterministic behavior for testing and live hackathon demos.
"""

from __future__ import annotations

import re
from typing import Optional

from medibridge.schemas.stock import (
    ParsedMedicineStockItem,
    StockActionEnum,
    StockUpdateProposal,
)

# -----------------------------------------------------------------------------
# Regex Patterns for Uncertainty, Future Availability, and Conversational Text
# -----------------------------------------------------------------------------

# Explicit markers of doubt or unconfirmed status. Must NEVER be treated as stock.
_UNCERTAIN_WORDS = re.compile(
    r"\b("
    r"shayad|maybe|probably|lagta\s+hai|not\s+sure|doubt|might|"
    r"check\s+karke|confirm\s+nahi|pakka\s+nahi|dekhna\s+padega"
    r")\b",
    re.IGNORECASE,
)

# Markers of future or incoming stock. Must NEVER be treated as current inventory.
_FUTURE_WORDS = re.compile(
    r"\b("
    r"kal|tomorrow|parso|aayega|aayegi|aayenge|aane\s+wala|will\s+come|"
    r"will\s+arrive|expected|next\s+week|shaam\s+ko|evening\s+me|subah\s+ko"
    r")\b",
    re.IGNORECASE,
)

# Conversational greetings, inquiries, or pricing questions.
_CONVERSATIONAL_WORDS = re.compile(
    r"(\?|"
    r"\b(rate|price|cost|chahiye|kitna|kaise|kya|kab|when|how|"
    r"hello|hi|hey|good\s+morning|good\s+evening|thanks|shukriya|"
    r"milega\s+kya|available\s+hai\s+kya|hai\s+kya|bhaiya|sir|ji|ok|okay|done)\b)",
    re.IGNORECASE,
)

# -----------------------------------------------------------------------------
# Regex Patterns for Medical Attributes and Stock Status
# -----------------------------------------------------------------------------

# Medical strength / potency (e.g. '650mg', '500 mg', '5ml', '1g').
# Always extracted separately so numbers like '650' are never mistaken for stock count.
_STRENGTH_PATTERN = re.compile(
    r"\b(?P<strength>\d+(?:\.\d+)?\s*(?:mg|g|ml|mcg|iu|%))\b",
    re.IGNORECASE,
)

# Quantities accompanied by explicit packaging / dosage units.
_QTY_WITH_UNIT_PATTERN = re.compile(
    r"\b(?P<qty>\d+)\s*"
    r"(?P<unit>strips?|stripes?|patte?|patta|tablets?|tabs?|goli(?:ya|yan)?|"
    r"bottles?|shishi|vials?|boxes?|packs?|packets?|capsules?|caps?)\b",
    re.IGNORECASE,
)

# Standalone numbers adjacent to availability keywords (e.g. '20 available', 'only 5 left').
_QTY_BEFORE_STATUS = re.compile(
    r"\b(?P<qty>\d+)\s*(?:available|in\s+stock|hai|uplabdh|left|bacha|bache)\b",
    re.IGNORECASE,
)
_QTY_AFTER_STATUS = re.compile(
    r"\b(?:available|in\s+stock|hai|uplabdh|left|bacha|bache|count|quantity:?)\s*(?P<qty>\d+)\b",
    re.IGNORECASE,
)

# Stock status keywords ordered by specificity:
# Out of stock (checked first so 'available nahi hai' is recognized as OUT_OF_STOCK)
_OUT_KEYWORDS = re.compile(
    r"\b(khatam|khtm|nahi\s+hai|nahin\s+hai|naahi\s+hai|out\s+of\s+stock|"
    r"not\s+available|nil|zero\s+stock|finish|stock\s+out)\b",
    re.IGNORECASE,
)

# Low stock keywords
_LOW_KEYWORDS = re.compile(
    r"\b(low\s+stock|kam\s+hai|thoda\s+hai|kam\s+bacha|few\s+left|only|sirf)\b",
    re.IGNORECASE,
)

# In stock keywords
_IN_KEYWORDS = re.compile(
    r"\b(available|in\s+stock|in-stock|hai|uplabdh|mil\s+jayega|present|rakha\s+hai|stock\s+me)\b",
    re.IGNORECASE,
)

# Multi-segment delimiters (commas, semicolons, newlines, conjunctions)
_SEGMENT_DELIMITERS = re.compile(
    r"[;,\n\r]+|\b(?:aur|and|\+)\b|&",
    re.IGNORECASE,
)

# Phrases and filler words to clean out when isolating the medicine name
_STATUS_PHRASES = re.compile(
    r"\b(in\s+stock|out\s+of\s+stock|not\s+available|available\s+nahi|"
    r"nahi\s+hai|nahin\s+hai|kam\s+hai|thoda\s+hai|low\s+stock)\b",
    re.IGNORECASE,
)

_FILLER_WORDS = re.compile(
    r"\b(in|of|ke|ka|ki|ko|se|bhi|mein|me|par|pe|bhaiya|sir|ji|hain?|"
    r"tha|thi|the|bache?|bacha|left|stock|only|sirf|khatam|khtm|nahi|"
    r"nahin|available|uplabdh|present|count|quantity)\b",
    re.IGNORECASE,
)

# Mapping of raw informal unit words to standardized dosage forms
_DOSAGE_FORM_MAP: dict[str, str] = {
    "strip": "Strip",
    "strips": "Strip",
    "stripes": "Strip",
    "patte": "Strip",
    "patta": "Strip",
    "tablet": "Tablet",
    "tablets": "Tablet",
    "tab": "Tablet",
    "tabs": "Tablet",
    "goli": "Tablet",
    "goliya": "Tablet",
    "goliyan": "Tablet",
    "capsule": "Capsule",
    "capsules": "Capsule",
    "caps": "Capsule",
    "bottle": "Bottle",
    "bottles": "Bottle",
    "shishi": "Bottle",
    "vial": "Injection",
    "vials": "Injection",
    "box": "Box",
    "boxes": "Box",
    "pack": "Pack",
    "packs": "Pack",
    "packet": "Pack",
    "packets": "Pack",
}


def _split_into_segments(text: str) -> list[str]:
    """Split a compound message into individual potential medicine segments."""
    parts = _SEGMENT_DELIMITERS.split(text)
    return [p.strip() for p in parts if p.strip()]


def _clean_medicine_name(raw_text: str) -> str:
    """Strip status phrases, filler words, and excess whitespace to isolate medicine name."""
    text = _STATUS_PHRASES.sub(" ", raw_text)
    text = _FILLER_WORDS.sub(" ", text)
    # Remove leftover punctuation except internal hyphens (like 'A-Z')
    text = re.sub(r"[^\w\s\-]", " ", text)
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned


def _parse_segment(segment: str) -> Optional[ParsedMedicineStockItem]:
    """Parse a single message segment into a ParsedMedicineStockItem.

    Returns None if the segment is uncertain, future-oriented, conversational,
    or does not contain a recognizable medicine and status.
    """
    seg = segment.strip()
    if not seg:
        return None

    # Safety Rule: Reject uncertainty, future delivery, and questions
    if _UNCERTAIN_WORDS.search(seg) or _FUTURE_WORDS.search(seg) or _CONVERSATIONAL_WORDS.search(seg):
        return None

    # 1. Determine stock action status (negation checked first)
    status: Optional[StockActionEnum] = None
    if _OUT_KEYWORDS.search(seg):
        status = StockActionEnum.OUT_OF_STOCK
    elif _LOW_KEYWORDS.search(seg):
        status = StockActionEnum.LOW_STOCK
    elif _IN_KEYWORDS.search(seg):
        status = StockActionEnum.IN_STOCK

    # 2. Extract medical strength (e.g. '650mg') and remove from text to avoid false quantities
    strength: Optional[str] = None
    strength_match = _STRENGTH_PATTERN.search(seg)
    if strength_match:
        strength = strength_match.group("strength").strip()
        seg_without_strength = _STRENGTH_PATTERN.sub(" ", seg)
    else:
        seg_without_strength = seg

    # 3. Extract quantity and dosage form
    quantity: Optional[int] = None
    dosage_form: Optional[str] = None
    seg_for_name = seg_without_strength

    qty_unit_match = _QTY_WITH_UNIT_PATTERN.search(seg_without_strength)
    if qty_unit_match:
        quantity = int(qty_unit_match.group("qty"))
        unit_text = qty_unit_match.group("unit").lower()
        dosage_form = _DOSAGE_FORM_MAP.get(unit_text)
        seg_for_name = (
            seg_without_strength[: qty_unit_match.start()]
            + " "
            + seg_without_strength[qty_unit_match.end() :]
        )
    else:
        # Check standalone quantity adjacent to status keywords
        qty_before = _QTY_BEFORE_STATUS.search(seg_without_strength)
        qty_after = _QTY_AFTER_STATUS.search(seg_without_strength)
        target_qty_match = qty_before or qty_after
        if target_qty_match:
            quantity = int(target_qty_match.group("qty"))
            seg_for_name = (
                seg_without_strength[: target_qty_match.start()]
                + " "
                + seg_without_strength[target_qty_match.end() :]
            )

    # 4. Infer status if quantity with unit was provided without explicit status word
    if status is None and quantity is not None and quantity > 0:
        status = StockActionEnum.IN_STOCK

    # If quantity is explicitly 0, force status to OUT_OF_STOCK and preserve quantity=0
    if quantity == 0:
        status = StockActionEnum.OUT_OF_STOCK

    # Reject if no status could be verified
    if status is None:
        return None

    # For OUT_OF_STOCK, clear non-zero quantity to prevent contradictory claims
    if status == StockActionEnum.OUT_OF_STOCK and quantity is not None and quantity > 0:
        quantity = None

    # 5. Extract and validate clean medicine name
    med_name = _clean_medicine_name(seg_for_name)

    # Reject empty, single-character, or digits-only medicine names
    if not med_name or len(med_name) < 2 or med_name.isdigit():
        return None

    try:
        return ParsedMedicineStockItem(
            medicine_name=med_name,
            status=status,
            quantity=quantity,
            dosage_form=dosage_form,
            strength=strength,
        )
    except Exception:
        # Failsafe: if data violates Pydantic constraints, reject segment safely
        return None


def parse_stock_message(message: str) -> Optional[StockUpdateProposal]:
    """Parse a natural-language stock message into an unconfirmed StockUpdateProposal.

    Evaluates chemist messages in English, Hindi, and Hinglish. Supports multi-medicine
    updates, distinguishes medicine strength from inventory quantity, and strictly
    rejects uncertain, future-oriented, or conversational messages.

    Args:
        message: The raw text string received from the chemist.

    Returns:
        A validated StockUpdateProposal if one or more valid medicine updates were extracted;
        None if the message is empty, conversational, ambiguous, future-oriented, or unparseable.

    Raises:
        TypeError: If message is not a string.

    Note:
        This function operates purely in memory. It NEVER modifies confirmed inventory
        or interacts with the database.
    """
    if not isinstance(message, str):
        raise TypeError("Message must be a string.")

    cleaned_input = message.strip()
    if not cleaned_input:
        return None

    # Split message into individual candidate segments (e.g. for multi-item updates)
    segments = _split_into_segments(cleaned_input)
    parsed_items: list[ParsedMedicineStockItem] = []

    for seg in segments:
        item = _parse_segment(seg)
        if item is not None:
            parsed_items.append(item)

    # Safety Rule: If no valid medicine items were extracted, do not fabricate a proposal
    if not parsed_items:
        return None

    return StockUpdateProposal(
        items=parsed_items,
        raw_message=cleaned_input,
    )
