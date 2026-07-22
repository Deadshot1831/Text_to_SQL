"""Database engine and schema-loading helpers (engine-agnostic via SQLAlchemy)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import sqlparse
from sqlalchemy import Engine, create_engine, text

from app.config import get_settings

INIT_DIR = Path(__file__).resolve().parent.parent / "db" / "init"


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    if url.startswith("duckdb://"):
        # Ensure the parent directory for the DuckDB file exists.
        path = url.split("///", 1)[-1]
        if path and path not in (":memory:",):
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    return create_engine(url)


def dialect_name(engine: Engine | None = None) -> str:
    return (engine or get_engine()).dialect.name


def run_sql_script(engine: Engine, sql: str) -> None:
    """Execute a multi-statement SQL script (used for local seeding)."""
    with engine.begin() as conn:
        for statement in sqlparse.split(sql):
            stmt = statement.strip()
            if stmt:
                conn.exec_driver_sql(stmt)


def seed_local(engine: Engine | None = None) -> None:
    """Load schema + seed into the configured DB (DuckDB local dev).

    Skips 03_grants.sql — that is PostgreSQL-only and applied by the
    docker-entrypoint init scripts, not here.
    """
    engine = engine or get_engine()
    for name in ("01_schema.sql", "02_seed.sql"):
        run_sql_script(engine, (INIT_DIR / name).read_text())


def table_names(engine: Engine | None = None) -> list[str]:
    from sqlalchemy import inspect

    return sorted(inspect(engine or get_engine()).get_table_names())


if __name__ == "__main__":  # `python -m app.db` re-seeds the local database.
    eng = get_engine()
    existing = set(table_names(eng))
    if existing:
        with eng.begin() as conn:
            for t in ("order_items", "orders", "customers", "products", "categories"):
                conn.exec_driver_sql(f"DROP TABLE IF EXISTS {t}")
    seed_local(eng)
    with eng.connect() as conn:
        n = conn.execute(text("SELECT count(*) FROM orders")).scalar()
    print(f"Seeded {dialect_name(eng)} at {get_settings().database_url} — orders={n}")
