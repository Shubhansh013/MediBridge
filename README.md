# MediBridge 💊
### Connecting Patients to Nearby Pharmacies Through Smarter Medicine Availability

**Find medicines. Verify availability. Make access simpler.**

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688)
![Pydantic](https://img.shields.io/badge/Pydantic-Validation-E92063)
![AI/LLM](https://img.shields.io/badge/AI%2FLLM-Integrated-8A2BE2)
![Testing](https://img.shields.io/badge/Testing-pytest-blue)
![Status](https://img.shields.io/badge/Status-In%20Development-orange)

## 🌐 Overview

MediBridge is a pharmacy connectivity platform designed to simplify medicine discovery and inventory management through a shared backend, a medicine catalogue, and structured pharmacy workflows.

Patients can search for medicines, while chemists can use structured workflows to manage stock information. MediBridge combines deterministic parsing, AI/LLM-assisted message interpretation, data validation, and database operations to support more reliable pharmacy inventory management.

The platform is built around one important principle: **an interpreted message is a proposal, not proof of actual stock.** Inventory changes must follow the appropriate authorization and confirmation workflow.

## 🚨 The Problem

- **Uncertain availability:** Patients may visit pharmacies without knowing whether a medicine is in stock.
- **Time-consuming searches:** Finding medicines may require contacting multiple pharmacies.
- **Manual inventory management:** Updating stock records can be tedious for pharmacy staff.
- **Outdated information:** Unconfirmed inventory data can mislead patients.
- **Limited accessibility:** Smaller pharmacies may lack sophisticated inventory-management software.

## 💡 Our Solution

MediBridge connects patients and pharmacies through a shared inventory platform.

- 🔎 **Medicine discovery:** Search medicines by brand name, generic name, and supported aliases.
- 🏪 **Pharmacy connectivity:** Provide a foundation for discovering medicines across participating pharmacies.
- 💊 **Patient Mode:** Search the catalogue, inspect medicine details, and explore potential substitutes.
- 🧑‍⚕️ **Chemist Mode:** Provide an interface for pharmacy-side workflows.
- 💬 **Natural-language stock parsing:** Interpret supported English, Hindi, and Hinglish stock messages.
- 🧠 **AI/LLM-assisted interpretation:** Support structured extraction of medicine and stock information from natural-language messages.
- 🛡️ **Human-verified updates:** Keep proposed stock changes separate from confirmed inventory.
- 📋 **Inventory history:** Support traceable inventory operations and controlled reversals where implemented.

## ⚙️ Architecture

MediBridge follows a modular architecture in which the web client communicates with a Python backend through REST APIs. Application services handle catalogue operations, parsing, validation, and business rules, while the database layer manages persistence.

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

### Core components

| Component | Responsibility |
|---|---|
| Web client | Patient and chemist interfaces |
| FastAPI | REST endpoints and application integration |
| Pydantic | Structured data models and validation |
| Catalogue service | Medicine lookup, matching, and aliases |
| Deterministic parser | Extracts supported patterns from stock messages |
| AI/LLM service | Assists with natural-language interpretation |
| Database layer | Data models, queries, and persistence |
| Test suite | Validates schemas, parsing, API behaviour, and business rules |

### Architecture principles

- **Separation of concerns:** API routes, services, schemas, and persistence remain modular.
- **Shared business logic:** Interfaces use common backend services.
- **Human oversight:** Parsed messages do not automatically become confirmed stock updates.
- **Pharmacy-scoped access:** Protected operations must verify user permissions.
- **Auditability:** Inventory changes should preserve appropriate history.
- **Data integrity:** Validate proposals before applying changes.

## 🔄 How It Works

### 1. Patient medicine discovery

1. The patient opens MediBridge.
2. The patient searches the medicine catalogue.
3. The backend matches the query against supported catalogue fields.
4. Matching medicine records are returned to the web client.
5. Pharmacy availability can be displayed when supported by connected inventory data.

A catalogue match alone does not guarantee current stock at a specific pharmacy.

### 2. AI-assisted chemist stock workflow

Example message:

> Dolo 650 ke 20 strips hain

The workflow is designed to:

1. **Receive:** Accept a stock-related message.
2. **Interpret:** Use deterministic parsing and AI/LLM assistance as appropriate.
3. **Structure:** Extract the medicine, strength, quantity, and supported details.
4. **Validate:** Check the structured output using Pydantic schemas.
5. **Resolve:** Match the medicine against the catalogue and verify pharmacy context.
6. **Review:** Present the proposal for the required confirmation.
7. **Apply:** Update inventory only through the authorized workflow.
8. **Record:** Preserve the appropriate stock history.

AI/LLM output must be treated as untrusted input. It must not bypass validation, authorization, or required human confirmation.

## ✨ Features and Development Status

| Feature | Status |
|---|---|
| Python backend foundation | Implemented |
| FastAPI REST API | Implemented |
| Pydantic schemas and validation | Implemented and tested in the existing foundation |
| Deterministic stock-message parser | Implemented for supported patterns |
| AI/LLM-assisted interpretation | Included in the project architecture |
| Mobile-style web client | Implemented with Patient and Chemist modes |
| Medicine catalogue listing | Implemented |
| Medicine search | Implemented; matching behaviour should remain covered by regression tests |
| Dosage-form display | Frontend mapping uses the API's `form` field |
| Database models and operations | Database layer exists; complete workflow integration requires verification |
| Proposal review and confirmation | Requires end-to-end verification |
| Pharmacy-scoped authorization | Requires verification across protected operations |
| Audit history and undo | Requires end-to-end verification |
| Telegram integration | Requires verification against the current backend |
| Dashboard integration | Requires verification against the current backend |
| Production deployment | Not claimed as production-ready |

The AI/LL
