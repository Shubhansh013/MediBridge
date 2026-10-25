# MediBridge Business Rules and Invariants

**Version:** 1.0  
**Date:** 2026-10-24  
**Purpose:** Testable reference for rules MediBridge must preserve

---

## 1. Overview

This document defines the business rules, data invariants, and operational constraints that MediBridge enforces. Every rule is:
- **Testable**: Can be verified by automated tests or manual inspection
- **Enforceable**: Implemented at database, service, or interface layer
- **Traceable**: Linked to requirements in `docs/prd.md`

Rules are organized by domain: medicine catalogue, inventory, proposals, authorization, audit, and workflow orchestration.

---

## 2. Medicine Catalogue Rules

### RULE-CAT-001: Brand Name Uniqueness
**Statement:** Each combination of (brand_name, composition_key, strength, form, manufacturer) must be unique in the medicines table.

**Rationale:** Prevents duplicate catalogue entries for same product.

**Enforcement:** UNIQUE constraint at database level

**Test:** Insert two medicines with identical (brand_name, composition_key, strength, form, manufacturer) → Second insert raises IntegrityError

**Example:**
```sql
-- Valid: Different manufacturers
INSERT INTO medicines (brand_name, composition_key, strength, form, manufacturer)
VALUES ('Dolo 650', 'paracetamol', '650mg', 'tablet', 'Micro Labs');

INSERT INTO medicines (brand_name, composition_key, strength, form, manufacturer)
VALUES ('Dolo 650', 'paracetamol', '650mg', 'tablet', 'Generic Co');  -- ✅ Allowed

-- Invalid: Exact duplicate
INSERT INTO medicines (brand_name, composition_key, strength, form, manufacturer)
VALUES ('Dolo 650', 'paracetamol', '650mg', 'tablet', 'Micro Labs');  -- ❌ Raises IntegrityError
```

---

### RULE-CAT-002: Composition Key Normalization
**Statement:** All composition_key values must be normalized: lowercase, sorted alphabetically, joined with "+".

**Rationale:** Enables substitute discovery across differently-ordered multi-ingredient formulations.

**Enforcement:** `normalize_composition_key()` function called before insert/update

**Test:** Insert "Ibuprofen + Paracetamol" → stored as "ibuprofen+paracetamol"

**Algorithm:**
```python
def normalize_composition_key(raw: str) -> str:
    parts = [p.strip().lower() for p in raw.split('+')]
    return '+'.join(sorted(parts))

# Examples:
normalize_composition_key("Paracetamol + Ibuprofen")  # → "ibuprofen+paracetamol"
normalize_composition_key("Ibuprofen+Paracetamol")    # → "ibuprofen+paracetamol"
normalize_composition_key("Paracetamol")              # → "paracetamol"
```

---

### RULE-CAT-003: Alias Uniqueness
**Statement:** Each alias must be globally unique across all medicines.

**Rationale:** Prevents ambiguous alias resolution ("dolo" should not match two different medicines).

**Enforcement:** UNIQUE constraint on medicine_aliases.alias

**Test:** Insert alias "dolo" for Medicine A, then insert "dolo" for Medicine B → Second insert raises IntegrityError

---

### RULE-CAT-004: Schedule H Flag Immutability (Recommended)
**Statement:** Once a medicine is marked `is_schedule_h=True`, it should not be changed to False without pharmacist review.

**Rationale:** Controlled substances classification is regulatory; demoting requires verification.

**Enforcement:** Application-level validation (not database constraint)

**Test:** Attempt to update `is_schedule_h` from True to False → Service layer logs warning or rejects

**Note:** Currently not enforced; flagged for future enhancement.

---

## 3. Inventory Rules

### RULE-INV-001: One Inventory Row Per Pharmacy-Medicine
**Statement:** Each (pharmacy_id, medicine_id) pair must have at most one inventory row.

**Rationale:** Prevents duplicate stock records.

**Enforcement:** UNIQUE constraint on (pharmacy_id, medicine_id)

**Test:** Insert inventory for Pharmacy 5, Medicine 12 twice → Second insert raises IntegrityError

---

### RULE-INV-002: Stock Status and Quantity Consistency
**Statement:**
- `OUT_OF_STOCK` → quantity MUST be 0 or NULL
- `IN_STOCK` or `LOW_STOCK` → quantity MUST be > 0 or NULL

