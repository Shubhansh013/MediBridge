# MediBridge Database Layer - Status Report

**Generated**: 2026-10-12  
**Report covers**: medibridge_db/ package, tests/, docs/

---

## 1. Implemented vs Planned

| Component | Status | Notes |
|-----------|--------|-------|
| **DB Configuration** | | |
| `config.py` (DB URL, test URL) | IMPLEMENTED | Tests pass; URL functions working |
| `config.py` (stale threshold) | IMPLEMENTED | `get_stale_threshold_hours()` added; default 24h |
| **Database Layer** | | |
| `database.py` (engine, pragmas) | IMPLEMENTED | WAL mode, FK enforcement, busy timeout verified by tests |
| `database.py` (session factory) | IMPLEMENTED | get_session() context manager with commit/rollback |
| `database.py` (Base class) | IMPLEMENTED | DeclarativeBase for models |
| **Models** | | |
| `models.py` | DOCUMENTED ONLY | Module exists with docstring; no models defined yet |
| `pharmacies` model | NOT STARTED | Schema defined in SCHEMA_DESIGN.md |
| `users` model | NOT STARTED | Schema defined in SCHEMA_DESIGN.md |
| `user_pharmacies` model | NOT STARTED | Schema defined in SCHEMA_DESIGN.md |
| `medicines` model | NOT STARTED | Schema defined in SCHEMA_DESIGN.md |
| `medicine_aliases` model | NOT STARTED | Schema defined in SCHEMA_DESIGN.md |
| `inventory` model | NOT STARTED | Schema defined in SCHEMA_DESIGN.md |
| `stock_proposals` model | NOT STARTED | Schema defined in SCHEMA_DESIGN.md |
| `stock_history` model | NOT STARTED | Schema defined in SCHEMA_DESIGN.md |
| `substitution_approvals` model | NOT STARTED | Schema defined in SCHEMA_DESIGN.md |
| `sos_broadcasts` model (optional) | NOT STARTED | Schema defined in SCHEMA_DESIGN.md |
| `sos_responses` model (optional) | NOT STARTED | Schema defined in SCHEMA_DESIGN.md |
| **Operations** | | |
| `operations.py` | DOCUMENTED ONLY | Module exists with docstring; no functions defined yet |
| Inventory reads (5 functions) | NOT STARTED | Signatures defined in CONTRACT.md |
| Medicine & substitute lookup (3 functions) | NOT STARTED | Signatures defined in CONTRACT.md |
| Proposals (5 functions) | NOT STARTED | Signatures defined in CONTRACT.md |
| Apply confirmed proposal | NOT STARTED | Signature defined in CONTRACT.md |
| History reads (3 functions) | NOT STARTED | Signatures defined in CONTRACT.md |
| Undo (2 functions) | NOT STARTED | Signatures defined in CONTRACT.md |
| Substitution approvals (2 functions) | NOT STARTED | Signatures defined in CONTRACT.md |
| Pharmacy membership reads (3 functions) | NOT STARTED | Signatures defined in CONTRACT.md |
| **Utilities** | | |
| Composition key normalization | NOT STARTED | Logic documented in SCHEMA_DESIGN.md |
| Exception classes | NOT STARTED | Defined in CONTRACT.md |
| **Seed Data** | | |
| `seed.py` | DOCUMENTED ONLY | Module exists with docstring; no seed functions defined yet |
| **Tests** | | |
| `test_database.py` | IMPLEMENTED | 5 tests pass (connection, WAL, FK, commit, rollback) |
| Model tests | NOT STARTED | — |
| Operations tests | NOT STARTED | — |
| Seed tests | NOT STARTED | — |
| Integration tests | NOT STARTED | — |

**Test Suite Results**: 5 passed, 0 failed, 1 warning (SAWarning on test class name reuse)

---

## 2. Tables

All table definitions per `docs/SCHEMA_DESIGN.md`:

