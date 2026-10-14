import datetime
import os
import sqlite3
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from medibridge_db.database import Base, create_db_engine, utc_now
from medibridge_db.models import (
    User,
    UserRole,
    HistoryChangeType,
    Inventory,
    Medicine,
    Pharmacy,
    StockHistory,
    StockStatus,
    UpdateSource,
)
from medibridge_db.operations import (
    find_medicine_by_alias,
    get_substitutes_for_medicine,
    search_medicines,
)
from medibridge_db.seed import seed_database

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "medibridge.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

_engine = None
_SessionFactory = None


def get_engine():
    global _engine, _SessionFactory
    if _engine is None:
        _engine = create_db_engine(DATABASE_URL)
        _SessionFactory = sessionmaker(bind=_engine, class_=Session, expire_on_commit=False)
    return _engine


def get_session() -> Session:
    get_engine()
    return _SessionFactory()


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")  # Enable WAL mode for high concurrency
    return conn


def init_db():
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    with get_session() as session:
        count = session.query(Pharmacy).count()
        if count == 0:
            seed_database(session)
            session.commit()


def search_medicine(query: str) -> List[Dict[str, Any]]:
    init_db()
    results: List[Dict[str, Any]] = []
    seen_keys = set()

    with get_session() as session:
        meds = search_medicines(session, query, limit=10)

        # If no medicines found directly by brand/generic, try alias
        if not meds:
            alias_med = find_medicine_by_alias(session, query)
            if alias_med:
                meds = [alias_med]

        for med in meds:
            inv_items = session.query(Inventory).filter_by(medicine_id=med.id).all()
            for inv in inv_items:
                pharm = inv.pharmacy
                if not pharm or not pharm.is_active:
                    continue
                key = (pharm.id, med.id)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                ts_str = inv.last_confirmed_at.strftime("%I:%M %p") if inv.last_confirmed_at else "Recently"
                is_avail = inv.stock_status in (StockStatus.in_stock, StockStatus.low_stock)

                results.append({
                    "pharmacy_name": pharm.name,
                    "locality": pharm.locality,
                    "lat": pharm.latitude,
                    "lng": pharm.longitude,
                    "medicine_name": med.brand_name,
                    "salt": med.generic_name,
                    "is_available": is_avail,
                    "last_updated": ts_str,
                    "is_substitute": False,
                    "substitute_note": None,
                })

            # Look up substitutes for this medicine
            substitutes = get_substitutes_for_medicine(session, med.id, None)
            for sub in substitutes:
                sub_invs = session.query(Inventory).filter_by(medicine_id=sub.id).all()
                for inv in sub_invs:
                    pharm = inv.pharmacy
                    if not pharm or not pharm.is_active:
                        continue
                    key = (pharm.id, sub.id)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)

                    if inv.stock_status in (StockStatus.in_stock, StockStatus.low_stock):
                        ts_str = inv.last_confirmed_at.strftime("%I:%M %p") if inv.last_confirmed_at else "Recently"
                        results.append({
                            "pharmacy_name": pharm.name,
                            "locality": pharm.locality,
                            "lat": pharm.latitude,
                            "lng": pharm.longitude,
                            "medicine_name": sub.brand_name,
                            "salt": sub.generic_name,
                            "is_available": True,
                            "last_updated": ts_str,
                            "is_substitute": True,
                            "substitute_note": f"Alternative with identical {sub.generic_name} formulation. Confirm at counter.",
                        })

    return results


def update_stock(pharmacy_id: int, medicine_name: str, is_available: bool) -> bool:
    init_db()
    with get_session() as session:
        # Find medicine by exact brand or alias
        med = find_medicine_by_alias(session, medicine_name)
        if not med:
            meds = search_medicines(session, medicine_name, limit=1)
            if meds:
                med = meds[0]

        if not med:
            return False

        inv = session.query(Inventory).filter_by(pharmacy_id=pharmacy_id, medicine_id=med.id).first()
        new_status = StockStatus.in_stock if is_available else StockStatus.out_of_stock
        prev_status = inv.stock_status if inv else None
        prev_qty = inv.quantity if inv else 0
        now = utc_now()

        if inv:
            inv.stock_status = new_status
            inv.last_confirmed_at = now
            inv.last_update_source = UpdateSource.chemist
            if is_available and (inv.quantity is None or inv.quantity == 0):
                inv.quantity = 10
            elif not is_available:
                inv.quantity = 0
        else:
            inv = Inventory(
                pharmacy_id=pharmacy_id,
                medicine_id=med.id,
                stock_status=new_status,
                quantity=10 if is_available else 0,
                last_confirmed_at=now,
                last_update_source=UpdateSource.chemist,
            )
            session.add(inv)
            session.flush()

        # Append StockHistory audit trail
        history = StockHistory(
            inventory_id=inv.id,
            pharmacy_id=pharmacy_id,
            medicine_id=med.id,
            changed_by=session.query(User).filter_by(role=UserRole.chemist).first().id if session.query(User).filter_by(role=UserRole.chemist).first() else 1,
            change_type=HistoryChangeType.stock_update,
            previous_stock_status=prev_status,
            new_stock_status=new_status,
            previous_quantity=prev_qty,
            new_quantity=inv.quantity,
            change_reason="Updated via quick action",
            created_at=now,
        )
        session.add(history)
        session.commit()
        return True


# Automatically initialize database when imported
init_db()
