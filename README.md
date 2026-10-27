# MediBridge 💊
### Connecting Patients to Nearby Pharmacies Through Smarter Medicine Availability

<p align="center">
  <strong>Find medicines. Verify availability. Make access simpler.</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue" alt="Python" />
  <img src="https://img.shields.io/badge/FastAPI-Backend-009688" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Pydantic-Validation-E92063" alt="Pydantic" />
  <img src="https://img.shields.io/badge/AI%2FLLM-Integrated-8A2BE2" alt="AI/LLM" />
  <img src="https://img.shields.io/badge/Testing-pytest-blue" alt="Testing" />
  <img src="https://img.shields.io/badge/Status-In%20Development-orange" alt="Status" />
</p>

---

## 🌐 Overview

**MediBridge** is a pharmacy connectivity platform designed to make medicine availability easier to discover and manage.

Finding a medicine can be frustrating when patients have to call multiple pharmacies, travel between locations, or rely on outdated stock information. At the same time, pharmacies need a simple way to maintain accurate inventory without complicated workflows.

MediBridge aims to bridge this gap by connecting patients with nearby pharmacies and simplifying stock updates through natural-language messages.

The platform is being developed with a focus on usability, reliable inventory management, and human-verified stock updates.

> **Our core principle:** An interpreted message is a proposal, not proof of actual stock. Inventory changes must follow the appropriate verification and confirmation workflow.

---

## 🚨 The Problem

- **Uncertain availability:** Patients may visit pharmacies without knowing whether a medicine is in stock.
- **Time-consuming searches:** Finding a medicine can involve contacting multiple pharmacies.
- **Manual inventory management:** Updating stock records can be tedious for pharmacy staff.
- **Outdated information:** Unconfirmed inventory data can lead to misleading availability results.
- **Limited accessibility:** Not every pharmacy has access to sophisticated inventory-management software.

---

## 💡 Our Solution

MediBridge creates a reliable connection between patients and pharmacies through a shared inventory workflow:

- 🔎 **Medicine discovery:** Search medicines by brand name, generic formulation (salt), and common aliases.
- 🏪 **Pharmacy connectivity:** Discover medicines across participating independent pharmacies in Delhi NCR.
- 💊 **Patient Mode:** Search the catalogue, inspect medicine details, and explore therapeutic substitutes.
- 🧑‍⚕️ **Chemist Mode:** Provide an intuitive interface for pharmacy-side stock operations and pending proposal confirmations.
- 💬 **Natural-language stock parsing:** Interpret supported English, Hindi, and Hinglish stock messages.
- 🧠 **AI/LLM-assisted interpretation:** Fallback extraction of medicine and stock information from complex natural-language messages using Groq and Gemini.
- 🛡️ **Human-verified updates:** Require confirmation from an authorized chemist before a proposed stock change becomes official.
- 📋 **Inventory history:** Maintain an immutable audit trail of changes and support single-step reversals.

---

## ⚙️ Architecture

MediBridge follows a modular architecture where the web client communicates with a Python backend through REST APIs. Application services handle catalogue operations, parsing, validation, and business rules, while the database layer manages persistence.

```text
                    MediBridge
                         |
               Mobile-Style Web Client
                  /             \
           Patient Mode       Chemist Mode
                  \             /
                         |
                    FastAPI
                    REST API
                         |
          +--------------+--------------+
          |              |              |
      Catalogue      Parsing         Application
      Services       Pipeline         Services
                         |
                  Deterministic +
                  AI/LLM-Assisted
                  Interpretation
                         |
                  Pydantic Validation
                         |
                Catalogue and Business
                    Rule Checks
                         |
                   Human Review
                         |
                 Authorized Operations
                         |
                   Database Layer
```

### Core Components

| Component | Responsibility |
|---|---|
| **Web client** | Patient discovery and chemist operations interfaces (`/app/`) |
| **FastAPI** | REST API gateway, route orchestration, CORS, and response serialization |
| **Pydantic** | Structured data validation and contract enforcement |
| **Catalogue service** | Medicine lookup, fuzzy alias matching, and salt-equivalent substitutes |
| **Deterministic parser** | Offline regex and Hinglish rule-based extraction pipeline |
| **AI/LLM service** | Groq-first fallback parser with Gemini support for complex messages |
| **Proposal service** | Grounding parsed items against catalogue, checking conflicts, and managing 24h expiration |
| **Confirmation service** | Human-in-the-loop confirmation, atomic inventory application, and audit logging |
| **Database layer** | SQLite with WAL mode, foreign key enforcement, 11 ORM models, and 24 pure operations |
| **Test suite** | Automated verification of schemas, operations, services, API endpoints, and AI robustness |

