"""Adapter converting parsed stock items into database proposal inputs.

Bridges the deterministic AI parser (Phase 2A) and Member 2's frozen database
contract (v1.0 in docs/CONTRACT.md). Handles 1-to-many proposal adaptation,
flags review requirements, and explicitly tracks unresolved catalogue fields
without fabricating missing medical data.
"""

from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field

from medibridge.schemas.stock import (
    ParsedMedicineStockItem,
    StockActionEnum,
    StockUpdateProposal,
)


class ParsedProposalInput(BaseModel):
    """Database proposal input payload as specified in docs/CONTRACT.md.

    Represents a single verified medicine proposal row ready for Member 2's
    create_stock_proposal(pharmacy_id, chemist_user_id, raw_message, parsed).
    """

    model_config = ConfigDict(extra="forbid")

    brand_name: Optional[str] = Field(
        default=None,
        description="Brand name of the medicine (e.g. 'Dolo 650'), if known.",
    )
    parsed_salt: str = Field(
        ...,
        description="Active generic chemical composition / salt (e.g. 'paracetamol').",
    )
    parsed_strength: str = Field(
        ...,
        description="Strength / potency (e.g. '650mg'). Required by DB contract.",
    )
    parsed_form: Optional[str] = Field(
        default=None,
        description="Dosage form (e.g. 'tablet', 'strip', 'syrup').",
    )
    parsed_quantity: Optional[int] = Field(
        default=None,
        ge=0,
        description="Quantity in stock. Preserves 0 when explicitly reported.",
    )
    parsed_intent: StockActionEnum = Field(
        ...,
        description="Target inventory action: IN_STOCK, LOW_STOCK, or OUT_OF_STOCK.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Parsing / matching confidence score between 0.0 and 1.0.",
    )
    flagged_for_review: bool = Field(
        ...,
        description="True if confidence < 0.8 OR matched medicine is Schedule H.",
    )


class UnresolvedProposalItem(BaseModel):
    """Structured container for parsed items that cannot be safely converted.

    Emitted when essential contract fields (like salt, strength, or confidence)
    are missing from the message or unconfirmed by catalogue lookups.
    """

    model_config = ConfigDict(extra="forbid")

    item: ParsedMedicineStockItem = Field(
        ...,
        description="The original parsed medicine item from Phase 2A.",
    )
    raw_message: str = Field(
        ...,
        description="The original chemist chat message for traceability.",
    )
    missing_fields: list[str] = Field(
        ...,
        description="List of required database contract fields that could not be determined.",
    )
    reason: str = Field(
        ...,
        description="Human-readable explanation of why this item requires clarification.",
    )


class AdaptedProposalResult(BaseModel):
    """Aggregated output from adapting a multi-medicine StockUpdateProposal."""

    model_config = ConfigDict(extra="forbid")

    raw_message: str = Field(
        ...,
        description="The original chat message for traceability.",
    )
    resolved: list[ParsedProposalInput] = Field(
        default_factory=list,
        description="List of completely resolved database proposal payloads.",
    )
    unresolved: list[UnresolvedProposalItem] = Field(
        default_factory=list,
        description="List of parsed items needing catalogue matching or user clarification.",
    )

    @property
    def is_fully_resolved(self) -> bool:
        """True if every parsed medicine in the proposal was successfully resolved."""
        return len(self.unresolved) == 0 and len(self.resolved) > 0


def compute_review_flag(confidence: float, is_schedule_h: bool) -> bool:
    """Evaluates the contract rule: flagged_for_review = (confidence < 0.8) OR is_schedule_h.

    Requires explicit inputs. Does NOT invent default confidence or Schedule H status.

    Args:
        confidence: Matching confidence between 0.0 and 1.0.
        is_schedule_h: Whether the identified medicine is restricted under Schedule H.

    Returns:
        True if the proposal requires human pharmacist review; False otherwise.

    Raises:
        TypeError: If confidence or is_schedule_h has an invalid type.
        ValueError: If confidence is out of the valid [0.0, 1.0] range.
    """
    if confidence is None:
        raise ValueError("Confidence score must be explicitly provided.")
    if is_schedule_h is None:
        raise ValueError("Schedule H status must be explicitly provided.")

    if not isinstance(confidence, (int, float)):
        raise TypeError(f"Confidence must be a float, got {type(confidence).__name__}.")
    if not isinstance(is_schedule_h, bool):
        raise TypeError(f"is_schedule_h must be a boolean, got {type(is_schedule_h).__name__}.")

    confidence_float = float(confidence)
    if not (0.0 <= confidence_float <= 1.0):
        raise ValueError(f"Confidence must be between 0.0 and 1.0, got {confidence_float}.")

    return (confidence_float < 0.8) or is_schedule_h


