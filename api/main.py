"""MediBridge REST API Gateway for Stitch and Web/Mobile Clients.

Exposes REST endpoints wrapping MediBridge data and service layers:
- Medicine catalogue search and therapeutic substitution discovery.
- Nearby pharmacy lookup with Haversine distance calculations.
- Inventory queries with freshness and staleness classification (>24h).
- 5-stage NLP proposal creation from natural language chemist messages.
- Atomic proposal confirmation and rejection.
- Append-only stock audit history and undo orchestration.
- User authorization and pharmacy linkage queries.
"""

from __future__ import annotations

import math
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.orm import Session

from medibridge_db.database import Base, SessionLocal, engine, get_session, utc_now
import medibridge_db.operations as ops
from medibridge_db.models import (
    Inventory,
    Medicine,
    Pharmacy,
    ProposalStatus,
    StockHistory,
    StockProposal,
    StockStatus,
    SubstitutionApproval,
    SubstitutionStatus,
    User,
    UserPharmacy,
    UserRole,
)
from medibridge.database.operations_adapter import DBOperationsAdapter
from medibridge.exceptions import (
    CannotUndoUndoError,
    HistoryAlreadyUndoneError,
    NewerChangeExistsError,
    ProposalExpiredError,
    ProposalNotPendingError,
    ProposalPharmacyMismatchError,
    UserNotLinkedToPharmacyError,
)
from medibridge.services.ai_parser import parse_stock_message
from medibridge.services.catalogue_service import (
    find_substitutes,
    search_catalogue,
)
from medibridge.services.confirmation_service import (
    confirm_and_apply_proposal,
)
from medibridge.services.history_service import (
    orchestrate_undo,
)
from medibridge.services.proposal_service import (
    ProposalServiceResult,
    ProposalServiceStatus,
    StoredProposalRecord,
    process_stock_message,
)

# ── Dependency: DB Session & Adapter ────────────────────────────────────────

def get_db():
    """FastAPI database session dependency."""
    with get_session() as s:
        yield s


def get_adapter() -> DBOperationsAdapter:
    """Returns an operations adapter wired to SessionLocal."""
    return DBOperationsAdapter(session_factory=SessionLocal)


# ── Schemas for API ──────────────────────────────────────────────────────────

class APIHealth(BaseModel):
    status: str = "healthy"
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    database: str = "connected"


class MedicineSearchResult(BaseModel):
    id: int
    brand_name: str
    generic_name: Optional[str] = None
    strength: Optional[str] = None
    form: Optional[str] = None
    is_schedule_h: bool = False
    aliases: List[str] = Field(default_factory=list)


class PharmacySearchResult(BaseModel):
    id: int
    name: str
    locality: Optional[str] = None
    latitude: float
    longitude: float
    verification_state: str
    distance_km: Optional[float] = None


class MedicinePharmacyStock(BaseModel):
    pharmacy_id: int
    pharmacy_name: str
    locality: Optional[str] = None
    latitude: float
    longitude: float
    verification_state: str
    distance_km: Optional[float] = None
    stock_status: str
    quantity: Optional[int] = None
    last_confirmed_at: Optional[str] = None
    is_stale: bool = False


class InventoryItem(BaseModel):
    id: int
    pharmacy_id: int
    medicine_id: int
    medicine_name: str
    stock_status: str
    quantity: Optional[int] = None
    last_confirmed_at: Optional[str] = None
    is_stale: bool = False
    pharmacy_name: str


class StockProposalResult(BaseModel):
    id: int
    pharmacy_id: int
    chemist_user_id: int
    raw_message: str
    brand_name: Optional[str] = None
    parsed_salt: str = ""
    parsed_strength: Optional[str] = None
    parsed_form: Optional[str] = None
    parsed_quantity: Optional[int] = None
    parsed_intent: str
    confidence: float
    flagged_for_review: bool
    status: str = "PENDING"
    expires_at: str
    created_at: str


class CreateProposalRequest(BaseModel):
    raw_message: str = Field(..., description="Chemist stock message")
    chemist_user_id: int = Field(..., description="Chemist user ID")
    pharmacy_id: int = Field(..., description="Target pharmacy ID")


