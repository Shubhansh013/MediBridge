# MediBridge Product Requirements Document

**Version:** 1.0  
**Date:** 2026-10-24  
**Status:** In Development  
**Project:** MediBridge Pharmacy Connectivity Platform

---

## 1. Product Overview

### 1.1 Problem Statement

Finding essential medicines in local pharmacies is unnecessarily difficult. Patients often:
- Visit multiple pharmacies without knowing stock availability
- Waste time traveling to locations that don't have the required medicine
- Rely on phone calls that interrupt pharmacy operations
- Face outdated or unverified inventory information

Simultaneously, pharmacies lack simple tools to:
- Communicate real-time stock updates
- Update inventory without complex software
- Reach patients searching for medicines they stock

### 1.2 Solution

MediBridge is a pharmacy connectivity platform that bridges the gap between patients seeking medicines and pharmacies managing inventory through:

- **Natural-language stock updates**: Pharmacists communicate inventory changes in English, Hindi, or Hinglish
- **Human-verified workflow**: AI interprets messages as proposals; authorized staff confirm before inventory changes
- **Real-time availability**: Patients search for medicines and find nearby pharmacies with current stock information
- **Audit trail**: Every inventory change is recorded with history and controlled undo capability

### 1.3 Target Users

| User Type | Role | Primary Goals |
|-----------|------|---------------|
| **Patient** | Medicine seeker | Find available medicines at nearby pharmacies quickly |
| **Chemist** | Pharmacy staff | Update stock status through simple messages; review proposals |
| **Pharmacist** | Licensed professional | Approve substitute recommendations; oversee inventory accuracy |

---

## 2. User Workflows

### 2.1 Patient: Medicine Search

**Trigger:** Patient needs a medicine and wants to find nearby pharmacies that have it in stock.

**Flow:**
1. Patient opens Telegram bot or web interface
2. Patient sends medicine name (brand or generic): "Dolo 650", "paracetamol", "dolo"
3. System searches medicine catalogue and aliases
4. System identifies substitute medicines (same salt, strength, form) if applicable
5. System returns list of nearby pharmacies:
   - Pharmacy name and locality
   - Stock status (IN_STOCK / LOW_STOCK / OUT_OF_STOCK)
   - Last verification timestamp
   - Distance and directions link
6. Patient selects pharmacy and navigates there

**Success Criteria:**
- ✅ Search returns results within 2 seconds
- ✅ Results show pharmacies within configurable radius (default 10km)
- ✅ Stock status reflects last confirmed update
- ✅ Substitute suggestions display only for same composition+strength+form

**Edge Cases:**
- Medicine not in catalogue → "Medicine not found. Try brand name or generic salt."
- No nearby pharmacies → "No pharmacies found within [radius]. Expand search?"
- All OUT_OF_STOCK → Show list with status and last update time

---

### 2.2 Chemist: Submit Stock Update

**Trigger:** Chemist notices inventory change (medicine sold out, new stock arrived).

**Flow:**
1. Chemist sends natural-language message via Telegram: "Dolo 650 khatam" or "Crocin 20 strips aaye"
2. System parses message → extracts medicine, quantity, stock intent
3. System resolves medicine from catalogue using brand name, aliases, or salt
4. System creates PENDING proposal with:
   - Parsed details (salt, strength, form, quantity, intent)
   - Confidence score
   - Review flag if confidence <0.8 OR medicine is Schedule H
5. Chemist reviews proposal card showing:
   - Original message
   - Interpreted medicine and stock change
   - Schedule H warning if applicable
6. Chemist taps **Confirm** or **Cancel**
7. On confirm: inventory updates atomically; audit entry created
8. Chemist receives confirmation: "✅ Dolo 650 marked OUT_OF_STOCK"

**Success Criteria:**
- ✅ Supported message patterns parsed correctly (tested: 24 passing parser tests)
- ✅ Confidence threshold triggers review for uncertain messages
- ✅ Confirmation required before inventory change (no auto-apply)
- ✅ Rejected proposals discard without affecting inventory

