# MediBridge AI / LLM Integration — Production Readiness Guide

**Document Status:** Production Ready  
**Target Worktree:** `medidoc-llm`  
**Target Branch:** `feat/llm-integration-isolated`  
**Primary Provider:** Groq (`llama-3.3-70b-versatile`)  
**Optional Provider:** Google Gemini (`gemini-1.5-flash`)  

---

## 1. Architecture Summary

MediBridge employs a **deterministic-first, Groq-backed NLP architecture** for parsing natural-language stock messages from chemists across English, Hindi, and Hinglish.

```
                              [ Chemist Input Message ]
                                          │
                                          ▼
                      ┌──────────────────────────────────────┐
                      │  Deterministic Offline Regex Parser   │
                      └──────────────────────────────────────┘
                                    │          │
                 (Valid Extraction) │          │ (Deterministic Miss)
                                    │          ▼
                                    │   ┌────────────────────────────────┐
                                    │   │ Safety Circuit Breakers        │
                                    │   │ (Uncertainty, Future, Chat)    │
                                    │   └────────────────────────────────┘
                                    │        │                  │
                                    │        │ (Ineligible)     │ (Eligible Miss)
                                    │        ▼                  ▼
                                    │   [ Abstained ]   ┌────────────────┐
                                    │      (None)       │ Optional Groq  │
                                    │                   │  LLM Fallback  │
                                    │                   └────────────────┘
                                    │                            │
                                    │                            ▼
                                    │                 ┌────────────────────┐
                                    │                 │ Pydantic Validation│
                                    │                 └────────────────────┘
                                    │                            │
                                    └────────────┬───────────────┘
                                                 │
                                                 ▼
                                ┌──────────────────────────────────┐
                                │   StockUpdateProposal (PENDING)  │
                                └──────────────────────────────────┘
                                                 │
                                                 ▼
                                ┌──────────────────────────────────┐
                                │ Catalogue Grounding & Validation │
                                └──────────────────────────────────┘
                                                 │
                                                 ▼
                                ┌──────────────────────────────────┐
                                │ Pharmacist Review & Confirmation │
                                └──────────────────────────────────┘
                                                 │
                                                 ▼
                                [ Transactional Inventory & Audit ]
```

### Core Invariants

1. **Deterministic-First Priority:** Pure regex/token deterministic parsing always runs first; Groq LLM fallback is triggered only on parseable stock messages where deterministic parsing falls short.
2. **Ineligible Message Circuit-Breaker:** Conversational chit-chat, price queries, uncertain words (`shayad`, `maybe`), future-stock notices (`kal aayega`), and contradictory assertions are blocked from reaching the LLM and yield zero inventory updates.
3. **No Direct Inventory Writes:** Under no circumstance does raw LLM output or parser output mutate the pharmacy stock table. All updates flow through `StockUpdateProposal` with pharmacist review and confirmation.
4. **Mandatory Pharmacist Review:**
   $$\text{flagged\_for\_review} = (\text{confidence} < 0.8) \lor \text{is\_schedule\_h} \lor \text{strength\_discrepancy}$$

---

## 2. Environment Configuration

### Required & Optional Variables

