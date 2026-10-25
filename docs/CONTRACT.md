# MediBridge Database Contract

This document defines the database interface for the MediBridge Telegram assistant. It is the single source of truth for Member 1 (backend workflow), Member 3 (Telegram bot), and Member 4 (Streamlit dashboard).

All DB functions are **pure data access** — no authorization, no business rules. Member 1's workflow layer calls these functions after it has made authorization/confirmation decisions.

---

## A. Tables

| Table | Purpose | Key Fields | Relationships |
|-------|---------|------------|---------------|
| `pharmacies` | Verified pharmacy locations | `id`, `name`, `locality`, `latitude`, `longitude`, `telegram_chat_id`, `verification_state`, `active` | 1:M `inventory`, 1:M `stock_proposals`, 1:M `stock_history`, 1:M `substitution_approvals`, M:M `users` via `user_pharmacies` |
| `users` | Telegram users with roles | `id`, `telegram_user_id`, `display_name`, `role` (patient/chemist/pharmacist/admin), `active` | M:M `pharmacies` via `user_pharmacies`, 1:M `stock_proposals` (chemist), 1:M `stock_history`, 1:M `substitution_approvals` (pharmacist) |
| `user_pharmacies` | Staff ↔ pharmacy membership | `user_id`, `pharmacy_id`, `role_at_pharmacy` (chemist/pharmacist) | M:1 `users`, M:1 `pharmacies` |
| `medicines` | Brand-level medicine catalogue | `id`, `brand_name`, `generic_name`, `composition_key`, `strength`, `form`, `is_schedule_h`, `manufacturer`, `active` | 1:M `inventory`, 1:M `stock_proposals` (parsed_salt), 1:M `medicine_aliases`, 2× M:1 in `substitution_approvals` |
| `medicine_aliases` | Fuzzy name matching | `id`, `medicine_id`, `alias`, `alias_type` | M:1 `medicines` |
| `inventory` | Pharmacy × medicine stock | `id`, `pharmacy_id`, `medicine_id`, `stock_status` (IN_STOCK/LOW_STOCK/OUT_OF_STOCK), `quantity`, `last_confirmed_at`, `last_updated_by`, `last_update_source` | M:1 `pharmacies`, M:1 `medicines`, 1:M `stock_history` |
| `stock_proposals` | AI-parsed updates awaiting chemist confirmation | `id`, `pharmacy_id`, `chemist_user_id`, `raw_message`, `brand_name`, `parsed_salt`, `parsed_strength`, `parsed_form`, `parsed_quantity`, `parsed_intent`, `confidence`, `status`, `flagged_for_review`, `expires_at`, `renewed_from_id`, `confirmed_by`, `confirmed_at`, `review_note` | M:1 `pharmacies`, M:1 `users` (chemist), M:0..1 `stock_proposals` (renewed_from), 1:M `stock_history` |
| `stock_history` | Immutable audit log of applied changes + undos | `id`, `inventory_id`, `pharmacy_id`, `medicine_id`, `changed_by`, `source_proposal_id`, `change_type` (STOCK_UPDATE/UNDO), `undo_of_history_id`, `previous_stock_status`, `new_stock_status`, `previous_quantity`, `new_quantity`, `previous_last_confirmed_at`, `change_reason`, `created_at` | M:1 `inventory`, M:1 `pharmacies`, M:1 `medicines`, M:1 `users`, M:0..1 `stock_proposals`, M:0..1 self (undo_of) |
| `substitution_approvals` | Per-request pharmacist approval for substitutes | `id`, `pharmacy_id`, `requested_medicine_id`, `substitute_medicine_id`, `approving_pharmacist_id`, `status` (APPROVED/REJECTED), `request_reference`, `approved_at` | M:1 `pharmacies`, 2× M:1 `medicines`, M:1 `users` (pharmacist) |
| `sos_broadcasts` | **OPTIONAL** Patient SOS | `id`, `patient_user_id`, `medicine_id`, `latitude`, `longitude`, `radius_km`, `status`, `expires_at`, `fulfilled_at` | M:1 `users` (patient), M:1 `medicines`, 1:M `sos_responses` |
| `sos_responses` | **OPTIONAL** Chemist SOS replies | `id`, `sos_broadcast_id`, `pharmacy_id`, `chemist_user_id`, `can_supply`, `note`, `responded_at` | M:1 `sos_broadcasts`, M:1 `pharmacies`, M:1 `users` |

---

## B. Enums

All enums are stored as `TEXT` with `CHECK` constraints.

| Enum | Values | Table/Column |
|------|--------|--------------|
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

## C. Function Signatures