**Edge Cases:**
- Ambiguous message ("maybe 5 strips left") → Reject with "Cannot parse uncertain message"
- Medicine not in catalogue → "Medicine not found. Check spelling or use brand name."
- Proposal expires before confirmation (>24h) → Mark EXPIRED; allow renewal
- User not authorized for pharmacy → Reject with "Not authorized for this pharmacy"

---

### 2.3 Chemist: Undo Recent Change

**Trigger:** Chemist realizes last stock update was incorrect.

**Flow:**
1. Chemist taps **/undo** command or Undo button
2. System fetches latest STOCK_UPDATE for this pharmacy by this user
3. System validates undo constraints:
   - Target is STOCK_UPDATE (not UNDO)
   - Not already undone
   - No newer inventory change exists
4. System displays confirmation: "Undo: Dolo 650 OUT_OF_STOCK → IN_STOCK (50 strips)?"
5. Chemist confirms
6. System creates UNDO history entry, restores previous inventory state
7. Chemist sees: "✅ Undone. Dolo 650 restored to IN_STOCK (50 strips)"

**Success Criteria:**
- ✅ Only latest change can be undone
- ✅ Undo cannot be undone (create new proposal instead)
- ✅ Both original and undo entries remain in audit history

**Edge Cases:**
- No recent changes → "No changes to undo"
- Newer change exists → "Cannot undo: newer inventory change recorded"
- Already undone → "Already undone"

---

### 2.4 Pharmacist: Approve Substitute (Future)

**Trigger:** Patient requests substitute for unavailable medicine.

**Flow:**
1. Patient asks for substitute via bot: "Dolo 650 substitute?"
2. System finds medicines with same composition_key, strength, form
3. System checks if substitutes have prior approval at this pharmacy
4. If no approval exists, system requests pharmacist review
5. Pharmacist reviews substitute request with:
   - Requested medicine
   - Proposed substitute
   - Composition match details
6. Pharmacist approves or rejects with optional note
7. System records approval in substitution_approvals table
8. Patient receives substitute recommendation if approved

**Success Criteria:**
- ✅ Each substitute request independently evaluated (no reuse of old approvals)
- ✅ Pharmacist approval required before recommending substitutes
- ✅ Composition, strength, and form must match

---

## 3. Functional Requirements

### FR-001: Natural-Language Stock Message Parsing
**Priority:** P0 (MVP)  
**Status:** ✅ Implemented  

System shall parse English, Hindi, and Hinglish stock messages to extract:
- Medicine name (brand or generic)
- Quantity (optional)
- Stock intent (IN_STOCK, LOW_STOCK, OUT_OF_STOCK)
- Confidence score (0.0-1.0)

**Acceptance Criteria:**
- Parser handles "khatam", "out of stock", "finished" → OUT_OF_STOCK
- Parser handles "aaya", "available", "mil gaya" → IN_STOCK
- Parser rejects uncertain messages ("shayad", "maybe")
- Parser rejects future-oriented messages ("kal aayega", "tomorrow")
- Parser distinguishes medicine strength (650mg) from quantity (20 strips)

---

### FR-002: Medicine Catalogue and Alias Resolution
**Priority:** P0 (MVP)  
**Status:** ✅ Implemented  

System shall maintain medicine catalogue with:
- Brand name, generic name, composition_key, strength, form
- Schedule H flag
- Aliases (misspellings, Hinglish, short names)

**Acceptance Criteria:**
- Direct brand match: "Dolo 650" → finds Dolo 650
- Alias match: "dolo" → finds Dolo 650
- Generic match: "paracetamol 650mg" → finds all paracetamol 650mg brands

---

### FR-003: Proposal Workflow
**Priority:** P0 (MVP)  
**Status:** ✅ Implemented  

System shall create PENDING proposals requiring confirmation:
- Proposals expire after 24 hours
- Flagged for review if confidence <0.8 OR Schedule H
- Expired proposals can be renewed as new proposal

