# MediBridge Database Schema Design

## Conventions

| Convention | Choice | Rationale |
|------------|--------|-----------|
| Primary key type | `INTEGER` auto-increment | Simple, fast, SQLite-native; no UUID overhead for ~12 pharmacies |
| Timestamps | `DATETIME` UTC (`datetime.utcnow()`) | Consistent ordering, easy expiry/24h checks |
| Enums | `TEXT` with `CHECK` constraints | Portable, readable in raw SQL, no migration for new values |
| Naming | `snake_case` tables/columns, plural tables | SQLAlchemy default, consistent with Python |
| Foreign keys | Explicit `FOREIGN KEY` + `ON DELETE` | Data integrity; `CASCADE` only where safe |
| Soft deletes | `active` boolean flag on core entities | Never lose history; simple queries with `WHERE active=1` |

---

## Table Definitions

### 1. `pharmacies` — Verified pharmacy locations

| Column | Type | Nullable | Constraints | Index | Reason |
|--------|------|----------|-------------|-------|--------|
| `id` | INTEGER | NO | PK, AUTOINCREMENT | — | Primary reference |
| `name` | TEXT | NO | — | — | Display name |
| `locality` | TEXT | NO | — | **idx_pharmacies_locality** | Fast "nearby" filter by area |
| `address` | TEXT | NO | — | — | Full address for display |
| `latitude` | REAL | NO | — | — | Distance calculation + Maps link |
| `longitude` | REAL | NO | — | — | Distance calculation + Maps link |
| `contact_phone` | TEXT | YES | — | — | Optional contact |
| `telegram_chat_id` | INTEGER | NO | UNIQUE | **uq_pharmacies_telegram_chat_id** | 1:1 with Telegram chat for notifications |
| `verification_state` | TEXT | NO | CHECK IN ('pending','verified','rejected') DEFAULT 'pending' | **idx_pharmacies_verification** | Admin verifies new chemists |
| `is_active` | INTEGER | NO | CHECK IN (0,1) DEFAULT 1 | **idx_pharmacies_is_active** | Soft delete; history rows reference these |
| `created_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — | Audit |
| `updated_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — | Audit |

**Indexes:**
- `idx_pharmacies_locality` — filter pharmacies by locality for "nearby" queries
- `idx_pharmacies_verification` — admin dashboard: list pending verifications
- `uq_pharmacies_telegram_chat_id` — enforce one pharmacy per Telegram chat

---

### 2. `users` — Telegram users with roles

| Column | Type | Nullable | Constraints | Index | Reason |
|--------|------|----------|-------------|-------|--------|
| `id` | INTEGER | NO | PK, AUTOINCREMENT | — | Internal PK |
| `telegram_user_id` | INTEGER | NO | UNIQUE | **uq_users_telegram_user_id** | Stable Telegram identifier |
| `display_name` | TEXT | NO | — | — | Human-readable name |
| `role` | TEXT | NO | CHECK IN ('patient','chemist','pharmacist','admin') | **idx_users_role** | Role-based queries |
| `is_active` | INTEGER | NO | CHECK IN (0,1) DEFAULT 1 | **idx_users_is_active** | Soft delete; history rows reference these |
| `created_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — | Audit |
| `updated_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — | Audit |

**Indexes:**
- `uq_users_telegram_user_id` — fast lookup by Telegram ID (incoming messages)
- `idx_users_role` — list all chemists/pharmacists/admins

---

### 3. `user_pharmacies` — Many-to-many: staff ↔ pharmacies

