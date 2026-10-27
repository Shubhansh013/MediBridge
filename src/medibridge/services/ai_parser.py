"""Deterministic offline stock message parser for MediBridge.

Parses natural-language inventory updates sent by chemists in English,
Hindi, and Hinglish into structured Pydantic StockUpdateProposal objects.

This parser operates 100% offline with zero external network dependencies,
guaranteeing fast and deterministic behavior for testing and live hackathon demos.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

from medibridge.schemas.stock import (
    ParsedMedicineStockItem,
    StockActionEnum,
    StockUpdateProposal,
)

logger = logging.getLogger("medibridge.ai_parser")

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
    r"bottles?|shishi|vials?|boxes?|dabbe?|dabba|packs?|packets?|capsules?|caps?)\b",
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
    r"not\s+available|nil|zero\s+stock|finish(?:ed)?|stock\s+out)\b",
    re.IGNORECASE,
)

# Low stock keywords
_LOW_KEYWORDS = re.compile(
    r"\b(low\s+stock|kam\s+hai|thoda\s+hai|kam\s+bacha|few\s+left|only|sirf)\b",
    re.IGNORECASE,
)

# In stock keywords
_IN_KEYWORDS = re.compile(
    r"\b(available|in\s+stock|in-stock|hai|uplabdh|mil\s+jayega|present|rakha\s+hai|stock\s+me|add|added|daal\s+do|daalo?)\b",
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
    r"\b(add|added|update|put|please|pls|plz|kindly|kripya|daal\s+do|daalo?|jod\s+do|jodo?|"
    r"in|of|ke|ka|ki|ko|se|bhi|mein|me|par|pe|bhaiya|sir|ji|hain?|"
    r"tha|thi|the|bache?|bacha|left|stock|only|sirf|khatam|khtm|nahi|"
    r"nahin|available|uplabdh|present|count|quantity|is|are|remain|remains|remaining|finish|finished|rakha)\b",
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
    "dabba": "Box",
    "dabbe": "Box",
    "pack": "Pack",
    "packs": "Pack",
    "packet": "Pack",
    "packets": "Pack",
}


# Words representing dosage packaging units or number words that, if left behind
# in candidate medicine name, indicate an unparsed quantity/unit expression.
# When detected, the deterministic parser rejects the segment so eligible messages
# can fall back to the LLM cleanly rather than producing corrupted medicine names.
_UNPARSED_UNIT_OR_NUMBER = re.compile(
    r"\b("
    r"strips?|stripes?|patte?|patta|tablets?|tabs?|goli(?:ya|yan)?|"
    r"bottles?|shishi|vials?|boxes?|dabbe?|dabba|packs?|packets?|capsules?|caps?|"
    r"zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"twenty-five|thirty|forty|fifty|hundred|"
    r"ek|do|teen|char|chaar|paanch|chhe|chhah|saat|aath|nau|das|"
    r"gyarah|barah|terah|chaudah|pandrah|solah|satrah|atharah|unnis|bees|"
    r"pachis|tees|chalis|pachaas"
    r")\b",
    re.IGNORECASE,
)


def _redact_secret(value: Optional[str], *, visible: int = 4) -> str:
    """Return a safe, non-revealing representation of a credential for logging.

    Never logs raw API keys, bearer tokens, or key-bearing URLs. Preserves a short
    prefix so operators can still distinguish which provider/key is in use.
    """
    if not value:
        return "<empty>"
    cleaned = value.strip()
    if len(cleaned) <= visible:
        return "*" * len(cleaned)
    return f"{cleaned[:visible]}...{'*' * 4}"


def _resolve_llm_timeout(explicit_timeout: Optional[float]) -> float:
    """Resolve the LLM provider request timeout.

    Priority: explicit argument > LLM_TIMEOUT env var > 3.0s default.
    """
    if explicit_timeout is not None:
        try:
            return max(0.1, float(explicit_timeout))
        except (TypeError, ValueError):
            pass
    env_value = os.getenv("LLM_TIMEOUT")
    if env_value:
        try:
            return max(0.1, float(env_value))
        except (TypeError, ValueError):
            pass
    return 3.0


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

    # Reject if leftover packaging unit or number word is present in medicine name
    if _UNPARSED_UNIT_OR_NUMBER.search(med_name):
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


def parse_stock_message_deterministic(message: str) -> Optional[StockUpdateProposal]:
    """Deterministic offline stock message parser using regex and medical rules."""
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


DEFAULT_GEMINI_MODEL = "gemini-1.5-flash"
GEMINI_API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_API_URL = GEMINI_API_URL_TEMPLATE.format(model=DEFAULT_GEMINI_MODEL)

GEMINI_SYSTEM_PROMPT = """You are a medical inventory parser for Indian pharmacy updates in English, Hindi, and Hinglish.
Extract medicine inventory updates into JSON conforming to this schema:
{
  "items": [
    {
      "medicine_name": "string (brand or generic name e.g. 'Dolo 650', required)",
      "status": "IN_STOCK" | "LOW_STOCK" | "OUT_OF_STOCK",
      "quantity": integer or null,
      "dosage_form": string or null,
      "strength": string or null
    }
  ],
  "notes": "string or null"
}
Rules:
1. If the message is conversational, asking questions, uncertain ("maybe", "shayad"), or referring to future arrival ("kal", "tomorrow"), return {"items": []}.
2. Never invent medicines not present in the message.
3. For out of stock, status MUST be "OUT_OF_STOCK" and quantity MUST be 0 or null.
4. Output valid JSON only, no commentary.
"""


def parse_with_gemini(
    message: str,
    api_key: str,
    *,
    model: Optional[str] = None,
    timeout: Optional[float] = None,
    http_client: Optional[Any] = None,
) -> Optional[StockUpdateProposal]:
    """Optional LLM parser using Google Gemini REST API.

    Validates response against Pydantic schemas before returning.
    Returns None on any network, validation, or provider error so callers can safely fall back.
    """
    if not api_key or not api_key.strip():
        return None

    cleaned = message.strip()
    if not cleaned:
        return None

    resolved_timeout = _resolve_llm_timeout(timeout)
    selected_model = model or os.getenv("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL

    # If using default model and no explicit model/env override was provided,
    # use GEMINI_API_URL directly (preserving monkeypatch compatibility in tests).
    # Otherwise, construct the URL using the requested model.
    if selected_model == DEFAULT_GEMINI_MODEL and not model and not os.getenv("GEMINI_MODEL"):
        base_url = GEMINI_API_URL
    else:
        base_url = GEMINI_API_URL_TEMPLATE.format(model=selected_model)

    # Build the URL with the key query param, but never log the full URL.
    url = f"{base_url}?key={api_key.strip()}"
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": f"{GEMINI_SYSTEM_PROMPT}\n\nChemist stock update message: {cleaned}"
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.0,
            "responseMimeType": "application/json",
        },
    }

    response = None
    try:
        if http_client is not None:
            response = http_client.post(url, json=payload, timeout=resolved_timeout)
        else:
            import requests

            response = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=resolved_timeout,
            )

        status_code = getattr(response, "status_code", None)
        if status_code != 200:
            logger.warning(
                "Gemini API returned HTTP status %s (key=%s, timeout=%.1fs)",
                status_code,
                _redact_secret(api_key),
                resolved_timeout,
            )
            return None

        if callable(getattr(response, "json", None)):
            data = response.json()
        else:
            data = json.loads(getattr(response, "text", "{}"))

        if not isinstance(data, dict):
            return None

        candidates = data.get("candidates", [])
        if not candidates or not isinstance(candidates, list):
            return None

        first_cand = candidates[0]
        if not isinstance(first_cand, dict):
            return None

        content_obj = first_cand.get("content")
        if not isinstance(content_obj, dict):
            return None

        parts = content_obj.get("parts")
        if not parts or not isinstance(parts, list):
            return None

        first_part = parts[0]
        if not isinstance(first_part, dict) or "text" not in first_part:
            return None

        raw_text = first_part.get("text")
        if not raw_text or not str(raw_text).strip():
            return None

        raw_text = str(raw_text).strip()
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.IGNORECASE).rstrip("` \n\r\t")

        parsed_json = json.loads(raw_text)
        if isinstance(parsed_json, list):
            items_data = parsed_json
            notes_val = None
        elif isinstance(parsed_json, dict):
            items_data = parsed_json.get("items", [])
            notes_val = parsed_json.get("notes")
        else:
            return None

        if not items_data or not isinstance(items_data, list):
            return None

        validated_items: list[ParsedMedicineStockItem] = []
        for item_dict in items_data:
            if not isinstance(item_dict, dict):
                continue
            validated_items.append(ParsedMedicineStockItem.model_validate(item_dict))

        if not validated_items:
            return None

        return StockUpdateProposal(
            items=validated_items,
            raw_message=cleaned,
            notes=notes_val or f"Parsed via Gemini LLM ({selected_model})",
        )
    except Exception as exc:
        logger.warning(
            "Gemini LLM parsing failed (%s). Falling back to deterministic parser.",
            type(exc).__name__,
        )
        return None
    finally:
        if response is not None and hasattr(response, "close") and callable(response.close):
            try:
                response.close()
            except Exception:
                pass


GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"

GROQ_SYSTEM_PROMPT = """You are a medical inventory parser for Indian pharmacy updates in English, Hindi, and Hinglish.
Extract medicine inventory updates into JSON conforming to this schema:
{
  "items": [
    {
      "medicine_name": "string (brand or generic name e.g. 'Dolo 650', required)",
      "status": "IN_STOCK" | "LOW_STOCK" | "OUT_OF_STOCK",
      "quantity": integer or null,
      "dosage_form": string or null,
      "strength": string or null
    }
  ],
  "notes": "string or null"
}
Rules:
1. If the message is conversational, asking questions, uncertain ("maybe", "shayad"), or referring to future arrival ("kal", "tomorrow"), return {"items": []}.
2. Never invent medicines not present in the message.
3. For out of stock, status MUST be "OUT_OF_STOCK" and quantity MUST be 0 or null.
4. Output valid JSON only, no commentary.
"""


def parse_with_groq(
    message: str,
    api_key: str,
    *,
    model: Optional[str] = None,
    timeout: Optional[float] = None,
    http_client: Optional[Any] = None,
) -> Optional[StockUpdateProposal]:
    """Optional LLM parser using Groq OpenAI-compatible Chat Completions REST API.

    Validates response against Pydantic schemas before returning.
    Returns None on any network, validation, or provider error so callers can safely fall back.
    """
    if not api_key or not api_key.strip():
        return None

    cleaned = message.strip()
    if not cleaned:
        return None

    resolved_timeout = _resolve_llm_timeout(timeout)
    selected_model = model or os.getenv("GROQ_MODEL") or DEFAULT_GROQ_MODEL
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": selected_model,
        "messages": [
            {"role": "system", "content": GROQ_SYSTEM_PROMPT},
            {"role": "user", "content": f"Chemist stock update message: {cleaned}"},
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }

    response = None
    try:
        if http_client is not None:
            response = http_client.post(GROQ_API_URL, json=payload, headers=headers, timeout=resolved_timeout)
        else:
            import requests

            response = requests.post(
                GROQ_API_URL,
                json=payload,
                headers=headers,
                timeout=resolved_timeout,
            )

        status_code = getattr(response, "status_code", None)
        if status_code != 200:
            logger.warning(
                "Groq API returned HTTP status %s (key=%s, timeout=%.1fs)",
                status_code,
                _redact_secret(api_key),
                resolved_timeout,
            )
            return None

        if callable(getattr(response, "json", None)):
            data = response.json()
        else:
            data = json.loads(getattr(response, "text", "{}"))

        if not isinstance(data, dict):
            return None

        choices = data.get("choices", [])
        if not choices or not isinstance(choices, list):
            return None

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            return None

        msg_obj = first_choice.get("message")
        if not isinstance(msg_obj, dict):
            return None

        msg_content = msg_obj.get("content") or ""
        if not msg_content or not str(msg_content).strip():
            msg_content = msg_obj.get("reasoning_content") or ""
        if not msg_content or not str(msg_content).strip():
            return None

        raw_text = str(msg_content).strip()
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.IGNORECASE).rstrip("` \n\r\t")

        parsed_json = json.loads(raw_text)
        if isinstance(parsed_json, list):
            items_data = parsed_json
            notes_val = None
        elif isinstance(parsed_json, dict):
            items_data = parsed_json.get("items", [])
            notes_val = parsed_json.get("notes")
        else:
            return None

        if not items_data or not isinstance(items_data, list):
            return None

        validated_items: list[ParsedMedicineStockItem] = []
        for item_dict in items_data:
            if not isinstance(item_dict, dict):
                continue
            validated_items.append(ParsedMedicineStockItem.model_validate(item_dict))

        if not validated_items:
            return None

        return StockUpdateProposal(
            items=validated_items,
            raw_message=cleaned,
            notes=notes_val or f"Parsed via Groq LLM ({selected_model})",
        )
    except Exception as exc:
        logger.warning(
            "Groq LLM parsing failed (%s). Falling back to deterministic parser.",
            type(exc).__name__,
        )
        return None
    finally:
        if response is not None and hasattr(response, "close") and callable(response.close):
            try:
                response.close()
            except Exception:
                pass


def parse_stock_message(
    message: str,
    *,
    use_llm: bool = True,
    api_key: Optional[str] = None,
    http_client: Optional[Any] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    timeout: Optional[float] = None,
) -> Optional[StockUpdateProposal]:
    """Parse a natural-language stock message into an unconfirmed StockUpdateProposal.

    Evaluates chemist messages in English, Hindi, and Hinglish. Supports multi-medicine
    updates, distinguishes medicine strength from inventory quantity, and strictly
    rejects uncertain, future-oriented, or conversational messages.

    Deterministic offline parsing runs first. If valid items are extracted, the proposal
    is returned immediately with zero LLM latency and zero API cost. If deterministic
    parsing misses (and the message is not conversational, uncertain, or future-oriented),
    optional LLM fallback is attempted when use_llm is True and credentials exist.

    Provider Selection Precedence:
        1. Explicit function argument `provider` ('groq' or 'gemini').
        2. Environment variable `LLM_PROVIDER` ('groq' or 'gemini').
        3. Key prefix hint if provider is unspecified:
           - 'gsk_' prefix -> 'groq'
           - 'AIza' prefix -> 'gemini'
        4. Default provider: 'groq'.

    Groq is the primary default LLM provider; Gemini is an optional alternative.
    If credentials for the selected provider are missing, no network calls are made
    and None is returned safely (no automatic cross-provider fallback).
    On any failure or timeout, None is returned safely. Failures never produce
    unvalidated proposals and never mutate confirmed inventory — proposals are always
    created as PENDING pending human review.

    Args:
        message: The raw text string received from the chemist.
        use_llm: Whether to attempt LLM parsing when deterministic parsing misses and credentials are configured.
        api_key: Optional explicit API key override (defaults to GROQ_API_KEY or GEMINI_API_KEY).
        http_client: Optional injected HTTP client for testing.
        provider: Optional explicit provider selection ('groq' or 'gemini').
        model: Optional model identifier override for the selected provider.
        timeout: Optional per-request timeout override (seconds). Defaults to
            LLM_TIMEOUT env var, then 3.0s.

    Returns:
        A validated StockUpdateProposal if one or more valid medicine updates were extracted;
        None if the message is empty, conversational, ambiguous, future-oriented, or unparseable.

    Raises:
        TypeError: If message is not a string.
    """
    if not isinstance(message, str):
        raise TypeError("Message must be a string.")

    cleaned_input = message.strip()
    if not cleaned_input:
        return None

    # Step 1: Run deterministic offline parser first
    det_result = parse_stock_message_deterministic(cleaned_input)
    if det_result is not None and det_result.items:
        return det_result

    # Step 2: Safety guard against conversational, uncertain, or future-oriented messages
    # These must NEVER trigger an LLM call or fabricate stock proposals.
    if (
        _UNCERTAIN_WORDS.search(cleaned_input)
        or _FUTURE_WORDS.search(cleaned_input)
        or _CONVERSATIONAL_WORDS.search(cleaned_input)
    ):
        return None

    # Step 3: Optional LLM fallback for eligible deterministic misses
    if not use_llm:
        return None

    # Resolve provider with precedence: argument > LLM_PROVIDER env var > key prefix hint > default ('groq')
    raw_provider = (provider or os.getenv("LLM_PROVIDER") or "").strip().lower()
    if raw_provider:
        resolved_provider = raw_provider
    elif api_key and api_key.strip().startswith("gsk_"):
        resolved_provider = "groq"
    elif api_key and api_key.strip().startswith("AIza"):
        resolved_provider = "gemini"
    else:
        resolved_provider = "groq"

    resolved_timeout = _resolve_llm_timeout(timeout)

    # Route 1: Groq selection (default)
    if resolved_provider == "groq":
        groq_key = api_key if api_key is not None else os.getenv("GROQ_API_KEY")
        if groq_key and groq_key.strip() and groq_key.strip() != "your_groq_api_key_here":
            try:
                llm_result = parse_with_groq(
                    cleaned_input,
                    groq_key,
                    model=model,
                    timeout=resolved_timeout,
                    http_client=http_client,
                )
                if llm_result is not None and llm_result.items:
                    return llm_result
            except Exception as exc:
                logger.warning(
                    "Groq LLM parsing threw unexpected exception (%s).",
                    type(exc).__name__,
                )
        return None

    # Route 2: Gemini selection (optional alternative)
    elif resolved_provider == "gemini":
        gemini_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY")
        if gemini_key and gemini_key.strip() and gemini_key.strip() != "your_gemini_api_key_here":
            try:
                llm_result = parse_with_gemini(
                    cleaned_input,
                    gemini_key,
                    model=model,
                    timeout=resolved_timeout,
                    http_client=http_client,
                )
                if llm_result is not None and llm_result.items:
                    return llm_result
            except Exception as exc:
                logger.warning(
                    "Gemini LLM parsing threw unexpected exception (%s).",
                    type(exc).__name__,
                )
        return None

    # Unrecognized provider
    logger.warning("Unrecognized LLM provider '%s'.", resolved_provider)
    return None
