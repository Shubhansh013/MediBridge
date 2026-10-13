"""
Tests for database persistence, WAL mode, and transaction behavior.
"""

import pytest
import time
from pathlib import Path
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import sessionmaker

from medibridge_db.database import Base, create_db_engine, get_session
from medibridge_db.models import Pharmacy, User, UserRole, PharmacyVerification, Medicine


class TestPersistence:
    def test_file_database_persistence(self, tmp_path):
        """Data written to file database should persist after engine shutdown."""
        db_file = tmp_path / "test_persist.db"
        db_url = f"sqlite:///{db_file}"

        # Create engine, write data, close
        engine1 = create_db_engine(database_url=db_url)
        Base.metadata.create_all(bind=engine1)

        SessionLocal = sessionmaker(bind=engine1, expire_on_commit=False)
        session1 = SessionLocal()

        pharmacy = Pharmacy(
            name="Test Pharmacy",
            locality="Test Area",
            address="123 Test St",
            latitude=28.5,
            longitude=77.2,
            telegram_chat_id=999999,
            verification_state=PharmacyVerification.verified,
        )
        session1.add(pharmacy)
        session1.commit()
        pharmacy_id = pharmacy.id
        session1.close()
        engine1.dispose()

        # Reopen database, verify data exists
        engine2 = create_db_engine(database_url=db_url)
        SessionLocal2 = sessionmaker(bind=engine2, expire_on_commit=False)
        session2 = SessionLocal2()

        reloaded = session2.get(Pharmacy, pharmacy_id)
        assert reloaded is not None
        assert reloaded.name == "Test Pharmacy"
        assert reloaded.telegram_chat_id == 999999

        session2.close()
        engine2.dispose()

    def test_wal_mode_enabled(self, tmp_path):
        """SQLite should be in WAL (Write-Ahead Logging) mode."""
        db_file = tmp_path / "test_wal.db"
        db_url = f"sqlite:///{db_file}"

        engine = create_db_engine(database_url=db_url)
        Base.metadata.create_all(bind=engine)

        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA journal_mode")).scalar()
            assert result.lower() == "wal"

        engine.dispose()

    def test_foreign_keys_enforced(self, tmp_path):
        """Foreign key constraints should be enforced."""
        db_file = tmp_path / "test_fk.db"
        db_url = f"sqlite:///{db_file}"

        engine = create_db_engine(database_url=db_url)
        Base.metadata.create_all(bind=engine)

        SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
        session = SessionLocal()

        # Try to create a user with invalid pharmacy FK
        from medibridge_db.models import UserPharmacy, StaffRoleAtPharmacy

        invalid_link = UserPharmacy(
            user_id=99999,  # Non-existent user
            pharmacy_id=99999,  # Non-existent pharmacy
            role_at_pharmacy=StaffRoleAtPharmacy.chemist,
        )
        session.add(invalid_link)

        with pytest.raises(Exception):  # IntegrityError from FK violation
            session.commit()

        session.rollback()
        session.close()
        engine.dispose()

    def test_rollback_on_error(self, tmp_path):
        """Session should rollback on error, leaving DB unchanged."""
        db_file = tmp_path / "test_rollback.db"
        db_url = f"sqlite:///{db_file}"

        engine = create_db_engine(database_url=db_url)
        Base.metadata.create_all(bind=engine)

        SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
        session = SessionLocal()

        # Add valid pharmacy
        pharmacy1 = Pharmacy(
            name="Valid Pharmacy",
            locality="Test Area",
            address="123 Test St",
            latitude=28.5,
            longitude=77.2,
            telegram_chat_id=111111,
            verification_state=PharmacyVerification.verified,
        )
        session.add(pharmacy1)
        session.commit()

        # Try to add pharmacy with duplicate telegram_chat_id
        pharmacy2 = Pharmacy(
            name="Duplicate Chat ID",
            locality="Test Area",
            address="456 Test St",
            latitude=28.6,
            longitude=77.3,
            telegram_chat_id=111111,  # Duplicate!
            verification_state=PharmacyVerification.verified,
        )
        session.add(pharmacy2)

        with pytest.raises(Exception):  # IntegrityError
            session.commit()

        session.rollback()

        # Only the first pharmacy should exist
        count = session.execute(select(Pharmacy)).scalars().all()
        assert len(count) == 1
        assert count[0].name == "Valid Pharmacy"

        session.close()
        engine.dispose()

    def test_concurrent_readers_during_write(self, tmp_path):
        """Readers should be able to access database while a write is in progress (WAL mode)."""
        db_file = tmp_path / "test_concurrent.db"
        db_url = f"sqlite:///{db_file}"

        # Create and seed database
        engine = create_db_engine(database_url=db_url)
        Base.metadata.create_all(bind=engine)

        SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
        session = SessionLocal()

        pharmacy = Pharmacy(
            name="Concurrent Test Pharmacy",
            locality="Test Area",
            address="123 Test St",
            latitude=28.5,
            longitude=77.2,
            telegram_chat_id=222222,
            verification_state=PharmacyVerification.verified,
        )
        session.add(pharmacy)
        session.commit()
        session.close()

        # Open writer session (uncommitted transaction)
        writer_session = SessionLocal()
        writer_session.begin()
        new_pharmacy = Pharmacy(
            name="New Pharmacy",
            locality="Test Area",
            address="456 Test St",
            latitude=28.6,
            longitude=77.3,
            telegram_chat_id=333333,
            verification_state=PharmacyVerification.verified,
        )
        writer_session.add(new_pharmacy)
        writer_session.flush()  # Write to DB but don't commit

        # Open reader session - should still see old data
        reader_session = SessionLocal()
        pharmacies = reader_session.execute(select(Pharmacy)).scalars().all()

        # Reader should see only the original pharmacy (WAL isolation)
        assert len(pharmacies) == 1
        assert pharmacies[0].name == "Concurrent Test Pharmacy"

        reader_session.close()

        # Now commit writer
        writer_session.commit()
        writer_session.close()

        # New reader should see both pharmacies
        final_session = SessionLocal()
        pharmacies = final_session.execute(select(Pharmacy)).scalars().all()
        assert len(pharmacies) == 2
        final_session.close()

        engine.dispose()

    def test_busy_timeout_configured(self, tmp_path):
        """Busy timeout should be configured to prevent immediate locking errors."""
        db_file = tmp_path / "test_timeout.db"
        db_url = f"sqlite:///{db_file}"

        engine = create_db_engine(database_url=db_url)
        Base.metadata.create_all(bind=engine)

        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA busy_timeout")).scalar()
            assert result >= 5000  # 5 seconds minimum

        engine.dispose()


