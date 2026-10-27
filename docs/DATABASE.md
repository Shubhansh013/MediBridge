# MediBridge Database Documentation

## Quick Setup

```bash
# Install dependencies
pip install sqlalchemy pytest

# Initialize database (creates tables + seeds demo data)
python scripts/init_db.py

# Or with custom database URL
MEDIBRIDGE_DB_URL=sqlite:///custom.db python scripts/init_db.py

# Create tables only (no seed)
python scripts/init_db.py --no-seed

# Reset database (destructive, requires confirmation)
python scripts/init_db.py --reset --yes
```

## Database Schema

### Tables

#### 1. `pharmacies` — Verified pharmacy locations
| Column | Type | Nullable | Constraints | Index |
|--------|------|----------|-------------|-------|
| `id` | INTEGER | NO | PK, AUTOINCREMENT | — |
| `name` | TEXT | NO | — | — |
| `locality` | TEXT | NO | — | `idx_pharmacies_locality` |
| `address` | TEXT | NO | — | — |
| `latitude` | REAL | NO | CHECK (-90 to 90) | — |
| `longitude` | REAL | NO | CHECK (-180 to 180) | — |
| `contact_phone` | TEXT | YES | — | — |
| `telegram_chat_id` | INTEGER | YES | UNIQUE | `uq_pharmacies_telegram_chat_id` |
| `verification_state` | TEXT | NO | CHECK ('pending','verified','rejected') DEFAULT 'pending' | `idx_pharmacies_verification` |
| `is_active` | BOOLEAN | NO | CHECK (0,1) DEFAULT 1 | `idx_pharmacies_is_active` |
| `created_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — |
| `updated_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — |

**Relationships:** 1:M `inventory`, 1:M `stock_proposals`, 1:M `stock_history`, 1:M `substitution_approvals`, M:M `users` via `user_pharmacies`

---

#### 2. `users` — Telegram users with roles
| Column | Type | Nullable | Constraints | Index |
|--------|------|----------|-------------|-------|
| `id` | INTEGER | NO | PK, AUTOINCREMENT | — |
| `telegram_user_id` | INTEGER | NO | UNIQUE | `uq_users_telegram_user_id` |
| `display_name` | TEXT | NO | — | — |
| `role` | TEXT | NO | CHECK ('patient','chemist','pharmacist','admin') | `idx_users_role` |
| `is_active` | BOOLEAN | NO | CHECK (0,1) DEFAULT 1 | `idx_users_is_active` |
| `created_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — |
| `updated_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — |

**Relationships:** M:M `pharmacies` via `user_pharmacies`, 1:M `stock_proposals` (as chemist), 1:M `stock_history`, 1:M `substitution_approvals` (as pharmacist)

---

#### 3. `user_pharmacies` — Staff ↔ pharmacy membership
| Column | Type | Nullable | Constraints | Index |
|--------|------|----------|-------------|-------|
| `user_id` | INTEGER | NO | FK → users.id, PK | — |
| `pharmacy_id` | INTEGER | NO | FK → pharmacies.id, PK | `idx_user_pharmacies_pharmacy` |
| `role_at_pharmacy` | TEXT | NO | CHECK ('chemist','pharmacist') DEFAULT 'chemist' | — |
| `created_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — |

**Constraints:** PK (`user_id`, `pharmacy_id`), FK cascade on delete

---

#### 4. `medicines` — Brand-level medicine catalogue
| Column | Type | Nullable | Constraints | Index |
|--------|------|----------|-------------|-------|
| `id` | INTEGER | NO | PK, AUTOINCREMENT | — |
| `brand_name` | TEXT | NO | — | `idx_medicines_brand` |
| `generic_name` | TEXT | NO | — | — |
| `composition_key` | TEXT | NO | — | `idx_medicines_composition` |
| `strength` | TEXT | NO | — | — |
| `form` | TEXT | YES | — | — |
| `is_schedule_h` | BOOLEAN | NO | CHECK (0,1) DEFAULT 0 | `idx_medicines_schedule_h` |
| `manufacturer` | TEXT | YES | — | — |
| `is_active` | BOOLEAN | NO | CHECK (0,1) DEFAULT 1 | `idx_medicines_is_active` |
| `created_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — |
| `updated_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — |

**Unique Constraint:** `UNIQUE (brand_name, composition_key, strength, form, manufacturer)`
**Normalization:** Missing `manufacturer` → empty string `''` for UNIQUE constraint

**Indexes:**
- `idx_medicines_composition` (NON-unique) on `(composition_key, strength, form)` — substitute lookup

---

#### 5. `medicine_aliases` — Fuzzy name matching
| Column | Type | Nullable | Constraints | Index |
|--------|------|----------|-------------|-------|
| `id` | INTEGER | NO | PK, AUTOINCREMENT | — |
| `medicine_id` | INTEGER | NO | FK → medicines.id ON DELETE CASCADE | `idx_medicine_aliases_medicine` |
| `alias` | TEXT | NO | UNIQUE | `uq_medicine_aliases_alias` |
| `alias_type` | TEXT | NO | CHECK ('misspelling','hinglish','short','brand_variant') | — |
| `created_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — |

