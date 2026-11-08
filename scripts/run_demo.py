"""End-to-end demo from the command line (same services the web app uses).

    python scripts/run_demo.py

1. creates (or reuses) the demo workspace and login
2. generates and loads the synthetic Kasa Telecom data
3. runs the SQL data-quality checks and business analysis
4. trains the churn model (and the payment-delinquency model)
5. asks the Snwolley agent for insights (live or mock, per SNWOLLEY_MODE)
6. writes the PDF report
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from insightforge.agent.service import run_task  # noqa: E402
from insightforge.analysis import run_analysis, run_dq_checks, to_date  # noqa: E402
from insightforge.auth import AuthError, login, sign_up, update_workspace  # noqa: E402
from insightforge.db import init_db  # noqa: E402
from insightforge.ingest import get_dataset, load_synthetic  # noqa: E402
from insightforge.ml.pipeline import train_use_case  # noqa: E402
from insightforge.reporting.report import build_report  # noqa: E402
from insightforge.usecases import load_use_cases  # noqa: E402

DEMO_EMAIL = os.getenv("DEMO_EMAIL", "demo@businessinsights.example")
DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "BusinessInsights#2026")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--customers", type=int, default=8000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--skip-second-use-case", action="store_true")
    args = ap.parse_args()

    init_db()
    try:
        user = login(DEMO_EMAIL, DEMO_PASSWORD)
        print(f"[1] Logged in to existing workspace '{user.workspace_name}'")
    except AuthError:
        user = sign_up("Business Insights Demo", "Any industry", "Demo Administrator", DEMO_EMAIL, DEMO_PASSWORD,
                       objective="Identify risk, understand drivers, and prioritize high-value actions.", country="Global")
        print(f"[1] Created workspace '{user.workspace_name}' (login {DEMO_EMAIL})")
    update_workspace(user.workspace_id, active_use_case="churn_90d")

    ds_id = load_synthetic(user.workspace_id, args.customers, args.seed, user_id=user.user_id)
    ds = get_dataset(ds_id, user.workspace_id)
    print(f"[2] Loaded synthetic dataset {ds_id}: {ds['row_counts']}")

    dq = run_dq_checks(user.workspace_id)
    print(f"[3] Data quality: {int((dq.issues > 0).sum())} of {len(dq)} checks found issues")
    res = run_analysis(user.workspace_id, to_date(ds["as_of_date"]))
    k = res["kpi_summary"].data.iloc[0]
    print(f"    KPIs: {int(k.active_customers):,} active, MRR GHS {float(k.mrr_ghs):,.0f}, 90-day churn {float(k.churn_rate_90d_pct):.2f}%")

    ucs = load_use_cases()
    out = train_use_case(user.workspace_id, ds_id, ucs["churn_90d"], user.user_id, progress=lambda s: print("    -", s))
    print(f"[4] Churn model run {out.run_id}: {out.details['best_model']}  ROC-AUC {out.metrics['roc_auc']:.3f}  "
          f"PR-AUC {out.metrics['pr_auc']:.3f}  lift@top {out.metrics['lift_at_top']:.2f}x")
    if not args.skip_second_use_case:
        out2 = train_use_case(user.workspace_id, ds_id, ucs["payment_delinquency_90d"], user.user_id)
        print(f"    Payment-delinquency run {out2.run_id}: ROC-AUC {out2.metrics['roc_auc']:.3f}")

    for task in ["data_quality_brief", "kpi_explainer", "model_explainer", "retention_actions", "executive_summary"]:
        r = run_task(task, user.workspace_id, run_id=out.run_id, dataset_id=ds_id, user_id=user.user_id)
        print(f"[5] {task:<20} provider={r.provider} status={r.status} numbers_checked={r.validation['numbers_checked']}")
    r = run_task("ask", user.workspace_id, run_id=out.run_id, dataset_id=ds_id, user_id=user.user_id,
                 question="Which region contributes most to the recent rise in churn?")
    print(f"    ask -> status={r.status}")

    pdf = build_report(user.workspace_id, out.run_id, author=user.full_name, user_id=user.user_id)
    print(f"[6] Report written to {pdf}")


if __name__ == "__main__":
    main()
