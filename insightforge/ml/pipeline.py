"""Generic, configuration-driven ML pipeline.

    SQL feature snapshot(s) -> feature engineering -> preprocessing -> candidate
    models -> out-of-time evaluation -> driver analysis -> business simulation
    -> refit -> scoring -> drift baseline -> persistence

The pipeline knows nothing about churn: everything use-case specific comes from
the ``UseCase`` configuration (SQL files, horizon, business assumptions).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_recall_curve,
    precision_score,
    r2_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sqlalchemy import update

from ..analysis import add_months, latest_labelled_snapshot, month_end, snapshot_params, to_date
from ..auth import audit
from ..config import get_settings
from ..db import bulk_insert, get_engine, load_sql, run_query
from ..ingest import get_dataset
from ..logging_setup import get_logger
from ..schema import model_runs, predictions
from ..usecases import UseCase
from .features import TRANSFORMS, coerce_numeric_like, split_feature_types
from .monitoring import feature_drift, psi_numeric, psi_status

log = get_logger("ml")

META_COLS = {"entity_id", "target", "snapshot_date"}
MIN_ROWS, MIN_POSITIVES = 200, 20


class PipelineError(RuntimeError):
    pass


# --------------------------------------------------------------------------- helpers
def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return None if obj != obj or np.isinf(obj) else round(float(obj), 6)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (date, pd.Timestamp)):
        return obj.isoformat()[:10]
    if isinstance(obj, pd.DataFrame):
        return to_jsonable(obj.to_dict(orient="records"))
    return obj


def _fmt(v: Any) -> str:
    if isinstance(v, (int, np.integer)) or (isinstance(v, float) and float(v).is_integer()):
        return f"{int(v):,}"
    if isinstance(v, (float, np.floating)):
        return f"{v:.2f}" if abs(v) < 10 else f"{v:,.0f}"
    return str(v)


# --------------------------------------------------------------------------- data assembly
def plan_snapshots(uc: UseCase, as_of: date) -> tuple[list[date], date, date]:
    test = latest_labelled_snapshot(as_of, uc.horizon_days)
    train = [month_end(add_months(test, -uc.snapshot_step_months * k)) for k in range(uc.n_train_snapshots, 0, -1)]
    score = as_of if as_of == month_end(as_of) else month_end(add_months(as_of, -1))
    return train, test, score


def build_snapshot_frame(uc: UseCase, workspace_id: int, snapshot: date, labelled: bool = True) -> pd.DataFrame:
    params = {"ws": workspace_id, **snapshot_params(snapshot, uc.horizon_days)}
    X = run_query(load_sql(uc.features_sql), params)
    X = TRANSFORMS[uc.feature_transform or "identity"](X, snapshot)
    if labelled:
        y = run_query(load_sql(uc.label_sql), params)
        X = X.merge(y, on="entity_id", how="inner")
        X["target"] = X["target"].astype(int)
    X["snapshot_date"] = snapshot
    return coerce_numeric_like(X)


_UPLOAD_TABLE_RE = re.compile(r"^upload_w\d+_[0-9a-f]{8}$")


def load_custom_frame(uc: UseCase, positive_class: Any = None) -> tuple[pd.DataFrame, dict]:
    if not uc.custom_table or not _UPLOAD_TABLE_RE.match(uc.custom_table):
        raise PipelineError("Invalid dataset table.")  # table names are system-generated only
    df = run_query(f"SELECT * FROM {uc.custom_table}")
    if uc.target_column not in df.columns:
        raise PipelineError(f"Target column '{uc.target_column}' not found.")
    df = df[df[uc.target_column].notna()].copy()
    info: dict = {}
    if uc.task_type == "classification":
        classes = df[uc.target_column].astype(str).value_counts()
        if len(classes) != 2:
            raise PipelineError("Classification needs a target with exactly two classes (binary).")
        pos = str(positive_class) if positive_class is not None else classes.index[-1]  # minority by default
        df["target"] = (df[uc.target_column].astype(str) == pos).astype(int)
        info["positive_class"] = pos
    else:
        df["target"] = pd.to_numeric(df[uc.target_column], errors="coerce")
        df = df[df["target"].notna()]
    if uc.id_column and uc.id_column in df.columns:
        df["entity_id"] = df[uc.id_column].astype(str)
        df = df.drop(columns=[uc.id_column])
    else:
        df["entity_id"] = [f"row_{i + 1}" for i in range(len(df))]
    df = df.drop(columns=[uc.target_column])
    df["snapshot_date"] = None
    return coerce_numeric_like(df), info


def prepare_X(df: pd.DataFrame, numeric: list[str], categorical: list[str]) -> pd.DataFrame:
    X = df[numeric + categorical].copy()
    for c in categorical:
        X[c] = X[c].where(X[c].notna(), "Unknown").astype(str)
    for c in numeric:
        X[c] = pd.to_numeric(X[c], errors="coerce").astype(float)
    return X


# --------------------------------------------------------------------------- models
def make_preprocessor(numeric: list[str], categorical: list[str]) -> ColumnTransformer:
    return ColumnTransformer(
        [
            ("num", Pipeline([("impute", SimpleImputer(strategy="median", add_indicator=True)),
                              ("scale", StandardScaler())]), numeric),
            ("cat", Pipeline([("impute", SimpleImputer(strategy="constant", fill_value="Unknown")),
                              ("onehot", OneHotEncoder(handle_unknown="infrequent_if_exist", min_frequency=0.01,
                                                       sparse_output=False))]), categorical),
        ],
        verbose_feature_names_out=False,
    )


def candidate_models(task_type: str, seed: int) -> dict[str, Callable[[], Any]]:
    if task_type == "classification":
        return {
            "Baseline (base rate)": lambda: DummyClassifier(strategy="prior"),
            "Logistic regression": lambda: LogisticRegression(max_iter=3000, C=0.5),
            "Random forest": lambda: RandomForestClassifier(
                n_estimators=300, min_samples_leaf=20, max_features="sqrt", n_jobs=-1, random_state=seed),
            "Gradient boosting": lambda: HistGradientBoostingClassifier(
                learning_rate=0.05, max_iter=250, max_leaf_nodes=15, min_samples_leaf=40,
                l2_regularization=1.0, random_state=seed),
        }
    return {
        "Baseline (mean)": lambda: DummyRegressor(strategy="mean"),
        "Ridge regression": lambda: Ridge(alpha=1.0),
        "Random forest": lambda: RandomForestRegressor(n_estimators=300, min_samples_leaf=10, n_jobs=-1, random_state=seed),
        "Gradient boosting": lambda: HistGradientBoostingRegressor(learning_rate=0.05, max_iter=300, random_state=seed),
    }


def predict_score(model: Pipeline, X: pd.DataFrame, task_type: str) -> np.ndarray:
    return model.predict_proba(X)[:, 1] if task_type == "classification" else model.predict(X)


# --------------------------------------------------------------------------- evaluation
def evaluate_classifier(y: np.ndarray, p: np.ndarray, top_pct: float) -> dict:
    base = float(np.mean(y))
    n_top = max(1, int(round(top_pct * len(y))))
    order = np.argsort(-p, kind="stable")
    top = np.zeros(len(y), dtype=bool)
    top[order[:n_top]] = True
    prec_top = float(y[top].mean())
    pr, rc, th = precision_recall_curve(y, p)
    f1s = 2 * pr * rc / np.clip(pr + rc, 1e-9, None)
    best = int(np.nanargmax(f1s[:-1])) if len(th) else 0
    thr = float(th[best]) if len(th) else 0.5
    yhat = (p >= thr).astype(int)
    single_class = len(np.unique(y)) < 2
    return {
        "roc_auc": float("nan") if single_class else float(roc_auc_score(y, p)),
        "pr_auc": float("nan") if single_class else float(average_precision_score(y, p)),
        "brier": float(brier_score_loss(y, p)),
        "base_rate": base,
        "n_eval": int(len(y)),
        "positives_eval": int(y.sum()),
        "top_pct": top_pct,
        "precision_at_top": prec_top,
        "recall_at_top": float(y[top].sum() / max(1, y.sum())),
        "lift_at_top": float(prec_top / base) if base else float("nan"),
        "f1_threshold": thr,
        "precision_at_f1": float(precision_score(y, yhat, zero_division=0)),
        "recall_at_f1": float(recall_score(y, yhat, zero_division=0)),
        "f1": float(f1_score(y, yhat, zero_division=0)),
        "confusion_at_top": {
            "tp": int((top & (y == 1)).sum()), "fp": int((top & (y == 0)).sum()),
            "fn": int((~top & (y == 1)).sum()), "tn": int((~top & (y == 0)).sum()),
        },
    }


def evaluate_regressor(y: np.ndarray, p: np.ndarray) -> dict:
    return {
        "mae": float(mean_absolute_error(y, p)),
        "rmse": float(np.sqrt(mean_squared_error(y, p))),
        "r2": float(r2_score(y, p)),
        "n_eval": int(len(y)),
        "target_mean": float(np.mean(y)),
    }


def lift_table(y: np.ndarray, p: np.ndarray, bins: int = 10) -> list[dict]:
    df = pd.DataFrame({"y": y, "p": p}).sort_values("p", ascending=False).reset_index(drop=True)
    df["decile"] = (np.arange(len(df)) * bins // len(df)) + 1
    g = df.groupby("decile").agg(customers=("y", "size"), positives=("y", "sum"), avg_score=("p", "mean"))
    g["actual_rate"] = g["positives"] / g["customers"]
    g["cum_capture"] = g["positives"].cumsum() / max(1, df["y"].sum())
    g["lift"] = g["actual_rate"] / max(1e-9, df["y"].mean())
    return g.reset_index().to_dict(orient="records")


def curve_points(y: np.ndarray, p: np.ndarray, n: int = 60) -> dict:
    fpr, tpr, _ = roc_curve(y, p)
    pr, rc, _ = precision_recall_curve(y, p)
    i1 = np.linspace(0, len(fpr) - 1, min(n, len(fpr))).astype(int)
    i2 = np.linspace(0, len(pr) - 1, min(n, len(pr))).astype(int)
    return {"roc": {"fpr": fpr[i1].tolist(), "tpr": tpr[i1].tolist()},
            "pr": {"recall": rc[i2].tolist(), "precision": pr[i2].tolist()}}


# --------------------------------------------------------------------------- explanation
def driver_analysis(model: Pipeline, X: pd.DataFrame, y: np.ndarray, scores: np.ndarray, numeric: list[str],
                    categorical: list[str], uc: UseCase, seed: int) -> list[dict]:
    scoring = "average_precision" if uc.task_type == "classification" else "neg_root_mean_squared_error"
    sample = X.sample(min(len(X), 3000), random_state=seed).index
    pi = permutation_importance(model, X.loc[sample], y[X.index.get_indexer(sample)], scoring=scoring,
                                n_repeats=5, random_state=seed, n_jobs=1)
    overall = float(np.mean(scores))
    out = []
    for i, feat in enumerate(X.columns):
        d = {"feature": feat, "label": uc.label(feat), "importance": float(pi.importances_mean[i]),
             "importance_std": float(pi.importances_std[i]), "kind": "numeric" if feat in numeric else "categorical"}
        if feat in numeric:
            s = pd.Series(scores, index=X.index)
            rho = X[feat].corr(s, method="spearman")
            rho = 0.0 if rho != rho else float(rho)
            d["spearman_with_score"] = rho
            d["direction"] = "higher" if rho > 0.05 else "lower" if rho < -0.05 else "mixed"
            # outcome rate across quintiles of the feature
            try:
                present = X[feat].notna().to_numpy()
                bins = pd.qcut(X.loc[present, feat], 5, duplicates="drop")
                prof = pd.DataFrame({"bin": bins.astype(str).to_numpy(), "y": y[present]}).groupby("bin", sort=False)["y"].agg(["mean", "size"])
                order = sorted(prof.index, key=lambda b: float(b.split(",")[0].strip("(["))) if prof.index.size else []
                prof = prof.loc[order]
                missing = X[feat].isna()
                if missing.sum() >= 20:
                    prof.loc["missing"] = [float(y[missing.to_numpy()].mean()), int(missing.sum())]
                d["profile"] = [{"group": k, "outcome_rate": float(r["mean"]), "n": int(r["size"])}
                                for k, r in prof.iterrows()]
            except (ValueError, IndexError):
                d["profile"] = []
        else:
            g = pd.DataFrame({"c": X[feat], "s": scores, "y": y}).groupby("c").agg(
                mean_score=("s", "mean"), outcome_rate=("y", "mean"), n=("y", "size"))
            g = g[g["n"] >= 20].sort_values("outcome_rate", ascending=False)
            d["risky_categories"] = [str(c) for c, r in g.iterrows() if r["mean_score"] >= 1.25 * overall]
            d["direction"] = "category"
            d["profile"] = [{"group": str(c), "outcome_rate": float(r["outcome_rate"]), "n": int(r["n"])}
                            for c, r in g.iterrows()]
        out.append(d)
    out.sort(key=lambda r: -r["importance"])
    return out


def row_reasons(frame: pd.DataFrame, drivers: list[dict], top_k: int = 3, n_drivers: int = 8) -> pd.Series:
    """Plain-language reasons per row: which top drivers sit on their risky side."""
    texts = []
    floor = 0.05 * max([d["importance"] for d in drivers] + [0])  # ignore noise-level drivers
    for d in [d for d in drivers if d["importance"] > floor][:n_drivers]:
        col = d["feature"]
        if col not in frame:
            continue
        v = frame[col]
        if d["kind"] == "numeric" and d["direction"] in {"higher", "lower"}:
            num = pd.to_numeric(v, errors="coerce")
            q_hi, q_lo, med = num.quantile(0.8), num.quantile(0.2), num.median()
            mask = (num >= q_hi) & (num > med) if d["direction"] == "higher" else (num <= q_lo) & (num < med)
            word = "high" if d["direction"] == "higher" else "low"
            texts.append(np.where(mask.fillna(False), d["label"] + ": " + num.map(_fmt) + f" ({word})", None))
        elif d["kind"] == "categorical" and d.get("risky_categories"):
            mask = v.astype(str).isin(d["risky_categories"])
            texts.append(np.where(mask, d["label"] + ": " + v.astype(str), None))
    if not texts:
        return pd.Series([""] * len(frame), index=frame.index)
    mat = np.column_stack(texts)
    return pd.Series(["; ".join([t for t in row if t][:top_k]) for row in mat], index=frame.index)


# --------------------------------------------------------------------------- business framing
def campaign_simulation(y: np.ndarray, p: np.ndarray, value: np.ndarray | None, biz: dict) -> dict:
    n = len(y)
    n_target = max(1, int(round(biz["target_top_pct"] * n)))
    order = np.argsort(-p, kind="stable")[:n_target]
    caught = int(y[order].sum())
    random_caught = float(biz["target_top_pct"] * y.sum())
    avg_value = float(np.mean(value[order][y[order] == 1])) if value is not None and caught else float(np.mean(value)) if value is not None else 0.0
    saved = caught * biz["save_rate"]
    benefit = saved * avg_value * biz["value_months"]
    cost = n_target * biz["contact_cost_ghs"] + saved * biz["offer_cost_ghs"]
    random_saved = random_caught * biz["save_rate"]
    random_benefit = random_saved * avg_value * biz["value_months"]
    random_cost = n_target * biz["contact_cost_ghs"] + random_saved * biz["offer_cost_ghs"]
    return {
        "population": n, "contacted": n_target, "positives_in_population": int(y.sum()),
        "positives_reached_model": caught, "positives_reached_random": round(random_caught, 1),
        "customers_saved_model": round(saved, 1), "customers_saved_random": round(random_saved, 1),
        "avg_monthly_value_reached": round(avg_value, 2),
        "revenue_protected_model_ghs": round(benefit, 0), "campaign_cost_model_ghs": round(cost, 0),
        "net_benefit_model_ghs": round(benefit - cost, 0),
        "revenue_protected_random_ghs": round(random_benefit, 0),
        "net_benefit_random_ghs": round(random_benefit - random_cost, 0),
        "roi_model": round((benefit - cost) / cost, 2) if cost else None,
        "assumptions": biz,
    }


def assign_tiers(scores: np.ndarray) -> np.ndarray:
    ranks = pd.Series(scores).rank(ascending=False, method="first").to_numpy() / len(scores)
    return np.where(ranks <= 0.10, "High", np.where(ranks <= 0.30, "Medium", "Low"))


# --------------------------------------------------------------------------- orchestration
@dataclass
class RunOutput:
    run_id: int
    metrics: dict
    details: dict
    scored: pd.DataFrame = field(repr=False)


def train_use_case(workspace_id: int, dataset_id: int, uc: UseCase, user_id: int | None = None,
                   seed: int = 42, positive_class: Any = None, progress: Callable[[str], None] | None = None) -> RunOutput:
    say = progress or (lambda msg: None)
    t0 = time.time()
    dataset = get_dataset(dataset_id, workspace_id)
    engine = get_engine()
    with engine.begin() as conn:
        run_id = conn.execute(model_runs.insert().values(
            workspace_id=workspace_id, dataset_id=dataset_id, use_case_id=uc.id, task_type=uc.task_type,
            status="running", created_by=user_id)).inserted_primary_key[0]
    try:
        out = _train(run_id, workspace_id, dataset, uc, seed, positive_class, say)
    except Exception as exc:
        with engine.begin() as conn:
            conn.execute(update(model_runs).where(model_runs.c.id == run_id).values(
                status="failed", details=json.dumps({"error": str(exc)[:500]})))
        log.exception("training failed run_id=%s use_case=%s", run_id, uc.id)
        audit("model.failed", workspace_id, user_id, run_id=run_id, error=str(exc)[:200])
        raise
    audit("model.trained", workspace_id, user_id, run_id=run_id, use_case=uc.id,
          model=out.details["best_model"], seconds=round(time.time() - t0, 1))
    return out


def _train(run_id: int, workspace_id: int, dataset: dict, uc: UseCase, seed: int, positive_class: Any,
           say: Callable[[str], None]) -> RunOutput:
    info: dict[str, Any] = {"use_case": uc.id, "use_case_name": uc.name, "task_type": uc.task_type}
    # ---------------------------------------------------------------- 1. data
    if uc.is_snapshot_based:
        as_of = to_date(dataset["as_of_date"])
        train_snaps, test_snap, score_snap = plan_snapshots(uc, as_of)
        say(f"Extracting features with SQL for {len(train_snaps) + 2} snapshots")
        train_df = pd.concat([build_snapshot_frame(uc, workspace_id, s) for s in train_snaps], ignore_index=True)
        test_df = build_snapshot_frame(uc, workspace_id, test_snap)
        score_df = build_snapshot_frame(uc, workspace_id, score_snap, labelled=False)
        info.update(validation="out-of-time", train_snapshots=train_snaps, test_snapshot=test_snap,
                    score_snapshot=score_snap)
    else:
        say("Loading uploaded dataset")
        full, extra = load_custom_frame(uc, positive_class)
        info.update(extra)
        strat = full["target"] if uc.task_type == "classification" else None
        train_df, test_df = train_test_split(full, test_size=0.25, random_state=seed, stratify=strat)
        score_df = full.copy()
        score_snap = None
        info.update(validation="random 75/25 split", score_snapshot=None)

    if len(train_df) < MIN_ROWS or len(test_df) < 50:
        raise PipelineError("Not enough rows to train and evaluate a model.")
    if uc.task_type == "classification" and (train_df["target"].sum() < MIN_POSITIVES or test_df["target"].sum() < 5):
        raise PipelineError("Too few positive outcomes to learn from.")

    feature_cols = [c for c in train_df.columns if c not in META_COLS]
    numeric, categorical = split_feature_types(train_df[feature_cols])
    dropped = sorted(set(feature_cols) - set(numeric) - set(categorical))
    X_tr, X_te = prepare_X(train_df, numeric, categorical), prepare_X(test_df, numeric, categorical)
    y_tr, y_te = train_df["target"].to_numpy(), test_df["target"].to_numpy()
    info.update(rows_train=len(X_tr), rows_test=len(X_te), rows_scored=len(score_df), numeric_features=numeric,
                categorical_features=categorical, dropped_columns=dropped,
                target_rate_train=float(np.mean(y_tr)), target_rate_test=float(np.mean(y_te)))

    # ---------------------------------------------------------------- 2. candidates
    comparison, fitted, test_scores = [], {}, {}
    top_pct = uc.business["target_top_pct"]
    for name, factory in candidate_models(uc.task_type, seed).items():
        say(f"Training {name}")
        t = time.time()
        model = Pipeline([("prep", make_preprocessor(numeric, categorical)), ("model", factory())])
        model.fit(X_tr, y_tr)
        p = predict_score(model, X_te, uc.task_type)
        m = evaluate_classifier(y_te, p, top_pct) if uc.task_type == "classification" else evaluate_regressor(y_te, p)
        m["fit_seconds"] = round(time.time() - t, 2)
        comparison.append({"model": name, **m})
        fitted[name], test_scores[name] = model, p
    if uc.task_type == "classification":
        best = max(comparison, key=lambda r: (r["pr_auc"] if r["pr_auc"] == r["pr_auc"] else -1))
        baseline = comparison[0]
    else:
        best = min(comparison, key=lambda r: r["rmse"])
        baseline = comparison[0]
    best_name = best["model"]
    best_model, p_best = fitted[best_name], test_scores[best_name]
    info["comparison"] = comparison
    info["best_model"] = best_name
    info["selection_metric"] = "pr_auc" if uc.task_type == "classification" else "rmse"
    info["baseline"] = baseline

    # ---------------------------------------------------------------- 3. explain + business
    say("Explaining model drivers")
    info["drivers"] = driver_analysis(best_model, X_te, y_te, p_best, numeric, categorical, uc, seed)
    if uc.task_type == "classification":
        info["lift_table"] = lift_table(y_te, p_best)
        info["curves"] = curve_points(y_te, p_best)
        val = test_df[uc.value_column].to_numpy(dtype=float) if uc.value_column in test_df else None
        info["campaign_simulation"] = campaign_simulation(y_te, p_best, val, uc.business)

    # ---------------------------------------------------------------- 4. refit + score
    say("Refitting on all labelled data and scoring")
    full_train = pd.concat([train_df, test_df], ignore_index=True)
    final = Pipeline([("prep", make_preprocessor(numeric, categorical)),
                      ("model", candidate_models(uc.task_type, seed)[best_name]())])
    final.fit(prepare_X(full_train, numeric, categorical), full_train["target"].to_numpy())
    X_sc = prepare_X(score_df, numeric, categorical)
    s = predict_score(final, X_sc, uc.task_type)
    scored = pd.DataFrame({"entity_id": score_df["entity_id"].astype(str).to_numpy(), "score": s})
    scored["value"] = score_df[uc.value_column].to_numpy(dtype=float) if uc.value_column in score_df else np.nan
    if uc.task_type == "classification":
        scored["risk_tier"] = assign_tiers(s)
    else:
        scored["risk_tier"] = pd.qcut(pd.Series(s).rank(method="first"), 3, labels=["Low", "Medium", "High"]).astype(str).to_numpy()
    scored["top_reasons"] = row_reasons(score_df.reset_index(drop=True), info["drivers"]).to_numpy()
    scored = scored.sort_values("score", ascending=False).reset_index(drop=True)

    if uc.task_type == "classification":
        v = scored["value"].fillna(0).to_numpy()
        months = uc.business["value_months"]
        tiers = scored.groupby("risk_tier").agg(customers=("score", "size"), avg_score=("score", "mean"),
                                                expected_positives=("score", "sum"), monthly_value=("value", "sum"))
        info["scoring_summary"] = {
            "population": len(scored),
            "expected_positives": float(scored["score"].sum()),
            "expected_rate": float(scored["score"].mean()),
            "expected_value_at_risk_ghs": float(np.sum(scored["score"].to_numpy() * v) * months),
            "value_months": months,
            "tiers": tiers.reset_index().to_dict(orient="records"),
            "high_tier_value_at_risk_ghs": float(np.sum((scored["score"] * scored["value"].fillna(0))[scored["risk_tier"] == "High"]) * months),
        }
        reason_counts = (scored[scored["risk_tier"] == "High"]["top_reasons"].str.split("; ").explode()
                         .str.split(":").str[0].value_counts().head(8))
        info["high_risk_reason_counts"] = [{"reason": k, "customers": int(c)} for k, c in reason_counts.items() if k]

    # ---------------------------------------------------------------- 5. monitoring baseline
    ref = prepare_X(full_train, numeric, categorical)
    info["drift"] = feature_drift(ref, X_sc, numeric, categorical)
    p_ref = predict_score(final, ref, uc.task_type)
    score_psi = psi_numeric(pd.Series(p_ref), pd.Series(s))
    info["score_drift"] = {"psi": score_psi, "status": psi_status(score_psi),
                           "mean_reference": float(np.mean(p_ref)), "mean_current": float(np.mean(s))}

    # ---------------------------------------------------------------- 6. persist
    settings = get_settings()
    art_dir = settings.storage_dir / f"ws_{workspace_id}" / "models"
    art_dir.mkdir(parents=True, exist_ok=True)
    art_path = art_dir / f"run_{run_id}.joblib"
    joblib.dump({"model": final, "numeric": numeric, "categorical": categorical, "use_case": uc.id,
                 "task_type": uc.task_type, "best_model": best_name, "trained_at": time.time()}, art_path)
    metrics = {k: v for k, v in best.items() if not isinstance(v, dict)}
    details = to_jsonable(info)
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(update(model_runs).where(model_runs.c.id == run_id).values(
            status="completed", best_model=best_name, metrics=json.dumps(to_jsonable(metrics)),
            details=json.dumps(details), artifact_path=str(art_path.relative_to(settings.storage_dir)),
            score_snapshot=score_snap))
    pred_rows = scored.assign(run_id=run_id, workspace_id=workspace_id)
    bulk_insert(predictions, pred_rows[["run_id", "workspace_id", "entity_id", "score", "risk_tier", "value", "top_reasons"]], engine)
    log.info("run completed run_id=%s best=%s metrics=%s", run_id, best_name,
             {k: round(v, 3) for k, v in metrics.items() if isinstance(v, float)})
    return RunOutput(run_id, to_jsonable(metrics), details, scored)


# --------------------------------------------------------------------------- reading runs back
def latest_run(workspace_id: int, use_case_id: str | None = None) -> dict | None:
    from sqlalchemy import select

    q = select(model_runs).where(model_runs.c.workspace_id == workspace_id, model_runs.c.status == "completed")
    if use_case_id:
        q = q.where(model_runs.c.use_case_id == use_case_id)
    with get_engine().connect() as conn:
        row = conn.execute(q.order_by(model_runs.c.id.desc())).mappings().first()
    return _decode_run(row)


def get_run(run_id: int, workspace_id: int) -> dict | None:
    from sqlalchemy import select

    with get_engine().connect() as conn:
        row = conn.execute(select(model_runs).where(model_runs.c.id == run_id,
                                                    model_runs.c.workspace_id == workspace_id)).mappings().first()
    return _decode_run(row)


def _decode_run(row) -> dict | None:
    if not row:
        return None
    d = dict(row)
    d["metrics"] = json.loads(d["metrics"] or "{}")
    d["details"] = json.loads(d["details"] or "{}")
    return d


def run_predictions(run_id: int, workspace_id: int) -> pd.DataFrame:
    return run_query(
        "SELECT entity_id, score, risk_tier, value, top_reasons FROM predictions "
        "WHERE run_id = :r AND workspace_id = :ws ORDER BY score DESC",
        {"r": run_id, "ws": workspace_id},
    )


def load_artifact(run: dict) -> dict:
    path = get_settings().storage_dir / run["artifact_path"]
    return joblib.load(Path(path))