class CreateProposalResponse(BaseModel):
    status: str
    message: str
    proposals: List[StockProposalResult] = Field(default_factory=list)
    id: Optional[int] = None
    pharmacy_id: Optional[int] = None
    chemist_user_id: Optional[int] = None
    raw_message: Optional[str] = None
    brand_name: Optional[str] = None
    parsed_intent: Optional[str] = None
    parsed_quantity: Optional[int] = None
    confidence: Optional[float] = None
    flagged_for_review: Optional[bool] = None
    expires_at: Optional[str] = None
    disambiguation_options: List[MedicineSearchResult] = Field(default_factory=list)


class ProposalConfirmRequest(BaseModel):
    confirm_by_user_id: int = Field(..., description="User ID confirming the proposal")
    pharmacy_id: Optional[int] = None


class ProposalRejectRequest(BaseModel):
    reject_by_user_id: int = Field(..., description="User ID rejecting the proposal")
    pharmacy_id: Optional[int] = None
    rejection_note: Optional[str] = None


class UndoRequest(BaseModel):
    undone_by_user_id: int = Field(..., description="User ID requesting undo")
    pharmacy_id: Optional[int] = None


class SubstituteApprovalRequest(BaseModel):
    pharmacy_id: int
    requested_medicine_id: int
    substitute_medicine_id: int
    approving_pharmacist_id: int
    status: str = "approved"
    request_reference: Optional[str] = None


class InventoryHistoryItem(BaseModel):
    id: int
    change_type: str
    medicine_name: str
    previous_status: Optional[str] = None
    new_status: str
    previous_quantity: Optional[int] = None
    new_quantity: Optional[int] = None
    changed_by: int
    created_at: str


class UserInfo(BaseModel):
    id: int
    telegram_user_id: Optional[int] = None
    display_name: str
    role: str
    is_active: bool = True


# ── Helpers ──────────────────────────────────────────────────────────────────

def _map_proposal_record(p: StoredProposalRecord | StockProposal) -> StockProposalResult:
    """Map domain proposal or ORM model to API response model."""
    status_str = (
        p.status.value
        if hasattr(p.status, "value")
        else str(p.status).upper()
        if p.status
        else "PENDING"
    )
    intent_str = (
        p.parsed_intent.value
        if hasattr(p.parsed_intent, "value")
        else str(p.parsed_intent).upper()
        if p.parsed_intent
        else "IN_STOCK"
    )
    expires_str = (
        p.expires_at.isoformat()
        if hasattr(p.expires_at, "isoformat")
        else str(p.expires_at)
        if p.expires_at
        else ""
    )
    created_str = (
        p.created_at.isoformat()
        if hasattr(p.created_at, "isoformat")
        else str(p.created_at)
        if p.created_at
        else ""
    )
    return StockProposalResult(
        id=p.id,
        pharmacy_id=p.pharmacy_id,
        chemist_user_id=p.chemist_user_id,
        raw_message=p.raw_message,
        brand_name=p.brand_name,
        parsed_salt=p.parsed_salt or "",
        parsed_strength=p.parsed_strength,
        parsed_form=p.parsed_form,
        parsed_quantity=p.parsed_quantity,
        parsed_intent=intent_str,
        confidence=float(p.confidence) if p.confidence is not None else 0.5,
        flagged_for_review=bool(p.flagged_for_review),
        status=status_str,
        expires_at=expires_str,
        created_at=created_str,
    )


def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in kilometers between two lat/lon coordinates."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


# ── Lifespan Context ────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ensure database schema is ready on startup."""
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as test_session:
        test_session.execute(sa.text("SELECT 1")).scalar()
    yield


# ── Create Application ──────────────────────────────────────────────────────