class TestQueryPerformance:
    def test_inventory_query_timing(self, test_session):
        """Time common inventory queries (no assertion, just measurement)."""
        from medibridge_db.seed import seed_database
        from medibridge_db.operations import get_pharmacy_inventory

        seed_database(test_session)
        test_session.commit()

        # Get first pharmacy
        pharmacy = test_session.execute(select(Pharmacy)).scalars().first()

        start = time.perf_counter()
        inventory = get_pharmacy_inventory(
            test_session,
            pharmacy_id=pharmacy.id,
            include_stale=True,
            status_filter=None,
            include_inactive=False,
        )
        elapsed = time.perf_counter() - start

        print(f"\nget_pharmacy_inventory: {elapsed*1000:.2f}ms for {len(inventory)} items")

    def test_substitute_lookup_timing(self, test_session):
        """Time substitute medicine lookup (no assertion, just measurement)."""
        from medibridge_db.seed import seed_database
        from medibridge_db.operations import get_substitutes_for_medicine

        seed_database(test_session)
        test_session.commit()

        # Find Dolo 650
        dolo = test_session.execute(
            select(Medicine).where(Medicine.brand_name == "Dolo 650")
        ).scalar_one()

        start = time.perf_counter()
        substitutes = get_substitutes_for_medicine(
            test_session,
            medicine_id=dolo.id,
            pharmacy_id=None,
            include_inactive=False,
        )
        elapsed = time.perf_counter() - start

        print(f"\nget_substitutes_for_medicine: {elapsed*1000:.2f}ms for {len(substitutes)} substitutes")

    def test_nearby_pharmacy_query_timing(self, test_session):
        """Time nearby pharmacy search (no assertion, just measurement)."""
        from medibridge_db.seed import seed_database
        from medibridge_db.operations import get_pharmacies_nearby

        seed_database(test_session)
        test_session.commit()

        # Saket coordinates
        lat, lon = 28.5244, 77.2066

        start = time.perf_counter()
        pharmacies = get_pharmacies_nearby(
            test_session,
            lat=lat,
            lon=lon,
            radius_km=5.0,
            limit=10,
        )
        elapsed = time.perf_counter() - start

        print(f"\nget_pharmacies_nearby: {elapsed*1000:.2f}ms for {len(pharmacies)} pharmacies")
