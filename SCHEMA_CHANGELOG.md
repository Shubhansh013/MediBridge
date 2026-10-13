# Schema Changelog

All schema changes for MediBridge must be documented here and coordinated with Member 1 (backend workflow owner) before implementation.

## Format
```
## [Version] - YYYY-MM-DD
### Added
- New tables, columns, indexes

### Changed
- Modifications to existing schema

### Removed
- Deprecated tables or columns

### Migration Notes
- Any special considerations for applying changes
```

---

## [v1.0] - 2026-10-12 — Contract Frozen
### Added
- Full schema for 11 tables (8 core + 2 optional SOS + user_pharmacies)
- Enums: UserRole, PharmacyVerification, StaffRoleAtPharmacy, StockStatus, ProposalStatus, HistoryChangeType, SubstitutionStatus, SosStatus, AliasType, UpdateSource
- Unique constraints: (brand_name, composition_key, strength, form, manufacturer) on medicines; (pharmacy_id, medicine_id) on inventory; (undo_of_history_id) on stock_history; telegram_chat_id on pharmacies; telegram_user_id on users; alias on medicine_aliases
- Check constraints: quantity >= 0; stock_status/quantity consistency (OUT_OF_STOCK → qty=0/NULL; IN_STOCK/LOW_STOCK → qty>0/NULL); latitude/longitude ranges
- Indexes: (composition_key, strength, form) on medicines; expires_at on stock_proposals; last_confirmed_at on inventory
- Composition key normalization (lowercase, split +, sort, join)
- Source message ref on stock_proposals (source_message_ref, nullable)

### Changed
- Stock status from boolean in_stock to 3-state enum (IN_STOCK, LOW_STOCK, OUT_OF_STOCK)
- Soft delete flag renamed from active to is_active on pharmacies, users, medicines
- Proposals: every update is PENDING until confirmed by authorized chemist; 24h expires_at checked at apply time
- Undo: only latest change per inventory (by id); undo entries cannot be undone
- Substitutions: per-request substitution_approvals table; pharmacist approval ONLY for substitutions, not stock updates

### Removed
- auto_applied proposal status
- Pharmacist confirmation for stock updates (only chemist confirms stock; pharmacist confirms substitutions)

### Notes
- Contract frozen per Member 1 confirmation
- Source_message_ref added as additive nullable column
- SOS tables kept as optional extension

---

## [Initial] - 2026-10-12
### Added
- Project structure initialized
- Database configuration with SQLite WAL mode
- Session management with automatic commit/rollback
- Base class for models

### Notes
- Models, operations, and seed data pending schema design in next step