---

#### 6. `inventory` — Pharmacy × medicine stock
| Column | Type | Nullable | Constraints | Index |
|--------|------|----------|-------------|-------|
| `id` | INTEGER | NO | PK, AUTOINCREMENT | — |
| `pharmacy_id` | INTEGER | NO | FK → pharmacies.id ON DELETE RESTRICT | `idx_inventory_pharmacy` |
| `medicine_id` | INTEGER | NO | FK → medicines.id ON DELETE RESTRICT | `idx_inventory_medicine` |
| `stock_status` | TEXT | NO | CHECK ('IN_STOCK','LOW_STOCK','OUT_OF_STOCK') DEFAULT 'IN_STOCK' | `idx_inventory_stock_status` |
| `quantity` | INTEGER | YES | CHECK (>= 0) | — |
| `last_confirmed_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | `idx_inventory_last_confirmed` |
| `last_updated_by` | INTEGER | YES | FK → users.id ON DELETE SET NULL | — |
| `last_update_source` | TEXT | YES | CHECK ('chemist','proposal_apply','undo','seed') | — |
| `created_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — |
| `updated_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — |

**Unique Constraint:** `UNIQUE (pharmacy_id, medicine_id)`

**Check Constraints:**
- `OUT_OF_STOCK` → `quantity IS NULL OR quantity = 0`
- `IN_STOCK`/`LOW_STOCK` → `quantity IS NULL OR quantity > 0`

**Computed:** `is_stale = (now - last_confirmed_at) > 24h`

---

#### 7. `stock_proposals` — AI-parsed updates awaiting confirmation
| Column | Type | Nullable | Constraints | Index |
|--------|------|----------|-------------|-------|
| `id` | INTEGER | NO | PK, AUTOINCREMENT | — |
| `pharmacy_id` | INTEGER | NO | FK → pharmacies.id ON DELETE RESTRICT | `idx_proposals_pharmacy` |
| `chemist_user_id` | INTEGER | NO | FK → users.id ON DELETE RESTRICT | `idx_proposals_chemist` |
| `raw_message` | TEXT | NO | — | — |
| `brand_name` | TEXT | YES | — | — |
| `parsed_salt` | TEXT | NO | — | `idx_proposals_salt` |
| `parsed_strength` | TEXT | NO | — | — |
| `parsed_form` | TEXT | YES | — | — |
| `parsed_quantity` | INTEGER | YES | CHECK (>= 0) | — |
| `parsed_intent` | TEXT | NO | CHECK ('IN_STOCK','LOW_STOCK','OUT_OF_STOCK') | — |
| `confidence` | REAL | NO | CHECK (0.0 to 1.0) | — |
| `status` | TEXT | NO | CHECK ('PENDING','CONFIRMED','REJECTED','EXPIRED') DEFAULT 'PENDING' | `idx_proposals_status` |
| `flagged_for_review` | BOOLEAN | NO | CHECK (0,1) DEFAULT 0 | `idx_proposals_flagged` |
| `expires_at` | DATETIME | NO | — | `idx_proposals_expires` |
| `renewed_from_id` | INTEGER | YES | FK → stock_proposals.id ON DELETE SET NULL | — |
| `confirmed_by` | INTEGER | YES | FK → users.id ON DELETE SET NULL | — |
| `confirmed_at` | DATETIME | YES | — | — |
| `review_note` | TEXT | YES | — | — |
| `source_message_ref` | TEXT | YES | — | `idx_proposals_source_message_ref` |
| `created_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | `idx_proposals_created` |
| `updated_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — |

**Expiry:** `expires_at = created_at + 24h`; cron marks PENDING → EXPIRED; EXPIRED never applies; renewal = new proposal with `renewed_from_id`

---

#### 8. `stock_history` — Immutable audit log
| Column | Type | Nullable | Constraints | Index |
|--------|------|----------|-------------|-------|
| `id` | INTEGER | NO | PK, AUTOINCREMENT | — |
| `inventory_id` | INTEGER | NO | FK → inventory.id ON DELETE RESTRICT | `idx_history_inventory` |
| `pharmacy_id` | INTEGER | NO | FK → pharmacies.id ON DELETE RESTRICT | `idx_history_pharmacy` |
| `medicine_id` | INTEGER | NO | FK → medicines.id ON DELETE RESTRICT | — |
| `changed_by` | INTEGER | NO | FK → users.id ON DELETE RESTRICT | `idx_history_user` |
| `source_proposal_id` | INTEGER | YES | FK → stock_proposals.id ON DELETE SET NULL | — |
| `change_type` | TEXT | NO | CHECK ('STOCK_UPDATE','UNDO') | — |
| `undo_of_history_id` | INTEGER | YES | FK → stock_history.id ON DELETE RESTRICT, UNIQUE | `uq_history_undo_of` |
| `previous_stock_status` | TEXT | YES | CHECK ('IN_STOCK','LOW_STOCK','OUT_OF_STOCK') | — |
| `new_stock_status` | TEXT | NO | CHECK ('IN_STOCK','LOW_STOCK','OUT_OF_STOCK') | — |
| `previous_quantity` | INTEGER | YES | CHECK (>= 0) | — |
| `new_quantity` | INTEGER | YES | CHECK (>= 0) | — |
| `previous_last_confirmed_at` | DATETIME | YES | — | — |
| `change_reason` | TEXT | YES | — | — |
| `created_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | `idx_history_created` |