| Column | Type | Nullable | Constraints | Index | Reason |
|--------|------|----------|-------------|-------|--------|
| `user_id` | INTEGER | NO | FK → users.id, PK | — | Part of composite PK |
| `pharmacy_id` | INTEGER | NO | FK → pharmacies.id, PK | — | Part of composite PK |
| `role_at_pharmacy` | TEXT | NO | CHECK IN ('chemist','pharmacist') DEFAULT 'chemist' | — | Same person can be chemist at one, pharmacist at another |
| `created_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — | Audit |

**Constraints:**
- PK (`user_id`, `pharmacy_id`) — one row per user-pharmacy pair
- FK cascade: deleting user or pharmacy removes association

**Indexes:**
- Implicit from PK; add `idx_user_pharmacies_pharmacy` on `pharmacy_id` for "who works here" queries

---

### 4. `medicines` — Canonical medicine catalogue

| Column | Type | Nullable | Constraints | Index | Reason |
|--------|------|----------|-------------|-------|--------|
| `id` | INTEGER | NO | PK, AUTOINCREMENT | — | Primary reference |
| `brand_name` | TEXT | NO | — | **idx_medicines_brand** | Display + search |
| `generic_name` | TEXT | NO | — | — | Human-readable primary salt (for display) |
| `composition_key` | TEXT | NO | — | **idx_medicines_composition** | **Normalized substitution key: lowercase, salts sorted alphabetically, joined by `+`** |
| `strength` | TEXT | NO | — | — | Strength (e.g., "650mg", "500mg+125mg") |
| `form` | TEXT | YES | — | — | tablet, syrup, injection, etc. |
| `is_schedule_h` | INTEGER | NO | CHECK IN (0,1) DEFAULT 0 | **idx_medicines_schedule_h** | Flag for admin review / Rx requirement |
| `manufacturer` | TEXT | YES | — | — | Optional display |
| `is_active` | INTEGER | NO | CHECK IN (0,1) DEFAULT 1 | **idx_medicines_is_active** | Soft delete; history rows reference these |
| `created_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — | Audit |
| `updated_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — | Audit |

**Unique constraint:** `UNIQUE (brand_name, composition_key, strength, form, manufacturer)` — one row per distinct brand+composition+strength+form+manufacturer

**Handling NULL manufacturer in SQLite:** SQLite treats NULL ≠ NULL for UNIQUE constraints, so two rows with identical other columns and both `manufacturer=NULL` would be allowed. To prevent this, the application **normalizes missing manufacturer to empty string `''`** before insert (COALESCE in SQL or Python `manufacturer or ''`). This makes the unique constraint work as intended.

**Indexes:**
- `idx_medicines_composition` (NON-unique) on `(composition_key, strength, form)` — **substitute lookup: returns ALL brands sharing the same salt(s)+strength+form**
- `idx_medicines_schedule_h` — filter Schedule H medicines
- `idx_medicines_brand` — brand search/autocomplete

**Composition key normalization (application-level, enforced on insert/update):**
- Split `generic_name` on `+` (combination medicines)
- Lowercase each salt, trim whitespace
- Sort salts alphabetically
- Join with `+`
- Examples:
  1. `"Paracetamol"` → `"paracetamol"`
  2. `"Paracetamol + Caffeine"` → `"caffeine+paracetamol"`
  3. `"Amoxicillin + Clavulanic Acid"` → `"amoxicillin+clavulanic acid"`

**Substitute lookup (returns multiple brands for same salt):**
```sql
SELECT id, brand_name, manufacturer
FROM medicines
WHERE composition_key = ? AND strength = ? AND form = ? AND active = 1
```
Example: For `composition_key='paracetamol'`, `strength='650mg'`, `form='tablet'` → returns Dolo 650, Calpol 650, Crocin 650, etc.

---

### 5. `medicine_aliases` — Aliases, misspellings, Hinglish names

| Column | Type | Nullable | Constraints | Index | Reason |
|--------|------|----------|-------------|-------|--------|
| `id` | INTEGER | NO | PK, AUTOINCREMENT | — | Primary reference |
| `medicine_id` | INTEGER | NO | FK → medicines.id, ON DELETE CASCADE | — | Link to canonical entry |
| `alias` | TEXT | NO | — | **uq_medicine_aliases_alias** | Unique alias for fast lookup |
| `alias_type` | TEXT | NO | CHECK IN ('misspelling','hinglish','short','brand_variant') | — | Categorize for debugging |
| `created_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — | Audit |

**Constraints:**
- `UNIQUE (alias)` — each alias maps to exactly one medicine
- FK cascade: deleting medicine removes its aliases

**Index:**
- `uq_medicine_aliases_alias` — **O(1) lookup: "dolo650" → medicine_id**

---

### 6. `inventory` — Pharmacy × medicine stock (one row per combination)

