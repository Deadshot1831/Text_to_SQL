"""Phase 1: schema introspection, relevance filtering, and prompt formatting.

Produces a structured, engine-agnostic snapshot of the database (tables,
columns, keys, and sample values for categorical columns) that becomes the
context the LLM uses to write SQL.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import Engine, inspect, text

from app.db import get_engine

# String/boolean columns with at most this many distinct values are treated as
# categorical and sampled, so the model can disambiguate literals (e.g. status).
CATEGORICAL_MAX_CARD = 25
SMALL_SCHEMA_TABLES = 8  # at/under this many tables, skip filtering entirely

# Optional business glossary: table -> one-line description.
TABLE_DESCRIPTIONS = {
    "categories": "product categories",
    "products": "catalog of products with list price and unit cost",
    "customers": "registered customers and their country",
    "orders": "customer orders with status and order date",
    "order_items": "line items: which products/quantities are on each order",
}


@dataclass
class Column:
    name: str
    type: str
    nullable: bool
    is_pk: bool = False
    fk_ref: str | None = None  # "referred_table.referred_column"
    samples: list = field(default_factory=list)


@dataclass
class Table:
    name: str
    columns: list[Column]
    description: str | None = None

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]


@dataclass
class SchemaSnapshot:
    tables: dict[str, Table]

    def format_for_prompt(self, only: list[str] | None = None) -> str:
        names = only or list(self.tables)
        blocks: list[str] = []
        for name in names:
            t = self.tables.get(name)
            if not t:
                continue
            header = f"Table: {t.name}"
            if t.description:
                header += f"  -- {t.description}"
            lines = [header]
            for c in t.columns:
                tags = []
                if c.is_pk:
                    tags.append("PK")
                if c.fk_ref:
                    tags.append(f"FK -> {c.fk_ref}")
                tag = f" [{', '.join(tags)}]" if tags else ""
                sample = ""
                if c.samples:
                    shown = ", ".join(str(s) for s in c.samples[:8])
                    sample = f"  -- e.g. {shown}"
                lines.append(f"  {c.name} {c.type}{tag}{sample}")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def to_dict(self) -> dict:
        """Serializable schema for the GET /v1/schema endpoint."""
        return {
            "tables": [
                {
                    "name": t.name,
                    "description": t.description,
                    "columns": [
                        {
                            "name": c.name,
                            "type": c.type,
                            "nullable": c.nullable,
                            "primary_key": c.is_pk,
                            "foreign_key": c.fk_ref,
                            "sample_values": [str(s) for s in c.samples],
                        }
                        for c in t.columns
                    ],
                }
                for t in self.tables.values()
            ],
            "foreign_keys": self.foreign_keys(),
        }

    def foreign_keys(self, only: list[str] | None = None) -> list[str]:
        names = set(only) if only else set(self.tables)
        out = []
        for t in self.tables.values():
            if t.name not in names:
                continue
            for c in t.columns:
                if c.fk_ref and (only is None or c.fk_ref.split(".")[0] in names):
                    out.append(f"{t.name}.{c.name} -> {c.fk_ref}")
        return out

    def relevant_tables(self, question: str, min_tables: int = 3) -> list[str]:
        """Lightweight lexical relevance filter (Phase 1.3).

        Scores each table by keyword overlap between the question and the
        table's name / columns / sample values / description, then expands the
        selection along foreign keys so JOINs stay possible. For schemas at or
        under SMALL_SCHEMA_TABLES it returns everything (filtering isn't worth
        the risk of dropping a needed table).

        ponytail: lexical overlap, not embeddings. Swap in vector similarity
        here if the schema grows past a few dozen tables.
        """
        if len(self.tables) <= SMALL_SCHEMA_TABLES:
            return list(self.tables)

        q_tokens = _tokens(question)
        scored: list[tuple[float, str]] = []
        for t in self.tables.values():
            bag = _tokens(t.name) | _tokens(t.description or "")
            for c in t.columns:
                bag |= _tokens(c.name)
                for s in c.samples:
                    bag |= _tokens(str(s))
            scored.append((len(q_tokens & bag), t.name))
        scored.sort(reverse=True)

        chosen = {name for score, name in scored if score > 0}
        for _, name in scored[:min_tables]:
            chosen.add(name)
        # Expand along FKs so selected tables remain joinable.
        for name in list(chosen):
            for c in self.tables[name].columns:
                if c.fk_ref:
                    chosen.add(c.fk_ref.split(".")[0])
        return [n for n in self.tables if n in chosen]


def _tokens(s: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", s.lower()) if len(w) > 2}


def _pk_columns(engine: Engine) -> dict[str, list[str]]:
    """Cross-engine PK lookup (DuckDB's dialect doesn't report PKs)."""
    sql = text(
        """
        SELECT tc.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_name = kcu.table_name
        WHERE tc.constraint_type = 'PRIMARY KEY'
        """
    )
    out: dict[str, list[str]] = {}
    with engine.connect() as c:
        for tname, col in c.execute(sql):
            out.setdefault(tname, []).append(col)
    return out


def _sample_values(engine: Engine, table: str, col: str, ctype: str) -> list:
    t = ctype.upper()
    if not any(k in t for k in ("CHAR", "TEXT", "BOOL", "STRING")):
        return []
    with engine.connect() as c:
        n = c.execute(text(f'SELECT COUNT(DISTINCT "{col}") FROM "{table}"')).scalar()
        if n is None or n > CATEGORICAL_MAX_CARD:
            return []
        rows = c.execute(
            text(f'SELECT DISTINCT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL ORDER BY 1')
        ).fetchall()
    return [r[0] for r in rows]


def introspect(engine: Engine | None = None) -> SchemaSnapshot:
    engine = engine or get_engine()
    insp = inspect(engine)
    pk_fallback = _pk_columns(engine)

    tables: dict[str, Table] = {}
    for tname in insp.get_table_names():
        pk_cols = insp.get_pk_constraint(tname).get("constrained_columns") or pk_fallback.get(tname, [])
        fk_by_col: dict[str, str] = {}
        for fk in insp.get_foreign_keys(tname):
            for local, ref in zip(fk["constrained_columns"], fk["referred_columns"]):
                fk_by_col[local] = f"{fk['referred_table']}.{ref}"

        columns = []
        for col in insp.get_columns(tname):
            ctype = str(col["type"])
            columns.append(
                Column(
                    name=col["name"],
                    type=ctype,
                    nullable=bool(col["nullable"]),
                    is_pk=col["name"] in pk_cols,
                    fk_ref=fk_by_col.get(col["name"]),
                    samples=_sample_values(engine, tname, col["name"], ctype),
                )
            )
        tables[tname] = Table(tname, columns, TABLE_DESCRIPTIONS.get(tname))
    return SchemaSnapshot(tables)


if __name__ == "__main__":  # self-check
    snap = introspect()
    assert set(snap.tables) == {"categories", "products", "customers", "orders", "order_items"}
    assert snap.tables["orders"].columns  # has columns
    assert any(c.is_pk for c in snap.tables["orders"].columns), "PK not detected"
    assert snap.foreign_keys(), "no FKs detected"
    status_col = next(c for c in snap.tables["orders"].columns if c.name == "status")
    assert "completed" in status_col.samples, "categorical sampling failed"
    print(snap.format_for_prompt())
    print("\nFKs:", snap.foreign_keys())
    print("\nrelevant(revenue by category):", snap.relevant_tables("revenue by category"))
    print("OK")
