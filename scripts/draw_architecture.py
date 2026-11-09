"""Draws docs/architecture.png (the system diagram used in the architecture note)."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "docs" / "architecture.png"
INK, MUTED, LINE = "#0b0b0b", "#52514e", "#b9b8b2"
FILL = {"ui": "#cde2fb", "svc": "#f0efec", "data": "#d3f0e3", "ai": "#fde3d6", "ext": "#ffffff"}


def box(ax, x, y, w, h, title, lines, kind):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.12",
                                fc=FILL[kind], ec=MUTED if kind != "ext" else LINE, lw=1.0,
                                ls="--" if kind == "ext" else "-"))
    ax.text(x + 0.12, y + h - 0.2, title, fontsize=9.5, weight="bold", color=INK, va="top")
    ax.text(x + 0.12, y + h - 0.55, "\n".join(lines), fontsize=7.6, color=MUTED, va="top", linespacing=1.45)


def arrow(ax, a, b, label=None, rad=0.0):
    ax.add_patch(FancyArrowPatch(a, b, arrowstyle="-|>", mutation_scale=10, color=MUTED, lw=1.0,
                                 connectionstyle=f"arc3,rad={rad}"))
    if label:
        ax.text(max(a[0], b[0]) + 0.08, (a[1] + b[1]) / 2, label, fontsize=7, color=MUTED, ha="left", va="center")


def main() -> None:
    fig, ax = plt.subplots(figsize=(11, 6.4))
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 9.3)
    ax.axis("off")
    # users / clients
    box(ax, 0.3, 7.2, 3.4, 1.7, "Business users", ["Browser", "Sign-up / login", "Workspace journey"], "ext")
    box(ax, 0.3, 4.9, 3.4, 1.7, "Other systems", ["CRM, dialler, BI", "Bearer-token REST"], "ext")
    # presentation
    box(ax, 4.6, 7.2, 3.6, 1.7, "HTML, CSS, JavaScript", ["Onboarding, data, SQL,", "model, actions, AI analyst,", "report, monitoring"], "ui")
    box(ax, 4.6, 4.9, 3.6, 1.7, "FastAPI service", ["/health  /model", "/predictions  /insights"], "ui")
    # services
    box(ax, 9.1, 4.3, 3.4, 4.6, "Service layer (Python)", [
        "auth - workspaces, PBKDF2,", "  audit log", "ingest - synthetic / CSV", "analysis - named SQL",
        "  (DQ checks, KPIs)", "ml.pipeline - use-case", "  registry (YAML), train,", "  evaluate, explain, score",
        "ml.monitoring - PSI", "reporting - PDF"], "svc")
    box(ax, 13.1, 6.1, 2.6, 2.8, "AI layer", ["context builder", "(aggregates, no PII)", "guardrails: prompt,", "number grounding,",
                                            "PII / overclaim checks", "audit (ai_outputs)"], "ai")
    box(ax, 13.1, 3.5, 2.6, 1.9, "Snwolley Agent", ["API Toolkit (HTTPS)", "key from env vars", "mock fallback"], "ext")
    # data
    box(ax, 4.6, 0.2, 7.9, 3.6, "Relational store - MySQL 8 / MariaDB (SQLite for local dev)", [
        "Platform: workspaces, users, datasets, model_runs, predictions,",
        "     ai_outputs, audit_log",
        "Bronze (raw, per-tenant via workspace_id): customers, plans, subscriptions,",
        "     monthly_usage, payments, support_tickets, uploaded CSV tables",
        "Silver (views): v_customers_clean, v_subscriptions_clean, v_usage_clean, ...",
        "     de-duplicated, standardised, PII columns dropped",
        "Gold (SQL at run time): point-in-time feature snapshots and labels"], "data")
    box(ax, 13.1, 0.4, 2.6, 2.4, "Artifact storage", ["model .joblib per run", "PDF reports", "(object storage", " in production)"], "data")
    # arrows
    arrow(ax, (3.7, 8.05), (4.6, 8.05))
    arrow(ax, (3.7, 5.75), (4.6, 5.75))
    arrow(ax, (8.2, 8.05), (9.1, 8.05))
    arrow(ax, (8.2, 5.75), (9.1, 5.75))
    arrow(ax, (12.5, 7.6), (13.1, 7.6))
    arrow(ax, (14.4, 6.1), (14.4, 5.4))
    arrow(ax, (10.8, 4.3), (10.8, 3.8), "SQL")
    arrow(ax, (12.5, 4.5), (13.1, 2.4), rad=-0.2)
    fig.savefig(OUT, dpi=170, bbox_inches="tight", facecolor="white")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
