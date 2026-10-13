"""Database connection, engine configuration, and session factory for MediBridge.

Configures SQLite with:
- PRAGMA foreign_keys = ON (ensures referential integrity)
- PRAGMA journal_mode = WAL (Write-Ahead Logging for concurrency)
- PRAGMA busy_timeout = 5000 (prevents lock contention errors)
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from medibridge.database.models import Base


def configure_sqlite_pragmas(engine: Engine) -> None:
    """Attaches a connection listener to enforce SQLite WAL mode and foreign keys."""
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


def create_sqlite_engine(db_url: str = "sqlite:///:memory:") -> Engine:
    """Creates a configured SQLAlchemy engine with SQLite pragmas enabled."""
    engine = create_engine(
        db_url,
        echo=False,
        future=True,
    )
    if db_url.startswith("sqlite"):
        configure_sqlite_pragmas(engine)
    return engine


def init_db(engine: Engine) -> None:
    """Creates all defined database tables."""
    Base.metadata.create_all(engine)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Returns a thread-safe session factory bound to the provided engine."""
    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )


@contextmanager
def get_session(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    """Context manager yielding a transactional session with automatic rollback on error."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
