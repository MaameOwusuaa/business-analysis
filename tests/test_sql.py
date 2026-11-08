from datetime import date

from insightforge.analysis import latest_labelled_snapshot, run_analysis, run_dq_checks, snapshot_params
from insightforge.db import load_named_queries, load_sql, run_query


def test_dq_checks_detect_injected_issues(workspace):
    dq = run_dq_checks(workspace["ws"]).set_index("check")
    assert len(dq) == len(load_named_queries("dq_checks.sql"))
    for check in ["customers_duplicate_phone", "usage_duplicate_months", "payments_double_posted",
                  "customers_nonstandard_region", "tickets_invalid_csat"]:
        assert dq.loc[check, "issues"] > 0, check
    assert dq.loc["orphan_usage", "issues"] == 0


def test_silver_views_fix_the_issues(workspace):
    ws = {"ws": workspace["ws"]}
    dup = run_query("SELECT COUNT(*) AS n FROM (SELECT subscription_id, usage_month FROM v_usage_clean "
                    "WHERE workspace_id = :ws GROUP BY subscription_id, usage_month HAVING COUNT(*) > 1) d", ws)
    assert int(dup.n[0]) == 0
    neg = run_query("SELECT COUNT(*) AS n FROM v_usage_clean WHERE workspace_id = :ws AND data_gb_used < 0", ws)
    assert int(neg.n[0]) == 0
    regions = set(run_query("SELECT DISTINCT region FROM v_customers_clean WHERE workspace_id = :ws", ws).region.dropna())
    assert regions <= {"Greater Accra", "Ashanti", "Western", "Central", "Eastern", "Northern", "Volta", "Bono",
                       "Upper East", "Upper West"}
    cols = run_query("SELECT * FROM v_customers_clean WHERE workspace_id = :ws LIMIT 1", ws).columns
    assert not {"full_name", "phone", "email"} & set(cols)  # PII never reaches analytics


def test_all_analysis_queries_run(workspace):
    res = run_analysis(workspace["ws"], date(2026, 9, 30))
    assert set(res) == set(load_named_queries("analysis.sql"))
    for name, r in res.items():
        assert not r.data.empty, name
    k = res["kpi_summary"].data.iloc[0]
    assert k["active_customers"] > 0 and 0 < k["churn_rate_90d_pct"] < 50


def test_tenant_isolation(workspace):
    other = run_query("SELECT COUNT(*) AS n FROM customers WHERE workspace_id = :ws", {"ws": 999_999})
    assert int(other.n[0]) == 0


def test_features_are_point_in_time(workspace):
    snap = latest_labelled_snapshot(date(2026, 9, 30))
    params = {"ws": workspace["ws"], **snapshot_params(snap)}
    feats = run_query(load_sql("features/customer_snapshot_features.sql"), params)
    labels = run_query(load_sql("labels/churn_90d.sql"), params)
    assert feats["entity_id"].is_unique
    assert set(feats["entity_id"]) == set(labels["entity_id"])
    # every customer in the snapshot started on or before the snapshot date
    assert (feats["start_date"].astype(str) <= snap.isoformat()).all()
    assert 0 < labels["target"].mean() < 0.5