| Column | Type | Nullable | Constraints | Index | Reason |
|--------|------|----------|-------------|-------|--------|
| `id` | INTEGER | NO | PK, AUTOINCREMENT | — | Primary reference |
| `pharmacy_id` | INTEGER | NO | FK → pharmacies.id | **idx_inventory_pharmacy** | "What does this pharmacy have?" |
| `medicine_id` | INTEGER | NO | FK → medicines.id | **idx_inventory_medicine** | "Which pharmacies have this medicine?" |
| `stock_status` | TEXT | NO | CHECK IN ('IN_STOCK','LOW_STOCK','OUT_OF_STOCK') DEFAULT 'IN_STOCK' | **idx_inventory_stock_status** | Three-state status enum |
| `quantity` | INTEGER | YES | CHECK (quantity >= 0) | — | Optional exact count |
| `last_confirmed_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | **idx_inventory_last_confirmed** | **24h staleness = `datetime('now') - last_confirmed_at > 24h`** |
| `last_updated_by` | INTEGER | YES | FK → users.id | — | Who/what last changed it |
| `last_update_source` | TEXT | YES | CHECK IN ('chemist','proposal_apply','undo','seed') | — | Audit trail |
| `created_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — | Audit |
| `updated_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — | Audit |

**Unique constraint:** `UNIQUE (pharmacy_id, medicine_id)` — one inventory row per pharmacy-medicine pair

**Check constraints for stock_status + quantity consistency:**
- `CHECK (stock_status = 'OUT_OF_STOCK' AND (quantity IS NULL OR quantity = 0))`
- `CHECK (stock_status IN ('IN_STOCK','LOW_STOCK') AND (quantity IS NULL OR quantity > 0))`

*Note: Low-stock threshold is a service-layer decision; DB only enforces that IN_STOCK/LOW_STOCK implies quantity > 0 (or NULL = unknown), and OUT_OF_STOCK implies quantity = 0 or NULL.*

**Indexes:**
- `idx_inventory_pharmacy` — pharmacy inventory page
- `idx_inventory_medicine` — patient search: "who has Dolo 650?"
- `idx_inventory_stock_status` — filter by status
- `idx_inventory_last_confirmed` — cron job: find stale entries (>24h)
- Composite `idx_inventory_pharmacy_medicine` (from unique) covers both directions

**Computed flag (not stored):**
```sql
-- Stale if not confirmed in 24h
CASE WHEN datetime('now') - last_confirmed_at > '24 hours' THEN 1 ELSE 0 END AS is_stale
```

---

### 7. `stock_proposals` — AI-parsed updates awaiting confirmation

| Column | Type | Nullable | Constraints | Index | Reason |
|--------|------|----------|-------------|-------|--------|
| `id` | INTEGER | NO | PK, AUTOINCREMENT | — | Primary reference |
| `pharmacy_id` | INTEGER | NO | FK → pharmacies.id | **idx_proposals_pharmacy** | Chemist's pharmacy |
| `chemist_user_id` | INTEGER | NO | FK → users.id | **idx_proposals_chemist** | Who sent it |
| `raw_message` | TEXT | NO | — | — | Original "Dolo 650 khatam" |
| `brand_name` | TEXT | YES | — | — | Optional brand mentioned |
| `parsed_salt` | TEXT | NO | — | **idx_proposals_salt** | Matched generic name |
| `parsed_strength` | TEXT | NO | — | — | Matched strength |
| `parsed_form` | TEXT | YES | — | — | Optional form |
| `parsed_quantity` | INTEGER | YES | CHECK (quantity >= 0) | — | Optional parsed quantity |
| `parsed_intent` | TEXT | NO | CHECK IN ('IN_STOCK','LOW_STOCK','OUT_OF_STOCK') | — | What chemist meant |
| `confidence` | REAL | NO | CHECK (confidence >= 0.0 AND confidence <= 1.0) | — | LLM confidence 0–1 |
| `status` | TEXT | NO | CHECK IN ('PENDING','CONFIRMED','REJECTED','EXPIRED') DEFAULT 'PENDING' | **idx_proposals_status** | Workflow state |
| `flagged_for_review` | INTEGER | NO | CHECK IN (0,1) DEFAULT 0 | **idx_proposals_flagged** | Low confidence OR Schedule H |
| `expires_at` | DATETIME | NO | — | **idx_proposals_expires** | Auto-expire 24h after created_at |
| `renewed_from_id` | INTEGER | YES | FK → stock_proposals.id | — | Link to expired proposal this renews |
| `confirmed_by` | INTEGER | YES | FK → users.id | — | Chemist who confirmed |
| `confirmed_at` | DATETIME | YES | — | — | When confirmed |
| `review_note` | TEXT | YES | — | — | Optional rejection reason |
| `created_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | **idx_proposals_created** | Time-based expiry |
| `updated_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — | Audit |

**Indexes:**
- `idx_proposals_status` — dashboard: list pending proposals
- `idx_proposals_flagged` — admin review queue
- `idx_proposals_pharmacy` + `idx_proposals_chemist` — ownership queries
- `idx_proposals_expires` — find overdue proposals for expiry job
- `idx_proposals_created` — time-based queries

**Expiry rules:**
- `expires_at = datetime(created_at, '+24 hours')` — set on insert
- Cron job marks PENDING → EXPIRED where `datetime('now') > expires_at`
- EXPIRED proposals can NEVER be applied; renewal = NEW proposal with `renewed_from_id` pointing to the expired one

---

### 8. `stock_history` — Immutable audit log of every applied change (+ undo entries)

| Column | Type | Nullable | Constraints | Index | Reason |
|--------|------|----------|-------------|-------|--------|
| `id` | INTEGER | NO | PK, AUTOINCREMENT | — | Primary reference |
| `inventory_id` | INTEGER | NO | FK → inventory.id | **idx_history_inventory** | All changes for one inventory row |
| `pharmacy_id` | INTEGER | NO | FK → pharmacies.id | **idx_history_pharmacy** | Pharmacy-level audit |
| `medicine_id` | INTEGER | NO | FK → medicines.id | — | Medicine-level audit |
| `changed_by` | INTEGER | NO | FK → users.id | **idx_history_user** | Who triggered it |
| `source_proposal_id` | INTEGER | YES | FK → stock_proposals.id | — | Link to originating proposal |
| `change_type` | TEXT | NO | CHECK IN ('STOCK_UPDATE','UNDO') | — | Distinguish undo from normal |
| `undo_of_history_id` | INTEGER | YES | FK → stock_history.id, UNIQUE | **uq_history_undo_of** | **DB-level: one undo per history row** |
| `previous_stock_status` | TEXT | YES | CHECK IN ('IN_STOCK','LOW_STOCK','OUT_OF_STOCK') | — | NULL for first insert |
| `new_stock_status` | TEXT | NO | CHECK IN ('IN_STOCK','LOW_STOCK','OUT_OF_STOCK') | — | New value |
| `previous_quantity` | INTEGER | YES | CHECK (quantity >= 0) | — | NULL if not tracked |
| `new_quantity` | INTEGER | YES | CHECK (quantity >= 0) | — | NULL if not tracked |
| `previous_last_confirmed_at` | DATETIME | YES | — | — | For undo restoration |
| `change_reason` | TEXT | YES | — | — | Human-readable: "chemist update", "undo #42" |
| `created_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | **idx_history_created** | Chronological audit log |

