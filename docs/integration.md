# MediBridge Integration Guide

**Version:** 1.0  
**Date:** 2026-10-24  
**Audience:** Developers integrating with MediBridge backend

---

## 1. Overview

This guide covers integrating new interfaces (bots, dashboards, APIs) with the MediBridge backend. The architecture enforces clean separation:

- **Interface Layer**: Telegram bot, Streamlit dashboard, future REST API
- **Service Layer**: Business logic orchestration (`src/medibridge/services/`)
- **Database Layer**: Pure data access (`medibridge_db/operations.py`)

**Integration Principle:** Interface layer calls service layer; service layer calls database operations. Never skip layers.

---

## 2. Quick Start: Add a New Interface

### Example: WhatsApp Bot

**Step 1: Import Services**
```python
from medibridge.services.proposal_service import process_stock_message, ProposalServiceResult
from medibridge.services.confirmation_service import confirm_and_apply_proposal
from medibridge.services.catalogue_service import search_medicine_by_name
from medibridge.database.operations_adapter import DBOperationsAdapter
from medibridge_db.database import get_session

# Initialize adapter
adapter = DBOperationsAdapter(session_factory=get_session)
```

**Step 2: Handle Stock Update Message**
```python
def handle_whatsapp_message(message_text: str, whatsapp_user_id: str, pharmacy_id: int):
    # Map WhatsApp user to internal user_id (lookup or create)
    user_id = get_or_create_user(whatsapp_user_id, role="chemist")
    
    # Process stock message
    result = process_stock_message(
        raw_message=message_text,
        chemist_user_id=user_id,
        pharmacy_id=pharmacy_id,
        catalogue_search=adapter.search_medicines,
        proposal_repository=adapter
    )
    
    if result.status == ProposalServiceResult.SUCCESS:
        # Send proposal card to chemist for confirmation
        send_proposal_card(result.proposal, whatsapp_user_id)
    else:
        send_error_message(result.error_message, whatsapp_user_id)
```

**Step 3: Handle Confirmation**
```python
def handle_confirmation(proposal_id: int, user_id: int, pharmacy_id: int):
    result = confirm_and_apply_proposal(
        proposal_id=proposal_id,
        confirmed_by_user_id=user_id,
        pharmacy_id=pharmacy_id,
        repository=adapter.apply_proposal
    )
    
    if result.success:
        send_success_message(f"✅ {result.inventory.medicine.brand_name} updated")
    else:
        send_error_message(result.error)
```

---

## 3. Service Layer API Reference

### 3.1 Proposal Service (`proposal_service.py`)

#### `process_stock_message()`

**Purpose:** Parse natural-language stock message → create PENDING proposal

**Signature:**
```python
def process_stock_message(
    raw_message: str,
    chemist_user_id: int,
    pharmacy_id: int,
    catalogue_search: Callable[[str, int], List[CatalogueMedicine]],
    proposal_repository: StockProposalRepositoryProtocol
) -> ProposalServiceResult
```

**Parameters:**
- `raw_message`: Original chemist message (e.g., "Dolo 650 khatam")
- `chemist_user_id`: Internal user ID (not Telegram/WhatsApp ID)
- `pharmacy_id`: Target pharmacy
- `catalogue_search`: Function to search medicines by name (pass `adapter.search_medicines`)
- `proposal_repository`: Repository with `create_proposal` method (pass `adapter`)

**Returns:**
```python
class ProposalServiceResult:
    status: ProposalServiceStatus  # SUCCESS, PARSE_ERROR, CATALOGUE_NOT_FOUND, etc.
    proposal: Optional[StoredProposalRecord]
    error_message: Optional[str]
```