All functions raise named exception classes on integrity violations. The service layer (Member 1) handles authorization; these functions only enforce **data integrity**.

### Exception Classes
```python
class ProposalNotPendingError(Exception): pass
class ProposalExpiredError(Exception): pass
class ProposalPharmacyMismatchError(Exception): pass
class InventoryNotFoundError(Exception): pass
class HistoryAlreadyUndoneError(Exception): pass
class NewerChangeExistsError(Exception): pass
class CannotUndoUndoError(Exception): pass
class StockStatusQuantityMismatchError(Exception): pass
class PharmacyScopeMismatchError(Exception): pass
class UserNotLinkedToPharmacyError(Exception): pass
```

---

### Inventory Reads

| Function | Parameters | Returns | Errors | Behavior |
|----------|------------|---------|--------|----------|
| `get_pharmacies_nearby` | `lat: float, lon: float, radius_km: float, limit: int` | `List[Pharmacy]` | — | Haversine filter; only `active=1`, `verification_state='verified'` |
| `get_inventory_for_medicine` | `pharmacy_id: int, medicine_id: int, include_stale: bool` | `Optional[Inventory]` | — | Single row; if `include_stale=False`, filters `is_stale=0` |
| `get_pharmacy_inventory` | `pharmacy_id: int, include_stale: bool, status_filter: Optional[StockStatus]` | `List[InventoryWithMedicine]` | — | All inventory rows for a pharmacy; optional status filter |
| `get_substitutes_for_medicine` | `medicine_id: int, pharmacy_id: Optional[int]` | `List[Medicine]` | — | Finds medicines with same `composition_key`, `strength`, `form`; if `pharmacy_id` given, only those with `stock_status != OUT_OF_STOCK` at that pharmacy |
| `get_stale_inventory` | `pharmacy_id: Optional[int], threshold_hours: int = 24` | `List[Inventory]` | — | Cron job: entries where `datetime('now') - last_confirmed_at > threshold_hours` |

---

### Medicine & Substitute Lookup

| Function | Parameters | Returns | Errors | Behavior |
|----------|------------|---------|--------|----------|
| `find_medicine_by_alias` | `alias: str` | `Optional[Medicine]` | — | Looks up `medicine_aliases.alias` → `medicines` |
| `search_medicines` | `query: str, limit: int` | `List[Medicine]` | — | ILIKE on `brand_name` + `generic_name` + aliases |
| `get_medicine_by_id` | `medicine_id: int` | `Optional[Medicine]` | — | Direct PK lookup |

---

### Proposals

| Function | Parameters | Returns | Errors | Behavior |
|----------|------------|---------|--------|----------|
| `create_stock_proposal` | `pharmacy_id: int, chemist_user_id: int, raw_message: str, parsed: ParsedProposalInput` | `StockProposal` | `UserNotLinkedToPharmacyError` | Inserts PENDING proposal; sets `expires_at = created_at + 24h`; verifies `chemist_user_id` linked to `pharmacy_id` |
| `get_proposal_by_id` | `proposal_id: int` | `Optional[StockProposal]` | — | PK lookup |
| `list_pending_proposals` | `pharmacy_id: Optional[int], flagged_only: bool, limit: int` | `List[StockProposal]` | — | Filters `status=PENDING` AND `datetime('now') < expires_at`; optional pharmacy filter |
| `expire_overdue_proposals` | `cutoff: datetime` | `int` (count updated) | — | Updates `status=EXPIRED` where `status=PENDING` AND `expires_at < cutoff`; returns count |
| `renew_proposal` | `expired_proposal_id: int, chemist_user_id: int, raw_message: str, parsed: ParsedProposalInput` | `StockProposal` | `ProposalNotPendingError` (if not EXPIRED), `UserNotLinkedToPharmacyError` | Creates NEW proposal with `renewed_from_id=expired_proposal_id`; copies pharmacy, sets new `expires_at` |

---

### Apply Confirmed Proposal

| Function | Parameters | Returns | Errors | Behavior |
|----------|------------|---------|--------|----------|
| `apply_confirmed_proposal` | `proposal_id: int, confirmed_by_user_id: int, pharmacy_id: int` | `Inventory` | `ProposalNotPendingError` (if not CONFIRMED), `ProposalExpiredError`, `ProposalPharmacyMismatchError`, `InventoryNotFoundError`, `StockStatusQuantityMismatchError`, `UserNotLinkedToPharmacyError` | **Single transaction**: 1) Verify proposal is CONFIRMED, not expired, belongs to `pharmacy_id`, `confirmed_by_user_id` linked to pharmacy. 2) Create `stock_history` row (`STOCK_UPDATE`) with previous/new values. 3) Update `inventory` row (stock_status, quantity, last_confirmed_at, last_updated_by, last_update_source='proposal_apply'). 4) Update proposal `status=CONFIRMED` (already), `confirmed_by`, `confirmed_at`. **Called ONLY by Member 1's approved workflow.** |

