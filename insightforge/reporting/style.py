"""Shared visual language for charts (PDF via matplotlib, app via Plotly).

Categorical slots are used in a fixed order (validated palette, adjacent-pair
CVD-safe); status colours are reserved for risk tiers and always shown with a
text label.
"""
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"
SURFACE = "#fcfcfb"
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
TIER_COLORS = {"High": STATUS["critical"], "Medium": STATUS["serious"], "Low": "#86b6ef"}
VERIFY_COLORS = {"verified": STATUS["good"], "needs_review": STATUS["warning"], "blocked": STATUS["critical"],
                 "error": STATUS["critical"]}
VERIFY_LABELS = {"verified": "Verified - every figure matches the source data",
                 "needs_review": "Needs review - some figures could not be matched to the source data",
                 "blocked": "Blocked by safety checks", "error": "Agent error"}