**Example:**
```python
result = process_stock_message(
    raw_message="Crocin 20 strips aaye",
    chemist_user_id=42,
    pharmacy_id=5,
    catalogue_search=adapter.search_medicines,
    proposal_repository=adapter
)

if result.status == ProposalServiceStatus.SUCCESS:
    print(f"Proposal {result.proposal.id} created")
    print(f"Medicine: {result.proposal.brand_name}")
    print(f"Intent: {result.proposal.parsed_intent}")
    print(f"Confidence: {result.proposal.confidence:.2f}")
    print(f"Flagged: {result.proposal.flagged_for_review}")
else:
    print(f"Error: {result.error_message}")
```

---

### 3.2 Confirmation Service (`confirmation_service.py`)

#### `confirm_and_apply_proposal()`

**Purpose:** Authorize → apply confirmed proposal → update inventory atomically

**Signature:**
```python
def confirm_and_apply_proposal(
    proposal_id: int,
    confirmed_by_user_id: int,
    pharmacy_id: int,
    repository: Callable
) -> ConfirmationResult
```

**Parameters:**
- `proposal_id`: Proposal to confirm
- `confirmed_by_user_id`: User performing confirmation
- `pharmacy_id`: Pharmacy scope (must match proposal's pharmacy_id)
- `repository`: Function wrapping `apply_confirmed_proposal` (pass `adapter.apply_proposal`)

**Returns:**
```python
class ConfirmationResult:
    success: bool
    inventory: Optional[Inventory]
    error: Optional[str]
```

**Example:**
```python
result = confirm_and_apply_proposal(
    proposal_id=101,
    confirmed_by_user_id=42,
    pharmacy_id=5,
    repository=adapter.apply_proposal
)

if result.success:
    inv = result.inventory
    print(f"✅ Updated: {inv.medicine.brand_name}")
    print(f"New status: {inv.stock_status}")
    print(f"New quantity: {inv.quantity}")
else:
    print(f"❌ {result.error}")
```

#### `reject_proposal_workflow()`

**Purpose:** Mark proposal as REJECTED with optional note

**Signature:**
```python
def reject_proposal_workflow(
    proposal_id: int,
    rejected_by_user_id: int,
    rejection_note: Optional[str],
    repository: Callable
) -> ConfirmationResult
```

---

### 3.3 History Service (`history_service.py`)

#### `orchestrate_undo()`

**Purpose:** Validate constraints → undo last inventory change

**Signature:**
```python
def orchestrate_undo(
    history_id: int,
    undone_by_user_id: int,
    pharmacy_id: int,
    repository: Callable
) -> UndoExecutionResult
```

**Parameters:**
- `history_id`: History entry to undo
- `undone_by_user_id`: User performing undo
- `pharmacy_id`: Pharmacy scope
- `repository`: Function wrapping `undo_last_change` (pass `adapter.undo_change`)

**Returns:**
```python
class UndoExecutionResult:
    success: bool
    undo_history: Optional[StockHistory]
    restored_inventory: Optional[Inventory]
    error_message: Optional[str]
```

**Example:**
```python
result = orchestrate_undo(
    history_id=201,
    undone_by_user_id=42,
    pharmacy_id=5,
    repository=adapter.undo_change
)

if result.success:
    inv = result.restored_inventory
    print(f"✅ Undone: {inv.medicine.brand_name} restored to {inv.stock_status}")
else:
    print(f"❌ {result.error_message}")
```

---

### 3.4 Catalogue Service (`catalogue_service.py`)

#### `search_medicine_by_name()`

**Purpose:** Search medicines by brand, generic, or alias

**Signature:**
```python
def search_medicine_by_name(
    query: str,
    limit: int,
    repository: Callable
) -> List[CatalogueMedicine]
```

**Example:**
```python
medicines = catalogue_service.search_medicine_by_name(
    query="dolo",
    limit=10,
    repository=adapter.search_medicines
)

for med in medicines:
    print(f"{med.brand_name} ({med.generic_name} {med.strength})")
```

#### `find_medicine_by_brand_or_alias()`

**Purpose:** Exact match by brand name or alias

**Signature:**
```python
def find_medicine_by_brand_or_alias(
    name: str,
    repository: Callable
) -> Optional[CatalogueMedicine]
```

#### `get_substitutes_with_approval_status()`

**Purpose:** Find substitutes (same composition/strength/form) with approval status

**Signature:**
```python
def get_substitutes_with_approval_status(
    medicine_id: int,
    pharmacy_id: int,
    repository: Callable
) -> List[SubstituteWithApproval]
```

---

## 4. Database Operations Adapter (`operations_adapter.py`)

The `DBOperationsAdapter` wraps all database operations and manages sessions.

### Initialization

```python
from medibridge.database.operations_adapter import DBOperationsAdapter
from medibridge_db.database import get_session

adapter = DBOperationsAdapter(session_factory=get_session)
```

### Key Methods

#### Proposal Operations
```python
# Create proposal
proposal = adapter.create_proposal(
    pharmacy_id=5,
    chemist_user_id=42,
    raw_message="Dolo 650 khatam",
    parsed=ParsedProposalInput(...)
)

# Get proposal
proposal = adapter.get_proposal_by_id(proposal_id=101)

# List pending proposals
proposals = adapter.list_pending_proposals(
    pharmacy_id=5,
    flagged_only=False,
    limit=20
)
```

#### Inventory Operations
```python
# Get nearby pharmacies
pharmacies = adapter.get_pharmacies_nearby(
    lat=28.5494,
    lon=77.2501,
    radius_km=10,
    limit=20
)

# Get pharmacy inventory
inventory = adapter.get_pharmacy_inventory(
    pharmacy_id=5,
    include_stale=False,
    status_filter=None  # or StockStatus.IN_STOCK
)

# Get substitutes
substitutes = adapter.get_substitutes_for_medicine(
    medicine_id=12,
    pharmacy_id=5
)
```

#### Authorization
```python
# Check user-pharmacy link
is_linked = adapter.is_user_linked_to_pharmacy(
    user_id=42,
    pharmacy_id=5
)
```

---

## 5. Schema Reference

### Key Pydantic Models

#### `ParsedMedicineStockItem` (from `ai_parser.parse_stock_message`)
```python
{
    "medicine_name": "Dolo 650",
    "salt": "paracetamol",
    "strength": "650mg",
    "form": "tablet",
    "quantity": 20,  # Optional
    "action": "IN_STOCK",  # StockActionEnum
    "confidence": 0.92
}
```

#### `StoredProposalRecord` (from `process_stock_message`)
```python
{
    "id": 101,
    "pharmacy_id": 5,
    "chemist_user_id": 42,
    "raw_message": "Dolo 650 khatam",
    "brand_name": "Dolo 650",
    "parsed_salt": "paracetamol",
    "parsed_strength": "650mg",
    "parsed_form": "tablet",
    "parsed_quantity": None,
    "parsed_intent": "OUT_OF_STOCK",
    "confidence": 0.92,
    "status": "PENDING",
    "flagged_for_review": False,
    "expires_at": "2026-10-25T14:30:00Z",
    "created_at": "2026-10-24T14:30:00Z"
}
```

#### `CatalogueMedicine` (from catalogue search)
```python
{
    "id": 12,
    "brand_name": "Dolo 650",
    "generic_name": "paracetamol",
    "strength": "650mg",
    "form": "tablet",
    "is_schedule_h": False,
    "aliases": ["dolo", "dolo650"]
}
```

---

## 6. Error Handling

### Exception Hierarchy

All database exceptions inherit from base exceptions in `medibridge_db.exceptions`:

```python
from medibridge_db.exceptions import (
    ProposalExpiredError,
    UserNotLinkedToPharmacyError,
    HistoryAlreadyUndoneError,
    NewerChangeExistsError
)

try:
    result = confirm_and_apply_proposal(...)
except ProposalExpiredError:
    send_message("Proposal expired. Please renew.")
except UserNotLinkedToPharmacyError:
    send_message("Not authorized for this pharmacy.")
except Exception as e:
    logger.error(f"Unexpected error: {e}")
    send_message("Internal error. Please try again.")
```

### Service Layer Error Handling

Service layer functions return result objects with `success` flag:

```python
result = process_stock_message(...)

if not result.success:
    if result.status == ProposalServiceStatus.PARSE_ERROR:
        # Cannot interpret message
        send_message("Could not understand message. Try: 'Medicine name out of stock'")
    elif result.status == ProposalServiceStatus.CATALOGUE_NOT_FOUND:
        # Medicine not in catalogue
        send_message(f"Medicine not found: {result.error_message}")
    elif result.status == ProposalServiceStatus.AUTHORIZATION_ERROR:
        # User not authorized
        send_message("Not authorized for this pharmacy")
    else:
        # Generic error
        send_message(f"Error: {result.error_message}")
```

---

## 7. Testing Your Integration

### Unit Test Template

```python
import pytest
from medibridge.services.proposal_service import process_stock_message
from medibridge.database.operations_adapter import DBOperationsAdapter
from medibridge_db.database import get_test_db_url, create_engine
from medibridge_db.seed import seed_all

@pytest.fixture
def test_adapter():
    engine = create_engine(get_test_db_url())
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    
    with SessionLocal() as session:
        seed_all(session)
        session.commit()
    
    adapter = DBOperationsAdapter(session_factory=SessionLocal)
    yield adapter
    
    Base.metadata.drop_all(engine)

def test_stock_message_creates_proposal(test_adapter):
    result = process_stock_message(
        raw_message="Dolo 650 khatam",
        chemist_user_id=1,  # From seed data
        pharmacy_id=1,      # From seed data
        catalogue_search=test_adapter.search_medicines,
        proposal_repository=test_adapter
    )
    
    assert result.success
    assert result.proposal is not None
    assert result.proposal.status == "PENDING"
    assert result.proposal.parsed_intent == "OUT_OF_STOCK"
```

### Integration Test: Full Workflow

```python
def test_full_proposal_workflow(test_adapter):
    # 1. Parse and create proposal
    result1 = process_stock_message(
        raw_message="Crocin 20 strips aaye",
        chemist_user_id=1,
        pharmacy_id=1,
        catalogue_search=test_adapter.search_medicines,
        proposal_repository=test_adapter
    )
    assert result1.success
    proposal_id = result1.proposal.id
    
    # 2. Confirm and apply
    result2 = confirm_and_apply_proposal(
        proposal_id=proposal_id,
        confirmed_by_user_id=1,
        pharmacy_id=1,
        repository=test_adapter.apply_proposal
    )
    assert result2.success
    assert result2.inventory.stock_status == "IN_STOCK"
    assert result2.inventory.quantity == 20
    
    # 3. Undo
    history = test_adapter.get_pharmacy_history(pharmacy_id=1, limit=1)[0]
    result3 = orchestrate_undo(
        history_id=history.id,
        undone_by_user_id=1,
        pharmacy_id=1,
        repository=test_adapter.undo_change
    )
    assert result3.success
    assert result3.restored_inventory.stock_status != "IN_STOCK"
```

---

## 8. Common Integration Patterns

### Pattern 1: Patient Search (Read-Only)

```python
def handle_patient_search(query: str, lat: float, lon: float):
    # Search medicines
    medicines = catalogue_service.search_medicine_by_name(
        query=query,
        limit=10,
        repository=adapter.search_medicines
    )
    
    results = []
    for med in medicines:
        # Get nearby pharmacies with this medicine
        pharmacies = adapter.get_pharmacies_nearby(
            lat=lat,
            lon=lon,
            radius_km=10,
            limit=20
        )
        
        for pharm in pharmacies:
            inv = adapter.get_inventory_for_medicine(
                pharmacy_id=pharm.id,
                medicine_id=med.id,
                include_stale=False
            )
            if inv:
                results.append({
                    "medicine": med.brand_name,
                    "pharmacy": pharm.name,
                    "locality": pharm.locality,
                    "status": inv.stock_status,
                    "last_verified": inv.last_confirmed_at
                })
    
    return results
```

### Pattern 2: Chemist Stock Update (Write)

```python
def handle_chemist_update(message: str, user_id: int, pharmacy_id: int):
    # Step 1: Create proposal
    result = process_stock_message(
        raw_message=message,
        chemist_user_id=user_id,
        pharmacy_id=pharmacy_id,
        catalogue_search=adapter.search_medicines,
        proposal_repository=adapter
    )
    
    if not result.success:
        return {"error": result.error_message}
    
    # Step 2: Auto-confirm if high confidence and not Schedule H
    proposal = result.proposal
    if not proposal.flagged_for_review:
        confirm_result = confirm_and_apply_proposal(
            proposal_id=proposal.id,
            confirmed_by_user_id=user_id,
            pharmacy_id=pharmacy_id,
            repository=adapter.apply_proposal
        )
        
        if confirm_result.success:
            return {"status": "applied", "inventory": confirm_result.inventory}
        else:
            return {"error": confirm_result.error}
    else:
        # Needs manual review
        return {"status": "pending_review", "proposal": proposal}
```

### Pattern 3: Dashboard Inventory View

```python
def get_dashboard_data(pharmacy_id: int):
    # Get all inventory
    inventory = adapter.get_pharmacy_inventory(
        pharmacy_id=pharmacy_id,
        include_stale=True,  # Show stale with indicator
        status_filter=None   # All statuses
    )
    
    # Get pending proposals
    proposals = adapter.list_pending_proposals(
        pharmacy_id=pharmacy_id,
        flagged_only=False,
        limit=50
    )
    
    # Get recent history
    history = adapter.get_pharmacy_history(
        pharmacy_id=pharmacy_id,
        limit=100
    )
    
    return {
        "inventory": [
            {
                "medicine": inv.medicine.brand_name,
                "status": inv.stock_status,
                "quantity": inv.quantity,
                "last_updated": inv.last_confirmed_at,
                "is_stale": is_stale(inv.last_confirmed_at)
            }
            for inv in inventory
        ],
        "pending_proposals": [
            {
                "id": prop.id,
                "message": prop.raw_message,
                "parsed": f"{prop.brand_name} → {prop.parsed_intent}",
                "confidence": prop.confidence,
                "flagged": prop.flagged_for_review,
                "expires_at": prop.expires_at
            }
            for prop in proposals
        ],
        "recent_history": [
            {
                "medicine": hist.medicine.brand_name,
                "change": f"{hist.previous_stock_status} → {hist.new_stock_status}",
                "type": hist.change_type,
                "changed_by": hist.changed_by.display_name,
                "created_at": hist.created_at
            }
            for hist in history
        ]
    }

def is_stale(last_confirmed_at):
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone.utc) - last_confirmed_at > timedelta(hours=24)
```

---

## 9. Environment Configuration

### Required Environment Variables

```bash
# Database
DATABASE_URL=sqlite:///./data/medibridge.db

# Telegram (if using bot)
TELEGRAM_BOT_TOKEN=your_bot_token

# Optional: LLM parser fallback
GEMINI_API_KEY=your_gemini_key
GROQ_API_KEY=your_groq_key
```

### Load Environment

```python
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/medibridge.db")
```

---

## 10. Best Practices

### ✅ DO

1. **Use service layer functions** for all business logic
2. **Pass `adapter` methods as repository parameters** to service functions
3. **Handle service result objects** with success/error checks
4. **Wrap database calls in try/except** for graceful error handling
5. **Test with seeded database** using `medibridge_db.seed.seed_all`
6. **Use type hints** for clarity
7. **Log errors** with context (user_id, pharmacy_id, proposal_id)
8. **Display user-friendly error messages** (not raw exceptions)

### ❌ DON'T

1. **Don't import `medibridge_db.operations` directly** → Use `DBOperationsAdapter`
2. **Don't call database operations without service layer** → Skips authorization checks
3. **Don't modify `stock_history` rows** → Immutable audit trail
4. **Don't manually set proposal status to CONFIRMED** → Use confirmation service
5. **Don't assume user is authorized** → Check `is_user_linked_to_pharmacy`
6. **Don't apply expired proposals** → Service layer checks; database enforces
7. **Don't undo UNDO entries** → Raises `CannotUndoUndoError`
8. **Don't commit transactions manually** → Adapter handles sessions

---

## 11. Migration Checklist: Existing Interface to MediBridge Backend

If you have an existing interface (bot, dashboard) and want to integrate MediBridge:

**Step 1: Database Migration**
- [ ] Run `medibridge_db.seed.seed_all()` to populate catalogue and demo data
- [ ] Migrate existing inventory to MediBridge schema (if any)
- [ ] Create user records for existing Telegram/WhatsApp users

**Step 2: Code Integration**
- [ ] Replace direct database calls with `DBOperationsAdapter` methods
- [ ] Wrap message handling with `process_stock_message`
- [ ] Wrap confirmation flow with `confirm_and_apply_proposal`
- [ ] Wrap undo with `orchestrate_undo`

**Step 3: Testing**
- [ ] Write unit tests for adapter integration
- [ ] Test parse → propose → confirm → apply flow
- [ ] Test undo workflow
- [ ] Test search and substitute discovery

**Step 4: Deployment**
- [ ] Set environment variables (.env file)
- [ ] Run database migrations (if using Alembic)
- [ ] Deploy interface with new backend
- [ ] Monitor logs for errors

---

## 12. Example: Minimal WhatsApp Bot

```python
# whatsapp_bot.py
from twilio.rest import Client
from medibridge.services.proposal_service import process_stock_message
from medibridge.services.confirmation_service import confirm_and_apply_proposal
from medibridge.database.operations_adapter import DBOperationsAdapter
from medibridge_db.database import get_session
import os

# Initialize
client = Client(os.getenv("TWILIO_SID"), os.getenv("TWILIO_AUTH"))
adapter = DBOperationsAdapter(session_factory=get_session)

# User mapping (WhatsApp number → internal user_id)
USER_MAP = {}  # Load from database

@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    incoming_msg = request.values.get("Body", "")
    from_number = request.values.get("From", "")
    
    user_id = USER_MAP.get(from_number)
    if not user_id:
        send_whatsapp(from_number, "Please register first: /register")
        return "OK"
    
    # Parse as stock message
    result = process_stock_message(
        raw_message=incoming_msg,
        chemist_user_id=user_id,
        pharmacy_id=get_user_pharmacy(user_id),
        catalogue_search=adapter.search_medicines,
        proposal_repository=adapter
    )
    
    if result.success:
        # Send proposal card
        msg = f"📋 Proposal:\n"
        msg += f"{result.proposal.brand_name} → {result.proposal.parsed_intent}\n"
        msg += f"Confidence: {result.proposal.confidence:.0%}\n"
        msg += f"Reply 'yes {result.proposal.id}' to confirm"
        send_whatsapp(from_number, msg)
    else:
        send_whatsapp(from_number, f"❌ {result.error_message}")
    
    return "OK"

def send_whatsapp(to: str, message: str):
    client.messages.create(
        from_="whatsapp:+14155238886",
        body=message,
        to=to
    )
```

---

## Summary

**Key Integration Points:**
1. **Service Layer** (`src/medibridge/services/`) — Use these for all workflows
2. **DBOperationsAdapter** (`src/medibridge/database/operations_adapter.py`) — Pass to service functions as repository
3. **Pydantic Schemas** (`src/medibridge/schemas/`) — Validate input/output
4. **Exception Handling** (`medibridge_db/exceptions.py`) — Catch and display user-friendly errors

**Workflow:**
```
Interface → Service Layer → Adapter → Database Operations → SQLite
```

**Never Skip Layers:** Authorization checks live in service layer; skipping directly to database bypasses security.

For questions or issues, refer to `docs/prd.md` (requirements), `docs/architecture.md` (system design), and `docs/rules.md` (business rules).
