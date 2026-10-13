"""
SQLAlchemy 2.0 models for MediBridge.

All models use DeclarativeBase with Mapped/mapped_column type annotations.
Enums are Python enums stored as strings with check constraints.
Timestamps use UTCDateTime (timezone-aware UTC only).
"""

import enum
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    REAL,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .database import Base, UTCDateTime, utc_now


# =============================================================================
# Enums
# =============================================================================

class UserRole(str, enum.Enum):
    patient = "patient"
    chemist = "chemist"
    pharmacist = "pharmacist"
    admin = "admin"


class PharmacyVerification(str, enum.Enum):
    pending = "pending"
    verified = "verified"
    rejected = "rejected"


class StaffRoleAtPharmacy(str, enum.Enum):
    chemist = "chemist"
    pharmacist = "pharmacist"


class StockStatus(str, enum.Enum):
    in_stock = "IN_STOCK"
    low_stock = "LOW_STOCK"
    out_of_stock = "OUT_OF_STOCK"


class ProposalStatus(str, enum.Enum):
    pending = "PENDING"
    confirmed = "CONFIRMED"
    rejected = "REJECTED"
    expired = "EXPIRED"


class HistoryChangeType(str, enum.Enum):
    stock_update = "STOCK_UPDATE"
    undo = "UNDO"


class SubstitutionStatus(str, enum.Enum):
    approved = "APPROVED"
    rejected = "REJECTED"


class SosStatus(str, enum.Enum):
    active = "active"
    fulfilled = "fulfilled"
    expired = "expired"
    cancelled = "cancelled"


class AliasType(str, enum.Enum):
    misspelling = "misspelling"
    hinglish = "hinglish"
    short = "short"
    brand_variant = "brand_variant"


class UpdateSource(str, enum.Enum):
    chemist = "chemist"
    proposal_apply = "proposal_apply"
    undo = "undo"
    seed = "seed"


# =============================================================================
# Helper functions
# =============================================================================

def _normalize_manufacturer(value: Optional[str]) -> str:
    """Normalize manufacturer for unique constraint: NULL -> empty string."""
    return value or ""


# =============================================================================
# Models
# =============================================================================

