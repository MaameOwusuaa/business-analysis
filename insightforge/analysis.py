"""SQL-driven data-quality checks and business analysis."""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from .db import load_named_queries, run_query
from .logging_setup import get_logger

log = get_logger("analysis")


# --------------------------------------------------------------------------- date helpers
def month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def add_months(d: date, n: int) -> date:
    """Shift to the first day of the month ``n`` months away."""
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def to_date(value) -> date:
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()


def snapshot_params(snapshot: date, horizon_days: int = 90) -> dict:
    """Parameters for feature / label SQL at a given snapshot (month-end) date."""
    snap_month = month_start(snapshot)
    return {
        "snapshot": snapshot.isoformat(),
        "horizon_end": (snapshot + timedelta(days=horizon_days)).isoformat(),
        "m3_start": add_months(snap_month, -2).isoformat(),  # 3 months incl. snapshot month
        "m6_start": add_months(snap_month, -5).isoformat(),
        "d90_snapshot": (snapshot - timedelta(days=90)).isoformat(),
        "t3": (snapshot - timedelta(days=91)).isoformat(),
        "t12": (snapshot - timedelta(days=365)).isoformat(),
        "t24": (snapshot - timedelta(days=730)).isoformat(),
    }


def latest_labelled_snapshot(as_of: date, horizon_days: int = 90) -> date:
    """Last month-end whose full outcome window is observed by ``as_of``."""
    d = month_end(add_months(as_of, -3))
    while d + timedelta(days=horizon_days) > as_of:
        d = month_end(add_months(d, -1))
    return d


def analysis_params(workspace_id: int, as_of: date) -> dict:
    snapshot = latest_labelled_snapshot(as_of)
    p = {"ws": workspace_id, "as_of": as_of.isoformat()}
    p.update(snapshot_params(snapshot))
    p.update(
        {
            "d90_start": (as_of - timedelta(days=90)).isoformat(),
            "d180_start": (as_of - timedelta(days=180)).isoformat(),
            "d365_start": (as_of - timedelta(days=365)).isoformat(),
            "trend_start": add_months(month_start(as_of), -17).isoformat(),
            "month_after_as_of": add_months(month_start(as_of), 1).isoformat(),
        }
    )
    return p


# --------------------------------------------------------------------------- checks
@dataclass
class QueryResult:
    name: str
    title: str
    sql: str
    meta: dict
    data: pd.DataFrame


def run_dq_checks(workspace_id: int) -> pd.DataFrame:
    rows = []
    for q in load_named_queries("dq_checks.sql").values():
        df = run_query(q.sql, {"ws": workspace_id})
        issues = int(df.iloc[0]["issue_count"] or 0)
        total = int(df.iloc[0]["total_rows"] or 0)
        rows.append(
            {
                "check": q.name,
                "description": q.title,
                "table": q.meta.get("table", ""),
                "severity": q.meta.get("severity", ""),
                "issues": issues,
                "rows_checked": total,
                "issue_pct": round(100.0 * issues / total, 2) if total else 0.0,
                "handling": q.meta.get("handling", ""),
                "sql": q.sql,
            }
        )
    out = pd.DataFrame(rows)
    log.info("dq checks run workspace_id=%s failing=%s", workspace_id, int((out.issues > 0).sum()))
    return out


def run_analysis(workspace_id: int, as_of: date, names: list[str] | None = None) -> dict[str, QueryResult]:
    params = analysis_params(workspace_id, as_of)
    results = {}
    for q in load_named_queries("analysis.sql").values():
        if names and q.name not in names:
            continue
        df = run_query(q.sql, params)
        for col in df.columns:
            if col.endswith(("month_start",)):
                df[col] = pd.to_datetime(df[col])
        results[q.name] = QueryResult(q.name, q.title, q.sql, q.meta, df)
    return results


def kpis(workspace_id: int, as_of: date) -> dict:
    res = run_analysis(workspace_id, as_of, ["kpi_summary"])["kpi_summary"].data
    return {k: (None if pd.isna(v) else float(v)) for k, v in res.iloc[0].items()}
