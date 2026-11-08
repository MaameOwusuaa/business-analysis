import numpy as np
import pandas as pd

from insightforge.ingest import get_dataset, load_csv_upload
from insightforge.ml.monitoring import psi_numeric
from insightforge.ml.pipeline import run_predictions, train_use_case
from insightforge.usecases import custom_use_case, load_use_cases


def test_churn_model_beats_baseline(churn_run, workspace):
    comp = {r["model"]: r for r in churn_run.details["comparison"]}
    best = comp[churn_run.details["best_model"]]
    assert best["roc_auc"] > 0.65
    assert best["pr_auc"] > comp["Baseline (base rate)"]["pr_auc"] * 2
    assert churn_run.details["validation"] == "out-of-time"
    preds = run_predictions(churn_run.run_id, workspace["ws"])
    assert len(preds) == churn_run.details["rows_scored"]
    assert set(preds["risk_tier"]) == {"High", "Medium", "Low"}
    assert preds["score"].between(0, 1).all()


def test_drivers_and_business_outputs(churn_run):
    d = churn_run.details
    assert len(d["drivers"]) > 10 and d["drivers"][0]["importance"] > 0
    sim = d["campaign_simulation"]
    assert sim["positives_reached_model"] > sim["positives_reached_random"]
    assert d["score_drift"]["status"] in {"stable", "watch", "drift"}


def test_use_cases_are_configuration():
    ucs = load_use_cases()
    assert {"churn_90d", "payment_delinquency_90d"} <= set(ucs)
    assert ucs["churn_90d"].features_sql == ucs["payment_delinquency_90d"].features_sql


def test_custom_csv_classification_and_regression(workspace):
    rng = np.random.default_rng(0)
    n = 900
    df = pd.DataFrame({"Lead ID": [f"L{i}" for i in range(n)], "Source": rng.choice(["Web", "Event", "Referral"], n),
                       "Visits": rng.poisson(3, n), "Employees": rng.integers(1, 500, n)})
    logit = -2 + 0.4 * df["Visits"] + 1.0 * (df["Source"] == "Referral")
    df["Converted"] = np.where(rng.random(n) < 1 / (1 + np.exp(-logit)), "Yes", "No")
    df["Deal Value"] = 1000 + 80 * df["Visits"] + rng.normal(0, 100, n)
    ds = get_dataset(load_csv_upload(workspace["ws"], df, "leads"), workspace["ws"])
    clf = train_use_case(workspace["ws"], ds["id"], custom_use_case(ds, "converted", "classification", "lead_id"),
                         positive_class="Yes")
    assert clf.details["positive_class"] == "Yes"
    assert clf.metrics["roc_auc"] > 0.65
    reg = train_use_case(workspace["ws"], ds["id"], custom_use_case(ds, "deal_value", "regression", "lead_id"))
    assert reg.metrics["r2"] > 0.5


def test_psi():
    rng = np.random.default_rng(1)
    a = pd.Series(rng.normal(0, 1, 5000))
    assert psi_numeric(a, pd.Series(rng.normal(0, 1, 5000))) < 0.05
    assert psi_numeric(a, pd.Series(rng.normal(1, 1, 5000))) > 0.25