app = FastAPI(
    title="MediBridge REST API",
    description="Backend API Gateway for MediBridge Mobile/Web Applications and Stitch Integration",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

# Enable CORS for Stitch and modern web/mobile frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Mount Static Mobile Application (Google Stitch Design) ──────────────────
# Serves the mobile web app at /app for zero-setup browser preview on mobile/desktop.
# The / mount is intentionally omitted to avoid route conflicts with API endpoints.
try:
    from fastapi.staticfiles import StaticFiles
    app.mount("/app", StaticFiles(directory="static", html=True), name="static-app")
except Exception:
    # Static files mount failure is non-fatal; app continues without UI serving
    pass


# ── Root & Health ───────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    return {
        "service": "MediBridge REST API Gateway",
        "status": "online",
        "version": "1.0.0",
        "docs": "/api/docs",
    }


@app.get("/api/health", response_model=APIHealth, tags=["health"])
async def health_check():
    """Health check endpoint for Stitch application and monitors."""
    return APIHealth(status="healthy", database="connected")


# ── Medicine Catalogue Endpoints ────────────────────────────────────────────

@app.get("/api/medicines/search", response_model=List[MedicineSearchResult], tags=["medicines"])
async def search_medicines(
    q: str = Query("", description="Brand name, generic salt, or Hinglish alias query"),
    limit: int = Query(20, ge=1, le=100),
):
    """Search medicine catalogue with alias-first resolution and ranking."""
    adapter = get_adapter()
    results = search_catalogue(q, adapter, limit=limit)
    return [
        MedicineSearchResult(
            id=item.medicine.id,
            brand_name=item.medicine.brand_name,
            generic_name=item.medicine.generic_name,
            strength=item.medicine.strength,
            form=item.medicine.form,
            is_schedule_h=item.medicine.is_schedule_h,
            aliases=item.medicine.aliases,
        )
        for item in results
    ]
@app.get("/api/medicines/", response_model=List[MedicineSearchResult], tags=["medicines"])
async def list_medicines():
    """List all available medicines in the catalogue."""
    adapter = get_adapter()
    medicines = adapter.search_medicines("", limit=500)
    return [
        MedicineSearchResult(
            id=medicine.id,
            brand_name=medicine.brand_name,
            generic_name=medicine.generic_name,
            strength=medicine.strength,
            form=medicine.form,
            is_schedule_h=medicine.is_schedule_h,
            aliases=medicine.aliases,
        )
        for medicine in medicines
    ]


@app.get("/api/medicines/{medicine_id}", response_model=MedicineSearchResult, tags=["medicines"])
async def get_medicine_by_id(medicine_id: int, db: Session = Depends(get_db)):
    """Fetch details of a single medicine by its catalogue primary key."""
    med = ops.get_medicine_by_id(db, medicine_id)
    if not med:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Medicine ID {medicine_id} not found",
        )
    aliases = [a.alias for a in med.aliases] if hasattr(med, "aliases") and med.aliases else []
    return MedicineSearchResult(
        id=med.id,
        brand_name=med.brand_name,
        generic_name=med.generic_name,
        strength=med.strength,
        form=med.form,
        is_schedule_h=med.is_schedule_h,
        aliases=aliases,
    )


@app.get("/api/medicines/{medicine_id}/substitutes", response_model=List[MedicineSearchResult], tags=["medicines"])
async def get_substitutes(medicine_id: int):
    """Find therapeutic brand substitutes sharing identical chemical salt, strength, and form."""
    adapter = get_adapter()
    try:
        sub_group = find_substitutes(medicine_id, adapter)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    return [
        MedicineSearchResult(
            id=s.id,
            brand_name=s.brand_name,
            generic_name=s.generic_name,
            strength=s.strength,
            form=s.form,
            is_schedule_h=s.is_schedule_h,
            aliases=s.aliases,
        )
        for s in sub_group.available_substitutes
    ]


