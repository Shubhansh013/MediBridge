# MediBridge Mobile Application Requirements

**Version:** 1.0  
**Date:** 2026-10-25  
**Document Status:** Approved for Implementation  
**Visual Reference:** Google Stitch Mobile UI Design System  
**Behavioral Reference:** `docs/CONTRACT.md`, `docs/rules.md`, `src/medibridge/services/`  

---

## 1. Product Vision & Dual User Roles

MediBridge is a hyper-local medicine connectivity and inventory management application serving two distinct user roles with strict separation of privileges:

1. **Patient (Public / Unauthenticated & Authenticated Discovery)**:
   - Search medicines by brand name, generic salt, or Hinglish colloquial aliases.
   - Discover verified nearby pharmacies within a configurable radius (default 10 km) sorted by physical Haversine distance.
   - View stock availability states (`IN_STOCK`, `LOW_STOCK`, `OUT_OF_STOCK`) with explicit freshness timestamps.
   - Discover therapeutic generic brand substitutes sharing identical chemical salt, strength, and dosage form.

2. **Chemist / Pharmacist (Staff / Role-Protected Inventory Management)**:
   - Select authorized pharmacy context from verified store affiliations.
   - Update medicine inventory using natural language text updates (English, Hindi, Hinglish).
   - Review AI-generated structured stock proposals (`PENDING`) with parse confidence and Schedule H safety indicators.
   - Confirm or reject proposals (human-in-the-loop: no database mutation occurs without explicit confirmation).
   - View append-only immutable audit history and perform single-step rollbacks (undo) on eligible changes.
   - Record pharmacist review decisions on suggested brand substitutions.

---

## 2. Functional Requirements (FR)

### Module A: Patient Medicine & Pharmacy Discovery

- **FR-PAT-01: Medicine Search**: Patients can input text queries matching brand names (e.g., "Dolo 650"), generic compositions (e.g., "paracetamol"), or registered aliases (e.g., "crocin pain relief"). Queries resolve against the verified catalogue via `/api/medicines/search`.
- **FR-PAT-02: Nearby Pharmacy Mapping**: Using user-supplied geolocation (`lat`, `lon`), locate verified pharmacies within 0.5–50 km radius via `/api/pharmacies/nearby`.
- **FR-PAT-03: Stock Freshness Transparency**: Availability items must display confirmation timestamps and an explicit visual indicator if the record was confirmed >24 hours ago (`is_stale = true`). No fabricated live guarantees.
- **FR-PAT-04: Therapeutic Substitutes**: For a selected medicine, discover verified alternatives sharing identical `composition_key`, `strength`, and `form` via `/api/medicines/{id}/substitutes`.

### Module B: Chemist Natural Language Stock Management

- **FR-CHM-01: Pharmacy Scoping**: Chemists must select an active, verified pharmacy to which their user ID is linked (`/api/users/{user_id}/pharmacies`). Operations on unauthorized stores are rejected server-side with HTTP 403.
- **FR-CHM-02: Natural Language Input**: Chemists submit free-form text updates (e.g., "Dolo 650 20 strips available", "Combiflam khatam ho gaya").
- **FR-CHM-03: 5-Stage NLP Parsing**: Input is parsed offline deterministically (regex + medical rules) with optional LLM fallback (Groq / Gemini) when credentials are provided. Strength (e.g., 650mg) is strictly separated from stock quantity (e.g., 20).
- **FR-CHM-04: Unconfirmed Proposal Creation**: Valid updates generate an unconfirmed proposal stored with status `PENDING` and a 24-hour expiration window (`expires_at`). **Zero inventory mutation occurs during parsing.**
- **FR-CHM-05: Review Flagging**: Proposals with confidence < 0.8 or medicines classified as Schedule H prescription drugs are flagged (`flagged_for_review = true`).
- **FR-CHM-06: Human Confirmation**: Chemists review the structured interpretation and explicitly confirm (`POST /api/proposals/{id}/confirm`). Only upon confirmation is the inventory row updated and an audit history record created.
- **FR-CHM-07: Proposal Rejection**: Chemists can cancel or reject inaccurate proposals (`POST /api/proposals/{id}/reject`) with optional review notes.

### Module C: Audit History & Undo

- **FR-AUD-01: Immutable Audit Trail**: All applied stock changes generate an immutable history entry recording previous state, new state, user ID, timestamp, and proposal ID (`/api/pharmacies/{id}/history`).
- **FR-AUD-02: Single-Step Rollback (Undo)**: Chemists can undo the latest stock modification for an inventory row via `/api/history/{id}/undo`. Invariants enforced:
  - An `UNDO` entry cannot itself be undone.
  - An entry cannot be undone twice.
  - If a newer change exists for the same inventory row, older changes cannot be undone until newer ones are rolled back.

### Module D: Pharmacist Substitution Approvals

- **FR-SUB-01: Substitution Authorization**: Licensed pharmacists can record per-request substitution approvals or rejections via `/api/substitutions/approve` with reference IDs.

---

## 3. Non-Functional Requirements (NFR)

- **NFR-01: Human-in-the-Loop Integrity**: Automated parsers must NEVER mutate confirmed stock. All inventory changes require authorized human review and confirmation.
- **NFR-02: Zero Leakage of Design Placeholders**: Illustrative fixture names from Stitch designs (e.g., "Apex Pharmacy", "Hazratganj, Lucknow", dummy batch IDs) must NEVER be displayed as live data. Production screens must render actual backend payloads or clean empty/loading states.
- **NFR-03: Offline & Latency Tolerance**: Primary NLP parser operates with zero network latency (pure Python regex). Client interfaces must handle network timeouts, displaying clear retry indicators.
- **NFR-04: Responsive Mobile Experience**: Mobile interface conforms to modern iOS/Android viewport standards (360px–430px phone widths, 16px side gutters, smooth touch targets ≥ 48px).
- **NFR-05: Security & Secret Protection**: No API keys, database credentials, or secret tokens shall ever be bundled or exposed in mobile frontend code.

---

## 4. Acceptance Criteria

1. All 4 core workflows (Patient search, Chemist stock update, Proposal confirmation, History undo) function end-to-end against the live backend REST API.
2. Unconfirmed stock updates leave inventory balances untouched.
3. Server-side authorization blocks any user from confirming or updating pharmacies outside their verified membership links.
4. All existing automated tests (274 tests) continue to pass without regressions.
