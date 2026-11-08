"""Static (matplotlib) charts for the PDF report."""
from __future__ import annotations

from pathlib import Path

import matplotlib
import matplotlib.dates

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from .style import GRID, SERIES, TEXT_PRIMARY, TEXT_SECONDARY, TIER_COLORS  # noqa: E402

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 8.5, "axes.edgecolor": GRID, "axes.labelcolor": TEXT_SECONDARY,
    "xtick.color": TEXT_SECONDARY, "ytick.color": TEXT_SECONDARY, "axes.titlesize": 10,
    "axes.titleweight": "bold", "axes.titlecolor": TEXT_PRIMARY, "axes.titlelocation": "left",
    "figure.facecolor": "white", "axes.facecolor": "white", "savefig.dpi": 200,
})


def _frame(ax, grid_axis: str = "y") -> None:
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(GRID)
    if grid_axis in ("x", "y", "both"):
        ax.grid(axis=grid_axis, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


def _save(fig, path: Path) -> Path:
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def churn_trend(trend: pd.DataFrame, path: Path) -> Path:
    fig, ax = plt.subplots(figsize=(7.2, 2.6))
    x = pd.to_datetime(trend["month_start"])
    ax.plot(x, trend["churn_rate_pct"], color=SERIES[0], linewidth=2, marker="o", markersize=3.5, label="Monthly churn rate")
    ax.plot(x, trend["churn_rate_3m_avg"], color=SERIES[1], linewidth=2, linestyle="--", label="3-month average")
    peak = trend.loc[trend["churn_rate_pct"].idxmax()]
    ax.annotate(f"{peak['churn_rate_pct']:.2f}%", (pd.to_datetime(peak["month_start"]), peak["churn_rate_pct"]),
                xytext=(0, 6), textcoords="offset points", ha="center", color=TEXT_PRIMARY, fontsize=8)
    ax.set_ylabel("% of active base")
    ax.set_title("Monthly churn rate")
    ax.legend(frameon=False, loc="lower left", ncol=2)
    ax.set_ylim(0, trend["churn_rate_pct"].max() * 1.25)
    _frame(ax)
    fig.autofmt_xdate(rotation=0, ha="center")
    return _save(fig, path)


def hbar(df: pd.DataFrame, label_col: str, value_col: str, title: str, path: Path, unit: str = "%",
         color: str = SERIES[0], figsize=(3.5, 2.2), fmt: str = "{:.1f}") -> Path:
    df = df.iloc[::-1]
    fig, ax = plt.subplots(figsize=figsize)
    bars = ax.barh(df[label_col].astype(str), df[value_col], color=color, height=0.6)
    for b, v in zip(bars, df[value_col]):
        ax.text(b.get_width(), b.get_y() + b.get_height() / 2, " " + fmt.format(v) + unit, va="center",
                color=TEXT_PRIMARY, fontsize=7.5)
    ax.set_title(title)
    ax.set_xlim(0, df[value_col].max() * 1.25)
    ax.set_xticks([])
    _frame(ax, "none")
    ax.spines["bottom"].set_visible(False)
    return _save(fig, path)


def grouped_regions(shift: list[dict], path: Path, top: int = 6) -> Path:
    df = pd.DataFrame([r for r in shift if r["region"] != "Unknown"]).head(top).iloc[::-1]
    fig, ax = plt.subplots(figsize=(3.5, 2.4))
    y = range(len(df))
    ax.barh([i + 0.2 for i in y], df["avg_monthly_churn_pct_last_6m"], height=0.38, color=SERIES[0], label="Last 6 months")
    ax.barh([i - 0.2 for i in y], df["avg_monthly_churn_pct_prior_6m"], height=0.38, color=SERIES[1], label="Prior 6 months")
    ax.set_yticks(list(y), df["region"])
    ax.set_title("Avg monthly churn by region")
    ax.set_xlabel("% per month")
    ax.legend(frameon=False, fontsize=7, loc="lower right")
    _frame(ax, "x")
    return _save(fig, path)


def region_vs_rest(region_df: pd.DataFrame, region: str, path: Path) -> Path:
    df = region_df.copy()
    df["month_start"] = pd.to_datetime(df["month_start"])
    df["grp"] = df["region"].where(df["region"] == region, "Rest of country")
    g = df.groupby(["month_start", "grp"])[["churned", "active_at_start"]].sum()
    rate = (100 * g["churned"] / g["active_at_start"]).unstack()
    fig, ax = plt.subplots(figsize=(3.5, 2.4))
    for col, color in ((region, SERIES[0]), ("Rest of country", SERIES[1])):
        ax.plot(rate.index, rate[col], color=color, linewidth=2, label=col)
    ax.set_title(f"Monthly churn: {region} vs rest")
    ax.set_ylabel("% per month")
    ax.set_ylim(0, rate.max().max() * 1.3)
    ax.legend(frameon=False, fontsize=7, loc="upper left", ncol=2)
    ax.xaxis.set_major_locator(matplotlib.dates.MonthLocator(bymonth=[1, 4, 7, 10]))
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b %y"))
    _frame(ax)
    return _save(fig, path)


def capture_curve(lift: list[dict], path: Path) -> Path:
    df = pd.DataFrame(lift)
    x = [0] + (df["decile"] * 10).tolist()
    y = [0] + (df["cum_capture"] * 100).tolist()
    fig, ax = plt.subplots(figsize=(3.5, 2.4))
    ax.plot(x, y, color=SERIES[0], linewidth=2, marker="o", markersize=3, label="Model")
    ax.plot([0, 100], [0, 100], color=TEXT_SECONDARY, linewidth=1, linestyle=":", label="Random")
    ax.annotate(f"{y[2]:.0f}% at 20%", (20, y[2]), xytext=(6, -10), textcoords="offset points", fontsize=7.5, color=TEXT_PRIMARY)
    ax.set_xlabel("% of customers contacted (riskiest first)")
    ax.set_ylabel("% of churners reached")
    ax.set_title("Who the model catches")
    ax.legend(frameon=False, fontsize=7, loc="lower right")
    _frame(ax)
    return _save(fig, path)


def drivers(drv: list[dict], path: Path, top: int = 8) -> Path:
    df = pd.DataFrame(drv).head(top)
    df["importance"] = df["importance"].clip(lower=0)
    return hbar(df, "label", "importance", "Top model drivers (permutation importance)", path, unit="",
                fmt="{:.3f}", figsize=(7.2, 2.6))


def tiers(tier_rows: list[dict], path: Path) -> Path:
    order = ["High", "Medium", "Low"]
    df = pd.DataFrame(tier_rows).set_index("risk_tier").reindex(order).reset_index()
    fig, ax = plt.subplots(figsize=(3.5, 1.8))
    bars = ax.barh(df["risk_tier"][::-1], df["customers"][::-1], color=[TIER_COLORS[t] for t in order[::-1]], height=0.55)
    for b, (_, r) in zip(bars, df[::-1].iterrows()):
        ax.text(b.get_width(), b.get_y() + b.get_height() / 2,
                f" {int(r['customers']):,} customers - avg risk {100 * r['avg_score']:.1f}%", va="center", fontsize=7.5)
    ax.set_xlim(0, df["customers"].max() * 2.1)
    ax.set_xticks([])
    ax.set_title("Customers by risk tier")
    _frame(ax, "none")
    ax.spines["bottom"].set_visible(False)
    return _save(fig, path)