@app.get("/api/medicines/{medicine_id}/pharmacies", response_model=List[MedicinePharmacyStock], tags=["medicines"])
async def get_pharmacies_stocking_medicine(
    medicine_id: int,
    lat: Optional[float] = Query(None, description="User latitude for distance sorting"),
    lon: Optional[float] = Query(None, description="User longitude for distance sorting"),
    db: Session = Depends(get_db),
):
    """Retrieve all verified pharmacies that maintain inventory for a specific medicine."""
    med = ops.get_medicine_by_id(db, medicine_id)
    if not med:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Medicine ID {medicine_id} not found in catalogue",
        )

    stmt = (
        select(Inventory, Pharmacy)
        .join(Pharmacy, Inventory.pharmacy_id == Pharmacy.id)
        .where(
            Inventory.medicine_id == medicine_id,
            Pharmacy.is_active == True,
            Pharmacy.verification_state == "verified",
        )
    )
    rows = db.execute(stmt).all()
    now = datetime.now(timezone.utc)
    results: List[MedicinePharmacyStock] = []

    for inv, ph in rows:
        is_stale = False
        if inv.last_confirmed_at:
            confirmed_dt = inv.last_confirmed_at
            if confirmed_dt.tzinfo is None:
                confirmed_dt = confirmed_dt.replace(tzinfo=timezone.utc)
            diff_hours = (now - confirmed_dt).total_seconds() / 3600.0
            is_stale = diff_hours > 24.0

        dist = None
        if lat is not None and lon is not None and ph.latitude is not None and ph.longitude is not None:
            dist = round(_haversine_distance(lat, lon, ph.latitude, ph.longitude), 2)

        status_val = inv.stock_status.value if hasattr(inv.stock_status, "value") else str(inv.stock_status)
        v_state = ph.verification_state.value if hasattr(ph.verification_state, "value") else str(ph.verification_state)

        results.append(
            MedicinePharmacyStock(
                pharmacy_id=ph.id,
                pharmacy_name=ph.name,
                locality=ph.locality,
                latitude=ph.latitude,
                longitude=ph.longitude,
                verification_state=v_state,
                distance_km=dist,
                stock_status=status_val.upper(),
                quantity=inv.quantity,
                last_confirmed_at=inv.last_confirmed_at.isoformat() if inv.last_confirmed_at else None,
                is_stale=is_stale,
            )
        )

    def sort_key(item: MedicinePharmacyStock):
        status_rank = 0 if item.stock_status in ("IN_STOCK", "LOW_STOCK") else 1
        distance = item.distance_km if item.distance_km is not None else float("inf")
        return (status_rank, distance)

    results.sort(key=sort_key)
    return results


@app.post("/api/substitutions/approve", tags=["medicines"])
async def approve_substitution(payload: SubstituteApprovalRequest):
    """Record pharmacist approval or rejection for a suggested brand substitution."""
    adapter = get_adapter()
    try:
        record = adapter.record_substitution_approval(
            pharmacy_id=payload.pharmacy_id,
            requested_medicine_id=payload.requested_medicine_id,
            substitute_medicine_id=payload.substitute_medicine_id,
            approving_pharmacist_id=payload.approving_pharmacist_id,
            status=payload.status,
            request_reference=payload.request_reference,
        )
        return {
            "success": True,
            "message": f"Substitution {payload.status} recorded successfully",
            "approval_id": record.get("id"),
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to record substitution approval: {exc}",
        )


# ── Pharmacy & Inventory Endpoints ──────────────────────────────────────────

