"""Getting a business's data into its workspace.

Two routes:

* ``load_synthetic`` - generate the Kasa Telecom scenario and load it into the
  relational business tables (scoped by ``workspace_id``).
* ``load_csv_upload`` - a business brings a flat CSV extract of its own; it is
  validated and stored in its own table so the generic ("custom") use case can
  model it.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date

import pandas as pd
from sqlalchemy import select, update

from .auth import audit
from .datagen.telco import GeneratorConfig, generate
from .db import bulk_insert, delete_workspace_business_data, get_engine, init_db
from .logging_setup import get_logger
from .schema import BUSINESS_TABLES, datasets

log = get_logger("ingest")

MAX_UPLOAD_ROWS = 250_000
MAX_UPLOAD_COLS = 150


class IngestError(ValueError):
    pass


def load_synthetic(
    workspace_id: int,
    n_customers: int = 8000,
    seed: int = 42,
    as_of: date = date(2026, 9, 30),
    user_id: int | None = None,
) -> int:
    if not 500 <= n_customers <= 50_000:
        raise IngestError("Number of customers must be between 500 and 50,000.")
    init_db()
    cfg = GeneratorConfig(n_customers=n_customers, seed=seed, as_of=as_of)
    data = generate(cfg)
    engine = get_engine()
    delete_workspace_business_data(workspace_id, engine)
    counts = {}
    for table in BUSINESS_TABLES:
        df = data[table.name].copy()
        df.insert(0, "workspace_id", workspace_id)
        counts[table.name] = bulk_insert(table, df, engine)
    with engine.begin() as conn:
        conn.execute(
            update(datasets)
            .where(datasets.c.workspace_id == workspace_id, datasets.c.source == "synthetic_telco")
            .values(status="replaced")
        )
        ds_id = conn.execute(
            datasets.insert().values(
                workspace_id=workspace_id,
                name=f"Kasa Telecom synthetic (seed {seed}, {n_customers:,} customers)",
                source="synthetic_telco",
                seed=seed,
                as_of_date=as_of,
                row_counts=json.dumps(counts),
                status="ready",
            )
        ).inserted_primary_key[0]
    audit("dataset.synthetic_loaded", workspace_id, user_id, dataset_id=ds_id, seed=seed, rows=counts)
    log.info("synthetic dataset loaded workspace_id=%s dataset_id=%s rows=%s", workspace_id, ds_id, counts)
    return ds_id


def _snake(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", str(name)).strip("_").lower()
    if not s or s[0].isdigit():
        s = f"col_{s}"
    return s[:60]


def load_csv_upload(workspace_id: int, df: pd.DataFrame, name: str, user_id: int | None = None) -> int:
    if df.empty:
        raise IngestError("The file has no rows.")
    if len(df) > MAX_UPLOAD_ROWS:
        raise IngestError(f"Uploads are limited to {MAX_UPLOAD_ROWS:,} rows in this prototype.")
    if df.shape[1] > MAX_UPLOAD_COLS or df.shape[1] < 2:
        raise IngestError(f"Expected between 2 and {MAX_UPLOAD_COLS} columns.")
    df = df.copy()
    cols = [_snake(c) for c in df.columns]
    seen: dict[str, int] = {}
    for i, c in enumerate(cols):  # de-duplicate column names
        if c in seen:
            seen[c] += 1
            cols[i] = f"{c}_{seen[c]}"
        else:
            seen[c] = 0
    df.columns = cols
    table_name = f"upload_w{workspace_id}_{uuid.uuid4().hex[:8]}"  # never derived from user text
    init_db()
    engine = get_engine()
    df.to_sql(table_name, engine, index=False, if_exists="fail", chunksize=5000)
    with engine.begin() as conn:
        ds_id = conn.execute(
            datasets.insert().values(
                workspace_id=workspace_id,
                name=name[:120] or "CSV upload",
                source="csv_upload",
                table_name=table_name,
                row_counts=json.dumps({table_name: len(df)}),
                status="ready",
            )
        ).inserted_primary_key[0]
    audit("dataset.csv_uploaded", workspace_id, user_id, dataset_id=ds_id, rows=len(df), cols=df.shape[1])
    return ds_id


def list_datasets(workspace_id: int) -> pd.DataFrame:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(datasets)
            .where(datasets.c.workspace_id == workspace_id, datasets.c.status == "ready")
            .order_by(datasets.c.id.desc())
        ).mappings().all()
    return pd.DataFrame([dict(r) for r in rows])


def get_dataset(dataset_id: int, workspace_id: int) -> dict:
    """Fetch a dataset, enforcing that it belongs to the caller's workspace."""
    with get_engine().connect() as conn:
        row = conn.execute(
            select(datasets).where(datasets.c.id == dataset_id, datasets.c.workspace_id == workspace_id)
        ).mappings().first()
    if not row:
        raise IngestError("Dataset not found in this workspace.")
    return dict(row)
