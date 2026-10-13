"""Medicine catalogue search and generic substitute discovery service.

Implements medicine lookup, fuzzy alias resolution, disambiguation formatting,
and composition-based substitute discovery as defined in docs/CONTRACT.md:
- Matches medicines by exact alias or brand/generic search.
- Computes match confidence and exact match flags.
- Disambiguates multiple matching candidates.
- Discovers therapeutic substitutes sharing the same composition_key, strength, and form.
- Records per-request pharmacist substitution approvals.
"""

from __future__ import annotations

import re
from typing import Optional, Protocol
from pydantic import BaseModel, ConfigDict, Field

from medibridge.exceptions import UserNotLinkedToPharmacyError
from medibridge.schemas.stock import StockActionEnum


class CatalogueMedicineDetail(BaseModel):
    """Detailed catalogue medicine record matching the medicines table."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(..., description="Medicine ID.")
    brand_name: str = Field(..., description="Brand name (e.g. 'Dolo 650').")
    generic_name: str = Field(..., description="Active chemical salt (e.g. 'paracetamol').")
    composition_key: str = Field(..., description="Normalized composition key (e.g. 'paracetamol_650mg_tablet').")
    strength: str = Field(..., description="Potency/strength (e.g. '650mg').")
    form: Optional[str] = Field(default=None, description="Dosage form (e.g. 'tablet', 'strip').")
    is_schedule_h: bool = Field(default=False, description="True if restricted Schedule H prescription drug.")
    manufacturer: Optional[str] = Field(default=None, description="Pharmaceutical manufacturer.")
    aliases: list[str] = Field(default_factory=list, description="Registered aliases and misspellings.")


class SearchResultItem(BaseModel):
    """Ranked search result item."""

    model_config = ConfigDict(extra="forbid")

    medicine: CatalogueMedicineDetail
    is_exact_match: bool
    match_type: str = Field(..., description="'alias', 'brand', or 'generic'.")
    confidence: float = Field(..., ge=0.0, le=1.0)


class SubstituteGroupResult(BaseModel):
    """Result of searching therapeutic substitutes for a requested medicine."""

    model_config = ConfigDict(extra="forbid")

    requested_medicine: CatalogueMedicineDetail
    available_substitutes: list[CatalogueMedicineDetail]
    composition_key: str


class CatalogueRepositoryProtocol(Protocol):
    """Repository protocol covering catalogue lookups, search, and substitution approvals."""

    def is_user_linked_to_pharmacy(self, user_id: int, pharmacy_id: int) -> bool:
        """Check if user has an active membership at the pharmacy."""
        ...

    def find_medicine_by_alias(self, alias: str) -> Optional[CatalogueMedicineDetail]:
        """Find medicine by exact alias match."""
        ...

    def search_medicines(self, query: str, limit: int = 10) -> list[CatalogueMedicineDetail]:
        """Search catalogue medicines by query across brand, generic, and aliases."""
        ...

    def get_medicine_by_id(self, medicine_id: int) -> Optional[CatalogueMedicineDetail]:
        """Fetch medicine by primary key."""
        ...

    def get_substitutes_for_medicine(
        self,
        medicine_id: int,
        pharmacy_id: Optional[int] = None,
    ) -> list[CatalogueMedicineDetail]:
        """Find medicines with same composition_key, strength, form."""
        ...

    def record_substitution_approval(
        self,
        pharmacy_id: int,
        requested_medicine_id: int,
        substitute_medicine_id: int,
        approving_pharmacist_id: int,
        status: str,
        request_reference: Optional[str] = None,
    ) -> dict:
        """Record per-request pharmacist approval."""
        ...


def search_catalogue(
    query: str,
    repository: CatalogueRepositoryProtocol,
    *,
    limit: int = 5,
) -> list[SearchResultItem]:
    """Searches the medicine catalogue with alias-first resolution and ranking.

    Args:
        query: Search string from chemist or patient.
        repository: Data access layer implementing CatalogueRepositoryProtocol.
        limit: Maximum results to return.

    Returns:
        List of SearchResultItem ranked by relevance.
    """
    if not isinstance(query, str) or not query.strip():
        return []

    cleaned = query.strip()
    results: list[SearchResultItem] = []
    seen_ids: set[int] = set()

    # 1. Alias lookup (highest confidence)
    alias_match = repository.find_medicine_by_alias(cleaned)
    if alias_match is not None:
        results.append(
            SearchResultItem(
                medicine=alias_match,
                is_exact_match=True,
                match_type="alias",
                confidence=1.0,
            )
        )
        seen_ids.add(alias_match.id)

    # 2. Broader search
    candidates = repository.search_medicines(cleaned, limit=limit)
    for med in candidates:
        if med.id in seen_ids:
            continue
        is_exact_brand = med.brand_name.strip().lower() == cleaned.lower()
        is_exact_generic = med.generic_name.strip().lower() == cleaned.lower()

        if is_exact_brand:
            conf = 1.0
            match_type = "brand"
            is_exact = True
        elif is_exact_generic:
            conf = 0.90
            match_type = "generic"
            is_exact = True
        else:
            conf = 0.75
            match_type = "brand"
            is_exact = False

        results.append(
            SearchResultItem(
                medicine=med,
                is_exact_match=is_exact,
                match_type=match_type,
                confidence=conf,
            )
        )
        seen_ids.add(med.id)
        if len(results) >= limit:
            break

    return results


def format_disambiguation_prompt(
    query: str,
    candidates: list[CatalogueMedicineDetail],
) -> str:
    """Formats a user-facing prompt to clarify ambiguous medicine matches.

    Args:
        query: Original input text.
        candidates: Matching medicine candidates.

    Returns:
        Human-readable prompt listing options.
    """
    if not candidates:
        return f"No medicines found matching '{query}'. Please check the spelling."

    options = "\n".join(
        f"{idx + 1}. {med.brand_name} ({med.generic_name} {med.strength})"
        for idx, med in enumerate(candidates)
    )
    return f"Multiple medicines match '{query}'. Please select one:\n{options}"


def find_substitutes(
    medicine_id: int,
    repository: CatalogueRepositoryProtocol,
    *,
    pharmacy_id: Optional[int] = None,
) -> SubstituteGroupResult:
    """Discovers generic brand substitutes sharing identical composition, strength, and form.

    Args:
        medicine_id: Primary key of requested medicine.
        repository: Data access layer implementing CatalogueRepositoryProtocol.
        pharmacy_id: Optional pharmacy filter.

    Returns:
        SubstituteGroupResult containing the requested medicine and available substitutes.

    Raises:
        ValueError: If requested medicine ID does not exist.
    """
    requested = repository.get_medicine_by_id(medicine_id)
    if requested is None:
        raise ValueError(f"Medicine with ID {medicine_id} does not exist.")

    substitutes = repository.get_substitutes_for_medicine(
        medicine_id=medicine_id,
        pharmacy_id=pharmacy_id,
    )
    # Exclude the requested medicine itself from the substitute list
    filtered_substitutes = [s for s in substitutes if s.id != medicine_id]

    return SubstituteGroupResult(
        requested_medicine=requested,
        available_substitutes=filtered_substitutes,
        composition_key=requested.composition_key,
    )


def record_substitute_approval(
    pharmacy_id: int,
    requested_medicine_id: int,
    substitute_medicine_id: int,
    approving_pharmacist_id: int,
    status: str,
    repository: CatalogueRepositoryProtocol,
    *,
    request_reference: Optional[str] = None,
) -> dict:
    """Validates composition equivalence and records per-request pharmacist approval.

    Args:
        pharmacy_id: Target pharmacy ID.
        requested_medicine_id: Original medicine requested by patient/doctor.
        substitute_medicine_id: Proposed substitute medicine.
        approving_pharmacist_id: Pharmacist authorizing the substitution.
        status: 'APPROVED' or 'REJECTED'.
        repository: Data access layer implementing CatalogueRepositoryProtocol.
        request_reference: Optional order or prescription reference.

    Returns:
        Created approval record dictionary.

    Raises:
        ValueError: If either medicine does not exist or composition_key / strength does not match.
    """
    # 1. Authorization check
    if not repository.is_user_linked_to_pharmacy(approving_pharmacist_id, pharmacy_id):
        raise UserNotLinkedToPharmacyError(
            f"Pharmacist {approving_pharmacist_id} is not linked to pharmacy {pharmacy_id}."
        )

    req_med = repository.get_medicine_by_id(requested_medicine_id)
    sub_med = repository.get_medicine_by_id(substitute_medicine_id)

    if req_med is None:
        raise ValueError(f"Requested medicine {requested_medicine_id} does not exist.")
    if sub_med is None:
        raise ValueError(f"Substitute medicine {substitute_medicine_id} does not exist.")

    # Integrity check: both medicines must share composition_key, strength, and form
    if (
        req_med.composition_key != sub_med.composition_key
        or req_med.strength != sub_med.strength
        or req_med.form != sub_med.form
    ):
        raise ValueError(
            f"Cannot substitute '{sub_med.brand_name}' for '{req_med.brand_name}': "
            f"composition mismatch ({req_med.composition_key} vs {sub_med.composition_key})."
        )

    return repository.record_substitution_approval(
        pharmacy_id=pharmacy_id,
        requested_medicine_id=requested_medicine_id,
        substitute_medicine_id=substitute_medicine_id,
        approving_pharmacist_id=approving_pharmacist_id,
        status=status,
        request_reference=request_reference,
    )