def adapt_parsed_item(
    item: ParsedMedicineStockItem,
    raw_message: str,
    *,
    brand_name: Optional[str] = None,
    salt: Optional[str] = None,
    strength: Optional[str] = None,
    form: Optional[str] = None,
    confidence: Optional[float] = None,
    is_schedule_h: Optional[bool] = None,
) -> ParsedProposalInput | UnresolvedProposalItem:
    """Adapts a single ParsedMedicineStockItem into a ParsedProposalInput or UnresolvedProposalItem.

    Preserves raw_message, quantity zero, and stock-status semantics. Does NOT
    invent missing salts, strengths, confidence scores, or Schedule H flags.
    """
    if not isinstance(item, ParsedMedicineStockItem):
        raise TypeError(f"Expected ParsedMedicineStockItem, got {type(item).__name__}.")
    if not isinstance(raw_message, str) or not raw_message.strip():
        raise ValueError("raw_message must be a non-empty string.")

    # 1. Resolve strength (prefer explicit context if provided, else item's extracted strength)
    resolved_strength = strength or item.strength
    resolved_form = form or item.dosage_form
    resolved_salt = salt

    # 2. Check for missing required contract fields
    missing: list[str] = []
    if not resolved_salt or not resolved_salt.strip():
        missing.append("parsed_salt")
    if not resolved_strength or not resolved_strength.strip():
        missing.append("parsed_strength")
    if confidence is None:
        missing.append("confidence")
    if is_schedule_h is None:
        missing.append("is_schedule_h")

    if missing:
        return UnresolvedProposalItem(
            item=item,
            raw_message=raw_message,
            missing_fields=missing,
            reason=(
                f"Missing required contract fields: {', '.join(missing)}. "
                "Catalogue matching or human clarification required."
            ),
        )

    # 3. Compute review flag (confidence and is_schedule_h guaranteed non-None here)
    assert confidence is not None
    assert is_schedule_h is not None
    flagged = compute_review_flag(confidence=confidence, is_schedule_h=is_schedule_h)

    # 4. Construct valid database proposal payload
    return ParsedProposalInput(
        brand_name=brand_name or item.medicine_name,
        parsed_salt=resolved_salt.strip(),
        parsed_strength=resolved_strength.strip(),
        parsed_form=resolved_form.strip() if resolved_form else None,
        parsed_quantity=item.quantity,
        parsed_intent=item.status,
        confidence=float(confidence),
        flagged_for_review=flagged,
    )


def adapt_stock_proposal(
    proposal: StockUpdateProposal,
    *,
    catalogue_matches: Optional[dict[str, dict[str, Any]]] = None,
) -> AdaptedProposalResult:
    """Adapts an overall StockUpdateProposal into database-ready proposal inputs.

    Produces one proposal input per medicine item. If catalogue_matches are
    provided (keyed by medicine_name or index), uses them to resolve generic
    salts, strengths, confidence scores, and Schedule H flags.

    Args:
        proposal: The validated StockUpdateProposal from Phase 2A.
        catalogue_matches: Optional dictionary mapping medicine names to their
                           catalogue lookup metadata:
                           {
                               "Dolo 650": {
                                   "salt": "paracetamol",
                                   "strength": "650mg",
                                   "form": "tablet",
                                   "confidence": 0.95,
                                   "is_schedule_h": False,
                               }
                           }

    Returns:
        AdaptedProposalResult containing separated resolved and unresolved lists.
    """
    if not isinstance(proposal, StockUpdateProposal):
        raise TypeError(f"Expected StockUpdateProposal, got {type(proposal).__name__}.")

    matches = catalogue_matches or {}
    resolved_list: list[ParsedProposalInput] = []
    unresolved_list: list[UnresolvedProposalItem] = []

    for item in proposal.items:
        # Check if catalogue context is available for this medicine
        item_ctx = matches.get(item.medicine_name) or {}

        adapted = adapt_parsed_item(
            item=item,
            raw_message=proposal.raw_message,
            brand_name=item_ctx.get("brand_name"),
            salt=item_ctx.get("salt"),
            strength=item_ctx.get("strength"),
            form=item_ctx.get("form"),
            confidence=item_ctx.get("confidence"),
            is_schedule_h=item_ctx.get("is_schedule_h"),
        )

        if isinstance(adapted, ParsedProposalInput):
            resolved_list.append(adapted)
        else:
            unresolved_list.append(adapted)

    return AdaptedProposalResult(
        raw_message=proposal.raw_message,
        resolved=resolved_list,
        unresolved=unresolved_list,
    )
