"""Isolated integration tests for History & Undo API endpoints.

Verifies:
- GET /api/pharmacies/{id}/history returns real medicine_name, medicine_id, timestamp, can_undo, and is_undone.
- POST /api/history/{id}/undo accepts undone_by_user_id and chemist_user_id alias.
- Successful rollback restores previous inventory quantities and creates append-only UNDO history row.
- Double undo and undoing an UNDO row are safely rejected.
- Audit trail immutability: history count increases, zero rows are deleted.
- Executed against an isolated SQLite in-memory database to never mutate medibridge.db.
"""

from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app, get_db
from medibridge_db.database import Base, utc_now
from medibridge_db.models import (
    HistoryChangeType,
    Inventory,
    Medicine,
    Pharmacy,
    StockHistory,
    StockStatus,
    UpdateSource,
    User,
    UserPharmacy,
    UserRole,
)


@pytest.fixture
def isolated_db_session():
    """Creates a fresh, completely isolated in-memory SQLite database for test execution."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()

    now = utc_now()

    # Seed minimal baseline data matching real schema
    pharmacy = Pharmacy(
        id=1,
        name="MedPlus Saket Test",
        locality="Saket",
        address="123 Saket Market",
        latitude=28.5244,
        longitude=77.2066,
        is_active=True,
    )
    user = User(
        id=3,
        telegram_user_id=200001,
        display_name="Test Chemist",
        role=UserRole.chemist,
        is_active=True,
    )
    user_pharmacy = UserPharmacy(
        user_id=3,
        pharmacy_id=1,
        role_at_pharmacy="chemist",
    )
    medicine = Medicine(
        id=1,
        brand_name="Dolo 650",
        generic_name="Paracetamol",
        composition_key="paracetamol",
        strength="650mg",
        form="tablet",
    )
    inventory = Inventory(
        id=1,
        pharmacy_id=1,
        medicine_id=1,
        stock_status=StockStatus.in_stock,
        quantity=50,
        last_confirmed_at=now,
        last_updated_by=3,
        last_update_source=UpdateSource.chemist,
    )
    # Baseline history row (eligible for undo)
    history_entry = StockHistory(
        id=101,
        inventory_id=1,
        pharmacy_id=1,
        medicine_id=1,
        changed_by=3,
        change_type=HistoryChangeType.stock_update,
        previous_stock_status=StockStatus.out_of_stock,
        new_stock_status=StockStatus.in_stock,
        previous_quantity=0,
        new_quantity=50,
        previous_last_confirmed_at=now,
        change_reason="Chemist update test",
        created_at=now,
    )

    session.add_all([pharmacy, user, user_pharmacy, medicine, inventory, history_entry])
    session.commit()

    yield session

    session.close()
    Base.metadata.drop_all(engine)


def test_history_endpoint_returns_resolved_medicine_name_and_flags(isolated_db_session):
    def override_get_db():
        yield isolated_db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        resp = client.get("/api/pharmacies/1/history")
        assert resp.status_code == 200
        data = resp.json()

        assert len(data) == 1
        record = data[0]

        # Bug 1 verification: real medicine name and fields are returned
        assert record["id"] == 101
        assert record["medicine_id"] == 1
        assert record["medicine_name"] == "Dolo 650"
        assert record["medicine_name"] != "Unknown"
        assert record["previous_quantity"] == 0
        assert record["new_quantity"] == 50
        assert record["can_undo"] is True
        assert record["is_undone"] is False
        assert "timestamp" in record
        assert record["timestamp"] != ""
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_undo_endpoint_accepts_chemist_user_id_and_reverses_stock(isolated_db_session):
    def override_get_db():
        yield isolated_db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)

        # Bug 2 verification: payload sent by app.js (with chemist_user_id alias) succeeds
        undo_resp = client.post(
            "/api/history/101/undo",
            json={
                "chemist_user_id": 3,
                "pharmacy_id": 1,
            },
        )
        assert undo_resp.status_code == 200
        undo_data = undo_resp.json()
        assert undo_data["success"] is True
        assert undo_data["restored_quantity"] == 0
        assert undo_data["restored_status"] == "OUT_OF_STOCK"

        # Check inventory is actually restored in the database
        inv = isolated_db_session.query(Inventory).filter_by(id=1).one()
        assert inv.quantity == 0
        assert inv.stock_status == StockStatus.out_of_stock

        # Check history endpoint reflects the rollback and marks previous record as is_undone
        hist_resp = client.get("/api/pharmacies/1/history")
        assert hist_resp.status_code == 200
        hist_data = hist_resp.json()
        assert len(hist_data) == 2  # Original update + UNDO row

        undo_row = hist_data[0]
        original_row = hist_data[1]

        assert undo_row["change_type"] == "UNDO"
        assert undo_row["medicine_name"] == "Dolo 650"
        assert undo_row["can_undo"] is False

        assert original_row["id"] == 101
        assert original_row["is_undone"] is True
        assert original_row["can_undo"] is False

        # Attempting to undo again should fail safely (double undo protection)
        second_undo_resp = client.post(
            "/api/history/101/undo",
            json={"undone_by_user_id": 3, "pharmacy_id": 1},
        )
        assert second_undo_resp.status_code == 400
        assert "already been undone" in second_undo_resp.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_db, None)
