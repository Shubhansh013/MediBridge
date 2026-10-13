# MediBridge Database Layer - Status Report

**Generated**: 2026-10-13  
**Report covers**: medibridge_db/ package, tests/, docs/, scripts/

---

## 1. Implemented vs Planned

| Component | Status | Notes |
|-----------|--------|-------|
| **DB Configuration** | COMPLETE | config.py: DB URL, test URL, stale threshold (env-configurable) |
| **Database Layer** | COMPLETE | database.py: WAL mode, FK enforcement, busy timeout, UTCDateTime, utc_now(), session factory |
| **Models** | COMPLETE | models.py: All 11 models (8 core + 2 optional SOS + user_pharmacies); enums, constraints, indexes, relationships |
| **Normalize** | COMPLETE | normalize.py: Single source of truth for composition_key normalization |
| **Exceptions** | COMPLETE | exceptions.py: 9 custom exception classes |
| **Operations** | COMPLETE | operations.py: 24 public functions (23 from contract + get_latest_history_entry) |
| **Seed Data** | COMPLETE | seed.py: Idempotent seeding (12 pharmacies, 25 medicines, mixed inventory/proposals/history) |
| **Init Script** | COMPLETE | scripts/init_db.py: CLI with --db-url, --no-seed, --reset, --yes; SQLite URL safety checks |
| **Tests - Database** | COMPLETE | test_database.py: 5 tests (connection, WAL, FK, commit, rollback) |
| **Tests - Models** | COMPLETE | test_models.py: 57 tests (constraints, relationships, enums, timezone, staleness) |
| **Tests - Operations** | COMPLETE | test_operations.py: 42 tests (all 24 operations + edge cases) |
| **Tests - Seed** | COMPLETE | test_seed.py: 16 tests (idempotency, counts, substitute lookup, Dolo/Calpol scenario, Schedule H, undo linkage, etc.) |
| **Tests - Persistence** | COMPLETE | test_persistence.py: 9 tests (file persistence, WAL mode, FK enforcement, rollback, concurrent readers, query timing) |
| **Docs - CONTRACT** | FROZEN | CONTRACT.md: Interface contract for Member 1/3/4 |
| **Docs - SCHEMA_DESIGN** | COMPLETE | SCHEMA_DESIGN.md: Detailed table definitions + normalization rules |
| **Docs - DATABASE** | COMPLETE | DATABASE.md: Setup, full schema, all operation signatures, transaction examples, lifecycle, responsibilities, timestamp rules, limitations |
| **Docs - STATUS** | COMPLETE | This file |
| **Docs - CHANGELOG** | COMPLETE | SCHEMA_CHANGELOG.md: v1.0 contract freeze, v1.1 additive changes |
| **README** | COMPLETE | README.md: 10-line quickstart + project structure |

**Test Suite Results**: **129 passed** (5 database + 57 models + 42 operations + 16 seed + 9 persistence), 0 failed

---

## 2. Row Counts (Demo Seed Data)

| Table | Count |
|-------|-------|
| `pharmacies` | 12 |
| `users` | 9 |
| `user_pharmacies` | 7 |
| `medicines` | 25 |
| `medicine_aliases` | 17 |
| `inventory` | 12 |
| `stock_proposals` | 5 |
| `stock_history` | varies (2-4 depending on apply/undo success) |
| `substitution_approvals` | 0-1 (depending on seed execution) |

---

## 3. Task 1-4 Answers (from session)

### Task 1 Confirmations
✅ (a) Line `target_history.undo_of_history_id = undo_history.id` removed; "already undone" checks via SELECT query at `operations.py:946-953`  
✅ (b) Original history row unchanged after undo; test at `test_operations.py:997-999`  
✅ (c) `.ilike()` replaced with `func.lower()` equality at `operations.py:320`; wildcard test at `test_operations.py:450-475`

### Task 2: apply_confirmed_proposal Behavior
**CONTRACT.md line 55** lists `InventoryNotFoundError`. **Current behavior correct**: raises exception when inventory row missing. Does NOT create inventory rows.

### Task 3: get_latest_history_entry
**Added** at `operations.py:844-878`:
```python
def get_latest_history_entry(
    session: Session,
    inventory_id: int,
    pharmacy_id: int,
) -> Optional[StockHistory]
```
Returns most recent history row for inventory, scoped to pharmacy. Raises `PharmacyScopeMismatchError` if mismatch.  
Tests: `test_operations.py:934-985`