**Rationale:** Prevents contradictory states (e.g., "OUT_OF_STOCK with 50 units").

**Enforcement:** CHECK constraint at database level

**Test:**
```sql
-- Invalid: OUT_OF_STOCK with quantity > 0
UPDATE inventory SET stock_status='OUT_OF_STOCK', quantity=50 WHERE id=7;
-- ❌ Raises CHECK constraint violation

-- Valid: OUT_OF_STOCK with quantity=0
UPDATE inventory SET stock_status='OUT_OF_STOCK', quantity=0 WHERE id=7;
-- ✅ Allowed

-- Valid: IN_STOCK with quantity > 0
UPDATE inventory SET stock_status='IN_STOCK', quantity=50 WHERE id=7;
-- ✅ Allowed
```

---

### RULE-INV-003: Quantity Non-Negative
**Statement:** Inventory quantity, when not NULL, must be >= 0.

**Rationale:** Negative stock is nonsensical.

**Enforcement:** CHECK constraint `quantity >= 0`

**Test:** Update inventory quantity to -5 → Raises CHECK constraint violation

---

### RULE-INV-004: Staleness Threshold
**Statement:** Inventory is considered stale if `datetime.utcnow() - last_confirmed_at > 24 hours` (configurable).

**Rationale:** Old stock information may be inaccurate; flagged for patient awareness.

**Enforcement:** Application-level query filter (`get_stale_inventory`)

**Test:** Inventory with `last_confirmed_at = now - 25 hours` appears in `get_stale_inventory(threshold_hours=24)`

---

### RULE-INV-005: Last Update Source Traceability
**Statement:** Every inventory update must record `last_update_source` (chemist, proposal_apply, undo, seed).

**Rationale:** Audit trail for how inventory reached current state.

**Enforcement:** Application-level (operations.py sets on every update)

**Test:** Apply confirmed proposal → inventory.last_update_source = 'proposal_apply'

---

## 4. Stock Proposal Rules

### RULE-PROP-001: Proposals Created as PENDING
**Statement:** Every new proposal must have `status='PENDING'` and `expires_at = created_at + 24 hours`.

**Rationale:** All updates require confirmation; 24h window for review.

**Enforcement:** Database default + application logic in `create_stock_proposal`

**Test:** Create proposal → status='PENDING', expires_at is 24h from now

---

### RULE-PROP-002: Expiry Enforcement at Apply Time
**Statement:** `apply_confirmed_proposal` must independently verify `expires_at < current_time` and raise `ProposalExpiredError` if expired, regardless of cron job status.

**Rationale:** Defense-in-depth; cron may not run; proposal status may be manually changed.

**Enforcement:** Explicit check in `apply_confirmed_proposal`

**Test:**
1. Create proposal with `expires_at = now - 1 hour`
2. Manually set `status='CONFIRMED'`
3. Call `apply_confirmed_proposal` → Raises `ProposalExpiredError`

---

### RULE-PROP-003: Review Flag Trigger
**Statement:** Proposal `flagged_for_review=True` if `confidence < 0.8` OR matched medicine has `is_schedule_h=True`.

**Rationale:** Low-confidence parses and controlled substances require extra verification.

**Enforcement:** `compute_review_flag()` in `proposal_adapter.py`

**Test:**
- Confidence 0.75, is_schedule_h=False → flagged=True
- Confidence 0.95, is_schedule_h=True → flagged=True
- Confidence 0.95, is_schedule_h=False → flagged=False

---

### RULE-PROP-004: Proposal Status State Machine
**Statement:** Valid status transitions:
- PENDING → CONFIRMED (chemist confirms)
- PENDING → REJECTED (chemist rejects)
- PENDING → EXPIRED (cron job or 24h elapsed)
- EXPIRED → (no transitions; renewal creates new proposal)

**Rationale:** Prevents invalid state changes.

**Enforcement:** Application-level validation

**Test:** Attempt CONFIRMED → EXPIRED → Rejected by service layer

**Invalid Transitions:**
- CONFIRMED → PENDING
- EXPIRED → CONFIRMED
- REJECTED → PENDING

---

### RULE-PROP-005: Renewal Creates New Proposal
**Statement:** Expired proposals cannot be reactivated. Renewal creates a new PENDING proposal with `renewed_from_id` pointing to expired one.

