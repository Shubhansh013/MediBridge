# MediBridge Workflow Migration Map

**Version:** 1.0  
**Date:** 2026-10-25  
**Document Status:** Architectural Baseline  

This document maps all existing prototype workflows (from Telegram `bot.py` and Streamlit `medibridge_dashboard.py`) to their destination mobile screens, underlying backend operations, authorization constraints, test verifications, and readiness classification.

---

## 1. Feature Classification Breakdown

### Category A: Implemented and Ready for Integration
These workflows have complete service implementations, active REST API gateway endpoints, and automated tests.

| Workflow | Current Prototype | Destination Mobile UI Screen | Backend Service / API Endpoint | Auth & Scope Constraint | Test Coverage | Status |
|---|---|---|---|---|---|---|
| **Health Check** | `bot.py` ping | App Launch / System Status Banner | `GET /api/health` | Public | `tests/test_api.py::test_health_check` | Ready |
| **Medicine Search** | `bot.py` `/search <term>` | Patient Home / Search Bar | `search_catalogue` / `GET /api/medicines/search?q={q}` | Public | `tests/test_catalogue_service.py`, `tests/test_api.py::test_medicine_search` | Ready |
| **Medicine Detail** | Streamlit medicine view | Medicine Detail Screen | `get_medicine_by_id` / `GET /api/medicines/{id}` | Public | `tests/test_api.py::test_medicine_details_and_substitutes` | Ready |
| **Substitute Discovery** | `bot.py` substitute alert | Medicine Detail > Alternatives Tab | `find_substitutes` / `GET /api/medicines/{id}/substitutes` | Public | `tests/test_catalogue_service.py`, `tests/test_api.py::test_medicine_details_and_substitutes` | Ready |
| **Nearby Pharmacies** | `bot.py` location query | Patient Map & Pharmacy List View | `get_pharmacies_nearby` / `GET /api/pharmacies/nearby` | Public | `tests/test_operations.py`, `tests/test_api.py::test_nearby_pharmacies` | Ready |
| **Store Inventory List** | Streamlit inventory tab | Chemist Inventory Dashboard View | `get_pharmacy_inventory` / `GET /api/pharmacies/{id}/inventory` | Chemist / Pharmacist (store linked) | `tests/test_operations.py`, `tests/test_api.py::test_pharmacy_inventory` | Ready |
| **NLP Stock Proposal** | `bot.py` chemist text | Chemist "Quick Update" Chat / Bar | `process_stock_message` / `POST /api/proposals` | Staff linked to pharmacy (`UserPharmacy`) | `tests/test_proposal_service.py`, `tests/test_api.py::test_proposal_lifecycle_and_human_in_the_loop` | Ready |
| **Proposal Review Card** | `bot.py` inline buttons | Chemist Pending Proposals View | `list_pending_proposals` / `GET /api/proposals` | Staff linked to pharmacy | `tests/test_operations.py`, `tests/test_api.py::test_proposal_lifecycle_and_human_in_the_loop` | Ready |
| **Proposal Confirm** | `bot.py` callback `confirm_...` | Chemist Proposal Card > "Confirm" | `confirm_and_apply_proposal` / `POST /api/proposals/{id}/confirm` | Staff linked to pharmacy | `tests/test_confirmation_service.py`, `tests/test_api.py::test_proposal_lifecycle_and_human_in_the_loop` | Ready |
| **Proposal Reject** | `bot.py` callback `reject_...` | Chemist Proposal Card > "Cancel" | `reject_proposal` / `POST /api/proposals/{id}/reject` | Staff linked to pharmacy | `tests/test_confirmation_service.py`, `tests/test_api.py::test_proposal_rejection` | Ready |
| **Stock Audit Trail** | `bot.py` `/history` | Chemist Store History Tab | `get_pharmacy_history` / `GET /api/pharmacies/{id}/history` | Staff linked to pharmacy | `tests/test_history_service.py`, `tests/test_api.py::test_proposal_lifecycle_and_human_in_the_loop` | Ready |
| **Single-Step Undo** | `bot.py` `/undo` | Chemist History > "Undo" button | `orchestrate_undo` / `POST /api/history/{id}/undo` | Staff linked to pharmacy | `tests/test_history_service.py`, `tests/test_api.py::test_proposal_lifecycle_and_human_in_the_loop` | Ready |
| **Pharmacist Sub Approval**| Streamlit approval flow | Pharmacist Substitution Queue | `record_substitution_approval` / `POST /api/substitutions/approve`| Role: Pharmacist | `tests/test_catalogue_service.py`, `tests/test_api.py::test_pharmacist_substitution_approval` | Ready |
| **User Profile & Stores** | `bot.py` context resolve | Chemist Store Selector Header | `get_user_pharmacies` / `GET /api/users/{id}/pharmacies` | Authenticated user | `tests/test_operations.py`, `tests/test_ui_integration.py` | Ready |

---

### Category B: Implemented but Requiring Adaptation
Workflows where the backend service exists, but UI needs specific presentation mapping:

1. **Disambiguation Options**:
   - *Backend*: When multiple catalogue medicines match a colloquial term (e.g., "Metacin" matching Metacin 500 and Metacin Drops), `POST /api/proposals` returns `status: "UNRESOLVED"`.
   - *Mobile Adaptation*: The mobile proposal view presents candidate chips allowing the chemist to tap the exact brand, resubmitting with disambiguated brand name.
2. **Freshness (>24h Staleness) Warning**:
   - *Backend*: `is_stale = true` computed on inventory records where `last_confirmed_at` > 24 hours ago.
   - *Mobile Adaptation*: Display amber "Unconfirmed for >24h" badge next to stock status instead of showing a green "In Stock" pill without caveat.

---

### Category C: Missing API or Service Integration
None. All 14 required workflows are implemented in the service layer and exposed via the FastAPI REST gateway (`api/main.py`).

---

### Category D: Unsupported / Requiring Explicit Product Decision
1. **Direct In-App Medicine Purchasing / Payment Processing**:
   - Out of hackathon scope; MediBridge is strictly an availability and discovery platform. No payment gateway will be introduced.
2. **Direct Patient Delivery Tracking**:
   - Out of scope; availability coordinates and store contact information are provided for direct patient pickup.