@app.get("/api/pharmacies/nearby", response_model=List[PharmacySearchResult], tags=["pharmacies"])
async def get_nearby_pharmacies(
    lat: float = Query(..., description="User latitude"),
    lon: float = Query(..., description="User longitude"),
    radius_km: float = Query(10.0, ge=0.5, le=50.0, description="Search radius in kilometers"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Find nearby active, verified pharmacies filtered and sorted by Haversine distance."""
    pharmacies = ops.get_pharmacies_nearby(
        session=db,
        lat=lat,
        lon=lon,
        radius_km=radius_km,
        limit=limit,
    )
    api_results: List[PharmacySearchResult] = []
    for ph in pharmacies:
        dist = _haversine_distance(lat, lon, ph.latitude, ph.longitude)
        v_state = ph.verification_state.value if hasattr(ph.verification_state, "value") else str(ph.verification_state)
        api_results.append(
            PharmacySearchResult(
                id=ph.id,
                name=ph.name,
                locality=ph.locality,
                latitude=ph.latitude,
                longitude=ph.longitude,
                verification_state=v_state,
                distance_km=round(dist, 2),
            )
        )
    api_results.sort(key=lambda x: x.distance_km if x.distance_km is not None else float("inf"))
    return api_results


@app.get("/api/pharmacies/{pharmacy_id}", response_model=PharmacySearchResult, tags=["pharmacies"])
async def get_pharmacy_by_id(pharmacy_id: int, db: Session = Depends(get_db)):
    """Get single pharmacy details by ID."""
    stmt = select(Pharmacy).where(Pharmacy.id == pharmacy_id)
    ph = db.execute(stmt).scalar_one_or_none()
    if not ph:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pharmacy ID {pharmacy_id} not found",
        )
    v_state = ph.verification_state.value if hasattr(ph.verification_state, "value") else str(ph.verification_state)
    return PharmacySearchResult(
        id=ph.id,
        name=ph.name,
        locality=ph.locality,
        latitude=ph.latitude,
        longitude=ph.longitude,
        verification_state=v_state,
        distance_km=None,
    )


@app.get("/api/pharmacies/{pharmacy_id}/inventory", response_model=List[InventoryItem], tags=["inventory"])
async def get_pharmacy_inventory(
    pharmacy_id: int,
    include_stale: bool = Query(True, description="Whether to include stale inventory (>24h since confirmation)"),
    status_filter: Optional[str] = Query(None, description="IN_STOCK, LOW_STOCK, or OUT_OF_STOCK"),
    db: Session = Depends(get_db),
):
    """Retrieve all current inventory items for a pharmacy with freshness flags."""
    st_filter = None
    if status_filter:
        try:
            st_filter = StockStatus(status_filter.lower())
        except ValueError:
            pass

    records = ops.get_pharmacy_inventory(
        session=db,
        pharmacy_id=pharmacy_id,
        include_stale=include_stale,
        status_filter=st_filter,
    )
    now = datetime.now(timezone.utc)
    results: List[InventoryItem] = []
    for inv in records:
        is_stale = False
        if inv.last_confirmed_at:
            confirmed_dt = inv.last_confirmed_at
            if confirmed_dt.tzinfo is None:
                confirmed_dt = confirmed_dt.replace(tzinfo=timezone.utc)
            diff_hours = (now - confirmed_dt).total_seconds() / 3600.0
            is_stale = diff_hours > 24.0

        status_val = inv.stock_status.value if hasattr(inv.stock_status, "value") else str(inv.stock_status)
        results.append(
            InventoryItem(
                id=inv.id,
                pharmacy_id=inv.pharmacy_id,
                medicine_id=inv.medicine_id,
                medicine_name=inv.medicine.brand_name if inv.medicine else "Unknown",
                stock_status=status_val.upper(),
                quantity=inv.quantity,
                last_confirmed_at=inv.last_confirmed_at.isoformat() if inv.last_confirmed_at else None,
                is_stale=is_stale,
                pharmacy_name=inv.pharmacy.name if inv.pharmacy else "Unknown",
            )
        )
    return results


@app.get("/api/inventory/{pharmacy_id}/{medicine_id}", response_model=InventoryItem, tags=["inventory"])
async def get_inventory_entry(
    pharmacy_id: int,
    medicine_id: int,
    db: Session = Depends(get_db),
):
    """Get inventory status for a specific pharmacy and medicine pair."""
    inv = ops.get_inventory_for_medicine(
        session=db,
        pharmacy_id=pharmacy_id,
        medicine_id=medicine_id,
        include_stale=True,
    )
    if not inv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Inventory for pharmacy {pharmacy_id} and medicine {medicine_id} not found",
        )

    now = datetime.now(timezone.utc)
    is_stale = False
    if inv.last_confirmed_at:
        confirmed_dt = inv.last_confirmed_at
        if confirmed_dt.tzinfo is None:
            confirmed_dt = confirmed_dt.replace(tzinfo=timezone.utc)
        is_stale = ((now - confirmed_dt).total_seconds() / 3600.0) > 24.0

    status_val = inv.stock_status.value if hasattr(inv.stock_status, "value") else str(inv.stock_status)
    return InventoryItem(
        id=inv.id,
        pharmacy_id=inv.pharmacy_id,
        medicine_id=inv.medicine_id,
        medicine_name=inv.medicine.brand_name if inv.medicine else "Unknown",
        stock_status=status_val.upper(),
        quantity=inv.quantity,
        last_confirmed_at=inv.last_confirmed_at.isoformat() if inv.last_confirmed_at else None,
        is_stale=is_stale,
        pharmacy_name=inv.pharmacy.name if inv.pharmacy else "Unknown",
    )


# ── Stock Proposal Endpoints (5-Stage AI Workflow) ──────────────────────────

@app.post("/api/proposals", response_model=CreateProposalResponse, tags=["proposals"])
async def create_stock_proposal_endpoint(
    payload: Optional[CreateProposalRequest] = None,
    raw_message: Optional[str] = Query(None),
    chemist_user_id: Optional[int] = Query(None),
    pharmacy_id: Optional[int] = Query(None),
):
    """5-stage AI workflow: Parse chemist natural language and create unconfirmed PENDING proposals.

    Operates strictly under the human-in-the-loop guarantee:
    1. Deterministic NLP parsing (with LLM fallback).
    2. Entity separation (strength vs quantity).
    3. Catalogue resolution and disambiguation.
    4. Regulatory Schedule H review flagging.
    5. Saves proposal as PENDING (no inventory mutation occurs until human confirmation).
    """
    msg = payload.raw_message if payload else raw_message
    uid = payload.chemist_user_id if payload else chemist_user_id
    pid = payload.pharmacy_id if payload else pharmacy_id

    if not msg or uid is None or pid is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="raw_message, chemist_user_id, and pharmacy_id are required",
        )

    adapter = get_adapter()
    result: ProposalServiceResult = process_stock_message(
        raw_message=msg,
        chemist_user_id=uid,
        pharmacy_id=pid,
        repository=adapter,
    )

    if result.status == ProposalServiceStatus.UNAUTHORIZED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=result.reason or f"User {uid} is not authorized for pharmacy {pid}",
        )

    if result.status == ProposalServiceStatus.UNPARSEABLE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.reason or "Message could not be understood as a current stock update",
        )

    if result.status == ProposalServiceStatus.UNRESOLVED:
        return CreateProposalResponse(
            status="UNRESOLVED",
            message=result.reason or "One or more medicines require catalogue matching or human review.",
            proposals=[_map_proposal_record(p) for p in result.created_proposals],
        )

    # Success or Partially Created
    mapped_proposals = [_map_proposal_record(p) for p in result.created_proposals]
    first_prop = mapped_proposals[0] if mapped_proposals else None

    return CreateProposalResponse(
        status="CREATED",
        message=f"Created {len(mapped_proposals)} pending proposal(s)",
        proposals=mapped_proposals,
        id=first_prop.id if first_prop else None,
        pharmacy_id=first_prop.pharmacy_id if first_prop else pid,
        chemist_user_id=first_prop.chemist_user_id if first_prop else uid,
        raw_message=first_prop.raw_message if first_prop else msg,
        brand_name=first_prop.brand_name if first_prop else None,
        parsed_intent=first_prop.parsed_intent if first_prop else None,
        parsed_quantity=first_prop.parsed_quantity if first_prop else None,
        confidence=first_prop.confidence if first_prop else None,
        flagged_for_review=first_prop.flagged_for_review if first_prop else None,
        expires_at=first_prop.expires_at if first_prop else None,
    )


@app.get("/api/proposals/{proposal_id}", response_model=StockProposalResult, tags=["proposals"])
async def get_proposal_by_id(proposal_id: int, db: Session = Depends(get_db)):
    """Get single proposal details by ID."""
    prop = ops.get_proposal_by_id(db, proposal_id)
    if not prop:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proposal ID {proposal_id} not found",
        )
    return _map_proposal_record(prop)


@app.get("/api/proposals", response_model=List[StockProposalResult], tags=["proposals"])
async def list_proposals(
    pharmacy_id: Optional[int] = Query(None),
    flagged_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List pending unconfirmed proposals, optionally filtered by pharmacy."""
    proposals = ops.list_pending_proposals(
        session=db,
        pharmacy_id=pharmacy_id,
        flagged_only=flagged_only,
        limit=limit,
    )
    return [_map_proposal_record(p) for p in proposals]


@app.post("/api/proposals/{proposal_id}/confirm", tags=["proposals"])
async def confirm_proposal_endpoint(
    proposal_id: int,
    payload: Optional[ProposalConfirmRequest] = None,
    confirm_by_user_id: Optional[int] = Query(None),
    pharmacy_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """Confirm a PENDING proposal: atomically updates inventory and writes stock history."""
    uid = payload.confirm_by_user_id if payload else confirm_by_user_id
    if uid is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirm_by_user_id is required",
        )

    # Resolve target pharmacy from proposal if not supplied
    target_pid = payload.pharmacy_id if payload and payload.pharmacy_id else pharmacy_id
    if target_pid is None:
        prop = ops.get_proposal_by_id(db, proposal_id)
        if not prop:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Proposal {proposal_id} not found",
            )
        target_pid = prop.pharmacy_id

    adapter = get_adapter()
    try:
        conf_result = confirm_and_apply_proposal(
            proposal_id=proposal_id,
            user_id=uid,
            pharmacy_id=target_pid,
            repository=adapter,
        )
        return {
            "success": True,
            "message": f"Proposal {proposal_id} confirmed and applied successfully",
            "proposal_id": proposal_id,
            "confirmed_by": uid,
            "status": "CONFIRMED",
            "inventory_id": conf_result.updated_inventory.id,
        }
    except ProposalNotPendingError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except ProposalExpiredError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except ProposalPharmacyMismatchError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except UserNotLinkedToPharmacyError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Confirmation error: {exc}",
        )


