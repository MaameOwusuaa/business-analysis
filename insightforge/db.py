"""Database access: engine creation, schema bootstrap, named SQL queries."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable
import certifi

import pandas as pd
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url

from .config import SQL_DIR, get_settings
from .logging_setup import get_logger
from .schema import BUSINESS_TABLES, metadata

log = get_logger("db")


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    url = url or get_settings().database_url
    kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    # Never log the URL itself: it may contain a password.
    log.info("engine created dialect=%s", engine.dialect.name)
    return engine

@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    url = url or get_settings().database_url
    kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": True}

    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        engine = create_engine(url, **kwargs)

    elif url.startswith("mysql"):
        db_url = make_url(url)

        # Remove SSL settings from the URL so they are not passed twice.
        ssl_options = {
            "ssl_ca",
            "ssl_verify_cert",
            "ssl_verify_identity",
        }
        query = {
            key: value
            for key, value in db_url.query.items()
            if key not in ssl_options
        }

        db_url = db_url.set(query=query)

        # Use certifi's CA bundle and retain SSL certificate verification.
        kwargs["connect_args"] = {
            "ssl_ca": certifi.where(),
            "ssl_verify_cert": True,
            "ssl_verify_identity": True,
            "connect_timeout": 15,
        }

        engine = create_engine(db_url, **kwargs)

    else:
        engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    # Never log the URL itself: it may contain a password.
    log.info("engine created dialect=%s", engine.dialect.name)
    return engine

def init_db(engine: Engine | None = None) -> None:
    """Create tables (idempotent) and (re)create the silver-layer views."""
    engine = engine or get_engine()
    metadata.create_all(engine)

    statements = [
        s.strip()
        for s in (SQL_DIR / "views.sql").read_text().split(";;")
    ]

    with engine.begin() as conn:
        for stmt in statements:
            body = "\n".join(
                line for line in stmt.splitlines()
                if not line.strip().startswith("--")
            ).strip()

            if body:
                conn.execute(text(body))

    log.info("schema initialised")



# --------------------------------------------------------------------------- named queries
@dataclass
class NamedQuery:
    name: str
    sql: str
    meta: dict[str, str] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.meta.get("title", self.name)


_META_RE = re.compile(r"^--\s*(\w+):\s*(.*)$")


def load_named_queries(path: Path | str) -> dict[str, NamedQuery]:
    """Parse a .sql file made of blocks that start with ``-- name: <id>``.

    Lines of the form ``-- key: value`` directly after the name line become
    metadata (title, question, severity, insight ...). Everything else is SQL.
    """
    path = Path(path)
    if not path.is_absolute():
        path = SQL_DIR / path
    queries: dict[str, NamedQuery] = {}
    current: NamedQuery | None = None
    in_header = False
    for line in path.read_text().splitlines():
        m = _META_RE.match(line.strip())
        if m and m.group(1) == "name":
            if current:
                queries[current.name] = current
            current = NamedQuery(name=m.group(2).strip(), sql="")
            in_header = True
            continue
        if current is None:
            continue
        if in_header and m:
            key = m.group(1)
            current.meta[key] = (current.meta.get(key, "") + " " + m.group(2).strip()).strip()
            continue
        in_header = False
        current.sql += line + "\n"
    if current:
        queries[current.name] = current
    for q in queries.values():
        q.sql = q.sql.strip().rstrip(";")
    return queries


def load_sql(path: Path | str) -> str:
    path = Path(path)
    if not path.is_absolute():
        path = SQL_DIR / path
    lines = [l for l in path.read_text().splitlines() if not l.strip().startswith("--")]
    return "\n".join(lines).strip().rstrip(";")


def run_query(sql: str, params: dict[str, Any] | None = None, engine: Engine | None = None) -> pd.DataFrame:
    engine = engine or get_engine()
    # Portable case-sensitive comparison: SQLite compares text case-sensitively by
    # default, MySQL's default collations do not, so a marker becomes BINARY there.
    sql = sql.replace("/*CASE_SENSITIVE*/", "BINARY " if engine.dialect.name in {"mysql", "mariadb"} else "")
    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        df = pd.DataFrame(result.fetchall(), columns=list(result.keys()))
    # MySQL returns Decimal for some aggregates; normalise to float.
    for col in df.columns:
        if df[col].dtype == object and df[col].map(lambda v: isinstance(v, Decimal)).any():
            df[col] = pd.to_numeric(df[col], errors="coerce")
            non_null = df[col].dropna()
            if len(non_null) and (non_null == non_null.round()).all():
                df[col] = df[col].astype("Int64")
    return df


# --------------------------------------------------------------------------- bulk I/O
def _clean_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    records = df.to_dict(orient="records")
    for rec in records:
        for k, v in rec.items():
            if v is None:
                continue
            if isinstance(v, float) and math.isnan(v):
                rec[k] = None
            elif v is pd.NaT:
                rec[k] = None
            elif hasattr(v, "item") and not isinstance(v, (str, bytes)):
                rec[k] = v.item()  # numpy scalar -> python
    return records


def bulk_insert(table, df: pd.DataFrame, engine: Engine | None = None, chunk: int = 5000) -> int:
    engine = engine or get_engine()
    records = _clean_records(df)
    with engine.begin() as conn:
        for i in range(0, len(records), chunk):
            conn.execute(table.insert(), records[i : i + chunk])
    return len(records)


def delete_workspace_business_data(workspace_id: int, engine: Engine | None = None) -> None:
    engine = engine or get_engine()
    with engine.begin() as conn:
        for table in reversed(BUSINESS_TABLES):
            conn.execute(table.delete().where(table.c.workspace_id == workspace_id))


def table_counts(workspace_id: int, tables: Iterable = BUSINESS_TABLES, engine: Engine | None = None) -> dict[str, int]:
    engine = engine or get_engine()
    out = {}
    with engine.connect() as conn:
        for t in tables:
            out[t.name] = conn.execute(
                text(f"SELECT COUNT(*) FROM {t.name} WHERE workspace_id = :ws"), {"ws": workspace_id}
            ).scalar_one()
    return out
