"""Pydantic schemas and enums for stock proposals.

These schemas validate natural-language stock interpretations produced by the AI
before any updates are proposed to a chemist for confirmation.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StockActionEnum(str, Enum):
    """Possible stock availability states for a medicine."""

    IN_STOCK = "IN_STOCK"
    LOW_STOCK = "LOW_STOCK"
    OUT_OF_STOCK = "OUT_OF_STOCK"


class ParsedMedicineStockItem(BaseModel):
    """Represents a single parsed medicine stock item from a chemist's message.

    This model stores medicine attributes extracted from text. Note that
    strength and dosage form are strictly optional—they should only be populated
    if explicitly mentioned in the original message.
    """

    model_config = ConfigDict(extra="forbid")

    medicine_name: str = Field(
        ...,
        description="Brand or generic name of the medicine. Cannot be empty or whitespace.",
    )
    status: StockActionEnum = Field(
        ...,
        description="Stock status: IN_STOCK, LOW_STOCK, or OUT_OF_STOCK.",
    )
    quantity: Optional[int] = Field(
        default=None,
        ge=0,
        description="Non-negative count of available units (e.g. strips, bottles). Optional.",
    )
    dosage_form: Optional[str] = Field(
        default=None,
        description="Dosage form if explicitly mentioned (e.g., 'Tablet', 'Syrup'). Otherwise None.",
    )
    strength: Optional[str] = Field(
        default=None,
        description="Strength/potency if explicitly mentioned (e.g., '650mg', '500mg'). Otherwise None.",
    )

    @field_validator("medicine_name")
    @classmethod
    def validate_medicine_name(cls, value: str) -> str:
        """Ensure medicine name is non-empty after stripping leading/trailing whitespace."""
        if not isinstance(value, str):
            raise ValueError("Medicine name must be a string.")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Medicine name must not be empty or whitespace-only.")
        return cleaned

    @field_validator("dosage_form", "strength")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        """Normalize optional text fields by stripping whitespace, or return None if blank."""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned if cleaned else None


class StockUpdateProposal(BaseModel):
    """Represents an overall unconfirmed stock proposal derived from an incoming message.

    A proposal is a temporary data container created by AI interpretation. It requires
    explicit human chemist confirmation before any changes are committed to the inventory.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[ParsedMedicineStockItem] = Field(
        ...,
        min_length=1,
        description="List of parsed medicine stock items. Must contain at least one item.",
    )
    raw_message: str = Field(
        ...,
        description="The original message sent by the chemist.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Optional reasoning or notes generated during AI parsing.",
    )
    proposed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when this proposal was constructed.",
    )

    @field_validator("raw_message")
    @classmethod
    def validate_raw_message(cls, value: str) -> str:
        """Ensure raw_message is not empty or whitespace-only."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Raw message must not be empty or whitespace-only.")
        return cleaned
