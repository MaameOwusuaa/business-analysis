"""Drift monitoring utilities (Population Stability Index)."""
from __future__ import annotations

import numpy as np
import pandas as pd

EPS = 1e-4


def _psi(e: np.ndarray, a: np.ndarray) -> float:
    e = np.clip(e, EPS, None)
    a = np.clip(a, EPS, None)
    return float(np.sum((a - e) * np.log(a / e)))


def psi_numeric(expected: pd.Series, actual: pd.Series, bins: int = 10) -> float:
    e = pd.to_numeric(expected, errors="coerce").dropna()
    a = pd.to_numeric(actual, errors="coerce").dropna()
    if len(e) < 20 or len(a) < 20:
        return float("nan")
    edges = np.unique(np.quantile(e, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:  # near-constant feature: compare value shares instead
        return psi_categorical(e.round(3).astype(str), a.round(3).astype(str))
    edges[0], edges[-1] = -np.inf, np.inf
    e_share = np.histogram(e, edges)[0] / len(e)
    a_share = np.histogram(a, edges)[0] / len(a)
    return _psi(e_share, a_share)


def psi_categorical(expected: pd.Series, actual: pd.Series) -> float:
    e = expected.fillna("Unknown").astype(str).value_counts(normalize=True)
    a = actual.fillna("Unknown").astype(str).value_counts(normalize=True)
    cats = e.index.union(a.index)
    return _psi(e.reindex(cats, fill_value=0).to_numpy(), a.reindex(cats, fill_value=0).to_numpy())


def psi_status(value: float) -> str:
    if value != value:  # NaN
        return "insufficient data"
    if value < 0.1:
        return "stable"
    if value < 0.25:
        return "watch"
    return "drift"


def feature_drift(reference: pd.DataFrame, current: pd.DataFrame, numeric: list[str],
                  categorical: list[str]) -> list[dict]:
    rows = []
    for col in numeric + categorical:
        if col not in reference or col not in current:
            continue
        value = psi_numeric(reference[col], current[col]) if col in numeric else psi_categorical(reference[col], current[col])
        rows.append(
            {
                "feature": col,
                "psi": round(value, 4) if value == value else None,
                "status": psi_status(value),
                "missing_pct_reference": round(100 * reference[col].isna().mean(), 2),
                "missing_pct_current": round(100 * current[col].isna().mean(), 2),
            }
        )
    return sorted(rows, key=lambda r: -(r["psi"] or 0))