| Table | Primary Key | Key Fields (Type, Nullable) | Foreign Keys | Relationships |
|-------|-------------|----------------------------|--------------|---------------|
| `pharmacies` | `id` (INTEGER, AUTOINCREMENT) | `name` (TEXT, NOT NULL), `locality` (TEXT, NOT NULL), `latitude` (REAL, NOT NULL), `longitude` (REAL, NOT NULL), `telegram_chat_id` (INTEGER, UNIQUE, NOT NULL), `verification_state` (TEXT, NOT NULL, DEFAULT 'pending'), `is_active` (INTEGER, NOT NULL, DEFAULT 1) | — | 1:M `inventory`, 1:M `stock_proposals`, 1:M `stock_history`, 1:M `substitution_approvals`, M:M `users` via `user_pharmacies` |
| `users` | `id` (INTEGER, AUTOINCREMENT) | `telegram_user_id` (INTEGER, UNIQUE, NOT NULL), `display_name` (TEXT, NOT NULL), `role` (TEXT, NOT NULL), `is_active` (INTEGER, NOT NULL, DEFAULT 1) | — | M:M `pharmacies` via `user_pharmacies`, 1:M `stock_proposals`, 1:M `stock_history`, 1:M `substitution_approvals` |
| `user_pharmacies` | Composite: (`user_id`, `pharmacy_id`) | `role_at_pharmacy` (TEXT, NOT NULL, DEFAULT 'chemist') | `user_id` → users.id, `pharmacy_id` → pharmacies.id | M:1 users, M:1 pharmacies |
| `medicines` | `id` (INTEGER, AUTOINCREMENT) | `brand_name` (TEXT, NOT NULL), `generic_name` (TEXT, NOT NULL), `composition_key` (TEXT, NOT NULL), `strength` (TEXT, NOT NULL), `form` (TEXT, NULLABLE), `is_schedule_h` (INTEGER, NOT NULL, DEFAULT 0), `manufacturer` (TEXT, NULLABLE), `is_active` (INTEGER, NOT NULL, DEFAULT 1), UNIQUE(brand_name, composition_key, strength, form, manufacturer) | — | 1:M `inventory`, 1:M `medicine_aliases`, 2× in `substitution_approvals` |
| `medicine_aliases` | `id` (INTEGER, AUTOINCREMENT) | `alias` (TEXT, UNIQUE, NOT NULL), `alias_type` (TEXT, NOT NULL) | `medicine_id` → medicines.id (CASCADE) | M:1 medicines |
| `inventory` | `id` (INTEGER, AUTOINCREMENT) | `stock_status` (TEXT, NOT NULL, DEFAULT 'IN_STOCK'), `quantity` (INTEGER, NULLABLE, CHECK >= 0), `last_confirmed_at` (DATETIME, NOT NULL), `last_updated_by` (INTEGER, NULLABLE), `last_update_source` (TEXT, NULLABLE), UNIQUE(pharmacy_id, medicine_id) | `pharmacy_id` → pharmacies.id, `medicine_id` → medicines.id, `last_updated_by` → users.id | M:1 pharmacies, M:1 medicines, 1:M stock_history |
| `stock_proposals` | `id` (INTEGER, AUTOINCREMENT) | `raw_message` (TEXT, NOT NULL), `brand_name` (TEXT, NULLABLE), `parsed_salt` (TEXT, NOT NULL), `parsed_strength` (TEXT, NOT NULL), `parsed_form` (TEXT, NULLABLE), `parsed_quantity` (INTEGER, NULLABLE, CHECK >= 0), `parsed_intent` (TEXT, NOT NULL), `confidence` (REAL, NOT NULL, CHECK 0-1), `status` (TEXT, NOT NULL, DEFAULT 'PENDING'), `flagged_for_review` (INTEGER, NOT NULL, DEFAULT 0), `expires_at` (DATETIME, NOT NULL), `renewed_from_id` (INTEGER, NULLABLE), `confirmed_by` (INTEGER, NULLABLE), `confirmed_at` (DATETIME, NULLABLE) | `pharmacy_id` → pharmacies.id, `chemist_user_id` → users.id, `renewed_from_id` → stock_proposals.id, `confirmed_by` → users.id | M:1 pharmacies, M:1 users (chemist), M:0..1 stock_proposals (renewed_from), 1:M stock_history |
| `stock_history` | `id` (INTEGER, AUTOINCREMENT) | `change_type` (TEXT, NOT NULL), `undo_of_history_id` (INTEGER, UNIQUE, NULLABLE), `previous_stock_status` (TEXT, NULLABLE), `new_stock_status` (TEXT, NOT NULL), `previous_quantity` (INTEGER, NULLABLE, CHECK >= 0), `new_quantity` (INTEGER, NULLABLE, CHECK >= 0), `previous_last_confirmed_at` (DATETIME, NULLABLE), `change_reason` (TEXT, NULLABLE) | `inventory_id` → inventory.id, `pharmacy_id` → pharmacies.id, `medicine_id` → medicines.id, `changed_by` → users.id, `source_proposal_id` → stock_proposals.id, `undo_of_history_id` → stock_history.id (self) | M:1 inventory, M:1 pharmacies, M:1 medicines, M:1 users, M:0..1 stock_proposals, M:0..1 self (undo_of) |
| `substitution_approvals` | `id` (INTEGER, AUTOINCREMENT) | `status` (TEXT, NOT NULL), `request_reference` (TEXT, NULLABLE), `approved_at` (DATETIME, NOT NULL) | `pharmacy_id` → pharmacies.id, `requested_medicine_id` → medicines.id, `substitute_medicine_id` → medicines.id, `approving_pharmacist_id` → users.id | M:1 pharmacies, 2× M:1 medicines, M:1 users (pharmacist) |
| `sos_broadcasts` (optional) | `id` (INTEGER, AUTOINCREMENT) | `latitude` (REAL, NULLABLE), `longitude` (REAL, NULLABLE), `radius_km` (REAL, NOT NULL, DEFAULT 10.0), `status` (TEXT, NOT NULL, DEFAULT 'active'), `expires_at` (DATETIME, NOT NULL), `fulfilled_at` (DATETIME, NULLABLE) | `patient_user_id` → users.id, `medicine_id` → medicines.id | M:1 users (patient), M:1 medicines, 1:M sos_responses |
| `sos_responses` (optional) | `id` (INTEGER, AUTOINCREMENT) | `can_supply` (INTEGER, NOT NULL), `note` (TEXT, NULLABLE), `responded_at` (DATETIME, NOT NULL) | `sos_broadcast_id` → sos_broadcasts.id, `pharmacy_id` → pharmacies.id, `chemist_user_id` → users.id | M:1 sos_broadcasts, M:1 pharmacies, M:1 users |