**Constraints:**
- No `ON DELETE CASCADE` on FKs — history must survive referenced row deletion
- `previous_*` nullable for initial seed/creation entries
- `UNIQUE (undo_of_history_id)` — **database enforces: at most one undo per history row**
- `undo_of_history_id` FK is self-referential, nullable (NULL for non-undo rows)

**Indexes:**
- `idx_history_inventory` — "show history for this medicine at this pharmacy"
- `idx_history_pharmacy` — pharmacy audit trail
- `idx_history_user` — user activity log
- `idx_history_created` — time-range queries
- `uq_history_undo_of` — enforces single-undo constraint

**Undo rules (enforced at DB + application layer inside one transaction):**

1. **Single-undo constraint**: `UNIQUE (undo_of_history_id)` prevents a second undo of the same history row. Attempting a second `INSERT` with the same `undo_of_history_id` fails with constraint violation.

2. **No-undo-if-newer-exists rule**: Before inserting an undo row for `target_history_id`, the function checks:
   ```sql
   SELECT 1 FROM stock_history
   WHERE inventory_id = (SELECT inventory_id FROM stock_history WHERE id = ?)
     AND id > ?
   ```
   If any row exists with a **higher `id`** (auto-increment guarantees chronological order), the undo is rejected — a newer change has superseded the target.

3. **No-undo-of-undo rule**: An undo entry (`change_type='UNDO'`) cannot itself be undone. The function checks `target.change_type == 'UNDO'` and rejects. To change stock again after an undo, a new stock update must be applied (not an undo-of-undo).

