# MediBridge Technical Architecture: End-to-End Mobile & Backend Specification

**Version:** 2.0  
**Date:** 2026-10-25  
**Document Status:** Production Reference  

---

## 1. High-Level 4-Tier Topology

MediBridge enforces a clean separation of concerns across four connected tiers:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        1. USER INTERFACE TIER                          │
│                                                                        │
│    ┌───────────────────────────┐      ┌───────────────────────────┐    │
│    │  Patient Mobile View      │      │  Chemist Mobile View      │    │
│    │  - Search & Discovery     │      │  - Conversational NLP     │    │
│    │  - Geo Nearby Pharmacies  │      │  - Proposal Confirmation  │    │
│    │  - Substitute Alternatives│      │  - Stock Audit & Undo     │    │
│    └─────────────┬─────────────┘      └─────────────┬─────────────┘    │
│                  │                                  │                  │
│                  └─────────────────┬────────────────┘                  │
└────────────────────────────────────┼───────────────────────────────────┘
                                     │ JSON over HTTPS (CORS-enabled)
┌────────────────────────────────────▼───────────────────────────────────┐
│                       2. REST API GATEWAY TIER                         │
│                                                                        │
│    FastAPI Application (`api/main.py`)                                 │
│    - Route Dispatching, CORS Middleware & Error Normalization          │
│    - Endpoint Validation via Pydantic Schemas                          │
│    - Geolocation Haversine Distance Computation                        │
│    - Server-side User & Pharmacy Scoping Authorization                 │
└────────────────────────────────────┬───────────────────────────────────┘
                                     │ Pure Python Function Invocations
┌────────────────────────────────────▼───────────────────────────────────┐
│                          3. SERVICE LAYER                              │
│                                                                        │
│    ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐ │
│    │  Proposal Service │  │Confirmation Service│ │  History Service  │ │
│    │ (process_stock_   │  │(confirm_and_apply_ │ │ (orchestrate_undo)│ │
│    │      message)     │  │     proposal)      │ │                   │ │
│    └─────────┬─────────┘  └─────────┬─────────┘  └─────────┬─────────┘ │
│              │                      │                      │           │
│    ┌─────────┴─────────┐  ┌─────────┴─────────┐            │           │
│    │  AI Parser Engine │  │ Proposal Adapter  │            │           │
│    │ (Deterministic &  │  │(compute_review_   │            │           │
│    │   LLM Fallback)   │  │      flag)        │            │           │
│    └───────────────────┘  └───────────────────┘            │           │
│                                                            │           │
│    ┌───────────────────────────────────────────────────────┴─────────┐ │
│    │  Operations Adapter (`DBOperationsAdapter`)                     │ │
│    │  Transaction Boundary Management, UTC Timestamp Enforcement     │ │
│    └────────────────────────────────┬────────────────────────────────┘ │
└─────────────────────────────────────┼──────────────────────────────────┘
                                      │ SQLAlchemy 2.0 ORM & Operations
┌─────────────────────────────────────▼──────────────────────────────────┐
│                         4. DATA PERSISTENCE TIER                       │
│                                                                        │
│    Pure Data Access: `medibridge_db/operations.py` (23 Functions)      │
│    Relational Models: `medibridge_db/models.py` (11 Relational Tables) │
│    Storage Engine: SQLite with WAL Mode & Foreign Keys (`PRAGMA`)      │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 5-Stage AI / NLP Processing Pipeline

Chemist inventory updates follow an immutable 5-stage human-in-the-loop pipeline:

```mermaid
sequenceDiagram
    autonumber
    actor Chemist
    participant MobileUI as Mobile App
    participant Gateway as FastAPI Gateway
    participant Svc as Proposal Service
    participant NLP as AI Parser Engine
    participant Adapt as Proposal Adapter
    participant DB as SQLite Database

    Chemist->>MobileUI: Submits text "Dolo 650 20 strips available"
    MobileUI->>Gateway: POST /api/proposals (raw_message, chemist_user_id, pharmacy_id)
    Gateway->>Svc: process_stock_message()
    Svc->>DB: is_user_linked_to_pharmacy(chemist_user_id, pharmacy_id)
    alt Unauthorized
        DB-->>Svc: False
        Svc-->>Gateway: UNAUTHORIZED
        Gateway-->>MobileUI: HTTP 403 Forbidden
    end

    Note over Svc,NLP: Stage 1 & 2: Natural Language Understanding
    Svc->>NLP: parse_stock_message(raw_message)
    NLP->>NLP: Deterministic regex parsing (English / Hindi / Hinglish)
    opt Primary returned None & API Key Present
        NLP->>NLP: LLM Fallback (Groq / Gemini)
    end

    Note over Svc,Adapt: Stage 3: Entity Extraction & Catalogue Lookup
    Svc->>DB: find_medicine_by_alias() or search_medicines()
    alt Multiple Candidates
        Svc-->>Gateway: UNRESOLVED / Disambiguation
        Gateway-->>MobileUI: HTTP 200 (status: DISAMBIGUATION_REQUIRED)
    end

    Note over Adapt: Stage 4: Safety & Regulatory Validation
    Adapt->>Adapt: compute_review_flag(confidence, is_schedule_h)
    Note right of Adapt: Flagged if confidence < 0.8 OR Schedule H drug

    Note over Svc,DB: Stage 5: Unconfirmed Proposal Persistence
    Svc->>DB: create_stock_proposal(status=PENDING, expires_at=now+24h)
    DB-->>Svc: Proposal Record (id=56)
    Svc-->>Gateway: Success (Proposal #56, PENDING)
    Gateway-->>MobileUI: HTTP 200 (status: CREATED, proposals: [...])
    
    MobileUI->>Chemist: Renders structured proposal card for human verification
```

---

## 3. Human-in-the-Loop Confirmation & Undo Sequence

```mermaid
sequenceDiagram
    autonumber
    actor Chemist
    participant MobileUI as Mobile App
    participant Gateway as FastAPI Gateway
    participant ConfSvc as Confirmation Service
    participant UndoSvc as History Service
    participant DB as Database Engine

    Chemist->>MobileUI: Taps "Confirm Stock Update"
    MobileUI->>Gateway: POST /api/proposals/{id}/confirm
    Gateway->>ConfSvc: confirm_and_apply_proposal(proposal_id, user_id, pharmacy_id)
    ConfSvc->>DB: Verify status is PENDING and not expired (<24h)
    ConfSvc->>DB: Atomic Transaction:
    Note over ConfSvc,DB: 1. Update Inventory row (quantity, status, last_confirmed_at=now)<br/>2. Create StockHistory record (STOCK_UPDATE)<br/>3. Mark Proposal as CONFIRMED
    DB-->>ConfSvc: Committed
    ConfSvc-->>Gateway: ConfirmationResult(success=True)
    Gateway-->>MobileUI: HTTP 200 (Success)
    MobileUI->>Chemist: Visual confirmation banner + Undo button

    opt Mistake Made / Undo Requested
        Chemist->>MobileUI: Taps "Undo Change"
        MobileUI->>Gateway: POST /api/history/{history_id}/undo
        Gateway->>UndoSvc: orchestrate_undo(history_id, user_id, pharmacy_id)
        UndoSvc->>DB: Validate invariants (not an UNDO, not already undone, no newer changes)
        UndoSvc->>DB: Atomic Transaction:
        Note over UndoSvc,DB: 1. Restore previous quantity and stock_status<br/>2. Create new StockHistory record (change_type=UNDO, undo_of_history_id)
        DB-->>UndoSvc: Committed
        UndoSvc-->>Gateway: UndoExecutionResult(success=True)
        Gateway-->>MobileUI: HTTP 200 (Restored Previous Inventory)
    end
```

---

## 4. Security & Isolation Boundaries

1. **Server-Side Authorization**: Every state-mutating endpoint independently validates that `chemist_user_id` holds active employment at `pharmacy_id`.
2. **Transaction Integrity**: Inventory mutations occur exclusively inside explicit ACID transaction boundaries in `DBOperationsAdapter`. Unhandled errors automatically invoke rollback.
3. **No Direct Frontend-DB Link**: The mobile client has zero direct database connectivity or credentials.
4. **Zero Live Placeholder Leakage**: All values displayed in live flows originate from API payloads.