---

## 3. Enums

All enums per `docs/CONTRACT.md`:

| Enum Name | Values | Table/Column |
|-----------|--------|--------------|
| `UserRole` | `patient`, `chemist`, `pharmacist`, `admin` | `users.role` |
| `PharmacyVerification` | `pending`, `verified`, `rejected` | `pharmacies.verification_state` |
| `StaffRoleAtPharmacy` | `chemist`, `pharmacist` | `user_pharmacies.role_at_pharmacy` |
| `StockStatus` | `IN_STOCK`, `LOW_STOCK`, `OUT_OF_STOCK` | `inventory.stock_status`, `stock_proposals.parsed_intent` |
| `ProposalStatus` | `PENDING`, `CONFIRMED`, `REJECTED`, `EXPIRED` | `stock_proposals.status` |
| `HistoryChangeType` | `STOCK_UPDATE`, `UNDO` | `stock_history.change_type` |
| `SubstitutionStatus` | `APPROVED`, `REJECTED` | `substitution_approvals.status` |
| `SosStatus` | `active`, `fulfilled`, `expired`, `cancelled` | `sos_broadcasts.status` |
| `AliasType` | `misspelling`, `hinglish`, `short`, `brand_variant` | `medicine_aliases.alias_type` |
| `UpdateSource` | `chemist`, `proposal_apply`, `undo`, `seed` | `inventory.last_update_source` |