**Constraints:** No CASCADE on FKs; `UNIQUE (undo_of_history_id)` = one undo per history row

**Undo Rules:**
1. Target must be `STOCK_UPDATE` (not `UNDO`)
2. `undo_of_history_id` must be NULL (not already undone)
3. No newer history row for same `inventory_id` (`id > target.id`)
4. Pharmacy scope must match

---

#### 9. `substitution_approvals` — Pharmacist approvals for substitutes
| Column | Type | Nullable | Constraints | Index |
|--------|------|----------|-------------|-------|
| `id` | INTEGER | NO | PK, AUTOINCREMENT | — |
| `pharmacy_id` | INTEGER | NO | FK → pharmacies.id ON DELETE RESTRICT | `idx_sub_approvals_pharmacy` |
| `requested_medicine_id` | INTEGER | NO | FK → medicines.id ON DELETE RESTRICT | — |
| `substitute_medicine_id` | INTEGER | NO | FK → medicines.id ON DELETE RESTRICT | `idx_sub_approvals_substitute` |
| `approving_pharmacist_id` | INTEGER | NO | FK → users.id ON DELETE RESTRICT | `idx_sub_approvals_pharmacist` |
| `status` | TEXT | NO | CHECK ('APPROVED','REJECTED') | `idx_sub_approvals_status` |
| `request_reference` | TEXT | YES | — | — |
| `approved_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — |
| `created_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — |

**Constraints:** `CHECK (requested_medicine_id != substitute_medicine_id)`; same composition/strength/form enforced in application; **append-only** — no reuse of old approvals

