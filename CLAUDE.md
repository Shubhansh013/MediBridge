# MediBridge - Database & Persistence Layer

## Project Context
MediBridge is a Telegram assistant for Delhi NCR (~12 pharmacies) that tells patients which nearby independent pharmacies have a medicine in stock.

- Chemists update stock via casual chat: "Dolo 650 khatam"
- LLM parses to `{salt, strength, in_stock, intent}`
- One-tap Undo button for chemists
- Salt-equivalent substitutes (e.g., Dolo 650 → Calpol 650, same paracetamol 650mg) require pharmacist confirmation
- SOS broadcast to nearby chemists if nothing available
- Entries >24h flagged "not recently confirmed"
- Schedule H / Rx-required medicines must be flagged
- **Availability only** — never dosage or prescription advice

**Roles**: patient, chemist (stock validator), pharmacist (substitute validator), admin (verifies chemists, reviews flagged entries)

## Tech Stack (Fixed)
- Python 3.11+
- SQLite in WAL mode
- SQLAlchemy 2.0 (DeclarativeBase, Mapped, mapped_column, select())
- pytest

## My Role (Database & Persistence ONLY)
Build: DB config, models, constraints/indexes, transaction handling, init + seed script, DB operations (retrieve inventory, persist approved stock changes, proposals, history, undo), tests, schema documentation.

**Boundary**: Member 1 owns authorization, confirmation rules, and approved backend workflow.
- My functions must NOT decide who is allowed to do something
- Functions that apply stock changes called only by Member 1's approved workflow
- Any schema change must be coordinated with Member 1 → keep `SCHEMA_CHANGELOG.md`

## Coding Conventions
- SQLAlchemy 2.0 style only (DeclarativeBase, Mapped, mapped_column, select())
- Type hints everywhere
- Small, well-named functions
- Docstrings on every public function
- No business or authorization logic in DB layer
- Easy to import and use from both async and sync code