### Architecture Principles

- **Separation of concerns:** API routes, services, schemas, and persistence remain strictly modular.
- **Shared business logic:** All interfaces consume common backend services and database adapters.
- **Human oversight:** Parsed messages never automatically update live inventory without pharmacist confirmation.
- **Pharmacy-scoped access:** Protected operations verify user linkage and pharmacy scoping before mutating state.
- **Auditability:** Every stock change records a corresponding `StockHistory` entry.
- **Data integrity:** Validate proposals through Pydantic schemas before applying changes.

---

## 🏗️ Technical Architecture: 4-Tier Breakdown

MediBridge is structured across four clean tiers:

```text
+---------------------------------------------------------------+
| Tier 1: Client Layer                                          |
| - Google Stitch Mobile Web Client (/app/)                     |
| - Patient Mode (Search, Pharmacy Discovery, Substitutes)      |
| - Chemist Mode (Natural Language Input, Proposal Confirm/Undo)|
+---------------------------------------------------------------+
                               |  HTTP / JSON
                               v
+---------------------------------------------------------------+
| Tier 2: API Gateway Layer                                     |
| - FastAPI REST API (23 endpoints in api/main.py)              |
| - CORS Middleware, Static File Mounting, Health Checks        |
| - Pydantic Request / Response Validation                      |
+---------------------------------------------------------------+
                               |  Service Orchestration
                               v
+---------------------------------------------------------------+
| Tier 3: Core Domain Services                                  |
| - AI Parser: Deterministic regex-first + Groq/Gemini LLM      |
| - Proposal Service: 5-stage pipeline, 24h expiration          |
| - Confirmation Service: Atomic review, application, auditing  |
| - History Service: Append-only ledger, single-step undo       |
| - Catalogue Service: Salt substitution, alias search          |
+---------------------------------------------------------------+
                               |  Operations Adapter
                               v
+---------------------------------------------------------------+
| Tier 4: Database & Persistence Layer                          |
| - SQLite WAL Mode (WAL, busy_timeout=5000, foreign_keys=ON)  |
| - SQLAlchemy 2.0 Declarative Models (11 relational tables)   |
| - 24 Pure Operations Functions (medibridge_db/operations.py)  |
+---------------------------------------------------------------+
```

---

## 🔄 Core Workflows

### 1. Patient Medicine Discovery Workflow

1. **Access:** Patient opens the MediBridge web application at `/app/`.
2. **Search:** Enters medicine brand name, generic name, or alias (e.g., `"Dolo"`, `"Paracetamol"`).
3. **Lookup:** Backend searches catalogue with case-insensitive matching and alias resolution.
4. **Availability:** System checks inventory across registered pharmacies and flags stock status (`IN_STOCK`, `LOW_STOCK`, `OUT_OF_STOCK`).
5. **Freshness:** Inventory entries updated more than 24 hours ago are clearly marked as `"not recently confirmed"`.
6. **Substitutes:** If unavailable, patient can view salt-equivalent alternatives (e.g., Calpol 650 for Dolo 650) with Schedule H flags highlighted.

### 2. Chemist AI Stock Update Workflow

A chemist sends a natural-language update:

> `Dolo 650 ke 20 strips hain`

The update is processed through a strict 5-stage pipeline:

```text
[Raw Message]
      |
      v
1. Input Hygiene & Intent Check ---> (Rejects conversational, future, or uncertain text)
      |
      v
2. Deterministic Regex Parser ------> (Extracts medicine, strength, quantity, status offline)
      | (if complex or ambiguous)
      v
3. AI/LLM Provider Fallback --------> (Groq default; Gemini optional; validated by Pydantic)
      |
      v
4. Catalogue Grounding & Proposal -> (Matches catalogue, checks strength conflict, sets 24h expiry)
      |
      v
5. Chemist Review & Confirmation --> (Authorized chemist reviews proposal; atomic update applied)
```