### Task 4: Function Signatures

| Function | Returns | Exceptions |
|----------|---------|------------|
| `create_stock_proposal` | ORM `StockProposal` with `source_message_ref` (TEXT nullable) | — |
| `get_proposal_by_id` | ORM `StockProposal` or `None` | — |
| `list_pending_proposals` | `List[StockProposal]` (ORM objects) | — |
| `renew_proposal` | ORM `StockProposal` | `ProposalNotPendingError` |
| `undo_last_change` | ORM `StockHistory` (the UNDO row) | `HistoryAlreadyUndoneError`, `NewerChangeExistsError`, `CannotUndoUndoError`, `PharmacyScopeMismatchError` |
| `record_substitution_approval` | ORM `SubstitutionApproval` | `ValueError` (same medicine / composition mismatch) |

All return ORM objects. `source_message_ref` is a direct field access (`.source_message_ref`), not nested.

---

## 4. Member 1 Integration Contract

### Service Layer Must Do
1. ✅ Call `is_user_linked_to_pharmacy(user_id, pharmacy_id)` before any mutating operation
2. ✅ Set proposal `status=CONFIRMED` + `confirmed_by` + `confirmed_at` before calling apply
3. ✅ Set `flagged_for_review` on proposals (confidence < 0.8 OR `medicine.is_schedule_h`)
4. ✅ Call `apply_confirmed_proposal()` and `undo_last_change()` only through approved workflow
5. ✅ Own the outer transaction (commit/rollback)

### DB Layer Enforces
1. ✅ Pharmacy scope (all mutating operations verify `pharmacy_id` match)
2. ✅ Proposal state (`status=CONFIRMED`, `expires_at >= current_time`)
3. ✅ Inventory consistency (`OUT_OF_STOCK` → qty=0/NULL; `IN_STOCK`/`LOW_STOCK` → qty>0/NULL)
4. ✅ Undo constraints (STOCK_UPDATE only, not already undone, no newer change, pharmacy scope)
5. ✅ SAVEPOINT transactions (flush but never commit)
6. ✅ UNIQUE constraints, CHECK constraints, FK integrity

### DB Layer Does NOT
1. ❌ Make authorization decisions
2. ❌ Decide low-stock thresholds
3. ❌ Parse LLM output
4. ❌ Set `flagged_for_review` (stores only)
5. ❌ Commit transactions

---

## 5. Schema Changes Since Contract Freeze

### v1.1 Additive Changes (2026-10-13)
- ✅ Added `get_latest_history_entry(session, inventory_id, pharmacy_id)` function
- No schema/model changes
- No signature changes to existing 23 functions
- CONTRACT.md interface remains frozen

---

## 6. Known Issues / Limitations

| Issue | Impact | Mitigation |
|-------|--------|------------|
| SQLite Haversine (Python, not SQL) | ~10-50ms query for 12 pharmacies within 50km | Acceptable for MVP; bounding box pre-filter |
| `apply_confirmed_proposal` medicine resolution | Ambiguous if brand_name=NULL and multiple brands match | Service layer should resolve brand before confirm; raises `InventoryNotFoundError` if ambiguous |
| No inventory auto-create | First stock update requires pre-seeded inventory row | Seed script creates initial inventory; service layer can manually insert if needed |
| Soft delete visibility | `is_active=0` rows remain in history | Default queries filter `is_active=1`; audit queries can include inactive |
| One Telegram chat per pharmacy | `telegram_chat_id` UNIQUE constraint | Acceptable for MVP (one chat = one pharmacy) |
| SOS tables optional | `sos_broadcasts`/`sos_responses` exist but no operations | Deferred to post-MVP |

---

## Summary

**Member 2 deliverables: COMPLETE**

- ✅ Config, models, constraints/indexes, transactions/savepoints
- ✅ 24 operations (23 contract + 1 new)
- ✅ Init script + idempotent seed (12 pharmacies, 25 medicines, mixed inventory)
- ✅ 129 tests (100% pass)
- ✅ Full documentation (CONTRACT, SCHEMA_DESIGN, DATABASE, STATUS, CHANGELOG, README)
- ✅ Member 1 integration: clear boundary (auth vs integrity)

**No blockers for Member 1 integration.**