**Acceptance Criteria:**
- Proposal created with status=PENDING, expires_at=created_at+24h
- Confirmation changes status to CONFIRMED
- Rejection changes status to REJECTED
- Apply operation independently checks expires_at

---

### FR-004: Authorization and Pharmacy Scope
**Priority:** P0 (MVP)  
**Status:** ✅ Implemented  

System shall enforce pharmacy-scoped access:
- Only users linked to pharmacy via user_pharmacies can create proposals
- Every mutating operation verifies user-pharmacy link
- Proposals/history isolated per pharmacy

**Acceptance Criteria:**
- Chemist at Pharmacy A cannot create proposals for Pharmacy B
- User not in user_pharmacies rejected with UserNotLinkedToPharmacyError

---

### FR-005: Atomic Inventory Updates
**Priority:** P0 (MVP)  
**Status:** ✅ Implemented  

System shall apply confirmed proposals atomically:
- Single transaction: create history + update inventory + mark proposal confirmed
- Stock status + quantity consistency enforced
- Audit trail records previous/new values

**Acceptance Criteria:**
- Transaction rolls back on any constraint violation
- History row captures full state delta
- Inventory reflects new stock status immediately

---

### FR-006: Audit History and Undo
**Priority:** P0 (MVP)  
**Status:** ✅ Implemented  

System shall maintain immutable audit trail:
- Every applied change writes STOCK_UPDATE history entry
- Every undo writes UNDO history entry with undo_of_history_id
- History rows never deleted or modified

**Undo Constraints:**
- Only latest change per inventory can be undone (by auto-increment id)
- UNDO entries cannot be undone
- Undo restores inventory to previous state recorded in history

**Acceptance Criteria:**
- Undo of non-latest change rejected with NewerChangeExistsError
- Double-undo rejected with HistoryAlreadyUndoneError
- Both original and undo rows remain in history

---

### FR-007: Medicine Search and Nearby Pharmacies
**Priority:** P0 (MVP)  
**Status:** ✅ Implemented  

System shall support patient medicine search:
- Search by brand name, generic name, or alias
- Return list of pharmacies within radius with stock status
- Calculate distance using Haversine formula
- Filter only active, verified pharmacies

**Acceptance Criteria:**
- Search returns results ordered by distance (closest first)
- Stock status shows last_confirmed_at timestamp
- Stale inventory flagged if last_confirmed_at > 24h ago

---

### FR-008: Substitute Discovery
**Priority:** P1 (Post-MVP)  
**Status:** ✅ Partially Implemented (DB operations ready)  

System shall identify substitute medicines:
- Match by composition_key, strength, form
- Optional pharmacy filter (only substitutes in stock at that pharmacy)
- Pharmacist approval workflow (substitution_approvals table)

**Acceptance Criteria:**
- Substitutes have identical normalized composition
- Substitutes match strength and form
- Per-request pharmacist approval recorded

---

### FR-009: Telegram Bot Interface
**Priority:** P0 (MVP)  
**Status:** ✅ Implemented  

System shall provide Telegram bot interface:
- Dual mode: patient (search) / chemist (stock updates)
- /start, /mode commands for mode switching
- /undo command for chemists
- Inline keyboards for proposal confirmation

**Acceptance Criteria:**
- Bot responds within 2 seconds
- Proposal cards show parsed details with Confirm/Cancel buttons
- Search results include Google Maps links

---

### FR-010: Streamlit Dashboard
**Priority:** P0 (MVP)  
**Status:** ✅ Implemented  

System shall provide web dashboard:
- Real-time inventory view per pharmacy
- Pending proposals list
- Stock history timeline
- Filter by medicine, status, date range

**Acceptance Criteria:**
- Dashboard updates reflect database changes
- Proposal confirmation triggers inventory refresh
- History shows all changes with timestamps

---

## 4. Non-Functional Requirements

### NFR-001: Reliability
- System uptime: 99% (target)
- Database: SQLite with WAL mode for concurrent reads
- Transaction isolation: atomic updates prevent partial state