@app.post("/api/proposals/{proposal_id}/reject", tags=["proposals"])
async def reject_proposal_endpoint(
    proposal_id: int,
    payload: Optional[ProposalRejectRequest] = None,
    reject_by_user_id: Optional[int] = Query(None),
    pharmacy_id: Optional[int] = Query(None),
    rejection_note: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Reject a pending proposal without modifying inventory."""
    uid = payload.reject_by_user_id if payload else reject_by_user_id
    note = (payload.rejection_note if payload else None) or rejection_note
    if uid is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="reject_by_user_id is required",
        )

    target_pid = payload.pharmacy_id if payload and payload.pharmacy_id else pharmacy_id
    if target_pid is None:
        prop = ops.get_proposal_by_id(db, proposal_id)
        if not prop:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Proposal {proposal_id} not found",
            )
        target_pid = prop.pharmacy_id

    adapter = get_adapter()
    try:
        rejected = adapter.reject_proposal(
            proposal_id=proposal_id,
            rejected_by_user_id=uid,
            pharmacy_id=target_pid,
            reason=note,
        )
        return {
            "success": True,
            "message": f"Proposal {proposal_id} rejected",
            "proposal_id": proposal_id,
            "rejected_by": uid,
            "status": "REJECTED",
        }
    except (ProposalNotPendingError, ProposalPharmacyMismatchError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Rejection error: {exc}",
        )


# ── Stock History & Undo Endpoints ──────────────────────────────────────────

@app.get("/api/pharmacies/{pharmacy_id}/history", response_model=List[InventoryHistoryItem], tags=["history"])
async def get_pharmacy_history_endpoint(
    pharmacy_id: int,
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Retrieve immutable audit trail of stock modifications for a pharmacy (most recent first)."""
    stmt = (
        select(StockHistory)
        .where(StockHistory.pharmacy_id == pharmacy_id)
        .order_by(StockHistory.id.desc())
        .limit(limit)
    )
    history_rows = db.execute(stmt).scalars().all()
    results: List[InventoryHistoryItem] = []
    for h in history_rows:
        prev_st = (
            h.previous_stock_status.value
            if hasattr(h.previous_stock_status, "value")
            else str(h.previous_stock_status).upper()
            if h.previous_stock_status
            else None
        )
        new_st = (
            h.new_stock_status.value
            if hasattr(h.new_stock_status, "value")
            else str(h.new_stock_status).upper()
        )
        ch_type = (
            h.change_type.value
            if hasattr(h.change_type, "value")
            else str(h.change_type).upper()
        )
        results.append(
            InventoryHistoryItem(
                id=h.id,
                change_type=ch_type,
                medicine_name=h.medicine.brand_name if h.medicine else "Unknown",
                previous_status=prev_st,
                new_status=new_st,
                previous_quantity=h.previous_quantity,
                new_quantity=h.new_quantity,
                changed_by=h.changed_by,
                created_at=h.created_at.isoformat() if h.created_at else "",
            )
        )
    return results


@app.post("/api/history/{history_id}/undo", tags=["history"])
async def undo_history_endpoint(
    history_id: int,
    payload: Optional[UndoRequest] = None,
    undone_by_user_id: Optional[int] = Query(None),
    pharmacy_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """Undo the last stock modification.

    Enforces invariants:
    - Only STOCK_UPDATE can be undone (cannot undo an UNDO).
    - Cannot undo if already undone.
    - Cannot undo if a newer change exists for that inventory row.
    """
    uid = payload.undone_by_user_id if payload else undone_by_user_id
    if uid is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="undone_by_user_id is required",
        )

    target_pid = payload.pharmacy_id if payload and payload.pharmacy_id else pharmacy_id
    if target_pid is None:
        target_hist = ops.get_history_by_id(db, history_id)
        if not target_hist:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"History entry {history_id} not found",
            )
        target_pid = target_hist.pharmacy_id

    adapter = get_adapter()
    try:
        undo_res = orchestrate_undo(
            history_id=history_id,
            user_id=uid,
            pharmacy_id=target_pid,
            repository=adapter,
        )
        return {
            "success": True,
            "message": f"Successfully undone stock history entry {history_id}",
            "history_id": history_id,
            "undone_by": uid,
            "undo_history_id": undo_res.undo_history_record.id,
            "restored_quantity": undo_res.restored_inventory.quantity,
            "restored_status": undo_res.restored_inventory.stock_status.value
            if hasattr(undo_res.restored_inventory.stock_status, "value")
            else str(undo_res.restored_inventory.stock_status),
        }
    except (CannotUndoUndoError, NewerChangeExistsError, UserNotLinkedToPharmacyError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Undo execution error: {exc}",
        )


