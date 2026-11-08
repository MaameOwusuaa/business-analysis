"""Builds the two analysis notebooks from source (keeps them reviewable in git).

    python scripts/build_notebooks.py          # writes notebooks/*.ipynb
    jupyter nbconvert --to notebook --execute --inplace notebooks/*.ipynb
"""
from __future__ import annotations

from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parents[1]
md, code = nbf.v4.new_markdown_cell, nbf.v4.new_code_cell

SETUP = '''import os, sys
from pathlib import Path
ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
sys.path.insert(0, str(ROOT))
os.environ.setdefault("STORAGE_DIR", str(ROOT / "storage" / "notebooks"))  # isolated from the app database
os.environ.setdefault("LOG_LEVEL", "WARNING")
import warnings; warnings.filterwarnings("ignore")
import pandas as pd, matplotlib.pyplot as plt
pd.set_option("display.width", 160); pd.set_option("display.max_columns", 30)

from insightforge.db import init_db, run_query, load_named_queries, load_sql
from insightforge.auth import sign_up, login, AuthError
from insightforge.ingest import load_synthetic, list_datasets, get_dataset
init_db()
try:
    user = login("notebook@kasatelecom.example", "Notebook#2026")
except AuthError:
    user = sign_up("Kasa Telecom", "Telecommunications", "Christokin Christopherson",
                   "notebook@kasatelecom.example", "Notebook#2026")
WS = user.workspace_id
ds = list_datasets(WS)
DATASET_ID = int(ds.iloc[0]["id"]) if not ds.empty else load_synthetic(WS, n_customers=8000, seed=42)
dataset = get_dataset(DATASET_ID, WS)
print(f"workspace {WS}, dataset {DATASET_ID}: {dataset['name']}")'''


