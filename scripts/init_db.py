#!/usr/bin/env python3
"""
Initialize MediBridge database: create tables, optionally seed demo data.

Usage:
    python scripts/init_db.py                    # Create tables + seed
    python scripts/init_db.py --no-seed          # Create tables only
    python scripts/init_db.py --reset --yes      # Drop all, recreate, seed
    python scripts/init_db.py --db-url sqlite:///custom.db
"""

import argparse
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from medibridge_db.database import create_db_engine, Base, get_session
from medibridge_db.seed import seed_database
# Import models to register them with Base.metadata
from medibridge_db.models import (  # noqa: F401
    Pharmacy, User, UserPharmacy, Medicine, MedicineAlias,
    Inventory, StockProposal, StockHistory, SubstitutionApproval,
    SOSBroadcast, SOSResponse
)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Initialize MediBridge database")
    parser.add_argument(
        "--db-url",
        type=str,
        help="Database URL (default: MEDIBRIDGE_DB_URL env or sqlite:///medibridge.db)",
    )
    parser.add_argument(
        "--no-seed",
        action="store_true",
        help="Skip seeding demo data",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop all tables before creating (requires --yes)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm destructive operations",
    )
    args = parser.parse_args()

    # Safety check for reset
    if args.reset and not args.yes:
        print("ERROR: --reset requires --yes to confirm data loss")
        sys.exit(1)

    # Prevent accidental deletion of non-SQLite databases
    if args.reset and args.db_url:
        if not args.db_url.startswith("sqlite://"):
            print(f"ERROR: --reset is only allowed for SQLite URLs")
            print(f"       Refusing to drop tables from: {args.db_url}")
            sys.exit(1)

    # Create engine
    engine = create_db_engine(database_url=args.db_url)

    print(f"Database: {engine.url}")

    # Reset if requested
    if args.reset:
        print("\n[RESET] Dropping all tables...")
        Base.metadata.drop_all(bind=engine)
        print("[OK] All tables dropped")

    # Create tables
    print("\nCreating tables...")
    Base.metadata.create_all(bind=engine)
    print("[OK] Tables created")

    # Seed data
    if not args.no_seed:
        print("\nSeeding demo data...")
        # Create session from the same engine we just created tables on
        from sqlalchemy.orm import sessionmaker, Session
        SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
        session = SessionLocal()
        try:
            counts = seed_database(session)
            session.commit()

            if "note" in counts:
                print(f"[OK] {counts['note']}")
            else:
                print("[OK] Seed completed")
                print("\nRow counts:")
                for table, count in sorted(counts.items()):
                    print(f"  {table:25} {count:4}")
        finally:
            session.close()
    else:
        print("\n[INFO] Skipping seed (--no-seed)")

    print("\n[OK] Database initialization complete")


if __name__ == "__main__":
    main()
