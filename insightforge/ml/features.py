"""Feature engineering steps applied after SQL extraction.

SQL does the heavy lifting (joins, windows, aggregation). The Python transforms
here only derive point-in-time quantities that need the snapshot date (tenure,
age) and ratios that are clearer in pandas.
"""
from __future__ import annotations

from datetime import date
from typing import Callable

import numpy as np
import pandas as pd


def _ratio(num: pd.Series, den: pd.Series, clip: float = 5.0) -> pd.Series:
    num = pd.to_numeric(num, errors="coerce")
    den = pd.to_numeric(den, errors="coerce")
    r = num / den.where(den > 0)
    return r.clip(upper=clip)


def telco_customer(df: pd.DataFrame, snapshot: date) -> pd.DataFrame:
    out = df.copy()
    snap = pd.Timestamp(snapshot)
    start = pd.to_datetime(out.pop("start_date"), errors="coerce")
    dob = pd.to_datetime(out.pop("date_of_birth"), errors="coerce")
    out["tenure_months"] = ((snap - start).dt.days / 30.44).round(1)
    age = ((snap - dob).dt.days / 365.25).round(0)
    out["age"] = age.where(age.between(16, 100))  # implausible ages treated as missing
    out["data_usage_trend"] = _ratio(out["data_gb_avg_3m"], out["data_gb_avg_prev3m"])
    out["voice_usage_trend"] = _ratio(out["voice_min_avg_3m"], out["voice_min_avg_prev3m"])
    out["data_utilisation"] = _ratio(out["data_gb_avg_3m"], out["plan_data_gb"])
    out["last_month_vs_avg"] = _ratio(out["data_gb_last_month"], out["data_gb_avg_3m"])
    out["autopay"] = out["autopay"].astype(int)
    return out


def identity(df: pd.DataFrame, snapshot: date | None = None) -> pd.DataFrame:
    return df.copy()


TRANSFORMS: dict[str, Callable[[pd.DataFrame, date], pd.DataFrame]] = {
    "telco_customer": telco_customer,
    "identity": identity,
}


def split_feature_types(X: pd.DataFrame, max_categories: int = 50) -> tuple[list[str], list[str]]:
    """Numeric vs categorical columns; high-cardinality text columns are dropped."""
    numeric, categorical = [], []
    for col in X.columns:
        if pd.api.types.is_bool_dtype(X[col]):
            numeric.append(col)
        elif pd.api.types.is_numeric_dtype(X[col]):
            numeric.append(col)
        elif X[col].nunique(dropna=True) <= max_categories:
            categorical.append(col)
    return numeric, categorical


def coerce_numeric_like(df: pd.DataFrame) -> pd.DataFrame:
    """Object columns that are really numbers (e.g. from MySQL Decimals) -> float."""
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == object:
            conv = pd.to_numeric(out[col], errors="coerce")
            if conv.notna().sum() >= 0.95 * out[col].notna().sum() and out[col].notna().any():
                out[col] = conv
        elif str(out[col].dtype) == "Int64":
            out[col] = out[col].astype(float)
    return out.replace([np.inf, -np.inf], np.nan)