---

## 4. Function Signatures

All functions per `docs/CONTRACT.md`:

### Inventory Reads (Planned)
- `get_pharmacies_nearby(lat: float, lon: float, radius_km: float, limit: int) -> List[Pharmacy]` — Haversine filter; only is_active=1, verified
- `get_inventory_for_medicine(pharmacy_id: int, medicine_id: int, include_stale: bool, include_inactive: bool = False) -> Optional[Inventory]` — Single row with filters
- `get_pharmacy_inventory(pharmacy_id: int, include_stale: bool, status_filter: Optional[StockStatus], include_inactive: bool = False) -> List[InventoryWithMedicine]` — All inventory for pharmacy
- `get_substitutes_for_medicine(medicine_id: int, pharmacy_id: Optional[int], include_inactive: bool = False) -> List[Medicine]` — Same composition_key+strength+form
- `get_stale_inventory(pharmacy_id: Optional[int], threshold_hours: Optional[int] = None, include_inactive: bool = False) -> List[Inventory]` — Cron job; uses config threshold

### Medicine & Substitute Lookup (Planned)
- `find_medicine_by_alias(alias: str, include_inactive: bool = False) -> Optional[Medicine]` — Alias → medicine lookup
- `search_medicines(query: str, limit: int, include_inactive: bool = False) -> List[Medicine]` — ILIKE search
- `get_medicine_by_id(medicine_id: int, include_inactive: bool = False) -> Optional[Medicine]` — PK lookup

### Proposals (Planned)
- `create_stock_proposal(pharmacy_id: int, chemist_user_id: int, raw_message: str, parsed: ParsedProposalInput) -> StockProposal` — Insert PENDING; expires_at = now + 24h
- `get_proposal_by_id(proposal_id: int) -> Optional[StockProposal]` — PK lookup
- `list_pending_proposals(pharmacy_id: Optional[int], flagged_only: bool, limit: int) -> List[StockProposal]` — Filter PENDING & not expired
- `expire_overdue_proposals(cutoff: datetime) -> int` — Update PENDING → EXPIRED; returns count
- `renew_proposal(expired_proposal_id: int, chemist_user_id: int, raw_message: str, parsed: ParsedProposalInput) -> StockProposal` — New proposal with renewed_from_id

### Apply Confirmed Proposal (Planned)
- `apply_confirmed_proposal(proposal_id: int, confirmed_by_user_id: int, pharmacy_id: int, current_time: Optional[datetime] = None) -> Inventory` — Single transaction: verify CONFIRMED & not expired (checks expires_at < current_time); update inventory + write history. Raises: `ProposalNotPendingError`, `ProposalExpiredError`, `ProposalPharmacyMismatchError`, `InventoryNotFoundError`, `StockStatusQuantityMismatchError`

### History Reads (Planned)
- `get_inventory_history(inventory_id: int, limit: int) -> List[StockHistory]` — Chronological
- `get_pharmacy_history(pharmacy_id: int, limit: int) -> List[StockHistory]` — All history for pharmacy
- `get_user_history(user_id: int, limit: int) -> List[StockHistory]` — All changes by user

### Undo (Planned)
- `undo_last_change(history_id: int, undone_by_user_id: int, pharmacy_id: int) -> StockHistory` — Single transaction: verify STOCK_UPDATE, not already undone, no newer change (id > target.id), pharmacy scope. Raises: `HistoryAlreadyUndoneError`, `NewerChangeExistsError`, `CannotUndoUndoError`, `PharmacyScopeMismatchError`
- `can_undo(history_id: int) -> bool` — Check undo constraints without modifying