4. **Transaction sequence (apply_confirmed_proposal / undo_last_change):**
   ```python
   def apply_confirmed_proposal(proposal_id, confirmed_by_user_id, pharmacy_id):
       with get_session() as session:
           # 1. Load proposal and verify it's CONFIRMED, not expired, belongs to pharmacy
           proposal = session.get(StockProposal, proposal_id)
           if proposal.status != 'CONFIRMED':
               raise ProposalNotPendingError("Proposal must be CONFIRMED")
           if proposal.pharmacy_id != pharmacy_id:
               raise ProposalPharmacyMismatchError()
           
           # 2. Load inventory row
           inv = session.execute(
               select(Inventory).where(
                   Inventory.pharmacy_id == pharmacy_id,
                   Inventory.medicine_id == get_medicine_id_from_proposal(proposal)
               )
           ).scalar_one_or_none()
           if not inv:
               raise InventoryNotFoundError()
           
           # 3. Create history row (STOCK_UPDATE) with previous/new values
           hist = StockHistory(
               inventory_id=inv.id,
               pharmacy_id=pharmacy_id,
               medicine_id=inv.medicine_id,
               changed_by=confirmed_by_user_id,
               source_proposal_id=proposal_id,
               change_type='STOCK_UPDATE',
               undo_of_history_id=None,
               previous_stock_status=inv.stock_status,
               new_stock_status=proposal.parsed_intent,
               previous_quantity=inv.quantity,
               new_quantity=proposal.parsed_quantity,
               previous_last_confirmed_at=inv.last_confirmed_at,
               change_reason=f"proposal #{proposal_id}"
           )
           session.add(hist)
           
           # 4. Update inventory row
           inv.stock_status = proposal.parsed_intent
           inv.quantity = proposal.parsed_quantity
           inv.last_confirmed_at = datetime.utcnow()
           inv.last_updated_by = confirmed_by_user_id
           inv.last_update_source = 'proposal_apply'
           
           # 5. Update proposal (already CONFIRMED, just set confirmed_by/at if not set)
           if proposal.confirmed_by is None:
               proposal.confirmed_by = confirmed_by_user_id
               proposal.confirmed_at = datetime.utcnow()
           # Commit atomically
   ```

   ```python
   def undo_last_change(history_id, undone_by_user_id, pharmacy_id):
       with get_session() as session:
           # 1. Load target history row
           target = session.get(StockHistory, history_id)
           if not target:
               raise HistoryNotFoundError()
           
           # 2. Check no-undo-of-undo
           if target.change_type == 'UNDO':
               raise CannotUndoUndoError("Cannot undo an undo entry. Apply a new stock update instead.")
           
           # 3. Check single-undo (DB unique constraint will catch race, but check first for UX)
           existing_undo = session.execute(
               select(StockHistory).where(StockHistory.undo_of_history_id == history_id)
           ).scalar_one_or_none()
           if existing_undo:
               raise HistoryAlreadyUndoneError(f"History {history_id} already undone by #{existing_undo.id}")
           
           # 4. Check no newer history for same inventory (use id, not created_at)
           newer = session.execute(
               select(StockHistory.id).where(
                   StockHistory.inventory_id == target.inventory_id,
                   StockHistory.id > target.id
               )
           ).first()
           if newer:
               raise NewerChangeExistsError(f"Cannot undo: newer change #{newer.id} exists for this inventory")
           
           # 5. Verify pharmacy scope
           if target.pharmacy_id != pharmacy_id:
               raise PharmacyScopeMismatchError()
           
           # 6. Verify user linked to pharmacy
           if not is_user_linked_to_pharmacy(undone_by_user_id, pharmacy_id):
               raise UserNotLinkedToPharmacyError()
           
           # 7. Get current inventory state (for history record)
           inv = session.get(Inventory, target.inventory_id)
           
           # 8. Create undo history row
           undo_hist = StockHistory(
               inventory_id=target.inventory_id,
               pharmacy_id=target.pharmacy_id,
               medicine_id=target.medicine_id,
               changed_by=undone_by_user_id,
               source_proposal_id=target.source_proposal_id,
               change_type='UNDO',
               undo_of_history_id=target.id,  # UNIQUE constraint enforces single undo
               previous_stock_status=inv.stock_status,
               new_stock_status=target.previous_stock_status,
               previous_quantity=inv.quantity,
               new_quantity=target.previous_quantity,
               previous_last_confirmed_at=inv.last_confirmed_at,
               change_reason=f"undo of #{target.id}"
           )
           session.add(undo_hist)
           
           # 9. Restore inventory to previous state
           inv.stock_status = target.previous_stock_status
           inv.quantity = target.previous_quantity
           inv.last_confirmed_at = target.previous_last_confirmed_at or datetime.utcnow()
           inv.last_updated_by = undone_by_user_id
           inv.last_update_source = 'undo'
           # Commit atomically
   ```

---

### 9. `substitution_approvals` — Per-request pharmacist approvals for salt-equivalent substitutions

| Column | Type | Nullable | Constraints | Index | Reason |
|--------|------|----------|-------------|-------|--------|
| `id` | INTEGER | NO | PK, AUTOINCREMENT | — | Primary reference |
| `pharmacy_id` | INTEGER | NO | FK → pharmacies.id | **idx_sub_approvals_pharmacy** | Pharmacy where substitution is requested |
| `requested_medicine_id` | INTEGER | NO | FK → medicines.id | — | Out-of-stock brand patient asked for |
| `substitute_medicine_id` | INTEGER | NO | FK → medicines.id | **idx_sub_approvals_substitute** | In-stock equivalent offered |
| `approving_pharmacist_id` | INTEGER | NO | FK → users.id | **idx_sub_approvals_pharmacist** | Pharmacist who approved/rejected |
| `status` | TEXT | NO | CHECK IN ('APPROVED','REJECTED') | **idx_sub_approvals_status** | Outcome only — no "pending" |
| `request_reference` | TEXT | YES | — | — | Optional free-text context (patient name, order ID) |
| `approved_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — | When decision recorded |
| `created_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — | Audit |

**Constraints:**
- `CHECK (requested_medicine_id != substitute_medicine_id)`
- Same salt+strength enforced by application (join on `medicines.composition_key`, `medicines.strength`, `medicines.form`)
- **No lookup function reuses old approvals** — each request is independent; this table is append-only audit

**Indexes:**
- `idx_sub_approvals_pharmacy` — pharmacy substitution audit
- `idx_sub_approvals_pharmacist` — pharmacist activity log
- `idx_sub_approvals_status` — filter by outcome