---

#### 10. `sos_broadcasts` — OPTIONAL Patient SOS (optional extension)
| Column | Type | Nullable | Constraints | Index |
|--------|------|----------|-------------|-------|
| `id` | INTEGER | NO | PK, AUTOINCREMENT | — |
| `patient_user_id` | INTEGER | NO | FK → users.id ON DELETE RESTRICT | `idx_sos_patient` |
| `medicine_id` | INTEGER | NO | FK → medicines.id ON DELETE RESTRICT | — |
| `latitude` | REAL | YES | — | — |
| `longitude` | REAL | YES | — | — |
| `radius_km` | REAL | NO | DEFAULT 10.0 | — |
| `status` | TEXT | NO | CHECK ('active','fulfilled','expired','cancelled') DEFAULT 'active' | `idx_sos_status` |
| `expires_at` | DATETIME | NO | — | `idx_sos_expires` |
| `fulfilled_at` | DATETIME | YES | — | — |
| `created_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — |

---

#### 11. `sos_responses` — OPTIONAL Chemist responses (optional extension)
| Column | Type | Nullable | Constraints | Index |
|--------|------|----------|-------------|-------|
| `id` | INTEGER | NO | PK, AUTOINCREMENT | — |
| `sos_broadcast_id` | INTEGER | NO | FK → sos_broadcasts.id ON DELETE CASCADE | `idx_sos_resp_broadcast` |
| `pharmacy_id` | INTEGER | NO | FK → pharmacies.id ON DELETE RESTRICT | — |
| `chemist_user_id` | INTEGER | NO | FK → users.id ON DELETE RESTRICT | — |
| `can_supply` | BOOLEAN | NO | CHECK (0,1) | — |
| `note` | TEXT | YES | — | — |
| `responded_at` | DATETIME | NO | DEFAULT CURRENT_TIMESTAMP | — |

---

### Enums

| Enum | Values | Used In |
|------|--------|---------|
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

## Public Operation Signatures

All functions take `session: Session` as first parameter. Operations **flush but never commit** — caller owns the transaction.

### Inventory Reads
```python
get_pharmacies_nearby(session, lat: float, lon: float, radius_km: float, limit: int) -> List[Pharmacy]
get_inventory_for_medicine(session, pharmacy_id: int, medicine_id: int, include_stale: bool, include_inactive: bool = False) -> Optional[Inventory]
get_pharmacy_inventory(session, pharmacy_id: int, include_stale: bool, status_filter: Optional[StockStatus], include_inactive: bool = False) -> List[Inventory]
get_substitutes_for_medicine(session, medicine_id: int, pharmacy_id: Optional[int], include_inactive: bool = False) -> List[Medicine]
get_stale_inventory(session, pharmacy_id: Optional[int], threshold_hours: Optional[int] = None, include_inactive: bool = False) -> List[Inventory]
```

### Medicine Lookup
```python
find_medicine_by_alias(session, alias: str, include_inactive: bool = False) -> Optional[Medicine]
search_medicines(session, query: str, limit: int, include_inactive: bool = False) -> List[Medicine]
get_medicine_by_id(session, medicine_id: int, include_inactive: bool = False) -> Optional[Medicine]
get_latest_history_entry(session, inventory_id: int, pharmacy_id: int) -> Optional[StockHistory]
```

### Proposals
```python
create_stock_proposal(session, pharmacy_id: int, chemist_user_id: int, raw_message: str, parsed: dict) -> StockProposal
get_proposal_by_id(session, proposal_id: int) -> Optional[StockProposal]
list_pending_proposals(session, pharmacy_id: Optional[int], flagged_only: bool, limit: int) -> List[StockProposal]
expire_overdue_proposals(session, cutoff: datetime) -> int
renew_proposal(session, expired_proposal_id: int, chemist_user_id: int, raw_message: str, parsed: dict) -> StockProposal
```

### Apply & Undo
```python
apply_confirmed_proposal(session, proposal_id: int, confirmed_by_user_id: int, pharmacy_id: int, current_time: Optional[datetime] = None) -> Inventory
undo_last_change(session, history_id: int, undone_by_user_id: int, pharmacy_id: int) -> StockHistory
can_undo(session, history_id: int) -> bool
```

### History Reads
```python
get_inventory_history(session, inventory_id: int, limit: int) -> List[StockHistory]
get_pharmacy_history(session, pharmacy_id: int, limit: int) -> List[StockHistory]
get_user_history(session, user_id: int, limit: int) -> List[StockHistory]
```

### Substitution Approvals
```python
record_substitution_approval(session, pharmacy_id: int, requested_medicine_id: int, substitute_medicine_id: int, approving_pharmacist_id: int, status: SubstitutionStatus, request_reference: Optional[str] = None) -> SubstitutionApproval
get_substitution_approvals(session, pharmacy_id: int, requested_medicine_id: Optional[int] = None) -> List[SubstitutionApproval]
```

### Membership Reads
```python
is_user_linked_to_pharmacy(session, user_id: int, pharmacy_id: int) -> bool
get_user_pharmacies(session, user_id: int, include_inactive: bool = False) -> List[Pharmacy]
get_pharmacy_staff(session, pharmacy_id: int, role: Optional[StaffRoleAtPharmacy], include_inactive: bool = False) -> List[User]
```

### Exceptions
```python
ProposalNotPendingError
ProposalExpiredError
ProposalPharmacyMismatchError
InventoryNotFoundError
HistoryAlreadyUndoneError
NewerChangeExistsError
CannotUndoUndoError
StockStatusQuantityMismatchError
PharmacyScopeMismatchError
```

---

## Transaction Example

```python
from medibridge_db.database import get_session
from medibridge_db.operations import create_stock_proposal, apply_confirmed_proposal, undo_last_change, get_latest_history_entry