**Rationale:** Expired data may be stale; re-verification required.

**Enforcement:** `renew_proposal()` function

**Test:**
1. Proposal 101 expires
2. Call `renew_proposal(101, ...)` → Creates Proposal 102 with `renewed_from_id=101`
3. Proposal 101 remains `status='EXPIRED'`

---

## 5. Authorization and Pharmacy Scope Rules

### RULE-AUTH-001: User-Pharmacy Link Required
**Statement:** Every mutating operation (create proposal, apply, undo, record approval) must verify acting user has a row in `user_pharmacies` for the target pharmacy.

**Rationale:** Prevents cross-pharmacy data modification.

**Enforcement:** Database operations call `is_user_linked_to_pharmacy` or include inline check; raises `UserNotLinkedToPharmacyError` if not linked

**Test:**
1. User 10 linked to Pharmacy 5
2. User 10 attempts create_proposal for Pharmacy 7 → Raises `UserNotLinkedToPharmacyError`

---

### RULE-AUTH-002: Pharmacy Scope Verification
**Statement:** Every mutating operation must verify the record's `pharmacy_id` matches the passed `pharmacy_id` parameter.

**Rationale:** Prevents malicious pharmacy_id swap in request.

**Enforcement:** Explicit check in database operations; raises `PharmacyScopeMismatchError` if mismatch

**Test:**
1. Proposal 101 belongs to Pharmacy 5
2. Call `apply_confirmed_proposal(101, user_id, pharmacy_id=7)` → Raises `PharmacyScopeMismatchError`

---

### RULE-AUTH-003: Role-Based Actions (Service Layer)
**Statement:**
- **Chemists** can: create proposals, confirm proposals, undo own changes
- **Pharmacists** can: all chemist actions + record substitution approvals
- **Patients** can: search medicines, view pharmacy inventory (read-only)
- **Admins** can: all actions

**Rationale:** Separation of duties; controlled substances require pharmacist oversight.

**Enforcement:** Service layer checks user.role before invoking operations

**Test:** Patient user attempts `create_stock_proposal` → Service layer rejects with "Unauthorized role"

**Note:** Database layer does NOT enforce role checks; service layer responsibility.

---

## 6. Audit History Rules

### RULE-HIST-001: Immutable History
**Statement:** `stock_history` rows are never deleted or modified after creation.

**Rationale:** Audit trail integrity; regulatory compliance.

**Enforcement:** No DELETE or UPDATE operations on stock_history in operations.py; application convention

**Test:** Attempt `session.delete(history_row)` → Discouraged; no database constraint prevents, but application never calls

**Recommendation:** Add database trigger to prevent DELETE/UPDATE (future enhancement).

---

### RULE-HIST-002: Complete State Delta
**Statement:** Every `stock_history` entry must record both `previous_*` and `new_*` values for stock_status, quantity, last_confirmed_at.

**Rationale:** Enables accurate state reconstruction and rollback.

**Enforcement:** `apply_confirmed_proposal` and `undo_last_change` capture full state before modifying inventory

**Test:** Apply proposal changing IN_STOCK (50) → OUT_OF_STOCK (0) → History records previous_stock_status='IN_STOCK', previous_quantity=50, new_stock_status='OUT_OF_STOCK', new_quantity=0

---

### RULE-HIST-003: Change Type Distinction
**Statement:** `change_type` must be 'STOCK_UPDATE' for inventory changes from proposals, 'UNDO' for undo operations.

**Rationale:** Clear differentiation between forward changes and reversals.

**Enforcement:** Hardcoded in `apply_confirmed_proposal` (STOCK_UPDATE) and `undo_last_change` (UNDO)

**Test:** Apply proposal → change_type='STOCK_UPDATE'; undo that change → change_type='UNDO'

---

## 7. Undo Rules

### RULE-UNDO-001: Latest Change Only
**Statement:** Only the most recent `stock_history` entry for an `inventory_id` can be undone. A newer entry (higher `id`) blocks undo.

**Rationale:** Prevents out-of-order undos that would corrupt state.

**Enforcement:** `undo_last_change` checks `SELECT COUNT(*) FROM stock_history WHERE inventory_id=? AND id > ?`; raises `NewerChangeExistsError` if count > 0