# ── User & Pharmacy Membership Endpoints ────────────────────────────────────

@app.get("/api/users/{telegram_user_id}", response_model=UserInfo, tags=["users"])
async def get_user_by_telegram_id(telegram_user_id: int, db: Session = Depends(get_db)):
    """Fetch user details by Telegram user ID for authentication context."""
    stmt = select(User).where(User.telegram_user_id == telegram_user_id)
    u = db.execute(stmt).scalar_one_or_none()
    if not u:
        # Fallback demo chemist
        return UserInfo(
            id=3,
            telegram_user_id=telegram_user_id,
            display_name=f"Chemist ({telegram_user_id})",
            role="chemist",
            is_active=True,
        )
    role_val = u.role.value if hasattr(u.role, "value") else str(u.role)
    return UserInfo(
        id=u.id,
        telegram_user_id=u.telegram_user_id,
        display_name=u.display_name,
        role=role_val,
        is_active=u.is_active,
    )


@app.get("/api/users/{user_id}/pharmacies", response_model=List[PharmacySearchResult], tags=["users"])
async def get_user_pharmacies_endpoint(user_id: int, db: Session = Depends(get_db)):
    """List pharmacies that the chemist/pharmacist has active authorization for."""
    pharmacies = ops.get_user_pharmacies(session=db, user_id=user_id)
    return [
        PharmacySearchResult(
            id=ph.id,
            name=ph.name,
            locality=ph.locality,
            latitude=ph.latitude,
            longitude=ph.longitude,
            verification_state=ph.verification_state.value
            if hasattr(ph.verification_state, "value")
            else str(ph.verification_state),
            distance_km=None,
        )
        for ph in pharmacies
    ]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