# Service layer (Member 1) owns the transaction
with get_session() as session:
    # 1. Chemist sends message → create proposal
    proposal = create_stock_proposal(
        session,
        pharmacy_id=5,
        chemist_user_id=42,
        raw_message="Dolo 650 khatam",
        parsed={
            "brand_name": "Dolo 650",
            "parsed_salt": "paracetamol",
            "parsed_strength": "650mg",
            "parsed_form": "tablet",
            "parsed_quantity": 0,
            "parsed_intent": StockStatus.out_of_stock,
            "confidence": 0.92,
            "source_message_ref": "tg_msg_123",
        }
    )

    # 2. Member 1 workflow confirms → set status
    proposal.status = ProposalStatus.confirmed

    # 3. Apply confirmed proposal (single SAVEPOINT transaction)
    inventory = apply_confirmed_proposal(
        session,
        proposal_id=proposal.id,
        confirmed_by_user_id=42,
        pharmacy_id=5,
    )

    # 4. Later: chemist taps Undo → get latest history ID
    latest_hist = get_latest_history_entry(
        session,
        inventory_id=inventory.id,
        pharmacy_id=5,
    )

    # 5. Undo (single SAVEPOINT transaction)
    undo_row = undo_last_change(
        session,
        history_id=latest_hist.id,
        undone_by_user_id=42,
        pharmacy_id=5,
    )
```

---

## Propose → Confirm → Apply → Undo Lifecycle

```
1. PROPOSE (chemist)
   ┌─────────────────────────────────────┐
   │ stock_proposals                     │
   │   status=PENDING                    │
   │   expires_at=now+24h                │
   │   flagged_for_review=0/1            │
   └─────────────────────────────────────┘
                    ↓ (Member 1 confirms)

2. CONFIRM (service layer)
   ┌─────────────────────────────────────┐
   │ stock_proposals                     │
   │   status=CONFIRMED                  │
   │   confirmed_by=<chemist_id>         │
   │   confirmed_at=now                  │
   └─────────────────────────────────────┘
                    ↓ (apply_confirmed_proposal)

