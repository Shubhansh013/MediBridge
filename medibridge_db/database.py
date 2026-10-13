"""
Database engine, session factory, and base class for MediBridge.

Configures SQLite with WAL mode, foreign keys enforcement, and appropriate
pragmas for concurrent access. Provides a session context manager that
commits on success and rolls back on exception.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

from sqlalchemy import create_engine, event, Engine, TypeDecorator, DateTime
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

from .config import get_database_url, get_test_database_url


class UTCDateTime(TypeDecorator):
    """
    Custom TypeDecorator for timezone-aware UTC datetimes.

    Stores as naive UTC datetime in SQLite. Rejects naive datetimes on write.
    Always returns timezone-aware UTC datetime on read.
    """

    impl = DateTime(timezone=False)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetime is not allowed; use timezone-aware UTC")
        # Convert to UTC if not already
        if value.tzinfo != timezone.utc:
            value = value.astimezone(timezone.utc)
        # Return naive UTC datetime for storage in DateTime(timezone=False)
        return value.replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        # SQLite returns naive datetime; assume it's UTC and make it aware
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def utc_now() -> datetime:
    """
    Get current time in UTC with timezone info.

    Returns:
        Timezone-aware UTC datetime.
    """
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Base class for all MediBridge database models."""
    pass


def _apply_sqlite_pragmas(dbapi_connection, connection_record) -> None:
    """
    Apply SQLite pragmas on every new connection.

    Pragmas:
    - journal_mode=WAL: Write-Ahead Logging for better concurrency
    - foreign_keys=ON: Enforce foreign key constraints
    - busy_timeout=5000: Wait up to 5 seconds for locked database
    - synchronous=NORMAL: Balance between safety and performance
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def create_db_engine(database_url: str | None = None, *, test: bool = False) -> Engine:
    """
    Create a SQLAlchemy engine with SQLite pragmas configured.

    Args:
        database_url: Optional database URL. If None, uses default from config.
        test: If True, uses in-memory SQLite for testing.

    Returns:
        Configured SQLAlchemy Engine instance.
    """
    if test:
        url = get_test_database_url()
    elif database_url is None:
        url = get_database_url()
    else:
        url = database_url

    engine = create_engine(
        url,
        echo=False,  # Set to True for SQL debugging
        future=True,
    )

    # Apply pragmas on every connection
    event.listen(engine, "connect", _apply_sqlite_pragmas)

    return engine


# Default engine (file-based SQLite)
engine = create_db_engine()

# Session factory
SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    Context manager for database sessions.

    Yields a session that commits on successful exit and rolls back
    on any exception. The session is always closed.

    Usage:
        with get_session() as session:
            session.add(obj)
            # Commits automatically on success

    Yields:
        SQLAlchemy Session instance.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """
    Initialize the database by creating all tables.

    Call this once at application startup to create the schema.
    Models must be imported before calling this.
    """
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)


def drop_db() -> None:
    """
    Drop all tables from the database.

    Useful for testing or resetting the database.
    """
    Base.metadata.drop_all(bind=engine)