"""
Database configuration for MediBridge.

Provides the database URL from environment variable MEDIBRIDGE_DB_URL,
with a default SQLite file in the project root.
"""

import os
from pathlib import Path


def get_database_url() -> str:
    """
    Get the database URL from environment or use default.

    Returns:
        Database URL string for SQLAlchemy engine creation.
    """
    env_url = os.getenv("MEDIBRIDGE_DB_URL")
    if env_url:
        return env_url

    # Default: SQLite file in project root
    project_root = Path(__file__).parent.parent
    db_path = project_root / "medibridge.db"
    return f"sqlite:///{db_path}"


def get_test_database_url() -> str:
    """
    Get a test database URL (in-memory SQLite).

    Returns:
        In-memory SQLite URL for testing.
    """
    return "sqlite:///:memory:"


def get_stale_threshold_hours() -> int:
    """
    Get the stale inventory threshold in hours.

    Returns:
        Hours after which inventory is considered stale.
        Default: 24 hours.
    """
    return int(os.getenv("MEDIBRIDGE_STALE_THRESHOLD_HOURS", "24"))