1. **Receive:** The chemist inputs a natural-language message via the Web UI or Telegram.
2. **Deterministic Parse:** The offline regex parser attempts extraction without external API calls.
3. **LLM Fallback (if required):** If the message is complex, Groq (`llama-3.3-70b-versatile` or `openai/gpt-oss-120b`) extracts structured JSON.
4. **Pydantic Validation:** Extracted data is strictly validated into a `StockUpdateProposal`.
5. **Catalogue Grounding:** Proposal service matches the medicine against the catalogue, validates strength/form, and records a `PENDING` proposal.
6. **Human Review:** The chemist inspects the proposal in Chemist Mode.
7. **Apply:** Once confirmed, the system atomically updates inventory and writes an immutable audit record to `StockHistory`.

---

## 🛡️ Safety & Regulatory Integrity

- **Human-in-the-Loop:** Parsed AI proposals never modify confirmed inventory directly. An authorized chemist must verify and confirm every proposal.
- **Zero Hallucination Writes:** If an AI model hallucinates a non-existent medicine or invalid dosage, catalogue grounding rejects or flags the item for review.
- **24-Hour Expiration:** Unconfirmed proposals automatically expire after 24 hours to prevent applying stale updates.
- **Single-Step Undo:** Chemists can immediately reverse accidental stock updates with one tap.
- **Schedule H Compliance:** Prescription-required drugs are explicitly flagged in both patient and chemist views. Availability is shown without medical or dosage advice.

---

## 📡 REST API Quick Reference

FastAPI exposes 23 REST endpoints under `/api`:

| Group | Method | Endpoint | Description |
|---|---|---|---|
| **Health** | `GET` | `/api/health` | Service and database connectivity health check |
| **Medicines** | `GET` | `/api/medicines` | List all active medicines in catalogue |
| | `GET` | `/api/medicines/search` | Search by brand, salt, or alias (`?q=`) |
| | `GET` | `/api/medicines/{id}` | Retrieve medicine details |
| | `GET` | `/api/medicines/{id}/substitutes` | Find salt-equivalent therapeutic alternatives |
| **Pharmacies** | `GET` | `/api/pharmacies` | List all verified pharmacies |
| | `GET` | `/api/pharmacies/nearby` | Haversine distance search (`?lat=&lon=&radius_km=`) |
| | `GET` | `/api/pharmacies/{id}` | Pharmacy location and contact details |
| | `GET` | `/api/pharmacies/{id}/inventory` | Pharmacy live inventory with staleness flags |
| **Proposals** | `POST` | `/api/proposals` | Parse stock message into unconfirmed proposal |
| | `GET` | `/api/proposals/pending` | List pending proposals for pharmacy |
| | `GET` | `/api/proposals/{id}` | Proposal details with confidence score |
| | `POST` | `/api/proposals/{id}/confirm` | Authorized chemist confirms and applies update |
| | `POST` | `/api/proposals/{id}/reject` | Reject and dismiss unconfirmed proposal |
| **History & Undo** | `GET` | `/api/history/{pharmacy_id}` | Append-only stock audit ledger |
| | `POST` | `/api/history/{history_id}/undo` | Revert stock update to previous state |
| **Users** | `GET` | `/api/users/{user_id}/pharmacies` | Check chemist/pharmacist authorization |

---

## 🚀 Getting Started

### Prerequisites

- Python 3.10, 3.11, or 3.12
- SQLite 3.35+ (bundled with standard Python)
- Git

### 1. Clone the Repository

```bash
git clone https://github.com/your-org/medidoc.git
cd medidoc
```

### 2. Set Up a Virtual Environment

```bash
# On Linux/macOS
python3 -m venv .venv
source .venv/bin/activate

# On Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3. Install Dependencies

```bash
# Production dependencies
pip install -r requirements.txt

# Or editable install with development tools
pip install -e ".[dev]"
```

### 4. Configure Environment Variables (Optional)

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

```ini
# Primary LLM Provider (Offline deterministic parser operates without keys)
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile
LLM_PROVIDER=groq
LLM_TIMEOUT=3.0

# Optional Alternative Provider
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-1.5-flash