---

### 10. `sos_broadcasts` — **OPTIONAL EXTENSION**: Patient SOS + chemist responses

| Column | Type | Nullable | Constraints | Index | Reason |
|--------|------|----------|-------------|-------|--------|
| `id` | INTEGER | NO | PK, AUTOINCREMENT | — | Primary reference |
| `patient_user_id` | INTEGER | NO | FK → users.id | **idx_sos_patient** | Who triggered |
| `medicine_id` | INTEGER | NO | FK → medicines.id | — | What they need |
| `latitude` | REAL | YES | — | — | Patient location (optional) |
| `longitude` | REAL | YES | — | — | Patient location (optional) |
| `radius_km` | REAL | NO | DEFAULT 10.0 | — | Search radius |
| `status` | TEXT | NO | CHECK IN ('active','fulfilled','expired','cancelled') DEFAULT 'active' | **idx_sos_status** | Broadcast state |
| `expires_at` | DATETIME | NO | — | **idx_sos_expires** | Auto-expiry |
| `created_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — | Audit |
| `fulfilled_at` | DATETIME | YES | — | — | When resolved |

---

### 11. `sos_responses` — **OPTIONAL EXTENSION**: Chemist responses to SOS

| Column | Type | Nullable | Constraints | Index | Reason |
|--------|------|----------|-------------|-------|--------|
| `id` | INTEGER | NO | PK, AUTOINCREMENT | — | Primary reference |
| `sos_broadcast_id` | INTEGER | NO | FK → sos_broadcasts.id | **idx_sos_resp_broadcast** | Responses for a broadcast |
| `pharmacy_id` | INTEGER | NO | FK → pharmacies.id | — | Responding pharmacy |
| `chemist_user_id` | INTEGER | NO | FK → users.id | — | Responding chemist |
| `can_supply` | INTEGER | NO | CHECK IN (0,1) | — | Yes/no |
| `note` | TEXT | YES | — | — | "Have 10 strips" |
| `responded_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — | When responded |

---

## Relationship Diagram

```
pharmacies (1) ──────< (M) inventory (M) >────── (1) medicines
      │                                              │
      │                                              │
      ▼                                              ▼
user_pharmacies (M) >────── (1) users <──────────────┘
      │
      ▼
stock_proposals (M) ──────> (1) users (chemist)
      │
      │  (on confirm)
      ▼
stock_history (M) ──────> (1) inventory
      │
      │  (undo creates new row)
      ▼
substitution_approvals (M) ─> (1) pharmacies
      │
      ├──> (1) medicines (requested)
      └──> (1) medicines (substitute)

[OPTIONAL]
sos_broadcasts (1) ──────< (M) sos_responses
      │
      ├──> (1) users (patient)
      └──> (1) medicines

medicine_aliases (M) ──────> (1) medicines
```

**Key relationships:**
- `pharmacies` 1:M `inventory` M:1 `medicines`
- `users` M:M `pharmacies` via `user_pharmacies` (with role_at_pharmacy)
- `stock_proposals` M:1 `pharmacies`, M:1 `users` (chemist), M:0..1 `stock_proposals` (renewed_from)
- `stock_history` M:1 `inventory`, M:1 `pharmacies`, M:1 `medicines`, M:1 `users`, M:0..1 `stock_proposals`, M:0..1 `stock_history` (undo_of)
- `substitution_approvals` M:1 `pharmacies`, 2× M:1 `medicines` (requested, substitute), M:1 `users` (pharmacist)
- `medicine_aliases` M:1 `medicines`

---

## Planned DB Operation Functions

### Read Inventory
| Function | Parameters | Returns | Description |
|----------|------------|---------|-------------|
| `get_pharmacies_nearby(lat, lon, radius_km, limit)` | `float, float, float, int` | `List[Pharmacy]` | Haversine distance filter |
| `get_inventory_for_medicine(medicine_id, include_stale)` | `int, bool` | `List[InventoryWithPharmacy]` | Patient search: who has X? |
| `get_pharmacy_inventory(pharmacy_id, include_stale)` | `int, bool` | `List[InventoryWithMedicine]` | Chemist dashboard |
| `get_substitutes_for_medicine(medicine_id)` | `int` | `List[Medicine]` | Salt+strength match from `medicines` |
| `get_stale_inventory(threshold_hours=24)` | `int` | `List[Inventory]` | Cron job: entries not recently confirmed |