class Pharmacy(Base):
    """
    Verified pharmacy locations.
    """
    __tablename__ = "pharmacies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    locality: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    latitude: Mapped[float] = mapped_column(REAL, nullable=False)
    longitude: Mapped[float] = mapped_column(REAL, nullable=False)
    contact_phone: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    telegram_chat_id: Mapped[Optional[int]] = mapped_column(
        Integer, unique=True, nullable=True
    )
    verification_state: Mapped[PharmacyVerification] = mapped_column(
        Text, nullable=False, default=PharmacyVerification.pending
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    # Relationships
    inventories: Mapped[List["Inventory"]] = relationship(
        "Inventory", back_populates="pharmacy", passive_deletes=True
    )
    proposals: Mapped[List["StockProposal"]] = relationship(
        "StockProposal", back_populates="pharmacy", passive_deletes=True
    )
    history: Mapped[List["StockHistory"]] = relationship(
        "StockHistory", back_populates="pharmacy", passive_deletes=True
    )
    substitution_approvals: Mapped[List["SubstitutionApproval"]] = relationship(
        "SubstitutionApproval", back_populates="pharmacy", passive_deletes=True
    )
    user_links: Mapped[List["UserPharmacy"]] = relationship(
        "UserPharmacy", back_populates="pharmacy", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint("latitude >= -90 AND latitude <= 90", name="ck_pharmacies_latitude"),
        CheckConstraint("longitude >= -180 AND longitude <= 180", name="ck_pharmacies_longitude"),
        Index("idx_pharmacies_locality", "locality"),
        Index("idx_pharmacies_verification", "verification_state"),
        Index("idx_pharmacies_is_active", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<Pharmacy(id={self.id}, name={self.name!r})>"


class User(Base):
    """
    Telegram users with roles.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[UserRole] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    # Relationships
    pharmacy_links: Mapped[List["UserPharmacy"]] = relationship(
        "UserPharmacy", back_populates="user", passive_deletes=True
    )
    proposals: Mapped[List["StockProposal"]] = relationship(
        "StockProposal", back_populates="chemist", foreign_keys="StockProposal.chemist_user_id", passive_deletes=True
    )
    history: Mapped[List["StockHistory"]] = relationship(
        "StockHistory", back_populates="changed_by_user", passive_deletes=True
    )
    substitution_approvals: Mapped[List["SubstitutionApproval"]] = relationship(
        "SubstitutionApproval", back_populates="approving_pharmacist", passive_deletes=True
    )

    __table_args__ = (
        Index("idx_users_role", "role"),
        Index("idx_users_is_active", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, telegram_user_id={self.telegram_user_id}, role={self.role})>"


class UserPharmacy(Base):
    """
    Many-to-many: staff ↔ pharmacies with role at each pharmacy.
    """
    __tablename__ = "user_pharmacies"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    pharmacy_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pharmacies.id", ondelete="CASCADE"), primary_key=True
    )
    role_at_pharmacy: Mapped[StaffRoleAtPharmacy] = mapped_column(
        Text, nullable=False, default=StaffRoleAtPharmacy.chemist
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="pharmacy_links")
    pharmacy: Mapped["Pharmacy"] = relationship("Pharmacy", back_populates="user_links")

    __table_args__ = (
        Index("idx_user_pharmacies_pharmacy", "pharmacy_id"),
    )

    def __repr__(self) -> str:
        return f"<UserPharmacy(user_id={self.user_id}, pharmacy_id={self.pharmacy_id}, role={self.role_at_pharmacy})>"


class Medicine(Base):
    """
    Brand-level medicine catalogue.
    Each row is a BRAND (e.g., Dolo 650, Calpol 650).
    """
    __tablename__ = "medicines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brand_name: Mapped[str] = mapped_column(Text, nullable=False)
    generic_name: Mapped[str] = mapped_column(Text, nullable=False)
    composition_key: Mapped[str] = mapped_column(Text, nullable=False)
    strength: Mapped[str] = mapped_column(Text, nullable=False)
    form: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_schedule_h: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    manufacturer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    # Relationships
    inventories: Mapped[List["Inventory"]] = relationship(
        "Inventory", back_populates="medicine", passive_deletes=True
    )
    aliases: Mapped[List["MedicineAlias"]] = relationship(
        "MedicineAlias", back_populates="medicine", cascade="all, delete-orphan"
    )
    requested_substitutions: Mapped[List["SubstitutionApproval"]] = relationship(
        "SubstitutionApproval",
        back_populates="requested_medicine",
        foreign_keys="SubstitutionApproval.requested_medicine_id",
    )
    substitute_substitutions: Mapped[List["SubstitutionApproval"]] = relationship(
        "SubstitutionApproval",
        back_populates="substitute_medicine",
        foreign_keys="SubstitutionApproval.substitute_medicine_id",
    )

    __table_args__ = (
        UniqueConstraint(
            "brand_name", "composition_key", "strength", "form", "manufacturer",
            name="uq_medicines_brand_composition"
        ),
        Index("idx_medicines_composition", "composition_key", "strength", "form"),
        Index("idx_medicines_schedule_h", "is_schedule_h"),
        Index("idx_medicines_brand", "brand_name"),
        Index("idx_medicines_is_active", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<Medicine(id={self.id}, brand={self.brand_name!r}, composition={self.composition_key!r})>"

    @property
    def normalized_manufacturer(self) -> str:
        """Return normalized manufacturer for unique constraint comparison."""
        return _normalize_manufacturer(self.manufacturer)


class MedicineAlias(Base):
    """
    Aliases, misspellings, Hinglish names for fuzzy matching.
    """
    __tablename__ = "medicine_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    medicine_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("medicines.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    alias_type: Mapped[AliasType] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now
    )

    # Relationships
    medicine: Mapped["Medicine"] = relationship("Medicine", back_populates="aliases")

    __table_args__ = (
        Index("idx_medicine_aliases_medicine", "medicine_id"),
    )

    def __repr__(self) -> str:
        return f"<MedicineAlias(id={self.id}, alias={self.alias!r})>"


class Inventory(Base):
    """
    Pharmacy × medicine stock (one row per combination).
    """
    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False
    )
    medicine_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("medicines.id", ondelete="RESTRICT"), nullable=False
    )
    stock_status: Mapped[StockStatus] = mapped_column(
        Text, nullable=False, default=StockStatus.in_stock
    )
    quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_confirmed_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now
    )
    last_updated_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    last_update_source: Mapped[Optional[UpdateSource]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    # Relationships
    pharmacy: Mapped["Pharmacy"] = relationship("Pharmacy", back_populates="inventories", passive_deletes=True)
    medicine: Mapped["Medicine"] = relationship("Medicine", back_populates="inventories", passive_deletes=True)
    history: Mapped[List["StockHistory"]] = relationship(
        "StockHistory", back_populates="inventory", passive_deletes=True
    )
    last_updated_by_user: Mapped[Optional["User"]] = relationship("User")

    __table_args__ = (
        UniqueConstraint("pharmacy_id", "medicine_id", name="uq_inventory_pharmacy_medicine"),
        CheckConstraint("quantity IS NULL OR quantity >= 0", name="ck_inventory_quantity_nonneg"),
        CheckConstraint(
            "(stock_status = 'OUT_OF_STOCK' AND (quantity IS NULL OR quantity = 0)) "
            "OR (stock_status IN ('IN_STOCK', 'LOW_STOCK') AND (quantity IS NULL OR quantity > 0))",
            name="ck_inventory_status_quantity"
        ),
        Index("idx_inventory_pharmacy", "pharmacy_id"),
        Index("idx_inventory_medicine", "medicine_id"),
        Index("idx_inventory_stock_status", "stock_status"),
        Index("idx_inventory_last_confirmed", "last_confirmed_at"),
    )

    def __repr__(self) -> str:
        return f"<Inventory(id={self.id}, pharmacy={self.pharmacy_id}, medicine={self.medicine_id}, status={self.stock_status})>"

    def is_stale(self, threshold_hours: Optional[int] = None) -> bool:
        """
        Check if inventory is stale (not recently confirmed).

        Args:
            threshold_hours: Hours after which inventory is stale.
                           Uses config default if None.

        Returns:
            True if last_confirmed_at is older than threshold.
        """
        from .config import get_stale_threshold_hours

        if threshold_hours is None:
            threshold_hours = get_stale_threshold_hours()

        now = utc_now()
        delta = now - self.last_confirmed_at
        return delta.total_seconds() > threshold_hours * 3600


class StockProposal(Base):
    """
    AI-parsed updates awaiting chemist confirmation.
    """
    __tablename__ = "stock_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False
    )
    chemist_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    raw_message: Mapped[str] = mapped_column(Text, nullable=False)
    brand_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parsed_salt: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_strength: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_form: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parsed_quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    parsed_intent: Mapped[StockStatus] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(REAL, nullable=False)
    status: Mapped[ProposalStatus] = mapped_column(
        Text, nullable=False, default=ProposalStatus.pending
    )
    flagged_for_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    renewed_from_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("stock_proposals.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    review_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_message_ref: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    # Relationships
    pharmacy: Mapped["Pharmacy"] = relationship("Pharmacy", back_populates="proposals")
    chemist: Mapped["User"] = relationship(
        "User", back_populates="proposals", foreign_keys=[chemist_user_id]
    )
    confirmed_by_user: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[confirmed_by]
    )
    renewed_from: Mapped[Optional["StockProposal"]] = relationship(
        "StockProposal", remote_side=[id], foreign_keys=[renewed_from_id]
    )
    history: Mapped[List["StockHistory"]] = relationship(
        "StockHistory", back_populates="source_proposal"
    )

    __table_args__ = (
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0", name="ck_proposals_confidence"),
        CheckConstraint("parsed_quantity IS NULL OR parsed_quantity >= 0", name="ck_proposals_quantity_nonneg"),
        Index("idx_proposals_status", "status"),
        Index("idx_proposals_flagged", "flagged_for_review"),
        Index("idx_proposals_pharmacy", "pharmacy_id"),
        Index("idx_proposals_chemist", "chemist_user_id"),
        Index("idx_proposals_expires", "expires_at"),
        Index("idx_proposals_source_message_ref", "source_message_ref"),
    )

    def __repr__(self) -> str:
        return f"<StockProposal(id={self.id}, pharmacy={self.pharmacy_id}, salt={self.parsed_salt!r}, status={self.status})>"


class StockHistory(Base):
    """
    Immutable audit log of every applied change (+ undo entries).
    """
    __tablename__ = "stock_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inventory_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("inventory.id", ondelete="RESTRICT"), nullable=False
    )
    pharmacy_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False
    )
    medicine_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("medicines.id", ondelete="RESTRICT"), nullable=False
    )
    changed_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    source_proposal_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("stock_proposals.id", ondelete="SET NULL"), nullable=True
    )
    change_type: Mapped[HistoryChangeType] = mapped_column(Text, nullable=False)
    undo_of_history_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("stock_history.id", ondelete="RESTRICT"), unique=True, nullable=True
    )
    previous_stock_status: Mapped[Optional[StockStatus]] = mapped_column(Text, nullable=True)
    new_stock_status: Mapped[StockStatus] = mapped_column(Text, nullable=False)
    previous_quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    new_quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    previous_last_confirmed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    change_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now
    )

    # Relationships
    inventory: Mapped["Inventory"] = relationship("Inventory", back_populates="history")
    pharmacy: Mapped["Pharmacy"] = relationship("Pharmacy", back_populates="history")
    medicine: Mapped["Medicine"] = relationship("Medicine")
    changed_by_user: Mapped["User"] = relationship("User", back_populates="history", foreign_keys=[changed_by])
    source_proposal: Mapped[Optional["StockProposal"]] = relationship(
        "StockProposal", back_populates="history", foreign_keys=[source_proposal_id]
    )
    undo_of: Mapped[Optional["StockHistory"]] = relationship(
        "StockHistory", remote_side=[id], foreign_keys=[undo_of_history_id]
    )

    __table_args__ = (
        CheckConstraint("previous_quantity IS NULL OR previous_quantity >= 0", name="ck_history_prev_qty_nonneg"),
        CheckConstraint("new_quantity IS NULL OR new_quantity >= 0", name="ck_history_new_qty_nonneg"),
        # Note: Cannot express "undo entry cannot be undone" as a check constraint
        # because it requires checking change_type != 'UNDO' on the target row.
        # This is enforced in the operations layer.
        Index("idx_history_inventory", "inventory_id"),
        Index("idx_history_pharmacy", "pharmacy_id"),
        Index("idx_history_user", "changed_by"),
        Index("idx_history_created", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<StockHistory(id={self.id}, inventory={self.inventory_id}, type={self.change_type})>"


class SubstitutionApproval(Base):
    """
    Per-request pharmacist approvals for salt-equivalent substitutions.
    """
    __tablename__ = "substitution_approvals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False
    )
    requested_medicine_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("medicines.id", ondelete="RESTRICT"), nullable=False
    )
    substitute_medicine_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("medicines.id", ondelete="RESTRICT"), nullable=False
    )
    approving_pharmacist_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[SubstitutionStatus] = mapped_column(Text, nullable=False)
    request_reference: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now
    )

    # Relationships
    pharmacy: Mapped["Pharmacy"] = relationship("Pharmacy", back_populates="substitution_approvals")
    requested_medicine: Mapped["Medicine"] = relationship(
        "Medicine", back_populates="requested_substitutions", foreign_keys=[requested_medicine_id]
    )
    substitute_medicine: Mapped["Medicine"] = relationship(
        "Medicine", back_populates="substitute_substitutions", foreign_keys=[substitute_medicine_id]
    )
    approving_pharmacist: Mapped["User"] = relationship(
        "User", back_populates="substitution_approvals", foreign_keys=[approving_pharmacist_id]
    )

    __table_args__ = (
        CheckConstraint("requested_medicine_id != substitute_medicine_id", name="ck_sub_approvals_diff_med"),
        Index("idx_sub_approvals_pharmacy", "pharmacy_id"),
        Index("idx_sub_approvals_pharmacist", "approving_pharmacist_id"),
        Index("idx_sub_approvals_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<SubstitutionApproval(id={self.id}, pharmacy={self.pharmacy_id}, status={self.status})>"


class SOSBroadcast(Base):
    """
    OPTIONAL: Patient SOS broadcasts for medicine availability.
    """
    __tablename__ = "sos_broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    patient_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    medicine_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("medicines.id", ondelete="RESTRICT"), nullable=False
    )
    latitude: Mapped[Optional[float]] = mapped_column(REAL, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(REAL, nullable=True)
    radius_km: Mapped[float] = mapped_column(REAL, nullable=False, default=10.0)
    status: Mapped[SosStatus] = mapped_column(Text, nullable=False, default=SosStatus.active)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    fulfilled_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now
    )

    # Relationships
    patient: Mapped["User"] = relationship("User")
    medicine: Mapped["Medicine"] = relationship("Medicine")
    responses: Mapped[List["SOSResponse"]] = relationship(
        "SOSResponse", back_populates="broadcast", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_sos_broadcasts_patient", "patient_user_id"),
        Index("idx_sos_broadcasts_status", "status"),
        Index("idx_sos_broadcasts_expires", "expires_at"),
    )

    def __repr__(self) -> str:
        return f"<SOSBroadcast(id={self.id}, patient={self.patient_user_id}, status={self.status})>"


class SOSResponse(Base):
    """
    OPTIONAL: Chemist responses to SOS broadcasts.
    """
    __tablename__ = "sos_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sos_broadcast_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sos_broadcasts.id", ondelete="CASCADE"), nullable=False
    )
    pharmacy_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pharmacies.id", ondelete="RESTRICT"), nullable=False
    )
    chemist_user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    can_supply: Mapped[bool] = mapped_column(Boolean, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    responded_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now
    )

    # Relationships
    broadcast: Mapped["SOSBroadcast"] = relationship("SOSBroadcast", back_populates="responses")
    pharmacy: Mapped["Pharmacy"] = relationship("Pharmacy")
    chemist: Mapped["User"] = relationship("User")

    __table_args__ = (
        Index("idx_sos_responses_broadcast", "sos_broadcast_id"),
    )

    def __repr__(self) -> str:
        return f"<SOSResponse(id={self.id}, broadcast={self.sos_broadcast_id}, can_supply={self.can_supply})>"