def sql_notebook() -> nbf.NotebookNode:
    nb = nbf.v4.new_notebook()
    nb.cells = [
        md("# 01 - SQL data quality and business analysis\n\n"
           "**Business context.** Kasa Telecom (synthetic) sells prepaid bundles, postpaid plans and home fibre in Ghana. "
           "Management wants to know how fast customers are leaving, where, and what signals precede it.\n\n"
           "This notebook runs the same SQL the platform runs: data-quality checks on the raw tables, then business "
           "questions on the cleaned views. All SQL lives in `insightforge/sql/` and is portable across MySQL 8 / MariaDB and SQLite."),
        code(SETUP),
        md("## 1. Relational schema\n\nRaw (bronze) tables hold the operational data exactly as received, imperfections included. "
           "Cleaned (silver) views de-duplicate, standardise and drop personal data. See `docs/schema.md` for the full ERD."),
        code('''from insightforge.db import table_counts
pd.DataFrame(table_counts(WS).items(), columns=["table", "rows"])'''),
        code('''run_query("SELECT * FROM customers WHERE workspace_id = :ws LIMIT 5", {"ws": WS})  # raw: note PII and messy region labels'''),
        code('''run_query("SELECT * FROM v_customers_clean WHERE workspace_id = :ws LIMIT 5", {"ws": WS})  # clean: no PII, canonical regions'''),
        md("## 2. Data-quality checks\n\nEach check is a named SQL query in `sql/dq_checks.sql` returning the issue count and the rows checked."),
        code('''from insightforge.analysis import run_dq_checks
dq = run_dq_checks(WS)
dq.drop(columns=["sql"])'''),
        code('''print(load_named_queries("dq_checks.sql")["customers_duplicate_phone"].sql)'''),
        md("**Reading the checks.** The high-severity issues are the ones that would change business numbers: duplicate "
           "registrations and double-posted bills overstate customers and revenue, and subscriptions whose status or dates "
           "disagree would misstate churn - the outcome we model. Each is handled in the silver views "
           "(`ROW_NUMBER()` de-duplication, status re-derived from `end_date`, quarantine of impossible dates). "
           "Region spelling variants (e.g. 'Gt. Accra', 'kumasi') are mapped to canonical regions; out-of-range values become NULL "
           "and are imputed inside the ML pipeline."),
        md("## 3. Business questions\n\nParameters (snapshot dates, windows) are computed in Python and bound into the SQL, so "
           "the same queries work for any as-of date."),
        code('''from insightforge.analysis import analysis_params, run_analysis, to_date
AS_OF = to_date(dataset["as_of_date"])
params = analysis_params(WS, AS_OF)
res = run_analysis(WS, AS_OF)
params'''),
        md("### 3.1 Headline KPIs (CTE + conditional aggregation)"),
        code('''print(res["kpi_summary"].sql)
res["kpi_summary"].data'''),
        md("### 3.2 Monthly churn trend (CTE, LEAD for month boundaries, LAG for month-on-month change, moving-average window)"),
        code('''print(res["monthly_churn_trend"].sql)
trend = res["monthly_churn_trend"].data
trend'''),
        code('''fig, ax = plt.subplots(figsize=(9, 3))
ax.plot(trend.month_start, trend.churn_rate_pct, marker="o", color="#2a78d6", label="Monthly churn %")
ax.plot(trend.month_start, trend.churn_rate_3m_avg, ls="--", color="#eb6834", label="3-month average")
ax.set_ylabel("% of active base"); ax.legend(frameon=False); ax.spines[["top", "right"]].set_visible(False)
plt.show()'''),
        md("### 3.3 Is the change regional?"),
        code('''reg = res["monthly_churn_by_region"].data.copy()
reg["group"] = reg.region.where(reg.region == "Greater Accra", "Rest of country")
g = reg.groupby(["month_start", "group"])[["churned", "active_at_start"]].sum()
(100 * g.churned / g.active_at_start).unstack().round(2).tail(12)'''),
        md("**What it means.** Two events stand out. Churn jumped in **October 2025** when prepaid bundle prices rose 15% "
           "(the effect fades over the following months). Then from **April to July 2026** Greater Accra churn roughly doubled "
           "while the rest of the country stayed flat - the signature of a local competitor offer; 'Moved to competitor' "
           "is a top-four cancellation reason. Both are actionable: one is a pricing decision, the other needs a regional counter-offer."),
        md("### 3.4 Segment drivers (90-day outcome window after the last fully observed snapshot)"),
        code('''for name in ["churn_by_contract_plan", "churn_by_tenure", "payment_behaviour_vs_churn",
             "support_experience_vs_churn", "usage_decline_early_warning"]:
    q = res[name]
    print(f"\\n== {q.title}  -  {q.meta.get('question')}")
    display(q.data)'''),
        code('''print(res["usage_decline_early_warning"].sql)'''),
        md("**What it means.**\n\n"
           "* **Contract is the biggest structural lever:** month-to-month prepaid customers leave at roughly 7x the rate of 12-month fibre customers.\n"
           "* **New customers are fragile:** churn in the first year is about double that of customers with 2+ years' tenure, so onboarding matters.\n"
           "* **Falling usage is the clearest early warning:** customers whose data use fell 10-40% churn at about twice the rate of stable users "
           "(the small 'down 40%+' group is extreme but tiny - treat with care).\n"
           "* **Poor support experience** (tickets, and especially low CSAT) is associated with higher churn.\n"
           "* **Payment behaviour is a weaker signal** for voluntary churn than expected; missed payments mainly drive *involuntary* churn "
           "(the separate payment-delinquency use case models that directly).\n\n"
           "These are associations; the model in notebook 02 combines them and the retention test proposed in the report would establish cause."),
        md("### 3.5 Why customers say they leave (window function for share of total)"),
        code('''res["cancel_reasons"].data'''),
        code('''res["support_by_category"].data'''),
        md("Network and data-speed tickets take about three times as long to resolve and score lowest on CSAT - consistent with "
           "'Poor network' being a leading cancellation reason."),
    ]
    return nb