### Create/Read Proposals
| Function | Parameters | Returns | Description |
|----------|------------|---------|-------------|
| `create_stock_proposal(pharmacy_id, chemist_user_id, raw_msg, parsed)` | `int, int, str, ParsedProposal` | `StockProposal` | Insert pending proposal |
| `get_pending_proposals(pharmacy_id=None, flagged_only=False)` | `Optional[int], bool` | `List[StockProposal]` | Dashboard queues |
| `get_proposal_by_id(proposal_id)` | `int` | `Optional[StockProposal]` | Detail view |
| `update_proposal_status(proposal_id, status, reviewed_by, note)` | `int, str, int, Optional[str]` | `StockProposal` | Confirm/reject/expire |

### Apply Approved Stock Change
| Function | Parameters | Returns | Description |
|----------|------------|---------|-------------|
| `apply_stock_change(proposal_id, confirmed_by, new_in_stock, new_quantity)` | `int, int, int, Optional[int]` | `Inventory` | Update inventory + write history |
| `create_substitute_proposal(pharmacy_id, original_id, substitute_id, proposed_by)` | `int, int, int, int` | `SubstituteProposal` | Pharmacist confirms later |

### Read History
| Function | Parameters | Returns | Description |
|----------|------------|---------|-------------|
| `get_inventory_history(inventory_id, limit)` | `int, int` | `List[StockHistory]` | Audit trail for one item |
| `get_pharmacy_history(pharmacy_id, limit)` | `int, int` | `List[StockHistory]` | Pharmacy audit log |
| `get_user_history(user_id, limit)` | `int, int` | `List[StockHistory]` | User activity |

### Undo
| Function | Parameters | Returns | Description |
|----------|------------|---------|-------------|
| `undo_last_change(history_id, undone_by)` | `int, int` | `StockHistory` | Create undo entry, restore previous values |
| `can_undo(history_id)` | `int` | `bool` | Check if already undone |

### Optional: SOS
| Function | Parameters | Returns | Description |
|----------|------------|---------|-------------|
| `create_sos_broadcast(patient_id, medicine_id, lat, lon, radius)` | `int, int, float, float, float` | `SOSBroadcast` | Patient triggers SOS |
| `respond_to_sos(broadcast_id, pharmacy_id, chemist_id, can_supply, note)` | `int, int, int, int, str` | `SOSResponse` | Chemist replies |
| `get_active_sos_nearby(lat, lon, radius)` | `float, float, float` | `List[SOSBroadcast]` | Chemist dashboard |

---

## Questions for Member 1

1. **Stock granularity**: Is `in_stock` (boolean) sufficient, or do you need `quantity` (integer count) as a required field? Current design makes quantity optional.

2. **Proposal expiry**: What happens to a proposal after it expires? Auto-reject? Stay pending indefinitely? Current design has `expired` status — who/what triggers expiry?

3. **Undo window**: Is there a time limit on undo (e.g., only last change, or within 5 minutes)? Current design allows undo of any history row not yet undone (with DB-enforced single-undo and no-newer-change rules).

4. **Who can undo**: Only the chemist who made the change? Any chemist at that pharmacy? Admin? Current design: `undone_by` is any user; authorization is your layer.

5. **Substitute confirmation**: Does a confirmed substitute persist (becomes a standing rule for that pharmacy) or is it per-request? Current design: each proposal is independent.

6. **Schedule H handling**: When a Schedule H medicine is proposed, is it auto-flagged for admin review only, or does it require pharmacist confirmation too? Current: `flagged_for_review` covers both low confidence + Schedule H.

7. **SOS scope**: Is the SOS feature in scope for MVP? If yes, do responses need to be routed back to the patient via Telegram?

8. **Multi-pharmacy chemists**: Can one chemist manage inventory for multiple pharmacies? Current `user_pharmacies` supports this.

9. **Proposal parsing fields**: The LLM returns `{salt, strength, in_stock, intent}`. Should we also store `form` (tablet/syrup) and `brand_name` if mentioned? Current design includes optional `parsed_form`.

10. **Quantity in proposals**: Do chemist messages ever include quantities ("Dolo 650 - 5 strips")? If yes, should `stock_proposals` have `parsed_quantity`?

11. **High-confidence auto-apply vs. all-pending**: Are high-confidence AI parses (e.g., confidence ≥ 0.9) applied immediately to inventory (with undo button), while only low-confidence (< 0.9) or Schedule H proposals stay as `pending` in `stock_proposals`? Or is **every** update held as a proposal until explicitly confirmed?
    - **If auto-apply for high-confidence**: `apply_stock_change` is called directly, writing inventory + history in one transaction. The proposal row is created with `status='auto_applied'` (distinct from `'confirmed'` which is human-confirmed) and `reviewed_by=NULL`, `reviewed_at=now`. The chemist gets an Undo button referencing the new history row.
    - **If all-pending**: Every parsed message creates a `stock_proposals` row with `status='pending'`. Member 1's workflow confirms/rejects each. `apply_stock_change` is only called after human confirmation, setting `status='confirmed'` and `reviewed_by=<user_id>`.
    - **Impact on schema**: `stock_proposals.status` now includes `'auto_applied'` to distinguish auto-applied high-confidence updates from human-confirmed ones. The `apply_stock_change` function works identically in both workflows — it receives a `proposal_id` and updates inventory + history.

