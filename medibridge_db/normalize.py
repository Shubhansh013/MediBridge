"""
Composition key normalization for MediBridge medicines.

This module provides the single source of truth for normalizing medicine
compositions (salts) into a canonical form for substitution lookup.
"""

from typing import List


def normalize_composition(generic_name: str) -> str:
    """
    Normalize a generic medicine name into a canonical composition key.

    Normalization rules:
    1. Split on '+' to separate individual salts
    2. Lowercase each salt
    3. Strip whitespace from each salt
    4. Sort salts alphabetically
    5. Join with '+'

    Args:
        generic_name: Human-readable generic name, e.g., "Paracetamol + Caffeine"

    Returns:
        Normalized composition key, e.g., "caffeine+paracetamol"

    Examples:
        >>> normalize_composition("Paracetamol")
        'paracetamol'
        >>> normalize_composition("Paracetamol + Caffeine")
        'caffeine+paracetamol'
        >>> normalize_composition("Amoxicillin + Clavulanic Acid")
        'amoxicillin+clavulanic acid'
        >>> normalize_composition("  Paracetamol  +  Caffeine  ")
        'caffeine+paracetamol'
    """
    if not generic_name or not generic_name.strip():
        return ""

    # Split on '+', strip each part, lowercase, filter empty
    salts: List[str] = [
        part.strip().lower()
        for part in generic_name.split("+")
        if part.strip()
    ]

    # Sort alphabetically
    salts.sort()

    # Join with '+'
    return "+".join(salts)