3. APPLY (single SAVEPOINT)
   ┌─────────────────────────────────────┐
   │ inventory                           │
   │   stock_status=OUT_OF_STOCK         │
   │   quantity=0                        │
   │   last_confirmed_at=now             │
   │   last_update_source='proposal_apply'│
   │                                     │
   │ stock_history (STOCK_UPDATE)        │
   │   previous_stock_status=IN_STOCK    │
   │   new_stock_status=OUT_OF_STOCK     │
   │   undo_of_history_id=NULL           │
   └─────────────────────────────────────┘
                    ↓ (chemist taps Undo)

4. UNDO (single SAVEPOINT)
   ┌─────────────────────────────────────┐
   │ inventory (restored)                │
   │   stock_status=IN_STOCK             │
   │   quantity=50                       │
   │   last_confirmed_at=original        │
   │   last_update_source='undo'         │
   │                                     │
   │ stock_history (UNDO)                │
   │   change_type=UNDO                  │
   │   undo_of_history_id=<original_id>  │
   │   new_stock_status=IN_STOCK         │
   │   previous_stock_status=OUT_OF_STOCK│
   └─────────────────────────────────────┘
```

**Result:** Both history rows remain. Original `undo_of_history_id=NULL`, undo row points to original.

---

## DB Layer vs Service Layer Responsibility

| Responsibility | DB Layer (Member 2) | Service Layer (Member 1) |
|----------------|---------------------|--------------------------|
| Authorization | ❌ Never | ✅ `is_user_linked_to_pharmacy()` before mutating |
| Confirmation validity | ❌ Only checks `status=CONFIRMED` | ✅ Verifies chemist actually authorized |
| Low-stock threshold | ❌ Only enforces consistency | ✅ Decides quantity → status mapping |
| Proposal parsing | ❌ Stores what given | ✅ LLM parsing, salt/strength extraction |
| Substitution correctness | ✅ Checks composition_key/strength/form match | ✅ Verifies before calling `record_substitution_approval` |
| Flagging logic | ❌ Stores `flagged_for_review` | ✅ Sets based on confidence < 0.8 OR `is_schedule_h` |
| Integrity (scope, state, consistency, expiry) | ✅ All enforced | ❌ |
| Transaction ownership | ✅ SAVEPOINT inside caller's transaction | ✅ Owns outer transaction (commit/rollback) |

---

## Timestamp Rules

- All `DATETIME` columns use **timezone-aware UTC** (`UTCDateTime` type)
- `utc_now()` always returns timezone-aware `datetime.now(timezone.utc)`
- Naive datetimes **rejected on write** (`ValueError`)
- SQLite stores naive UTC; on read, assumed UTC and made aware
- `expires_at = created_at + 24h` set on proposal insert
- Staleness: `datetime('now') - last_confirmed_at > 24h` (configurable via `MEDIBRIDGE_STALE_THRESHOLD_HOURS`)

---

## Known Limitations

1. **SQLite Haversine**: `get_pharmacies_nearby` uses Python Haversine with bounding-box pre-filter (no native trig in SQLite). Adequate for ~50km radius / ~12 pharmacies.

2. **Proposal medicine resolution**: `apply_confirmed_proposal` matches medicine by `parsed_salt` + `parsed_strength` + `parsed_form` (+ `brand_name` if provided). Raises `InventoryNotFoundError` if ambiguous or missing. Service layer should resolve brand before confirm.

3. **No inventory auto-create**: `apply_confirmed_proposal` raises `InventoryNotFoundError` if no inventory row exists. First stock update requires pre-seeded inventory.

4. **Soft delete visibility**: `is_active=0` rows remain in history/audit tables. Default queries filter `is_active=1`.

5. **One Telegram chat per pharmacy**: `telegram_chat_id` is UNIQUE on `pharmacies`.

6. **SOS tables optional**: `sos_broadcasts`/`sos_responses` included but no operations implemented.

7. **Composition normalization**: Only `normalize_composition()` from `medibridge_db.normalize` is the source of truth.

8. **Undo not cascaded**: Undo restores inventory to exact previous values; no "undo of undo" allowed. To change stock after undo, apply new proposal.

9. **Substitution approvals append-only**: Each request independent; no lookup function reuses old approvals.
