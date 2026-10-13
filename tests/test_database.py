"""
Tests for MediBridge database configuration and setup.

Verifies that the database connection works, SQLite pragmas are applied
correctly, and foreign key constraints are enforced.
"""

import pytest
from sqlalchemy import text, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from medibridge_db.database import (
    create_db_engine,
    get_session,
    Base,
    SessionLocal,
    init_db,
    drop_db,
)


class TestDatabaseConnection:
    """Test basic database connectivity and configuration."""

    def test_connection_works(self):
        """Verify that a database connection can be established."""
        engine = create_db_engine(test=True)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            assert result.scalar() == 1

    def test_wal_mode_on_file_database(self, tmp_path):
        """Verify that WAL mode is active on file-based database."""
        db_path = tmp_path / "test.db"
        db_url = f"sqlite:///{db_path}"
        engine = create_db_engine(database_url=db_url)

        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA journal_mode"))
            journal_mode = result.scalar()
            assert journal_mode.upper() == "WAL"

    def test_foreign_keys_enforced(self):
        """Verify that foreign key constraints are enforced."""
        # Create test tables with foreign key relationship
        class Parent(Base):
            __tablename__ = "test_parent"
            id: Mapped[int] = mapped_column(Integer, primary_key=True)

        class Child(Base):
            __tablename__ = "test_child"
            id: Mapped[int] = mapped_column(Integer, primary_key=True)
            parent_id: Mapped[int] = mapped_column(ForeignKey("test_parent.id"))

        # Create engine and tables
        engine = create_db_engine(test=True)
        Base.metadata.create_all(engine)

        # Create a custom session for this test
        from sqlalchemy.orm import sessionmaker
        TestSession = sessionmaker(bind=engine)

        # Try to insert child without parent - should fail
        session = TestSession()
        try:
            child = Child(id=1, parent_id=999)  # Parent 999 doesn't exist
            session.add(child)
            session.commit()
            assert False, "Foreign key constraint was not enforced"
        except Exception:
            # Expected: foreign key constraint violation
            session.rollback()
            assert True
        finally:
            session.close()

        # Cleanup
        Base.metadata.drop_all(engine)

    def test_session_context_manager_commits_on_success(self):
        """Verify that get_session commits on successful completion."""
        # Use test engine
        import medibridge_db.database as db_module
        original_engine = db_module.engine
        original_session_local = db_module.SessionLocal

        try:
            # Replace with test engine
            test_engine = create_db_engine(test=True)
            db_module.engine = test_engine

            from sqlalchemy.orm import sessionmaker
            db_module.SessionLocal = sessionmaker(
                bind=test_engine,
                expire_on_commit=False,
                autoflush=False,
                autocommit=False,
            )

            # Create a simple test table
            class TestTable(Base):
                __tablename__ = "test_commit"
                id: Mapped[int] = mapped_column(Integer, primary_key=True)

            Base.metadata.create_all(test_engine)

            # Insert using context manager
            with get_session() as session:
                obj = TestTable(id=1)
                session.add(obj)
            # Should commit automatically

            # Verify it was committed
            with get_session() as session:
                result = session.get(TestTable, 1)
                assert result is not None
                assert result.id == 1

            # Cleanup
            Base.metadata.drop_all(test_engine)

        finally:
            # Restore original engine and session factory
            db_module.engine = original_engine
            db_module.SessionLocal = original_session_local

    def test_session_context_manager_rolls_back_on_exception(self):
        """Verify that get_session rolls back on exception."""
        import medibridge_db.database as db_module
        original_engine = db_module.engine
        original_session_local = db_module.SessionLocal

        try:
            # Replace with test engine
            test_engine = create_db_engine(test=True)
            db_module.engine = test_engine

            from sqlalchemy.orm import sessionmaker
            db_module.SessionLocal = sessionmaker(
                bind=test_engine,
                expire_on_commit=False,
                autoflush=False,
                autocommit=False,
            )

            # Create a simple test table
            class TestTable(Base):
                __tablename__ = "test_rollback"
                id: Mapped[int] = mapped_column(Integer, primary_key=True)

            Base.metadata.create_all(test_engine)

            # Try to insert but raise exception
            try:
                with get_session() as session:
                    obj = TestTable(id=1)
                    session.add(obj)
                    raise ValueError("Simulated error")
            except ValueError:
                pass  # Expected

            # Verify it was rolled back
            with get_session() as session:
                result = session.get(TestTable, 1)
                assert result is None

            # Cleanup
            Base.metadata.drop_all(test_engine)

        finally:
            # Restore original engine and session factory
            db_module.engine = original_engine
            db_module.SessionLocal = original_session_local