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


GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"

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
    # Build the URL with the key query param, but never log the full URL.
    url = f"{GEMINI_API_URL}?key={api_key.strip()}"
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

        candidates = data.get("candidates", [])
        if not candidates:
            return None

        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        if not parts or "text" not in parts[0]:
            return None

        raw_text = parts[0]["text"].strip()
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.IGNORECASE).rstrip("` \n\r\t")

        parsed_json = json.loads(raw_text)
        items_data = parsed_json.get("items", [])
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
            notes=parsed_json.get("notes") or "Parsed via Gemini LLM",
        )
    except Exception as exc:
        # Log only the exception type — the message may embed the key-bearing URL.
        logger.warning(
            "Gemini LLM parsing failed (%s). Falling back to deterministic parser.",
            type(exc).__name__,
        )
        return None


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

        choices = data.get("choices", [])
        if not choices:
            return None

        msg_content = choices[0].get("message", {}).get("content", "")
        if not msg_content:
            return None

        raw_text = msg_content.strip()
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.IGNORECASE).rstrip("` \n\r\t")

        parsed_json = json.loads(raw_text)
        items_data = parsed_json.get("items", [])
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
            notes=parsed_json.get("notes") or f"Parsed via Groq LLM ({selected_model})",
        )
    except Exception as exc:
        # Log only the exception type — the message may embed the key-bearing URL.
        logger.warning(
            "Groq LLM parsing failed (%s). Falling back to deterministic parser.",
            type(exc).__name__,
        )
        return None


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

    If an LLM API key is available and use_llm is True, attempts parsing via the
    configured LLM provider. Gemini is the default provider; Groq is only used when
    explicitly selected via LLM_PROVIDER=groq, a gsk_-prefixed key, or provider='groq'.
    On any failure, timeout, or missing key, safely falls back to the deterministic
    offline parser. Failures never produce unvalidated proposals and never mutate
    confirmed inventory — proposals are always created as PENDING pending human review.

    Args:
        message: The raw text string received from the chemist.
        use_llm: Whether to attempt LLM parsing when credentials are configured.
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

    if use_llm:
        configured_provider = (provider or os.getenv("LLM_PROVIDER") or "").strip().lower()
        resolved_timeout = _resolve_llm_timeout(timeout)

        # Route 1: Explicit Groq selection
        if configured_provider == "groq":
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
                        "Groq LLM parsing threw unexpected exception (%s). Falling back to deterministic.",
                        type(exc).__name__,
                    )

        # Route 2: Explicit Gemini selection
        elif configured_provider == "gemini":
            gemini_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY")
            if gemini_key and gemini_key.strip() and gemini_key.strip() != "your_gemini_api_key_here":
                try:
                    llm_result = parse_with_gemini(
                        cleaned_input,
                        gemini_key,
                        timeout=resolved_timeout,
                        http_client=http_client,
                    )
                    if llm_result is not None and llm_result.items:
                        return llm_result
                except Exception as exc:
                    logger.warning(
                        "Gemini LLM parsing threw unexpected exception (%s). Falling back to deterministic.",
                        type(exc).__name__,
                    )

        # Route 3: Auto-detection / default (preserve Gemini backward compatibility)
        else:
            # If explicit api_key was provided directly
            if api_key is not None and api_key.strip():
                if api_key.strip().startswith("gsk_"):
                    llm_result = parse_with_groq(
                        cleaned_input, api_key, model=model, timeout=resolved_timeout, http_client=http_client
                    )
                else:
                    llm_result = parse_with_gemini(
                        cleaned_input, api_key, timeout=resolved_timeout, http_client=http_client
                    )
                if llm_result is not None and llm_result.items:
                    return llm_result

            # Check environment keys: check GEMINI_API_KEY first for 100% backward compatibility
            gemini_key = os.getenv("GEMINI_API_KEY")
            if gemini_key and gemini_key.strip() and gemini_key.strip() != "your_gemini_api_key_here":
                try:
                    llm_result = parse_with_gemini(
                        cleaned_input, gemini_key, timeout=resolved_timeout, http_client=http_client
                    )
                    if llm_result is not None and llm_result.items:
                        return llm_result
                except Exception as exc:
                    logger.warning(
                        "Gemini LLM parsing threw unexpected exception (%s). Falling back to deterministic.",
                        type(exc).__name__,
                    )

            # Check GROQ_API_KEY
            groq_key = os.getenv("GROQ_API_KEY")
            if groq_key and groq_key.strip() and groq_key.strip() != "your_groq_api_key_here":
                try:
                    llm_result = parse_with_groq(
                        cleaned_input, groq_key, model=model, timeout=resolved_timeout, http_client=http_client
                    )
                    if llm_result is not None and llm_result.items:
                        return llm_result
                except Exception as exc:
                    logger.warning(
                        "Groq LLM parsing threw unexpected exception (%s). Falling back to deterministic.",
                        type(exc).__name__,
                    )

    return parse_stock_message_deterministic(cleaned_input)
