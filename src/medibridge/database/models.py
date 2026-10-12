"""SQLAlchemy 2.0 ORM models for MediBridge.

Follows the frozen database contract defined in docs/CONTRACT.md exactly:
- 10 core tables: pharmacies, users, user_pharmacies, medicines, medicine_aliases,
  inventory, stock_proposals, stock_history, substitution_approvals.
- Check constraints, unique constraints, and foreign key rules matching CONTRACT.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""
    pass


class Pharmacy(Base):
    """Verified pharmacy locations table."""

    __tablename__ = "pharmacies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    locality: Mapped[str] = mapped_column(String, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    telegram_chat_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    verification_state: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="pending",
    )
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint(
            "verification_state IN ('pending', 'verified', 'rejected')",
            name="check_pharmacy_verification_state",
        ),
    )


class User(Base):
    """Telegram users with roles."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint(
            "role IN ('patient', 'chemist', 'pharmacist', 'admin')",
            name="check_user_role",
        ),
    )


class UserPharmacy(Base):
    """Staff ↔ pharmacy membership table."""

    __tablename__ = "user_pharmacies"

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        primary_key=True,
        nullable=False,
    )
    pharmacy_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("pharmacies.id"),
        primary_key=True,
        nullable=False,
    )
    role_at_pharmacy: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="chemist",
    )

    __table_args__ = (
        CheckConstraint(
            "role_at_pharmacy IN ('chemist', 'pharmacist')",
            name="check_role_at_pharmacy",
        ),
    )


class Medicine(Base):
    """Brand-level medicine catalogue."""

    __tablename__ = "medicines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brand_name: Mapped[str] = mapped_column(String, nullable=False)
    generic_name: Mapped[str] = mapped_column(String, nullable=False)
    composition_key: Mapped[str] = mapped_column(String, nullable=False)
    strength: Mapped[str] = mapped_column(String, nullable=False)
    form: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_schedule_h: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manufacturer: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint(
            "brand_name",
            "composition_key",
            "strength",
            "form",
            "manufacturer",
            name="uq_medicine_brand_composition",
        ),
    )


class MedicineAlias(Base):
    """Fuzzy name matching table."""

    __tablename__ = "medicine_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    medicine_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("medicines.id", ondelete="CASCADE"),
        nullable=False,
    )
    alias: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    alias_type: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "alias_type IN ('misspelling', 'hinglish', 'short', 'brand_variant')",
            name="check_alias_type",
        ),
    )


class Inventory(Base):
    """Pharmacy × medicine stock table."""

    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("pharmacies.id"),
        nullable=False,
    )
    medicine_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("medicines.id"),
        nullable=False,
    )
    stock_status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="IN_STOCK",
    )
    quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_confirmed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_updated_by: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
    )
    last_update_source: Mapped[Optional[str]] = mapped_column(
        String,
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "medicine_id", name="uq_inventory_pharmacy_medicine"),
        CheckConstraint(
            "stock_status IN ('IN_STOCK', 'LOW_STOCK', 'OUT_OF_STOCK')",
            name="check_inventory_stock_status",
        ),
        CheckConstraint("quantity >= 0 OR quantity IS NULL", name="check_inventory_quantity_non_negative"),
        CheckConstraint(
            "last_update_source IN ('chemist', 'proposal_apply', 'undo', 'seed') OR last_update_source IS NULL",
            name="check_inventory_update_source",
        ),
    )


class StockProposal(Base):
    """AI-parsed updates awaiting chemist confirmation."""

    __tablename__ = "stock_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("pharmacies.id"),
        nullable=False,
    )
    chemist_user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
    )
    raw_message: Mapped[str] = mapped_column(Text, nullable=False)
    brand_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    parsed_salt: Mapped[str] = mapped_column(String, nullable=False)
    parsed_strength: Mapped[str] = mapped_column(String, nullable=False)
    parsed_form: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    parsed_quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    parsed_intent: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="PENDING")
    flagged_for_review: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    renewed_from_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("stock_proposals.id"),
        nullable=True,
    )
    confirmed_by: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=True,
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    review_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "parsed_intent IN ('IN_STOCK', 'LOW_STOCK', 'OUT_OF_STOCK')",
            name="check_proposal_parsed_intent",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'CONFIRMED', 'REJECTED', 'EXPIRED')",
            name="check_proposal_status",
        ),
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="check_proposal_confidence_range"),
        CheckConstraint("parsed_quantity >= 0 OR parsed_quantity IS NULL", name="check_proposal_quantity_non_negative"),
    )


class StockHistory(Base):
    """Immutable audit log of applied changes + undos."""

    __tablename__ = "stock_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inventory_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("inventory.id"),
        nullable=False,
    )
    pharmacy_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("pharmacies.id"),
        nullable=False,
    )
    medicine_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("medicines.id"),
        nullable=False,
    )
    changed_by: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
    )
    source_proposal_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("stock_proposals.id"),
        nullable=True,
    )
    change_type: Mapped[str] = mapped_column(String, nullable=False)
    undo_of_history_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("stock_history.id"),
        unique=True,
        nullable=True,
    )
    previous_stock_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    new_stock_status: Mapped[str] = mapped_column(String, nullable=False)
    previous_quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    new_quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    previous_last_confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    change_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        CheckConstraint("change_type IN ('STOCK_UPDATE', 'UNDO')", name="check_history_change_type"),
        CheckConstraint("previous_quantity >= 0 OR previous_quantity IS NULL", name="check_history_prev_quantity"),
        CheckConstraint("new_quantity >= 0 OR new_quantity IS NULL", name="check_history_new_quantity"),
    )


class SubstitutionApproval(Base):
    """Per-request pharmacist approval for substitutes."""

    __tablename__ = "substitution_approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("pharmacies.id"),
        nullable=False,
    )
    requested_medicine_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("medicines.id"),
        nullable=False,
    )
    substitute_medicine_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("medicines.id"),
        nullable=False,
    )
    approving_pharmacist_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String, nullable=False)
    request_reference: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('APPROVED', 'REJECTED')", name="check_substitution_status"),
    )