# Database Configuration
DATABASE_URL=sqlite:///./medibridge.db
```

### 5. Initialize the Database

MediBridge uses SQLite in WAL mode. Initialize and seed the database with 12 pharmacies, 25 medicines, and sample inventory:

```bash
python scripts/init_db.py
```

---

## 🏃 Running the Application

### Local Development (Primary Web App)

Start the MediBridge FastAPI server and web client:

```bash
python -m uvicorn api.main:app --reload
```

*(Alternatively, use the runner script: `python run.py`)*

Once running, access the application:
* **Web Client:** [http://127.0.0.1:8000/app/](http://127.0.0.1:8000/app/) *(Google Stitch mobile client for patients & chemists)*
* **Root Browser Redirect:** [http://127.0.0.1:8000/](http://127.0.0.1:8000/) *(Automatically redirects to `/app/`)*
* **Interactive API Documentation:** [http://127.0.0.1:8000/api/docs](http://127.0.0.1:8000/api/docs) *(Swagger UI)*
* **System Health Check:** [http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health)
* **Live Production App:** [https://medidoc-sigma.vercel.app/app/](https://medidoc-sigma.vercel.app/app/)

### Legacy Prototype Tools (Archival)

* **Legacy Streamlit ERP (`medibridge_dashboard.py`):**
  ```bash
  streamlit run medibridge_dashboard.py
  ```
  *(Note: Active chemist and patient workflows are served via the Web App at `/app/`)*
* **Deprecated Prototypes (`bot.py`, `dashboard.py`):**
  These legacy entry points have been deprecated and disabled. All active functionality is served via the REST API and modern web client.

---

## 🧪 Testing & Verification

MediBridge includes an automated test suite with **333 tests** verifying database models, operations, service orchestration, NLP parsing, API endpoints, catalogue safety, and AI evaluation benchmarks.

Run the entire test suite:

```bash
pytest
```

Run test suite with concise output:

```bash
pytest -q
```

Run specific test modules:

```bash
# REST API endpoint tests
pytest tests/test_api.py -v

# AI / LLM parser and provider tests
pytest tests/test_llm_parser.py -v

# Catalogue safety and conflict handling
pytest tests/test_phase4_catalogue_safety.py -v

# AI evaluation benchmark
pytest tests/test_evaluation_benchmark.py -v
```

---

## 🗺️ Project Milestones & Roadmap

- [x] **Phase 1: Database Persistence Tier:** 11 relational tables, SQLite WAL mode, and 24 operations functions.
- [x] **Phase 2: Service Layer & Business Rules:** Proposal, confirmation, history, and catalogue services.
- [x] **Phase 3: Hybrid NLP & LLM Parser:** Deterministic English/Hindi/Hinglish parser with Groq/Gemini fallback.
- [x] **Phase 4: Safety & Verification Engine:** Human-in-the-loop confirmation, 24h expiration, and single-step undo.
- [x] **Phase 5: REST API Gateway:** 23 endpoints in FastAPI with CORS and Pydantic validation.
- [x] **Phase 6: Mobile Web Client:** Responsive Google Stitch UI serving patient discovery and chemist workflows at `/app/`.
- [x] **Phase 7: Comprehensive AI Evaluation & Production Readiness:** 333 automated tests, 100% safety abstention, and zero direct LLM inventory writes.
- [ ] **Phase 8: Post-Hackathon Extensions:**
  - WhatsApp Business API integration for chemist stock updates.
  - Multi-city pharmacy onboarding and verification registry.
  - Optical Character Recognition (OCR) for handwritten doctor prescriptions.
  - Real-time WebSocket notifications for low-stock alerts.

---

## 👥 Contributors

MediBridge is developed collaboratively by the **MediBridge Hackathon Team**:
* **Backend Architecture & API Gateway**
* **Database Design & Relational Persistence**
* **AI / NLP Parsing & Prompt Engineering**
* **Frontend Mobile UX & Interactive Dashboards**

---

## 📄 License

This project is licensed under the MIT License — see the LICENSE file for details.

---

<p align="center">
  <strong>MediBridge — Making medicine availability simpler, transparent, and verified.</strong>
  <br />
  <sub>Built with care for patients and community pharmacies.</sub>
</p>