---

## Alternatives Considered

### 1. Substitution: separate `substitutes` table vs. computed from `medicines`
- **Chosen**: Computed via `generic_name + strength` on `medicines` (with unique constraint)
- **Alternative**: Explicit `substitutes` many-to-many table linking medicine pairs
- **Why chosen**: Single source of truth; adding a new brand automatically makes it substitutable; no sync issues. The unique constraint on `(generic_name, strength, form)` guarantees correctness.

### 2. Inventory: single table vs. separate `stock_levels` + `stock_status`
- **Chosen**: Single `inventory` table with `in_stock` + optional `quantity`
- **Alternative**: Separate `stock_status` (boolean) and `stock_levels` (quantity, batch, expiry)
- **Why chosen**: Hackathon scope — boolean + optional quantity covers 90% of cases. Batch/expiry is over-engineering for 12 pharmacies.

### 3. Proposals: single table with status vs. separate `pending_proposals` + `processed_proposals`
- **Chosen**: Single `stock_proposals` with `status` column
- **Alternative**: Archive confirmed/rejected proposals to a separate table
- **Why chosen**: Simpler queries, full history in one place, status index makes filtering fast. 12 pharmacies × low volume = no performance need for partitioning.

### 4. Soft delete vs. hard delete with audit table
- **Chosen**: `active` boolean on `pharmacies`, `users`, `medicines`
- **Alternative**: Hard delete + rely on `stock_history` for audit
- **Why chosen**: Soft delete is simpler for referential integrity (FKs stay valid), and "show active only" is a one-line `WHERE` clause.

---

## Constraint Validation Check (All Tables)

Re-verified that no constraint wrongly forbids legitimate data:

| Table | Constraint | Validates | Allows Legitimate Duplicates? |
|-------|------------|-----------|-------------------------------|
| `pharmacies` | `UNIQUE (telegram_chat_id)` | One pharmacy per Telegram chat | Yes — different chats, same owner |
| `users` | `UNIQUE (telegram_user_id)` | One user row per Telegram ID | Yes |
| `user_pharmacies` | PK `(user_id, pharmacy_id)` | One role per user-pharmacy pair | Yes — same user at multiple pharmacies |
| `medicines` | `UNIQUE (brand_name, composition_key, strength, form, manufacturer)` | Distinct brand entries | Yes — Dolo 650, Calpol 650, Crocin 650 all allowed (same composition_key) |
| `medicine_aliases` | `UNIQUE (alias)` | One alias → one medicine | Yes — each alias is unique |
| `inventory` | `UNIQUE (pharmacy_id, medicine_id)` | One stock row per pharmacy-medicine | Yes — same medicine at different pharmacies |
| `stock_history` | `UNIQUE (undo_of_history_id)` | One undo per history row | Yes — each history row can be undone once |

**Key checks passed:**
- Multiple brands with identical `composition_key + strength + form` → **allowed** (unique on brand+manufacturer, not composition alone)
- Same chemist at multiple pharmacies → **allowed** (`user_pharmacies` M:M)
- Same alias for different medicines → **forbidden correctly** (one alias = one medicine)
- Undo of undo → **forbidden** (application check + `change_type='undo'` rejected)
- Second undo of same history row → **forbidden** (DB `UNIQUE (undo_of_history_id)`)
- Inventory for same medicine at same pharmacy → **forbidden correctly** (one row per pair)

---

## Summary (10 lines)

1. **Core entities**: `pharmacies`, `users` (4 roles), `medicines` (brand-level with normalized `composition_key`), `inventory` (pharmacy×medicine stock).
2. **Substitution** is a fast indexed query on `medicines(composition_key, strength, form)` — returns ALL brands sharing the same salt(s) (e.g., Dolo 650, Calpol 650, Crocin 650).
3. **Aliases** table maps "dolo650"/"crocin" → `medicines.id` for fuzzy matching.
4. **Stock proposals** capture raw LLM parses with confidence + flags; workflow: pending → confirmed/rejected/expired/auto_applied.
5. **History** is immutable append-only; undo writes a compensating entry (never deletes).
6. **24h staleness** is computed from `last_confirmed_at`, not stored.
7. **Schedule H** flag on `medicines` drives auto-flagging of proposals.
8. **Many-to-many** `user_pharmacies` lets chemists/pharmacists work at multiple locations.
9. **Optional SOS** tables included for broadcast + responses.
10. **All FKs enforced**; indexes target the 5 main query patterns; constraints validated to allow legitimate duplicates.