| Variable | Required / Optional | Default | Description |
|---|:---:|:---:|---|
| `GROQ_API_KEY` | Optional (Required only for LLM fallback) | None | Groq API Key (`gsk_...`) from [console.groq.com](https://console.groq.com). |
| `GROQ_MODEL` | Optional | `llama-3.3-70b-versatile` | Model ID for Groq chat completions (e.g. `openai/gpt-oss-120b`). |
| `LLM_PROVIDER` | Optional | `groq` | Provider selection: `'groq'` (default) or `'gemini'`. |
| `LLM_TIMEOUT` | Optional | `3.0` | Per-request network timeout in seconds (minimum `0.1s`). |
| `GEMINI_API_KEY` | Optional | None | Google Gemini API Key (`AIza...`) from Google AI Studio. |
| `GEMINI_MODEL` | Optional | `gemini-1.5-flash` | Gemini model ID. |
| `DATABASE_URL` | Optional | `sqlite:///./medibridge.db` | SQLite database URI in WAL mode. |

### Safe Local Setup

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Populate `GROQ_API_KEY` if testing LLM fallback locally.
3. Never check `.env` into Git (`.gitignore` enforces this).
4. If `GROQ_API_KEY` is omitted or set to the placeholder `your_groq_api_key_here`, MediBridge operates in 100% offline deterministic mode with zero external network calls.

---

## 3. Provider Error & Reliability Handling

Every LLM network call in [`src/medibridge/services/ai_parser.py`](file:///C:/Users/Shubhansh%20Upadhyay/OneDrive/Desktop/medidoc-llm/src/medibridge/services/ai_parser.py) is hardened against failure modes:

| Failure Mode | Behavior | Security & Safety Guarantee |
|---|---|---|
| **Connection Timeout** | Returns `None` | Logs warning; does not crash; caller falls back or safely returns `None`. |
| **Read Timeout** | Returns `None` | Timeout enforced via `_resolve_llm_timeout()`; socket cleaned up. |
| **HTTP 401 / 403** | Returns `None` | Key is redacted via `_redact_secret()` (logs only prefix e.g. `gsk_...****`). |
| **HTTP 429 (Rate Limit)** | Returns `None` | Fails safely without retry storms; returns `None`. |
| **HTTP 5xx (Provider Error)**| Returns `None` | Fails safely; returns `None`. |
| **Malformed JSON** | Returns `None` | Catch-all `json.loads` block returns `None`. |
| **Blank / Whitespace Content**| Returns `None` | Handled before JSON parsing; returns `None`. |
| **Pydantic Schema Violation**| Returns `None` | Rejected via `ParsedMedicineStockItem.model_validate()`. |
| **Malformed Envelopes** | Returns `None` | Defensively checks dictionary types, choice arrays, and message objects. |
| **Cross-Provider Isolation** | No silent switch | If Groq fails, it will NOT silently call Gemini; if Gemini fails, it will NOT silently call Groq. |
| **Resource Cleanup** | Guaranteed | Deterministic `finally: response.close()` ensures zero socket leakage. |

---

## 4. Verification & Testing

### Running Parser & LLM Tests
```bash
# Run all parser unit tests (deterministic + mocked LLM provider tests)
pytest tests/test_ai_parser.py tests/test_llm_parser.py -v
```

### Running Catalogue & Proposal Safety Tests
```bash
# Run catalogue safety, proposal lifecycle, and confirmation tests
pytest tests/test_phase4_catalogue_safety.py tests/test_proposal_service.py tests/test_confirmation_service.py -v
```

### Running the Offline Evaluation Benchmark
```bash
# Run automated 48-case evaluation benchmark with metrics report
python src/medibridge/evaluation/evaluator.py
```
Or execute via pytest:
```bash
pytest tests/test_evaluation_benchmark.py -v
```

### Running the Complete Test Suite
```bash
pytest tests/
```
Expected result: **333 passed, 2 warnings** (100% pass rate).

---

## 5. Evaluation Benchmark Results

The 48-case reproducible benchmark in [`src/medibridge/evaluation/eval_dataset.py`](file:///C:/Users/Shubhansh%20Upadhyay/OneDrive/Desktop/medidoc-llm/src/medibridge/evaluation/eval_dataset.py) evaluates performance across English, Hindi, and Hinglish:

| Benchmark Metric | Result | Denominator | Clinical Significance |
|---|:---:|:---:|---|
| **Deterministic Success Rate** | 87.1% | 27 / 31 valid cases | Fast, zero-cost, zero-latency parsing. |
| **LLM Fallback Rate** | 12.9% | 4 / 31 valid cases | Complex phrasing handled via Groq fallback. |
| **Exact Match Accuracy** | 100.0% | 31 / 31 valid cases | Medicine name, status, and quantity correctly parsed. |
| **Medicine Name Accuracy** | 100.0% | 31 / 31 valid cases | Accurate extraction across colloquial verbs and filler words. |
| **Stock Status Accuracy** | 100.0% | 31 / 31 valid cases | Perfect distinction of `IN_STOCK`, `LOW_STOCK`, `OUT_OF_STOCK`. |
| **Quantity Accuracy** | 100.0% | 31 / 31 valid cases | Standard and Hindi/English word numbers normalized. |
| **Safety Abstention Rate** | 100.0% | 17 / 17 invalid cases | 100% of conversational, uncertain, and future messages blocked. |
| **Unsafe Acceptance Rate** | 0.0% | 0 / 17 invalid cases | Zero risk of phantom proposals from chit-chat. |
| **Incorrect Catalogue Match Rate**| 0.0% | 0 / 31 tested cases | Zero incorrect bindings or unflagged Schedule H proposals. |

---

## 6. Pre-Deployment Checklist (Human Invariants)

Before deploying to staging or production, operators must perform the following manual checks:

- [ ] **Secret Management:** Confirm `GROQ_API_KEY` is provisioned through the deployment platform's secret manager (e.g. Vercel environment variables, Doppler, or AWS Secrets Manager), NOT in committed files.
- [ ] **Model Stability:** Confirm that `GROQ_MODEL` points to an active model (e.g. `llama-3.3-70b-versatile` or `openai/gpt-oss-120b`).
- [ ] **Rate Limits:** Verify that the Groq API account tier provides sufficient Requests Per Minute (RPM) and Tokens Per Minute (TPM) for expected pharmacy volume.
- [ ] **Timeout Suitability:** Ensure `LLM_TIMEOUT=3.0` is suitable for the deployment region's latency to Groq endpoints.
- [ ] **Database WAL Mode:** Ensure SQLite database operates with WAL mode enabled (`PRAGMA journal_mode=WAL;`).
- [ ] **Human-in-the-Loop Training:** Ensure pharmacists understand that AI extraction generates **unconfirmed proposals** requiring their explicit one-click confirmation.
- [ ] **Schedule H Compliance:** Ensure pharmacy staff are aware that Schedule H medicines will always display a review flag.
- [ ] **Single-Step Undo Audit:** Verify that the 24-hour single-step undo operation functions on the confirmed stock ledger.