### NFR-002: Data Integrity
- Foreign key constraints enforced
- Check constraints prevent invalid stock_status+quantity combinations
- Unique constraints prevent duplicate inventory rows
- Expiry check at apply time (regardless of cron job)

### NFR-003: Security
- User authorization via pharmacy membership (user_pharmacies)
- No direct database access from bot/dashboard (service layer mediates)
- Telegram user IDs as authentication (demo mode: fallback to hardcoded demo users)

### NFR-004: Performance
- Medicine search: <2 seconds
- Nearby pharmacy query: <3 seconds (Haversine calculation)
- Proposal creation: <1 second
- Database: indexed on composition_key, expires_at, last_confirmed_at

### NFR-005: Usability
- Natural-language input (no forms or dropdowns for stock updates)
- Inline confirmation (single tap to confirm/reject)
- Clear error messages (parseable, actionable)
- Bilingual support (English, Hindi, Hinglish)

### NFR-006: Auditability
- Every inventory change traced to source proposal and acting user
- History immutable (append-only)
- Undo operations recorded, not deletions
- Timestamps in UTC

---

## 5. Business Rules

### BR-001: Human-Verified Updates
**Rule:** No parsed message directly modifies inventory. Every update is a proposal requiring chemist confirmation.

**Rationale:** Natural-language parsing is not 100% accurate. Human oversight prevents incorrect inventory changes.

### BR-002: Proposal Expiry
**Rule:** Proposals expire 24 hours after creation. Expired proposals cannot be applied.

**Rationale:** Stale proposals reflect outdated information. Expiry forces re-verification.

### BR-003: Pharmacy-Scoped Access
**Rule:** Users can only create proposals, view inventory, and perform actions for pharmacies they are linked to.

**Rationale:** Prevents cross-pharmacy data leakage and unauthorized modifications.

### BR-004: Latest-Change Undo
**Rule:** Only the most recent inventory change can be undone. Undo entries themselves cannot be undone.

**Rationale:** Prevents cascading undos that would corrupt audit history. To change inventory after undo, submit a new proposal.

### BR-005: Schedule H Flagging
**Rule:** Proposals for Schedule H medicines are automatically flagged for review, regardless of confidence score.

**Rationale:** Controlled substances require extra verification.

### BR-006: Substitute Composition Match
**Rule:** Substitutes must have identical composition_key, strength, and form.

**Rationale:** Substitution requires pharmaceutical equivalence, not just similar effects.

---

## 6. Error States and Edge Cases

### 6.1 Parser Failures
- **Ambiguous message**: "shayad 5 strips" → Reject with confidence explanation
- **Future tense**: "kal aayega" → Reject with "Cannot parse future-oriented message"
- **Conversational**: "hello" → Reject as non-stock message
- **Zero quantity with IN_STOCK**: Parser rejects; if slips through, database constraint raises StockStatusQuantityMismatchError

### 6.2 Authorization Failures
- **User not in user_pharmacies**: Reject with UserNotLinkedToPharmacyError
- **Telegram user not registered**: Bot falls back to demo chemist (Amit Verma at MedPlus Saket)

### 6.3 Proposal State Conflicts
- **Expired proposal applied**: Raise ProposalExpiredError
- **Proposal not CONFIRMED**: Raise ProposalNotPendingError
- **Pharmacy mismatch**: Raise ProposalPharmacyMismatchError

### 6.4 Undo Failures
- **Already undone**: Raise HistoryAlreadyUndoneError
- **Newer change exists**: Raise NewerChangeExistsError
- **Target is UNDO entry**: Raise CannotUndoUndoError

### 6.5 Medicine Not Found
- Brand name → Search aliases → Search generic → Return "Medicine not found"

---

## 7. Acceptance Criteria