**Test:**
1. History entries: 201 (STOCK_UPDATE), 202 (STOCK_UPDATE)
2. Attempt undo 201 → Raises `NewerChangeExistsError` (202 exists)
3. Undo 202 → Success
4. Now undo 201 → Success

---

### RULE-UNDO-002: STOCK_UPDATE Only
**Statement:** Only `change_type='STOCK_UPDATE'` entries can be undone. `change_type='UNDO'` cannot be undone.

**Rationale:** Prevents undo-of-undo cascades. To change stock after undo, submit new proposal.

**Enforcement:** `undo_last_change` checks `change_type`; raises `CannotUndoUndoError` if UNDO

**Test:** Undo history entry 202 (creates entry 203 with change_type='UNDO') → Attempt undo 203 → Raises `CannotUndoUndoError`

---

### RULE-UNDO-003: Single Undo Per History Entry
**Statement:** Each `stock_history` entry can be undone at most once.

**Rationale:** Prevents duplicate undos.

**Enforcement:** UNIQUE constraint on `undo_of_history_id`; raises IntegrityError on second undo attempt

**Test:**
1. Undo history 201 → Creates undo entry 202 with `undo_of_history_id=201`
2. Attempt undo 201 again → Raises `HistoryAlreadyUndoneError` (or IntegrityError if constraint reached first)

---

### RULE-UNDO-004: Undo Restores Previous State
**Statement:** Undo must restore inventory to the exact state recorded in the target history entry's `previous_*` fields.

**Rationale:** Accurate rollback.

**Enforcement:** `undo_last_change` reads target.previous_stock_status, target.previous_quantity, target.previous_last_confirmed_at and writes them to inventory

**Test:**
1. History 201: previous=IN_STOCK(50), new=OUT_OF_STOCK(0)
2. Undo 201 → Inventory becomes IN_STOCK(50), last_confirmed_at=201.previous_last_confirmed_at

---

## 8. Substitute Discovery Rules

### RULE-SUB-001: Composition Match
**Statement:** Substitutes must have identical `composition_key`, `strength`, and `form`.

**Rationale:** Pharmaceutical equivalence requires same active ingredient, potency, and delivery method.

**Enforcement:** `get_substitutes_for_medicine` filters on these three fields

**Test:**
- Medicine A: composition_key='paracetamol', strength='650mg', form='tablet'
- Medicine B: composition_key='paracetamol', strength='650mg', form='tablet' → Substitute ✅
- Medicine C: composition_key='paracetamol', strength='500mg', form='tablet' → Not substitute ❌
- Medicine D: composition_key='ibuprofen', strength='650mg', form='tablet' → Not substitute ❌

---

### RULE-SUB-002: Per-Request Pharmacist Approval
**Statement:** Each substitute recommendation requiring pharmacist approval must create a new `substitution_approvals` entry. Old approvals are not reused.

**Rationale:** Each patient case is unique; blanket approvals unsafe.

**Enforcement:** `record_substitution_approval` always inserts; no lookup for existing approval

**Test:**
1. Patient A requests Dolo 650 substitute → Pharmacist approves Crocin 650 (entry 1)
2. Patient B requests Dolo 650 substitute → Pharmacist re-evaluates and approves Crocin 650 (entry 2)
3. Two separate `substitution_approvals` rows exist

---

### RULE-SUB-003: Substitution Status Finality
**Statement:** `substitution_approvals.status` is 'APPROVED' or 'REJECTED'. No 'PENDING' state.

**Rationale:** Approval recorded only after pharmacist decision; no partial state persisted.

**Enforcement:** Enum definition + application workflow (only create row after approval/rejection)

**Test:** Check `substitution_approvals` → All rows have status='APPROVED' or 'REJECTED'

---

## 9. Workflow Orchestration Rules

### RULE-WORK-001: No Auto-Apply
**Statement:** No parsed message directly modifies inventory. Every update is a PENDING proposal requiring chemist confirmation.

**Rationale:** Natural-language parsing not 100% accurate; human oversight prevents errors.

**Enforcement:** Service layer workflow: parse → propose → (await confirmation) → apply

**Test:** Send stock message → Proposal created with status=PENDING, inventory unchanged → Confirm proposal → Inventory updated

---

