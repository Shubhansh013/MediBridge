# MediBridge Mobile Implementation Plan

**Version:** 1.0  
**Date:** 2026-10-25  
**Document Status:** Approved  

---

## 1. Objectives & Guardrails

- **Zero Regression**: Preserve existing database schemas, service logic, Telegram bot (`bot.py`), Streamlit dashboards (`dashboard.py`, `medibridge_dashboard.py`), and all 274 existing tests.
- **Strict Human-in-the-Loop**: Prevent any unconfirmed NLP text from mutating inventory balances.
- **Design Truth from Stitch**: Implement the mobile application adhering faithfully to the Google Stitch design system (layouts, typography, color palettes, and component hierarchy).
- **Backend Truth from Repository**: Wire every screen to real REST API endpoints; forbid mock data or design fixtures in live flows.

---

## 2. Phased Rollout Schedule

### Phase 1: Repository Audit & Baseline Verification (Completed)
- Inspected working tree, branches, commit logs, and directory structures.
- Verified all 274 existing tests pass cleanly without errors.
- Confirmed SQLite WAL database, 11 ORM models, and 23 operations functions.

### Phase 2: Documentation & Architectural Baselines (Completed)
- Authored `docs/APP_REQUIREMENTS.md` specifying dual patient/chemist requirements.
- Updated `docs/ARCHITECTURE.md` with 4-tier topology and sequence diagrams.
- Authored `docs/MIGRATION_MAP.md` mapping prototype flows to mobile screens.
- Authored `docs/IMPLEMENTATION_PLAN.md`.

### Phase 3: Mobile Application Presentation Layer (`static/` / Web PWA)
- Implement mobile web application serving Stitch UI components directly from the FastAPI gateway:
  - Responsive mobile viewport framework (iOS/Android touch ergonomics, side navigation, role switch).
  - Screen 1: **Patient Medicine Search & Discovery** (live `/api/medicines/search`, brand cards, salt specifications, Schedule H safety tags).
  - Screen 2: **Nearby Pharmacy Map & List** (live `/api/pharmacies/nearby` with Haversine distance, locality tags, freshness status).
  - Screen 3: **Medicine Detail & Substitute Explorer** (`/api/medicines/{id}/substitutes` composition matching).
  - Screen 4: **Chemist Quick Stock Update** (natural language text submission to `/api/proposals`).
  - Screen 5: **Chemist Proposal Verification Card** (review pending proposals with confidence badge, Schedule H review alert, Confirm and Reject action buttons).
  - Screen 6: **Chemist Pharmacy Inventory & Staleness Tracker** (`/api/pharmacies/{id}/inventory`).
  - Screen 7: **Store Stock Audit Trail & Undo** (`/api/pharmacies/{id}/history`, one-click undo via `/api/history/{id}/undo`).
- Provide static asset mounting on FastAPI (`/app` and `/`) for instant zero-dependency mobile browser preview.

### Phase 4: Verification & Regression Testing
- Execute end-to-end endpoint tests for mobile frontend flows.
- Verify real-time stock proposal lifecycle through the mobile UI interface.
- Run full pytest test suite across all 19 test modules.

### Phase 5: Git Commit & Remote Synchronization
- Commit implementation with conventional messages and required co-author attribution.
- Push clean commits to `origin/feat/backend-foundation`.
