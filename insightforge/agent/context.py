"""Build the context packs passed to the Snwolley agent.

Design rules (the "data contract" with the AI):

* Only aggregates, model metrics and segment-level results go in - never raw
  rows, names, phone numbers or e-mails. Customer IDs are excluded too.
* Every number is rounded and labelled with its unit, so the model can quote it
  exactly and the validator can check it afterwards.
* Free text that originated in the data is sanitised and truncated so it cannot
  smuggle instructions into the prompt.
* Each pack is size-limited.
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from ..analysis import run_analysis, run_dq_checks, to_date
from ..auth import get_workspace
from ..db import load_named_queries, run_query
from ..ingest import get_dataset
from ..ml.pipeline import get_run

MAX_CONTEXT_CHARS = 24_000  # ~6k tokens
PII_PATTERNS = [
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),  # e-mail
    re.compile(r"\+?233\d{9}"),  # Ghana phone (E.164)
    re.compile(r"\b0[235]\d{8}\b"),  # Ghana phone (local)
]


class ContextError(ValueError):
    pass


def sanitize_text(value: str, limit: int = 160) -> str:
    value = re.sub(r"[\x00-\x1f<>`]", " ", str(value))
    return re.sub(r"\s+", " ", value).strip()[:limit]


def _r(v: Any, nd: int = 2) -> Any:
    if v is None or (isinstance(v, float) and v != v):
        return None
    if isinstance(v, (np.integer, int)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return int(v) if float(v).is_integer() else round(float(v), nd)
    if isinstance(v, (date, pd.Timestamp)):
        return str(v)[:10]
    return sanitize_text(v)


def _records(df: pd.DataFrame, cols: list[str] | None = None, nd: int = 2, limit: int = 20) -> list[dict]:
    df = df[cols] if cols else df
    return [{k: _r(v, nd) for k, v in row.items()} for row in df.head(limit).to_dict(orient="records")]


def assert_no_pii(payload: dict) -> None:
    text = json.dumps(payload)
    for pat in PII_PATTERNS:
        if pat.search(text):
            raise ContextError("Context rejected: it appears to contain personal data.")


def finalize(payload: dict) -> dict:
    assert_no_pii(payload)
    if len(json.dumps(payload)) > MAX_CONTEXT_CHARS:
        raise ContextError("Context pack too large; reduce the sections included.")
    return payload


# --------------------------------------------------------------------------- sections
def business_section(workspace_id: int) -> dict:
    ws = get_workspace(workspace_id)
    return {"name": sanitize_text(ws.get("name", "")), "industry": ws.get("industry"),
            "objective": sanitize_text(ws.get("objective") or "", 300) or None}


def dq_section(workspace_id: int) -> dict:
    dq = run_dq_checks(workspace_id)
    failing = dq[dq["issues"] > 0].copy()
    failing["_sev"] = failing["severity"].map({"high": 0, "medium": 1, "low": 2}).fillna(3)
    failing = failing.sort_values(["_sev", "issue_pct"], ascending=[True, False])
    return {
        "checks_run": int(len(dq)),
        "checks_with_issues": int(len(failing)),
        "issues": _records(failing, ["description", "table", "severity", "issues", "rows_checked", "issue_pct", "handling"], limit=20),
    }


def _region_shift(region_df: pd.DataFrame) -> list[dict]:
    """Regional churn: last 6 months vs the 6 before, plus each region's worst
    3-month window in the last 12 months compared with the rest of the country."""
    df = region_df.copy()
    df["month_start"] = pd.to_datetime(df["month_start"])
    months = sorted(df["month_start"].unique())
    if len(months) < 12:
        return []
    recent, prior, last12 = months[-6:], months[-12:-6], months[-12:]
    piv_c = df.pivot_table(index="month_start", columns="region", values="churned", aggfunc="sum").fillna(0)
    piv_a = df.pivot_table(index="month_start", columns="region", values="active_at_start", aggfunc="sum").fillna(0)
    out = []
    for region in piv_c.columns:
        c, a = piv_c[region], piv_a[region]
        rest_c, rest_a = piv_c.drop(columns=region).sum(axis=1), piv_a.drop(columns=region).sum(axis=1)
        rate = lambda cc, aa, idx: 100 * cc.loc[idx].sum() / max(1, aa.loc[idx].sum())  # noqa: E731
        best = None
        for i in range(len(last12) - 2):
            win = last12[i:i + 3]
            excess = rate(c, a, win) - rate(rest_c, rest_a, win)
            if best is None or excess > best[0]:
                best = (excess, win)
        win = best[1]
        out.append({
            "region": region,
            "active_customers_now": int(a.loc[months[-1]]),
            "avg_monthly_churn_pct_last_6m": round(rate(c, a, recent), 2),
            "avg_monthly_churn_pct_prior_6m": round(rate(c, a, prior), 2),
            "change_pp": round(rate(c, a, recent) - rate(c, a, prior), 2),
            "worst_3m_window": f"{pd.Timestamp(win[0]):%Y-%m} to {pd.Timestamp(win[-1]):%Y-%m}",
            "worst_3m_avg_monthly_churn_pct": round(rate(c, a, win), 2),
            "rest_of_country_same_window_pct": round(rate(rest_c, rest_a, win), 2),
            "worst_3m_excess_pp": round(best[0], 2),
        })
    # rank by impact (excess rate x size of base) so small regions do not dominate on noise
    return sorted(out, key=lambda x: -x["worst_3m_excess_pp"] * x["active_customers_now"])


def analysis_section(workspace_id: int, as_of: date) -> dict:
    res = run_analysis(workspace_id, as_of)
    trend = res["monthly_churn_trend"].data.copy()
    trend["month"] = trend["month_start"].dt.strftime("%Y-%m")
    last12 = trend.tail(12)
    peaks = last12.sort_values("churn_rate_pct", ascending=False).head(3)
    k = res["kpi_summary"].data.iloc[0].to_dict()
    return {
        "as_of": str(as_of),
        "kpis": {
            "active_customers": _r(k["active_customers"]),
            "monthly_recurring_revenue_ghs": _r(k["mrr_ghs"], 0),
            "arpu_ghs": _r(k["arpu_ghs"]),
            "churned_last_90_days": _r(k["churned_last_90d"]),
            "involuntary_churn_last_90_days": _r(k["involuntary_last_90d"]),
            "new_customers_last_90_days": _r(k["new_last_90d"]),
            "net_adds_last_90_days": _r(k["net_adds_90d"]),
            "churn_rate_90d_pct": _r(k["churn_rate_90d_pct"]),
        },
        "monthly_churn_trend_last_12m": _records(last12, ["month", "active_at_start", "churned", "churn_rate_pct", "mom_change_pp", "churn_rate_3m_avg"], limit=12),
        "highest_churn_months_last_12m": _records(peaks, ["month", "churn_rate_pct", "churned"], limit=3),
        "regional_churn_shift": _region_shift(res["monthly_churn_by_region"].data)[:10],
        "churn_by_contract_and_plan_90d": _records(res["churn_by_contract_plan"].data, ["contract_type", "plan_type", "customers", "churn_rate_90d_pct"], limit=12),
        "churn_by_tenure_90d": _records(res["churn_by_tenure"].data, ["tenure_band", "customers", "churn_rate_90d_pct"]),
        "churn_by_payment_behaviour_90d": _records(res["payment_behaviour_vs_churn"].data, ["payment_profile", "customers", "churn_rate_90d_pct"]),
        "churn_by_support_experience_90d": _records(res["support_experience_vs_churn"].data, ["support_profile", "customers", "churn_rate_90d_pct"]),
        "churn_by_usage_trend_90d": _records(res["usage_decline_early_warning"].data, ["usage_trend", "customers", "churn_rate_90d_pct"]),
        "cancel_reasons_last_12m": _records(res["cancel_reasons"].data, ["cancel_reason", "cancellations", "share_pct", "lost_mrr_ghs"]),
        "support_by_category_last_6m": _records(res["support_by_category"].data, ["category", "tickets", "avg_resolution_hours", "avg_csat"]),
        "method_note": "90-day rates use customers active at the last fully observed snapshot and whether they cancelled in the following 90 days.",
    }


def model_section(run: dict, top_drivers: int = 8) -> dict:
    d, m = run["details"], run["metrics"]
    is_clf = run["task_type"] == "classification"
    comp_cols = (["model", "roc_auc", "pr_auc", "lift_at_top", "recall_at_top", "precision_at_top"] if is_clf
                 else ["model", "rmse", "mae", "r2"])
    drivers = []
    for i, x in enumerate(d.get("drivers", [])[:top_drivers], start=1):
        item = {"rank": i, "feature": x["label"], "importance": round(x["importance"], 4),
                "relationship": ("higher values -> higher predicted risk" if x.get("direction") == "higher"
                                 else "lower values -> higher predicted risk" if x.get("direction") == "lower"
                                 else "riskiest categories: " + ", ".join(x.get("risky_categories") or ["none stand out"])
                                 if x.get("direction") == "category" else "mixed / non-monotonic")}
        prof = x.get("profile") or []
        if prof:
            rates = [p["outcome_rate"] for p in prof if p["n"] >= 20]
            if rates:
                item["observed_outcome_rate_range_pct"] = [round(100 * min(rates), 1), round(100 * max(rates), 1)]
        drivers.append(item)
    sec = {
        "use_case": d.get("use_case_name"),
        "task_type": run["task_type"],
        "validation": d.get("validation"),
        "train_snapshots": d.get("train_snapshots"),
        "test_snapshot": d.get("test_snapshot"),
        "score_snapshot": d.get("score_snapshot"),
        "rows_train": d.get("rows_train"),
        "rows_test": d.get("rows_test"),
        "selected_model": run["best_model"],
        "selection_metric": d.get("selection_metric"),
        "model_comparison": [{c: _r(r.get(c), 3) for c in comp_cols} for r in d.get("comparison", [])],
        "top_drivers": drivers,
        "drivers_note": "Drivers come from permutation importance; they describe associations the model uses, not proven causes.",
    }
    if is_clf:
        sec["test_outcome_rate_pct"] = _r(100 * d.get("target_rate_test", 0), 2)
        sec["top_pct_contacted"] = _r(100 * m.get("top_pct", 0), 0)
        sec["campaign_simulation_on_test"] = {k: _r(v, 2) for k, v in d.get("campaign_simulation", {}).items() if k != "assumptions"}
        sec["campaign_assumptions"] = {k: _r(v, 2) for k, v in d.get("campaign_simulation", {}).get("assumptions", {}).items()}
        ss = d.get("scoring_summary", {})
        sec["current_scoring"] = {
            "customers_scored": ss.get("population"),
            "expected_positives_next_90d": _r(ss.get("expected_positives"), 0),
            "expected_rate_pct": _r(100 * (ss.get("expected_rate") or 0), 2),
            "expected_revenue_at_risk_ghs": _r(ss.get("expected_value_at_risk_ghs"), 0),
            "high_tier_revenue_at_risk_ghs": _r(ss.get("high_tier_value_at_risk_ghs"), 0),
            "revenue_horizon_months": ss.get("value_months"),
            "tiers": [{"tier": t["risk_tier"], "customers": int(t["customers"]),
                       "avg_predicted_risk_pct": round(100 * t["avg_score"], 1),
                       "expected_positives": round(t["expected_positives"], 0)}
                      for t in sorted(ss.get("tiers", []), key=lambda t: ["High", "Medium", "Low"].index(t["risk_tier"]))],
        }
        sec["high_tier_reason_counts"] = d.get("high_risk_reason_counts", [])
    sec["drift"] = {
        "score_psi": _r(d.get("score_drift", {}).get("psi"), 3),
        "score_status": d.get("score_drift", {}).get("status"),
        "features_flagged": [{"feature": x["feature"], "psi": x["psi"], "status": x["status"]}
                             for x in d.get("drift", []) if x["status"] in {"watch", "drift"}][:6],
    }
    return sec


def segment_section(workspace_id: int, run: dict) -> dict:
    qs = load_named_queries("model_insights.sql")
    params = {"ws": workspace_id, "run_id": run["id"]}
    seg = run_query(qs["risk_by_segment"].sql, params)
    reg = run_query(qs["risk_by_region"].sql, params)
    return {"risk_by_contract_and_plan": _records(seg, limit=12), "risk_by_region": _records(reg, limit=11)}


# --------------------------------------------------------------------------- task packs
def build_context(task: str, workspace_id: int, run_id: int | None = None, dataset_id: int | None = None) -> dict:
    run = get_run(run_id, workspace_id) if run_id else None
    ds = get_dataset(dataset_id or (run or {}).get("dataset_id"), workspace_id) if (dataset_id or run) else None
    telco = ds is not None and ds["source"] == "synthetic_telco"
    as_of = to_date(ds["as_of_date"]) if telco else None
    ctx: dict[str, Any] = {"task": task, "business": business_section(workspace_id)}
    if task == "data_quality_brief":
        ctx["data_quality"] = dq_section(workspace_id)
    elif task == "kpi_explainer":
        if not telco:
            raise ContextError("KPI explanation needs the relational business dataset.")
        ctx["analysis"] = analysis_section(workspace_id, as_of)
    elif task == "model_explainer":
        if not run:
            raise ContextError("Train a model first.")
        ctx["model"] = model_section(run)
    elif task == "retention_actions":
        if not run or run["task_type"] != "classification":
            raise ContextError("Action recommendations need a trained classification model.")
        ctx["model"] = model_section(run, top_drivers=6)
        for k in ("model_comparison", "train_snapshots", "drift"):
            ctx["model"].pop(k, None)
        if telco:
            ctx["segments"] = segment_section(workspace_id, run)
    elif task in {"executive_summary", "ask"}:
        if telco:
            a = analysis_section(workspace_id, as_of)
            for k in ("support_by_category_last_6m", "churn_by_support_experience_90d", "monthly_churn_trend_last_12m"):
                if task == "executive_summary":
                    a.pop(k, None)
            ctx["analysis"] = a
        if run:
            ms = model_section(run, top_drivers=6)
            ms.pop("train_snapshots", None)
            ctx["model"] = ms
            if telco:
                ctx["segments"] = segment_section(workspace_id, run)
        dq = dq_section(workspace_id) if telco else None
        if dq:
            ctx["data_quality_summary"] = {"checks_run": dq["checks_run"], "checks_with_issues": dq["checks_with_issues"],
                                           "high_severity": [i["description"] for i in dq["issues"] if i["severity"] == "high"]}
    else:
        raise ContextError(f"Unknown task {task!r}")
    return finalize(ctx)