### Substitution Approvals (Planned)
- `record_substitution_approval(pharmacy_id: int, requested_medicine_id: int, substitute_medicine_id: int, approving_pharmacist_id: int, status: SubstitutionStatus, request_reference: Optional[str]) -> SubstitutionApproval` — Insert approval; verify same composition. Raises: `PharmacyScopeMismatchError`
- `get_substitution_approvals(pharmacy_id: int, requested_medicine_id: Optional[int]) -> List[SubstitutionApproval]` — Audit log

### Pharmacy Membership Reads (Planned)
- `is_user_linked_to_pharmacy(user_id: int, pharmacy_id: int) -> bool` — Service layer calls this for authorization
- `get_user_pharmacies(user_id: int, include_inactive: bool = False) -> List[Pharmacy]` — User's pharmacies
- `get_pharmacy_staff(pharmacy_id: int, role: Optional[StaffRoleAtPharmacy], include_inactive: bool = False) -> List[User]` — Pharmacy staff

### Exception Classes (Planned)
- `ProposalNotPendingError`, `ProposalExpiredError`, `ProposalPharmacyMismatchError`, `InventoryNotFoundError`, `HistoryAlreadyUndoneError`, `NewerChangeExistsError`, `CannotUndoUndoError`, `StockStatusQuantityMismatchError`, `PharmacyScopeMismatchError`

---

## 5. How These Are Represented

### Pending Proposals
- Created with `status=PENDING`, `expires_at = created_at + 24h`
- `flagged_for_review` set by **service layer** (not DB) based on confidence + is_schedule_h
- Cron calls `expire_overdue_proposals(cutoff)` to mark PENDING → EXPIRED
- `apply_confirmed_proposal` **independently checks** `expires_at < current_time` at apply time (takes optional `current_time` for testing)
- Expired proposals are dead; renewal = new proposal with `renewed_from_id`

### Expiry (expires_at, status, check at apply time)
- `expires_at` set once on insert, never changed
- `expire_overdue_proposals` updates statuses for overdue PENDING proposals
- **Critical**: `apply_confirmed_proposal` checks `expires_at` at apply time and raises `ProposalExpiredError` regardless of whether cron has run
- Takes optional `current_time` parameter for testing

### Audit History
- Every applied change writes one `stock_history` row (`STOCK_UPDATE`)
- Every undo writes one `stock_history` row (`UNDO`) with `undo_of_history_id` pointing to original
- History rows **never deleted or modified**
- `previous_*` / `new_*` capture full state delta

### Undo (undo_of_history_id, latest-change rule)
- `undo_of_history_id` is UNIQUE — enforces one undo per history row at DB level
- Only **latest** change for an inventory row can be undone: check `id > target.id` (auto-increment guarantees chronological order)
- Undo entry (`change_type=UNDO`) **cannot** be undone
- To change stock after undo: apply a new proposal
- Both original and undo rows remain in history

### Pharmacy Membership (user_pharmacies, read-only helper)
- `user_pharmacies` is M:M source of truth
- Every mutating function takes `pharmacy_id` and verifies **record's** `pharmacy_id` matches passed `pharmacy_id` (scope check only)
- **Service layer** calls `is_user_linked_to_pharmacy(user_id, pharmacy_id)` for authorization before calling mutating functions
- DB records `acting_user_id` and `pharmacy_id` on every change for audit

### Pharmacist Approval (substitution_approvals)
- `substitution_approvals` is **append-only**; each request is independent
- `status` is APPROVED or REJECTED — no "pending" state
- No function reuses old approvals
- `request_reference` stores free-text context (patient name, order ID)

---

## 6. Service-Layer Assumptions