---

### History Reads

| Function | Parameters | Returns | Errors | Behavior |
|----------|------------|---------|--------|----------|
| `get_inventory_history` | `inventory_id: int, limit: int` | `List[StockHistory]` | — | Chronological (oldest first) |
| `get_pharmacy_history` | `pharmacy_id: int, limit: int` | `List[StockHistory]` | — | All history for a pharmacy |
| `get_user_history` | `user_id: int, limit: int` | `List[StockHistory]` | — | All changes made by a user |

---

### Undo

| Function | Parameters | Returns | Errors | Behavior |
|----------|------------|---------|--------|----------|
| `undo_last_change` | `history_id: int, undone_by_user_id: int, pharmacy_id: int` | `StockHistory` (the undo row) | `HistoryAlreadyUndoneError`, `NewerChangeExistsError`, `CannotUndoUndoError`, `PharmacyScopeMismatchError`, `UserNotLinkedToPharmacyError` | **Single transaction**: 1) Load target history row. 2) Verify `change_type=STOCK_UPDATE` (reject UNDO). 3) Verify `undo_of_history_id` is NULL (not already undone). 4) Verify no newer history row exists for same `inventory_id` (`id > target.id`). 5) Verify `pharmacy_id` matches history row. 6) Verify `undone_by_user_id` linked to pharmacy. 7) Create undo history row (`UNDO`, `undo_of_history_id=target.id`) with previous=current inventory, new=target.previous. 8) Restore inventory to target.previous values. **Called ONLY by Member 1's approved workflow.** |
| `can_undo` | `history_id: int` | `bool` | — | Checks rules 2-4 above without modifying |

---

### Substitution Approvals

| Function | Parameters | Returns | Errors | Behavior |
|----------|------------|---------|--------|----------|
| `record_substitution_approval` | `pharmacy_id: int, requested_medicine_id: int, substitute_medicine_id: int, approving_pharmacist_id: int, status: SubstitutionStatus, request_reference: Optional[str]` | `SubstitutionApproval` | `PharmacyScopeMismatchError`, `UserNotLinkedToPharmacyError` | Inserts approval record; verifies both medicines have same `composition_key`, `strength`, `form`; verifies pharmacist linked to pharmacy. **No lookup function reuses old approvals.** |
| `get_substitution_approvals` | `pharmacy_id: int, requested_medicine_id: Optional[int]` | `List[SubstitutionApproval]` | — | Audit log for a pharmacy |

---

### Pharmacy Membership Reads (READ-ONLY, for service layer authorization)

| Function | Parameters | Returns | Errors | Behavior |
|----------|------------|---------|--------|----------|
| `is_user_linked_to_pharmacy` | `user_id: int, pharmacy_id: int` | `bool` | — | True if `user_pharmacies` row exists with `active=1` on both ends |
| `get_user_pharmacies` | `user_id: int` | `List[Pharmacy]` | — | All pharmacies a user is linked to (active only) |
| `get_pharmacy_staff` | `pharmacy_id: int, role: Optional[StaffRoleAtPharmacy]` | `List[User]` | — | All users linked to a pharmacy, optional role filter |

---

## D. Representation Details

### Pending Proposals
- Created by `create_stock_proposal` with `status=PENDING`, `expires_at = created_at + 24h`
- `flagged_for_review=1` if `confidence < 0.8` OR `is_schedule_h=1` on matched medicine
- Member 1's workflow confirms/rejects: `status → CONFIRMED/REJECTED`, sets `confirmed_by`, `confirmed_at`
- Cron job (`expire_overdue_proposals`) marks `PENDING → EXPIRED` when `now > expires_at`
- **EXPIRED proposals are dead** — renewal = new proposal with `renewed_from_id`

### Expiry
- `expires_at` is set once on insert, never changed
- `expire_overdue_proposals(cutoff=now)` is idempotent, safe to run repeatedly

### Audit History
- Every applied change writes **one** `stock_history` row (`STOCK_UPDATE`)
- Every undo writes **one** `stock_history` row (`UNDO`) with `undo_of_history_id` pointing to the original
- History rows are **never deleted or modified**
- `previous_*` / `new_*` capture full state delta for reconstruction

