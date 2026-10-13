"""
MediBridge Database Package

Provides database configuration, models, and operations for the MediBridge
Telegram assistant. This package handles all data persistence including
pharmacy inventory, stock updates, substitute proposals, and audit history.
"""

from .config import (
    get_database_url,
    get_test_database_url,
    get_stale_threshold_hours,
)
from .database import (
    Base,
    SessionLocal,
    engine,
    get_session,
    init_db,
    drop_db,
    UTCDateTime,
    utc_now,
)
from .models import (
    # Enums
    UserRole,
    PharmacyVerification,
    StaffRoleAtPharmacy,
    StockStatus,
    ProposalStatus,
    HistoryChangeType,
    SubstitutionStatus,
    SosStatus,
    AliasType,
    UpdateSource,
    # Models
    Pharmacy,
    User,
    UserPharmacy,
    Medicine,
    MedicineAlias,
    Inventory,
    StockProposal,
    StockHistory,
    SubstitutionApproval,
    SOSBroadcast,
    SOSResponse,
)
from .normalize import normalize_composition

__all__ = [
    # Config
    "get_database_url",
    "get_test_database_url",
    "get_stale_threshold_hours",
    # Database
    "Base",
    "SessionLocal",
    "engine",
    "get_session",
    "init_db",
    "drop_db",
    "UTCDateTime",
    "utc_now",
    # Enums
    "UserRole",
    "PharmacyVerification",
    "StaffRoleAtPharmacy",
    "StockStatus",
    "ProposalStatus",
    "HistoryChangeType",
    "SubstitutionStatus",
    "SosStatus",
    "AliasType",
    "UpdateSource",
    # Models
    "Pharmacy",
    "User",
    "UserPharmacy",
    "Medicine",
    "MedicineAlias",
    "Inventory",
    "StockProposal",
    "StockHistory",
    "SubstitutionApproval",
    "SOSBroadcast",
    "SOSResponse",
    # Normalize
    "normalize_composition",
]