### RULE-WORK-002: Atomic Apply Transaction
**Statement:** `apply_confirmed_proposal` must execute as single atomic transaction: create history + update inventory + mark proposal confirmed.

**Rationale:** Prevents partial state (history written but inventory unchanged).

**Enforcement:** Database session commit at end; rollback on any error

**Test:**
1. Mock database to raise error during inventory update
2. Call `apply_confirmed_proposal` → Transaction rolls back
3. Verify: no history entry created, inventory unchanged, proposal status unchanged

---

### RULE-WORK-003: Atomic Undo Transaction
**Statement:** `undo_last_change` must execute as single atomic transaction: create undo history + restore inventory.

**Rationale:** Prevents partial rollback.

**Enforcement:** Database session commit at end; rollback on any error

**Test:** Similar to RULE-WORK-002

---

### RULE-WORK-004: Service Layer Authorization Before DB Call
**Statement:** Service layer must call `is_user_linked_to_pharmacy` and check result BEFORE invoking mutating database operations.

**Rationale:** Separation of concerns; service layer decides authorization, database enforces data integrity.

**Enforcement:** Convention in `proposal_service.py`, `confirmation_service.py`, `history_service.py`

**Test:**
```python
# confirmation_service.py
def confirm_and_apply_proposal(...):
    if not repository.is_user_linked_to_pharmacy(user_id, pharmacy_id):
        return ConfirmationResult(success=False, error="Unauthorized")
    # Now safe to call apply_confirmed_proposal
```

---

## 10. Parser Rules

### RULE-PARSE-001: Reject Uncertain Messages
**Statement:** Messages containing uncertainty markers ("shayad", "maybe", "लगता है") must be rejected.

**Rationale:** Uncertain information should not create proposals.

**Enforcement:** `ai_parser.parse_stock_message` raises validation error

**Test:** Parse "shayad 5 strips" → Raises ValidationError

---

### RULE-PARSE-002: Reject Future-Oriented Messages
**Statement:** Messages indicating future stock ("kal aayega", "tomorrow") must be rejected.

**Rationale:** Inventory reflects current state, not future promises.

**Enforcement:** `ai_parser.parse_stock_message` raises validation error

**Test:** Parse "kal stock aayega" → Raises ValidationError

---

### RULE-PARSE-003: Distinguish Strength from Quantity
**Statement:** Parser must distinguish medicine strength (650mg) from inventory quantity (20 strips).

**Rationale:** "Dolo 650" means paracetamol 650mg, not 650 units.

**Enforcement:** `ai_parser.parse_stock_message` logic checks units (mg, g, ml → strength; strips, tablets, boxes → quantity)

**Test:**
- Parse "Dolo 650 khatam" → strength="650mg", quantity=None
- Parse "Dolo 650 ke 20 strips" → strength="650mg", quantity=20

---

### RULE-PARSE-004: Confidence Scoring
**Statement:** Parser must assign confidence score (0.0-1.0) based on match quality.

**Rationale:** Enables review flagging for uncertain parses.

**Enforcement:** `ai_parser.parse_stock_message` returns `ParsedMedicineStockItem.confidence`

**Example:**
- Exact brand match: confidence=0.95
- Alias match: confidence=0.85
- Generic match with ambiguous quantity: confidence=0.70

---

## 11. Geographic Rules

### RULE-GEO-001: Coordinate Bounds
**Statement:** Latitude must be in [-90, 90], longitude in [-180, 180].

**Rationale:** Geographic validity.

**Enforcement:** CHECK constraints on pharmacies table

**Test:** Insert pharmacy with latitude=100 → Raises CHECK constraint violation

---

### RULE-GEO-002: Haversine Distance Filter
**Statement:** `get_pharmacies_nearby` must use Haversine formula for distance calculation, filtering to `radius_km`.

**Rationale:** Accurate geographic proximity.

**Enforcement:** `get_pharmacies_nearby` implementation

**Test:** Search with lat=28.5, lon=77.2, radius=5km → Only pharmacies within 5km returned

**Formula:**
```python
def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # Earth radius in km
    φ1, φ2 = radians(lat1), radians(lat2)
    Δφ = radians(lat2 - lat1)
    Δλ = radians(lon2 - lon1)
    a = sin(Δφ/2)**2 + cos(φ1) * cos(φ2) * sin(Δλ/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c
```

---

## 12. Telegram Integration Rules

