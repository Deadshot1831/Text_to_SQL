"""Pytest fixtures: seed a throwaway DuckDB so tests never touch committed data."""
import os
import pathlib
import tempfile

import pytest


@pytest.fixture(scope="session", autouse=True)
def _seed_db():
    tmp = pathlib.Path(tempfile.mkdtemp()) / "test.duckdb"
    os.environ["DATABASE_URL"] = f"duckdb:///{tmp}"
    os.environ["LLM_PROVIDER"] = "stub"  # force offline determinism in tests

    from app.config import get_settings
    from app import db as dbmod

    get_settings.cache_clear()
    dbmod.get_engine.cache_clear()
    dbmod.seed_local()
    yield