def ml_notebook() -> nbf.NotebookNode:
    nb = nbf.v4.new_notebook()
    nb.cells = [
        md("# 02 - Machine learning pipeline: 90-day churn\n\n"
           "**Problem.** Predict which customers active today will cancel in the next 90 days, so the retention team can call the "
           "riskiest first. **Target:** `1` if the subscription ends within 90 days after the snapshot date, else `0` "
           "(`sql/labels/churn_90d.sql`).\n\n"
           "The pipeline is generic: the use case is a YAML file naming a feature SQL, a label SQL and business assumptions. "
           "This notebook walks through it step by step and then runs the packaged pipeline the app uses."),
        code(SETUP),
        md("## 1. Use-case configuration"),
        code('''from insightforge.usecases import load_use_cases
ucs = load_use_cases()
uc = ucs["churn_90d"]
{k: v for k, v in uc.__dict__.items() if k != "feature_labels"}'''),
        md("## 2. Point-in-time snapshots and out-of-time validation\n\n"
           "Features use only data available on the snapshot date. We train on four earlier quarterly snapshots and test on the "
           "most recent snapshot whose 90-day outcome is fully observed. This mimics production (train on the past, predict the future) "
           "and is stricter than a random split."),
        code('''from insightforge.analysis import to_date
from insightforge.ml.pipeline import plan_snapshots, build_snapshot_frame
train_snaps, test_snap, score_snap = plan_snapshots(uc, to_date(dataset["as_of_date"]))
print("train:", train_snaps, "\\ntest:", test_snap, "\\nscore (no labels yet):", score_snap)'''),
        md("## 3. Feature extraction in SQL\n\nCTEs aggregate usage, payments and tickets per customer over trailing windows; a `ROW_NUMBER()` window picks the latest month; conditional aggregation splits windows."),
        code('''print(load_sql(uc.features_sql)[:2500], "...")'''),
        code('''train_df = pd.concat([build_snapshot_frame(uc, WS, s) for s in train_snaps], ignore_index=True)
test_df = build_snapshot_frame(uc, WS, test_snap)
print(train_df.shape, test_df.shape)
print("churn rate - train: %.2f%%, test: %.2f%%" % (100 * train_df.target.mean(), 100 * test_df.target.mean()))
train_df.head()'''),
        md("Python then derives point-in-time features (`insightforge/ml/features.py`): tenure and age at the snapshot, usage trends "
           "(last 3 months / prior 3 months), allowance utilisation and last month vs average."),
        code('''train_df[["tenure_months", "data_usage_trend", "voice_usage_trend", "data_utilisation", "last_month_vs_avg"]].describe().T'''),
        md("## 4. Preprocessing and candidate models\n\n"
           "* Numeric: median imputation **with missing-value indicators**, then scaling.\n"
           "* Categorical: 'Unknown' for missing, one-hot with rare categories pooled.\n"
           "* Candidates: a no-skill **baseline**, **logistic regression** (interpretable), **random forest**, **gradient boosting**.\n"
           "* Selection metric: **PR-AUC**, because churners are a small minority (~7%) and we care about ranking the riskiest correctly. "
           "We also report ROC-AUC, Brier score, and reach/lift at the team's capacity (top 15%)."),
        code('''from insightforge.ml.pipeline import train_use_case
out = train_use_case(WS, DATASET_ID, uc, progress=print)
d = out.details
pd.DataFrame(d["comparison"])[["model", "roc_auc", "pr_auc", "recall_at_top", "precision_at_top", "lift_at_top", "brier", "fit_seconds"]].round(3)'''),
        md("**Reading the comparison.** All three learned models beat the baseline by a wide margin and are close to each other; "
           "gradient boosting wins narrowly on PR-AUC. Logistic regression is almost as good, which is a useful fallback if "
           "explainability or simplicity were the priority."),
        md("## 5. How useful is it? Lift and capture"),
        code('''lift = pd.DataFrame(d["lift_table"]); lift[["decile", "customers", "positives", "actual_rate", "cum_capture", "lift"]].round(3)'''),
        code('''fig, ax = plt.subplots(figsize=(5, 3.5))
ax.plot([0] + list(lift.decile * 10), [0] + list(lift.cum_capture * 100), marker="o", color="#2a78d6", label="Model")
ax.plot([0, 100], [0, 100], ls=":", color="grey", label="Random")
ax.set_xlabel("% customers contacted"); ax.set_ylabel("% churners reached"); ax.legend(frameon=False)
ax.spines[["top", "right"]].set_visible(False); plt.show()'''),
        md("## 6. Drivers (permutation importance on the test set)"),
        code('''drv = pd.DataFrame(d["drivers"])[["label", "importance", "importance_std", "direction", "risky_categories"]]
drv.head(12).round(4)'''),
        md("**Business interpretation.** The strongest signals are **behavioural**: customers whose voice and data use is falling "
           "relative to their own history are disengaging before they leave. **Contract type** is the main structural driver "
           "(month-to-month is riskiest), followed by **short tenure** and **no autopay**. Implications: (1) usage decline should "
           "trigger an automatic re-engagement offer and a network check, (2) migrate month-to-month customers to commitment plans, "
           "(3) invest in the first months of the customer lifecycle, (4) push autopay enrolment. Importance measures what the model "
           "relies on - it is association, not proof of cause."),
        md("## 7. Business value at the team's capacity"),
        code('''d["campaign_simulation"]'''),
        md("Under the stated assumptions (15% of customers contacted, GHS 15 per call, GHS 60 incentive, 30% save rate, 12-month value) "
           "model targeting returns several times the net benefit of random targeting. The assumptions live in the use-case YAML and "
           "should be calibrated with a control-group pilot."),
        md("## 8. Scoring today's base"),
        code('''print({k: v for k, v in d["scoring_summary"].items() if k != "tiers"})
pd.DataFrame(d["scoring_summary"]["tiers"])'''),
        code('''out.scored.head(10)'''),
        md("## 9. Monitoring baseline: drift (PSI)\n\nPSI compares the scoring population with the training population: <0.1 stable, 0.1-0.25 watch, >0.25 drift."),
        code('''print(d["score_drift"])
pd.DataFrame(d["drift"]).head(8)'''),
        md("Tenure shows drift because the customer base has aged since the earliest training snapshot - expected, but it is exactly "
           "what monitoring should surface. In production this check runs at every scoring; when outcomes arrive 90 days later we "
           "recompute PR-AUC and calibration and retrain if performance drops more than 15% or a key feature drifts (PSI > 0.25)."),
        md("## 10. Same pipeline, different business problem\n\nThe payment-delinquency use case reuses the feature SQL and only swaps the label SQL - no code changes."),
        code('''out2 = train_use_case(WS, DATASET_ID, ucs["payment_delinquency_90d"])
pd.DataFrame(out2.details["comparison"])[["model", "roc_auc", "pr_auc", "lift_at_top"]].round(3)'''),
        code('''pd.DataFrame(out2.details["drivers"])[["label", "importance", "direction"]].head(6).round(4)'''),
        md("Here autopay and recent missed bills dominate - a different set of drivers for a different decision (collections rather "
           "than retention), produced by the same platform."),
    ]
    return nb


def main() -> None:
    out = ROOT / "notebooks"
    out.mkdir(exist_ok=True)
    for name, nb in [("01_sql_analysis.ipynb", sql_notebook()), ("02_ml_pipeline.ipynb", ml_notebook())]:
        nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
        nb.metadata["authors"] = [{"name": "Christokin Christopherson"}]
        nbf.write(nb, out / name)
        print("wrote", out / name)


if __name__ == "__main__":
    main()