### RULE-TG-001: Unique Chat ID Per Pharmacy
**Statement:** Each pharmacy has at most one `telegram_chat_id`.

**Rationale:** Simplifies bot routing; one chat = one pharmacy.

**Enforcement:** UNIQUE constraint on `pharmacies.telegram_chat_id`

**Test:** Assign same telegram_chat_id to two pharmacies → Second update raises IntegrityError

**Open Question:** Should one pharmacy support multiple chats (e.g., group + private)? Current design: no.

---

### RULE-TG-002: Demo Mode Fallback
**Statement:** If Telegram user not registered in `users` table, bot defaults to demo chemist (Amit Verma at MedPlus Saket).

**Rationale:** Demo usability without registration flow.

**Enforcement:** `resolve_chemist_context()` in `bot.py`

**Test:** Unregistered Telegram user sends stock message → Proposal created for demo pharmacy

---

## 13. Cron Job Rules

### RULE-CRON-001: Expire Overdue Proposals (Idempotent)
**Statement:** Cron job calls `expire_overdue_proposals(cutoff=datetime.utcnow())` to mark PENDING proposals with `expires_at < cutoff` as EXPIRED.

**Rationale:** Prevents stale proposals from being applied.

**Enforcement:** Scheduled task (not yet implemented; manual call for now)

**Test:**
1. Create proposal with `expires_at = now - 1 hour`
2. Call `expire_overdue_proposals(now)` → Proposal status='EXPIRED'
3. Call again → Idempotent (no changes)

---

### RULE-CRON-002: Flag Stale Inventory
**Statement:** Cron job identifies inventory with `last_confirmed_at > 24h ago` and flags for chemist re-verification.

**Rationale:** Old stock info may be inaccurate; prompt refresh.

**Enforcement:** `get_stale_inventory` query; notification logic pending

**Test:** Inventory with `last_confirmed_at = now - 25 hours` appears in `get_stale_inventory(threshold_hours=24)`

---

## 14. Test Coverage Requirements

### RULE-TEST-001: Parser Coverage
**Statement:** At least 20 test cases covering English, Hindi, Hinglish, edge cases (uncertain, future, zero quantity).

**Status:** ✅ 24 tests in `test_ai_parser.py`

---

### RULE-TEST-002: Constraint Coverage
**Statement:** Every database constraint (UNIQUE, CHECK, FK) must have a test verifying it raises expected error.

**Status:** ✅ Covered in `test_models.py`, `test_operations.py`

---

### RULE-TEST-003: Workflow Coverage
**Statement:** At least one end-to-end test for each critical workflow: parse→propose→confirm→apply, undo, substitute approval.

**Status:** ✅ `test_e2e_workflow.py` (10 tests)

---

## 15. Invariant Checks (Runtime Assertions)

### INV-001: Inventory Consistency
**Check:** After every `apply_confirmed_proposal`, verify:
- `stock_history` row created with correct previous/new values
- `inventory` row matches history's `new_*` values
- `stock_proposals` row marked CONFIRMED

**Enforcement:** Integration test assertion

---

### INV-002: Undo Reversibility
**Check:** After undo, verify:
- Inventory matches target history's `previous_*` values
- Undo history row created with `undo_of_history_id` pointing to target

**Enforcement:** Unit test assertion

---

### INV-003: No Orphaned Proposals
**Check:** Every `source_proposal_id` in `stock_history` must reference existing `stock_proposals.id`.

**Enforcement:** Foreign key constraint + periodic integrity check

---

## 16. Edge Case Handling

### EDGE-001: Zero Quantity IN_STOCK
**Scenario:** Chemist sends "Dolo 650 available" but quantity=0

**Rule:** Reject at validation layer (IN_STOCK requires quantity > 0 or NULL)

**Enforcement:** CHECK constraint + service-layer validation

---

### EDGE-002: Concurrent Proposal Apply
**Scenario:** Two users confirm same proposal simultaneously

**Rule:** First transaction succeeds, second raises `ProposalNotPendingError` (status already CONFIRMED)

**Enforcement:** Database-level status check in transaction

---

### EDGE-003: Concurrent Undo
**Scenario:** Two users undo same history entry simultaneously

**Rule:** First transaction succeeds, second raises `HistoryAlreadyUndoneError` or IntegrityError (UNIQUE constraint on `undo_of_history_id`)

