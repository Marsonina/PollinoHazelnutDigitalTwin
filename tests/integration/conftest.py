"""Fixtures for integration tests.

Tests are skipped automatically when PostgreSQL is not reachable.
Default URL matches docker-compose.yml; override with DATABASE_URL env var.
"""

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

_DEFAULT_URL = "postgresql+psycopg://pollino:pollino_dev@localhost:5432/pollino"


@pytest.fixture(scope="session")
def db_engine():
    url = os.environ.get("DATABASE_URL", _DEFAULT_URL)
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL not reachable — skipping integration tests: {exc}")
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """Session wrapped in a transaction that is always rolled back.

    Uses join_transaction_mode='create_savepoint' so that any session.flush()
    or session.begin_nested() calls inside a test create SAVEPOINTs rather than
    real sub-transactions, keeping everything within the outer rollback boundary.
    """
    conn = db_engine.connect()
    trans = conn.begin()
    session = Session(conn, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    trans.rollback()
    conn.close()