### What DB Assumes Service Layer Has Checked:
1. **Authorization and membership**: Acting user is allowed to perform the action AND is linked to pharmacy via `user_pharmacies`. Service layer calls `is_user_linked_to_pharmacy(user_id, pharmacy_id)` before calling mutating functions.
2. **Confirmation validity**: Proposal marked CONFIRMED was actually confirmed by authorized chemist.
3. **Low-stock threshold**: Service layer decides LOW_STOCK vs IN_STOCK; DB enforces: IN_STOCK/LOW_STOCK → quantity > 0 or NULL; OUT_OF_STOCK → quantity = 0 or NULL.
4. **Proposal parsing correctness**: LLM parsing (salt, strength, intent) is trusted.
5. **Substitution correctness**: Requested and substitute medicines share same composition_key, strength, form.
6. **Flagging logic**: Service layer sets `flagged_for_review` based on confidence threshold and `is_schedule_h`; DB stores both but never sets the flag.

### What DB Enforces (Integrity Only):
1. **Proposal state at apply time**: `apply_confirmed_proposal` rejects if status != CONFIRMED OR expires_at < current_time (optional param for testing, defaults to datetime.utcnow()).
2. **Pharmacy scope**: Every mutating function verifies record's pharmacy_id matches passed pharmacy_id.
3. **Undo constraints**: Target is STOCK_UPDATE, undo_of_history_id is NULL, no newer history row (id > target.id).
4. **Stock status + quantity consistency**: Check constraints reject contradictions (OUT_OF_STOCK with quantity > 0).
5. **Unique constraints**: All PK/UNIQUE per tables.
6. **FK integrity**: All FKs enforced; no cascading deletes on history/audit tables.
7. **Expiry enforcement**: `expire_overdue_proposals` updates statuses; `apply_confirmed_proposal` independently checks expires_at at apply time.

---

## 7. Known Gaps and Open Questions for Member 1

### Known Gaps
1. **No models implemented** — all 11 tables need SQLAlchemy 2.0 models with Mapped/mapped_column
2. **No operations implemented** — 23 functions need implementation
3. **No composition key normalization** — logic documented, needs implementation
4. **No exception classes** — 9 custom exceptions need definition
5. **No seed data** — realistic seed script for 12 pharmacies, medicines, inventory needed
6. **No operation tests** — only database.py is tested
7. **No integration tests** — end-to-end propose→confirm→apply→undo flow untested

### Open Questions for Member 1 (From CONTRACT.md)
1. **Can one pharmacy have more than one Telegram chat, or only one chat ID?** Currently `telegram_chat_id` is UNIQUE on `pharmacies`.
2. **Who sets `flagged_for_review` and with what rule?** DB stores the flag; service layer sets it (e.g., confidence < 0.8 OR is_schedule_h). Confirm threshold.
3. **Is renewal as a new proposal (with `renewed_from_id`) acceptable?** Current design: expired proposal stays EXPIRED; renewal = new proposal.

---

## 8. Member 1's Phase 2A Code Check

**Search performed**: Checked for backend/, phase2/, Phase2A/, workflow/, and similar directories in project root.

**Result**: **No Member 1 Phase 2A code found in this workspace.** The `backend/` directory exists but is empty (no Python files). Therefore, no naming or contract mismatches can be reported.

If Member 1's code exists elsewhere, provide the path for comparison.

---

## Summary

**Implemented**: DB configuration (URL, stale threshold), database layer (engine, pragmas, session management), 5 passing tests.

**Documented Only**: Schema for 11 tables, 23 function signatures, 10 enums, exception classes, composition key normalization logic.

**Not Started**: All models, all operations, exception classes, composition normalization, seed data, operation tests, integration tests.

**Critical for Part 1**: Permission/authorization removed from all DB functions; expiry check added at apply time; stale threshold configurable; soft delete with is_active + include_inactive; flagging logic clarified as service-layer only.