**Enforcement:** Database UNIQUE constraint

---

### EDGE-004: Proposal Expires During Confirmation
**Scenario:** User taps Confirm button, but proposal expires before transaction commits

**Rule:** `apply_confirmed_proposal` checks `expires_at < utcnow()` and raises `ProposalExpiredError`

**Enforcement:** Explicit check at apply time (not relying on cron job)

---

## 17. Summary Checklist

| Rule ID | Description | Enforcement | Tested |
|---------|-------------|-------------|--------|
| CAT-001 | Brand name uniqueness | DB UNIQUE constraint | ✅ |
| CAT-002 | Composition key normalization | `normalize_composition_key()` | ✅ |
| CAT-003 | Alias uniqueness | DB UNIQUE constraint | ✅ |
| INV-001 | One inventory per pharmacy-medicine | DB UNIQUE constraint | ✅ |
| INV-002 | Stock status-quantity consistency | DB CHECK constraint | ✅ |
| INV-003 | Quantity non-negative | DB CHECK constraint | ✅ |
| PROP-001 | Proposals created as PENDING | DB default + app logic | ✅ |
| PROP-002 | Expiry checked at apply time | `apply_confirmed_proposal` | ✅ |
| PROP-003 | Review flag trigger | `compute_review_flag()` | ✅ |
| AUTH-001 | User-pharmacy link required | `is_user_linked_to_pharmacy` | ✅ |
| AUTH-002 | Pharmacy scope verification | Explicit check in operations | ✅ |
| HIST-001 | Immutable history | Application convention | ⚠️ No DB trigger |
| HIST-002 | Complete state delta | Capture in apply/undo | ✅ |
| UNDO-001 | Latest change only | Check newer history rows | ✅ |
| UNDO-002 | STOCK_UPDATE only | `change_type` check | ✅ |
| UNDO-003 | Single undo per entry | DB UNIQUE constraint | ✅ |
| SUB-001 | Composition match | Filter in `get_substitutes` | ✅ |
| SUB-002 | Per-request approval | Always insert new row | ✅ |
| WORK-001 | No auto-apply | Service layer workflow | ✅ |
| WORK-002 | Atomic apply transaction | DB session commit | ✅ |
| WORK-003 | Atomic undo transaction | DB session commit | ✅ |
| PARSE-001 | Reject uncertain messages | Parser validation | ✅ |
| PARSE-002 | Reject future messages | Parser validation | ✅ |
| PARSE-003 | Distinguish strength from quantity | Parser logic | ✅ |
| GEO-001 | Coordinate bounds | DB CHECK constraint | ✅ |
| GEO-002 | Haversine distance | `get_pharmacies_nearby` | ✅ |
| TG-001 | Unique chat ID per pharmacy | DB UNIQUE constraint | ✅ |

**Total:** 27 rules defined, 26 tested, 1 recommended enhancement (HIST-001 DB trigger)

---

## Appendix: Rule Traceability Matrix

| Rule ID | Linked PRD Requirement | Database Constraint | Service Layer Check | Test File |
|---------|------------------------|---------------------|---------------------|-----------|
| CAT-001 | FR-002 | UNIQUE(brand, comp, strength, form, mfr) | — | test_models.py |
| CAT-002 | FR-002 | — | normalize_composition_key() | test_operations.py |
| INV-002 | NFR-002 | CHECK(status-quantity) | — | test_models.py |
| PROP-001 | FR-003 | DEFAULT status=PENDING | create_stock_proposal | test_operations.py |
| PROP-002 | FR-003 | — | apply_confirmed_proposal | test_operations.py |
| AUTH-001 | FR-004 | FK user_pharmacies | is_user_linked | test_operations.py |
| UNDO-001 | FR-006 | — | Check id > target.id | test_operations.py |
| UNDO-003 | FR-006 | UNIQUE(undo_of_history_id) | — | test_models.py |
| WORK-001 | BR-001 (PRD) | — | Service workflow | test_e2e_workflow.py |

**Legend:**
- FR = Functional Requirement (prd.md)
- NFR = Non-Functional Requirement (prd.md)
- BR = Business Rule (prd.md)

---

**Document Status:** Complete  
**Last Updated:** 2026-10-24  
**Next Review:** After pilot deployment feedback
