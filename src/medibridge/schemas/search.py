"""Pydantic schemas for patient medicine search queries and availability results.

These schemas are completely independent of database ORM models and define
the data structures used for patient inquiries and response formatting.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from medibridge.schemas.stock import StockActionEnum


class MedicineSearchQuery(BaseModel):
    """Represents a search query initiated by a patient looking for medicine availability."""

    model_config = ConfigDict(extra="forbid")

    medicine_name: str = Field(
        ...,
        description="Brand or generic medicine name requested. Cannot be empty or whitespace.",
    )
    locality: Optional[str] = Field(
        default=None,
        description="Optional Delhi NCR locality filter (e.g., 'Rohini', 'Connaught Place').",
    )

    @field_validator("medicine_name")
    @classmethod
    def validate_medicine_name(cls, value: str) -> str:
        """Strip surrounding whitespace and ensure medicine name is not empty."""
        if not isinstance(value, str):
            raise ValueError("Medicine name must be a string.")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Medicine search query must not be empty or whitespace-only.")
        return cleaned

    @field_validator("locality")
    @classmethod
    def normalize_locality(cls, value: Optional[str]) -> Optional[str]:
        """Strip whitespace from locality, converting blank strings to None."""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned if cleaned else None


class PharmacySearchResultItem(BaseModel):
    """Represents an individual pharmacy's stock result for a medicine search."""

    model_config = ConfigDict(extra="forbid")

    pharmacy_id: int = Field(
        ...,
        description="Unique identifier of the registered pharmacy.",
    )
    pharmacy_name: str = Field(
        ...,
        description="Name of the pharmacy store.",
    )
    address: str = Field(
        ...,
        description="Full physical address of the pharmacy.",
    )
    locality: str = Field(
        ...,
        description="Neighborhood/locality within Delhi NCR.",
    )
    phone: Optional[str] = Field(
        default=None,
        description="Contact phone number of the pharmacy if available.",
    )
    medicine_name: str = Field(
        ...,
        description="Name of the medicine in stock.",
    )
    generic_name: Optional[str] = Field(
        default=None,
        description="Generic chemical composition if cataloged (e.g., 'Paracetamol').",
    )
    dosage_form: Optional[str] = Field(
        default=None,
        description="Dosage form (e.g., 'Tablet', 'Syrup').",
    )
    strength: Optional[str] = Field(
        default=None,
        description="Potency or strength (e.g., '650mg').",
    )
    status: StockActionEnum = Field(
        ...,
        description="Current stock availability status.",
    )
    quantity: Optional[int] = Field(
        default=None,
        ge=0,
        description="Reported quantity available, if known. Must be non-negative.",
    )
    last_confirmed_at: Optional[datetime] = Field(
        default=None,
        description="UTC timestamp of when this stock was last confirmed by the chemist.",
    )


class SearchResponse(BaseModel):
    """Envelope containing the original search query and the list of matching results."""

    model_config = ConfigDict(extra="forbid")

    query: MedicineSearchQuery = Field(
        ...,
        description="The original validated search query.",
    )
    results: list[PharmacySearchResultItem] = Field(
        default_factory=list,
        description="List of matching pharmacies carrying the requested item.",
    )
    total_found: int = Field(
        ...,
        ge=0,
        description="Total number of matching results returned.",
    )