### Undo
- Only the **latest** change for an inventory row can be undone (check: no history row with `id > target.id`)
- An undo entry (`change_type=UNDO`) **cannot** be undone
- To change stock after undo: apply a new proposal (not undo-of-undo)
- Both original and undo rows remain in history

### Pharmacy Membership
- `user_pharmacies` is the source of truth for "user works at pharmacy"
- Every mutating function takes `pharmacy_id` and verifies the acting user is linked to it
- This is a **data-integrity scope check**, not an authorization decision
- Member 1's service layer calls `is_user_linked_to_pharmacy` before invoking mutating functions

### Pharmacist Approval (Substitutions)
- `substitution_approvals` is **append-only**; each request is independent
- `status` is `APPROVED` or `REJECTED` — no "pending" state in DB
- No function exists to "check if already approved" — each request is evaluated fresh
- `request_reference` stores free-text context (patient name, order ID)

---

## E. Step-by-Step Example: Propose → Confirm & Apply → Undo

**Scenario**: Chemist at Pharmacy #5 sends "Dolo 650 khatam" (out of stock). Pharmacist confirms. Later, chemist undoes it.

### 1. Propose
```python
# Chemist sends message
proposal = create_stock_proposal(
    pharmacy_id=5,
    chemist_user_id=42,
    raw_message="Dolo 650 khatam",
    parsed=ParsedProposalInput(
        brand_name="Dolo 650",
        parsed_salt="paracetamol",
        parsed_strength="650mg",
        parsed_form="tablet",
        parsed_quantity=None,
        parsed_intent=StockStatus.OUT_OF_STOCK,
        confidence=0.92
    )
)
# DB writes:
# stock_proposals: id=101, pharmacy_id=5, chemist_user_id=42, status=PENDING, expires_at=now+24h, flagged_for_review=0
```

### 2. Confirm & Apply (Member 1 workflow)
```python
# Member 1's workflow verifies chemist is authorized, then:
inventory = apply_confirmed_proposal(
    proposal_id=101,
    confirmed_by_user_id=42,  # same chemist confirms
    pharmacy_id=5
)
# DB writes (single transaction):
# stock_history: id=201, inventory_id=7, pharmacy_id=5, medicine_id=12,
#                changed_by=42, source_proposal_id=101, change_type=STOCK_UPDATE,
#                undo_of_history_id=NULL,
#                previous_stock_status=IN_STOCK, new_stock_status=OUT_OF_STOCK,
#                previous_quantity=50, new_quantity=0,
#                previous_last_confirmed_at=2026-01-15 10:00:00,
#                change_reason="proposal #101"
# inventory (id=7): stock_status=OUT_OF_STOCK, quantity=0, last_confirmed_at=now, last_updated_by=42, last_update_source='proposal_apply'
# stock_proposals (id=101): status=CONFIRMED, confirmed_by=42, confirmed_at=now
```

### 3. Undo (Chemist taps Undo button)
```python
# Member 1 workflow verifies chemist can undo, then:
undo_row = undo_last_change(
    history_id=201,
    undone_by_user_id=42,
    pharmacy_id=5
)
# DB writes (single transaction):
# stock_history: id=202, inventory_id=7, pharmacy_id=5, medicine_id=12,
#                changed_by=42, source_proposal_id=101, change_type=UNDO,
#                undo_of_history_id=201,
#                previous_stock_status=OUT_OF_STOCK, new_stock_status=IN_STOCK,
#                previous_quantity=0, new_quantity=50,
#                previous_last_confirmed_at=2026-01-15 10:00:00,
#                change_reason="undo of #201"
# inventory (id=7): stock_status=IN_STOCK, quantity=50, last_confirmed_at=2026-01-15 10:00:00, last_updated_by=42, last_update_source='undo'
```

**Result**: History now has rows 201 (STOCK_UPDATE) and 202 (UNDO). Inventory restored to IN_STOCK/50. Proposal 101 stays CONFIRMED.

---

## F. Assumptions About the Service Layer (Member 1)

### What DB Functions ASSUME the service layer has ALREADY checked:
1. **Authorization**: That the acting user is allowed to perform the action (e.g., only chemists can confirm proposals for their pharmacy; only pharmacists can record substitution approvals).
2. **Confirmation validity**: That a proposal marked `CONFIRMED` was actually confirmed by an authorized chemist — DB only checks `status=CONFIRMED`.
3. **Low-stock threshold**: The service layer decides what quantity constitutes `LOW_STOCK` vs `IN_STOCK`. DB only enforces: `IN_STOCK/LOW_STOCK → quantity > 0 or NULL`; `OUT_OF_STOCK → quantity = 0 or NULL`.
4. **Proposal parsing correctness**: The LLM parsing (salt, strength, intent) is trusted; DB stores what it's given.
5. **Substitution correctness**: The service layer verifies the requested and substitute medicines share the same `composition_key`, `strength`, `form` before calling `record_substitution_approval`.