### MVP Acceptance
- ✅ Parser handles 20+ message patterns (English, Hindi, Hinglish)
- ✅ Medicine catalogue with 50+ medicines and aliases
- ✅ Proposal workflow: parse → pending → confirm → apply → history
- ✅ Undo workflow: validate constraints → restore state → record undo
- ✅ Telegram bot: dual-mode demo (patient search, chemist updates)
- ✅ Streamlit dashboard: inventory view, pending proposals, history timeline
- ✅ Authorization: pharmacy-scoped access enforced
- ✅ Database: 11 tables, 23 operations, immutable audit trail
- ✅ Tests: 100+ tests covering parser, models, operations, services, e2e workflow

### Post-MVP Enhancements
- Pharmacist substitute approval workflow (UI pending)
- SOS broadcast feature (optional tables implemented)
- LLM-based parser fallback for complex messages (test harness exists)
- Multi-pharmacy support per Telegram chat
- Advanced search filters (radius, stock status, last update time)

---

## 8. External Dependencies

| Dependency | Purpose | Status |
|------------|---------|--------|
| Telegram Bot API | Conversational interface | ✅ Integrated |
| SQLite 3.37+ | Database with WAL mode | ✅ Configured |
| SQLAlchemy 2.0 | ORM and query builder | ✅ Implemented |
| Pydantic 2.7+ | Schema validation | ✅ Implemented |
| Streamlit 1.30+ | Web dashboard | ✅ Implemented |
| Google Gemini API | Optional LLM parser fallback | ⚠️ Configured, not active |
| Groq API | Optional LLM parser fallback | ⚠️ Configured, not active |

---

## 9. Assumptions

1. **Single pharmacy per Telegram chat**: `telegram_chat_id` is UNIQUE on pharmacies table
2. **Demo mode fallback**: Unregistered Telegram users default to demo chemist (Amit Verma)
3. **24-hour proposal window**: Sufficient time for chemist review; no sub-hour expiry needed
4. **SQLite adequate for demo**: Production deployment may migrate to PostgreSQL
5. **Haversine distance sufficient**: Exact road distance not required for MVP
6. **No real-time WebSocket**: Dashboard refreshes on page load; no push notifications yet
7. **Telegram auth trusted**: No additional password/OTP for demo

---

## 10. Out of Scope (MVP)

- ❌ Multi-language UI (only English bot responses; accepts Hindi/Hinglish input)
- ❌ Payment integration
- ❌ Prescription upload and verification
- ❌ Delivery logistics
- ❌ Patient accounts and order history
- ❌ Pharmacy analytics dashboard
- ❌ Mobile app (native iOS/Android)
- ❌ Email/SMS notifications
- ❌ Pharmacy onboarding workflow (manual seeding)
- ❌ Admin panel for medicine catalogue management

---

## 11. Open Questions

**Q1:** Should confidence threshold for review be configurable per pharmacy?  
**Current:** Hardcoded 0.8 in proposal_adapter.py  
**Decision Pending:** Member 1 confirmation

**Q2:** Can one pharmacy have multiple Telegram chats?  
**Current:** `telegram_chat_id` is UNIQUE  
**Decision Pending:** Requirement clarification

**Q3:** Should LOW_STOCK threshold be configurable?  
**Current:** Service layer decides; database enforces quantity > 0  
**Decision Pending:** Business rule confirmation

**Q4:** Is LLM fallback parser active for MVP?  
**Current:** Deterministic parser only; LLM code exists but not invoked  
**Decision Pending:** API key availability and cost approval

**Q5:** Are SOS tables (sos_broadcasts, sos_responses) in MVP?  
**Current:** Tables implemented but no UI/workflow  
**Decision Pending:** Feature prioritization

---

## Summary

MediBridge connects patients to nearby pharmacies through human-verified, natural-language stock updates. The MVP implements:
- **Core workflow**: Parse → Propose → Confirm → Apply → History
- **Dual interface**: Telegram bot (patient search, chemist updates) + Streamlit dashboard
- **Data integrity**: Pharmacy-scoped authorization, atomic updates, immutable audit trail, controlled undo
- **Tested foundation**: 100+ tests covering parser, database operations, services, and end-to-end workflows

**Next Steps:** Deploy demo, gather feedback from pilot pharmacies, refine parser patterns, and evaluate LLM fallback.