### What DB Functions DO Enforce (Integrity Only):
1. **Proposal state**: `apply_confirmed_proposal` rejects if `status != CONFIRMED` or `datetime('now') > expires_at`.
2. **Pharmacy scope**: Every mutating function verifies the record's `pharmacy_id` matches the passed `pharmacy_id`.
3. **User-pharmacy link**: Every mutating function verifies the acting `user_id` has a row in `user_pharmacies` for that `pharmacy_id`.
4. **Undo constraints**: 
   - Target history row is `STOCK_UPDATE` (not `UNDO`)
   - `undo_of_history_id` is NULL (not already undone)
   - No newer history row exists for same `inventory_id` (`id > target.id`)
5. **Stock status + quantity consistency**: Check constraints reject contradictory combinations.
6. **Unique constraints**: All PK/UNIQUE constraints as defined in tables.
7. **FK integrity**: All foreign keys enforced; no cascading deletes on history/audit tables.

### Critical Rule
> **`apply_confirmed_proposal` and `undo_last_change` must ONLY be called through Member 1's approved backend workflow.** They are not exposed to the Telegram bot or dashboard directly.

---

## G. Open Points

1. **Proposal confidence threshold for `flagged_for_review`**: Currently suggested at `< 0.8` — Member 1 to confirm or adjust.
2. **Schedule H flag source**: `is_schedule_h` on `medicines` drives auto-flag. Member 1 to confirm this is the correct trigger.
3. **Quantity parsing**: `parsed_quantity` in proposals is optional — Member 1 to confirm if LLM will extract quantities.
4. **Brand name in proposals**: `brand_name` is optional — Member 1 to confirm if needed for display/confirmation UI.
5. **Proposal `renewed_from_id` cascade**: If an expired proposal is renewed, should the original be marked in any way? Currently it stays EXPIRED with no back-reference.
6. **Inventory seeding**: How initial inventory is populated (seed script vs. first proposal).
7. **Stale threshold configurability**: Currently hardcoded 24h in `get_stale_inventory` default — Member 1 to confirm if it should be a config value.
8. **Soft delete cascade**: When a pharmacy/user/medicine is soft-deleted (`active=0`), should related proposals/history be hidden from default queries? Current design: they remain visible for audit.
9. **Telegram chat_id uniqueness**: One pharmacy per chat is enforced. Member 1 to confirm if a pharmacy can have multiple Telegram chats (e.g., group + private).
10. **SOS feature scope**: Marked optional — Member 1 to confirm if it's in MVP.

---

## Summary (10 lines)

1. **Tables**: 8 core + 2 optional (SOS). Each row belongs to a `pharmacy_id` for scope checks.
2. **Stock status**: Three-state enum (`IN_STOCK`/`LOW_STOCK`/`OUT_OF_STOCK`) with quantity consistency checks.
3. **Proposals**: Every update is a `PENDING` proposal with 24h `expires_at`; auto-expired by cron; renewal = new proposal.
4. **Apply**: Only `CONFIRMED` proposals applied via `apply_confirmed_proposal` (single transaction: history + inventory).
5. **History**: Immutable append-only; `STOCK_UPDATE` and `UNDO` rows; `undo_of_history_id` unique prevents double-undo.
6. **Undo**: Only latest change per inventory (by `id`); undo entries cannot be undone.
7. **Substitutions**: Per-request `substitution_approvals` table; no reuse of old approvals; pharmacist records APPROVED/REJECTED.
8. **Medicines**: Brands with normalized `composition_key`; non-unique index for multi-brand substitute lookup.
9. **Membership**: `user_pharmacies` M:M; every mutating call verifies user↔pharmacy link (integrity, not auth).
10. **Service layer contract**: DB assumes auth/confirmation done; enforces integrity (scope, state, consistency) only.

---

## Open Points

1. Proposal confidence threshold for `flagged_for_review` (suggested `< 0.8`)
2. Schedule H flag as auto-flag trigger — confirm
3. Quantity parsing in proposals — will LLM extract?
4. Brand name in proposals — needed for UI?
5. Renewal back-reference on expired proposals
6. Inventory seeding strategy
7. Stale threshold (24h) as config vs. hardcoded
8. Soft delete visibility for audit queries
9. Multiple Telegram chats per pharmacy?
10. SOS in MVP?