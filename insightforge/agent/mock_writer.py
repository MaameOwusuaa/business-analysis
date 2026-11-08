"""Deterministic narrative writer used by the mock Snwolley client.

It turns a context pack into Markdown using templates. Every figure it prints
comes straight from the context, so its output always passes the grounding
check - which also makes it a useful reference when evaluating the live agent.
"""
from __future__ import annotations

import re
from typing import Any


def _money(v: Any) -> str:
    return "n/a" if v is None else f"GHS {float(v):,.0f}"


def _pct(v: Any, nd: int = 1) -> str:
    return "n/a" if v is None else f"{float(v):.{nd}f}%"


def _n(v: Any) -> str:
    return "n/a" if v is None else f"{float(v):,.0f}"


def _x(v: Any) -> str:
    return "n/a" if v is None else f"{float(v):.2f}"


def _strip_prefix(label: str) -> str:
    return re.sub(r"^\d+\.\s*", "", str(label))


def _business(ctx: dict) -> str:
    return (ctx.get("business") or {}).get("name") or "The business"


# --------------------------------------------------------------------------- data quality
FIXES = {
    "duplicate": "Enforce a unique phone number at registration and merge existing duplicates in the CRM.",
    "region": "Replace free-text region entry with a drop-down of the official regions.",
    "status": "Make cancellation update status and end date in one transaction so they cannot disagree.",
    "end date": "Add a database rule that the end date cannot precede the start date.",
    "posted": "Make billing idempotent (one bill per subscription per month) to stop double posting.",
    "usage": "Add range checks in the usage-mediation feed and alert on negative or extreme readings.",
    "csat": "Validate survey scores at capture (1-5 only).",
    "resolution": "Derive resolution time from ticket timestamps instead of manual entry.",
    "date of birth": "Make date of birth mandatory at onboarding (with consent) where it is needed.",
    "payments dated": "Reconcile payment dates against the mobile-money settlement file.",
}


def data_quality_brief(ctx: dict) -> str:
    dq = ctx["data_quality"]
    issues = dq["issues"]
    high = [i for i in issues if i["severity"] == "high"]
    other = [i for i in issues if i["severity"] != "high"]
    lines = ["### What we found",
             f"{dq['checks_with_issues']} of {dq['checks_run']} automated checks found issues. "
             f"{len(high)} are high severity:"]
    for i in high or issues[:3]:
        lines.append(f"- **{i['description']}** - {_n(i['issues'])} records ({_pct(i['issue_pct'], 2)} of {i['table']}). {i['handling']}")
    if other:
        lines.append("Lower-severity issues: " + "; ".join(
            f"{i['description'].lower()} ({_n(i['issues'])})" for i in other[:6]) + ".")
    lines += ["", "### Why it matters"]
    text = " ".join(i["description"].lower() for i in issues)
    why = []
    if "duplicate" in text:
        why.append("Duplicate customer and billing records inflate customer counts and revenue, so KPIs would be overstated.")
    if "status" in text or "end date" in text:
        why.append("Subscriptions whose status or dates disagree would misstate who has churned - the very outcome the model learns from.")
    if "region" in text:
        why.append("Inconsistent region labels split one region into several, hiding regional trends.")
    if "usage" in text:
        why.append("Bad usage readings distort the usage-trend features that the model relies on.")
    lines += why or ["These issues can bias KPIs and model inputs if left untreated."]
    lines.append("All issues above are handled automatically in the cleaned data layer before analysis and modelling.")
    lines += ["", "### Recommended fixes at source"]
    added = set()
    for i in issues:
        d = i["description"].lower()
        for key, fix in FIXES.items():
            if key in d and fix not in added:
                lines.append(f"- {fix}")
                added.add(fix)
    return "\n".join(lines)


# --------------------------------------------------------------------------- KPIs
def kpi_explainer(ctx: dict) -> str:
    a = ctx["analysis"]
    k = a["kpis"]
    trend = a["monthly_churn_trend_last_12m"]
    lines = ["### Headline",
             f"As of {a['as_of']}, {_business(ctx)} has {_n(k['active_customers'])} active customers generating "
             f"{_money(k['monthly_recurring_revenue_ghs'])} a month (ARPU {_money(k['arpu_ghs'])}). "
             f"{_n(k['churned_last_90_days'])} customers left in the last 90 days - a 90-day churn rate of "
             f"{_pct(k['churn_rate_90d_pct'], 2)} - while {_n(k['new_customers_last_90_days'])} joined "
             f"(net adds {_n(k['net_adds_last_90_days'])}).",
             "", "### What changed"]
    if trend:
        first, last = trend[0], trend[-1]
        lines.append(f"The 3-month average churn rate moved from {_pct(first['churn_rate_3m_avg'], 2)} in {first['month']} "
                     f"to {_pct(last['churn_rate_3m_avg'], 2)} in {last['month']}.")
    peaks = a.get("highest_churn_months_last_12m", [])
    if peaks:
        lines.append("Highest monthly churn in the last 12 months: " + ", ".join(
            f"{p['month']} ({_pct(p['churn_rate_pct'], 2)}, {_n(p['churned'])} customers)" for p in peaks) + ".")
    shift = [r for r in a.get("regional_churn_shift", []) if r["region"] != "Unknown"]
    if shift:
        top = shift[0]
        lines.append(f"The largest regional spike was in **{top['region']}**: monthly churn averaged "
                     f"{_pct(top['worst_3m_avg_monthly_churn_pct'], 2)} from {top['worst_3m_window']}, against "
                     f"{_pct(top['rest_of_country_same_window_pct'], 2)} in the rest of the country "
                     f"({top['worst_3m_excess_pp']:+.2f} pp). Over the last 6 months it averaged "
                     f"{_pct(top['avg_monthly_churn_pct_last_6m'], 2)} versus {_pct(top['avg_monthly_churn_pct_prior_6m'], 2)} "
                     f"in the 6 months before.")
    reasons = a.get("cancel_reasons_last_12m", [])
    if reasons:
        lines.append("Top recorded cancellation reasons: " + ", ".join(
            f"{r['cancel_reason']} ({_pct(r['share_pct'])})" for r in reasons[:3]) + ".")
    lines += ["", "### Early warning signals"]
    usage = {_strip_prefix(r["usage_trend"]): r for r in a.get("churn_by_usage_trend_90d", [])}
    if "Down 10-40%" in usage and "Stable (+/-10%)" in usage:
        lines.append(f"- **Falling usage**: customers whose data use fell 10-40% churned at "
                     f"{_pct(usage['Down 10-40%']['churn_rate_90d_pct'], 2)} over 90 days, versus "
                     f"{_pct(usage['Stable (+/-10%)']['churn_rate_90d_pct'], 2)} for stable users.")
    pay = a.get("churn_by_payment_behaviour_90d", [])
    if len(pay) >= 2:
        worst = max(pay, key=lambda r: r["churn_rate_90d_pct"])
        lines.append(f"- **Payment behaviour**: '{_strip_prefix(worst['payment_profile'])}' customers churned at "
                     f"{_pct(worst['churn_rate_90d_pct'], 2)} versus {_pct(pay[0]['churn_rate_90d_pct'], 2)} for those always on time.")
    sup = a.get("churn_by_support_experience_90d", [])
    if len(sup) >= 2:
        worst = max(sup, key=lambda r: r["churn_rate_90d_pct"])
        lines.append(f"- **Support experience**: '{_strip_prefix(worst['support_profile'])}' customers churned at "
                     f"{_pct(worst['churn_rate_90d_pct'], 2)} versus {_pct(sup[0]['churn_rate_90d_pct'], 2)} with no tickets.")
    con = a.get("churn_by_contract_and_plan_90d", [])
    if con:
        hi, lo = con[0], con[-1]
        lines.append(f"- **Contract**: {hi['contract_type']} {hi['plan_type']} customers churned at "
                     f"{_pct(hi['churn_rate_90d_pct'], 2)} versus {_pct(lo['churn_rate_90d_pct'], 2)} for "
                     f"{lo['contract_type']} {lo['plan_type']}.")
    lines += ["", "### Questions to investigate"]
    if peaks:
        lines.append(f"- What happened in {peaks[0]['month']}? Check pricing changes, network incidents and competitor activity.")
    if shift:
        comp = next((r for r in reasons if r["cancel_reason"] == "Moved to competitor"), None)
        extra = f" 'Moved to competitor' accounts for {_pct(comp['share_pct'])} of cancellations." if comp else ""
        lines.append(f"- Is the rise in {shift[0]['region']} linked to a competitor offer or service quality?{extra}")
    lines.append("- Correlations above are descriptive; a controlled retention test is needed to confirm cause and effect.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- model
def _model_cmp(m: dict) -> tuple[dict, dict | None]:
    comp = m["model_comparison"]
    best = next(r for r in comp if r["model"] == m["selected_model"])
    base = comp[0] if comp and comp[0]["model"] != best["model"] else None
    return best, base


def model_explainer(ctx: dict) -> str:
    m = ctx["model"]
    best, base = _model_cmp(m)
    lines = ["### How good is the model"]
    if m["task_type"] == "classification":
        lines.append(
            f"The selected **{m['selected_model']}** was trained on {_n(m['rows_train'])} historical customer "
            f"snapshots and tested on {_n(m['rows_test'])} customers from {m.get('test_snapshot')} that it had never "
            f"seen ({m['validation']} validation). In that test {_pct(m['test_outcome_rate_pct'], 2)} of customers had the outcome.")
        lines.append(
            f"It ranks customers well: ROC-AUC {_x(best['roc_auc'])} and PR-AUC {_x(best['pr_auc'])}"
            + (f", against {_x(base['roc_auc'])} and {_x(base['pr_auc'])} for the no-skill baseline" if base else "") + ". "
            f"Contacting the riskiest {_pct(m['top_pct_contacted'], 0)} of customers would reach "
            f"{_pct(100 * best['recall_at_top'])} of those who go on to have the outcome - {_x(best['lift_at_top'])}x better than picking at random.")
        others = [r for r in m["model_comparison"] if r["model"] not in (best["model"], (base or {}).get("model"))]
        if others:
            lines.append("Alternatives tested: " + ", ".join(f"{r['model']} (PR-AUC {_x(r['pr_auc'])})" for r in others) + ".")
    else:
        lines.append(f"The selected **{m['selected_model']}** has RMSE {_x(best['rmse'])} and R² {_x(best['r2'])}"
                     + (f" versus RMSE {_x(base['rmse'])} for the baseline" if base else "") + ".")
    lines += ["", "### What drives the prediction"]
    for d in m["top_drivers"][:5]:
        rng = d.get("observed_outcome_rate_range_pct")
        extra = f" Observed outcome rate across its range: {_pct(rng[0])} to {_pct(rng[1])}." if rng else ""
        lines.append(f"{d['rank']}. **{d['feature']}** - {d['relationship']}.{extra}")
    lines.append(m.get("drivers_note", ""))
    if m["task_type"] == "classification" and m.get("campaign_simulation_on_test"):
        c, ca = m["campaign_simulation_on_test"], m.get("campaign_assumptions", {})
        lines += ["", "### Business value",
                  f"On the test month, contacting {_n(c['contacted'])} customers chosen by the model reaches "
                  f"{_n(c['positives_reached_model'])} would-be churners versus about {_n(c['positives_reached_random'])} "
                  f"with random selection. At a {_pct(100 * ca.get('save_rate', 0), 0)} save rate this protects "
                  f"{_money(c['revenue_protected_model_ghs'])} of revenue for a campaign cost of "
                  f"{_money(c['campaign_cost_model_ghs'])} - a net benefit of {_money(c['net_benefit_model_ghs'])} "
                  f"(ROI {_x(c['roi_model'])}) versus {_money(c['net_benefit_random_ghs'])} for random targeting."]
        cs = m.get("current_scoring", {})
        if cs:
            lines.append(f"Today, {_n(cs['customers_scored'])} customers are scored; the model expects about "
                         f"{_n(cs['expected_positives_next_90d'])} to leave in the next 90 days, putting "
                         f"{_money(cs['expected_revenue_at_risk_ghs'])} of revenue at risk over "
                         f"{_n(cs['revenue_horizon_months'])} months.")
    lines += ["", "### Caveats",
              "- Drivers are associations learned from history, not proven causes.",
              "- Business value depends on the save-rate and cost assumptions; validate them with a control group."]
    flagged = m.get("drift", {}).get("features_flagged") or []
    if flagged:
        lines.append("- Monitoring flags a population shift in: " + ", ".join(
            f"{f['feature']} (PSI {_x(f['psi'])})" for f in flagged[:3]) + ". Review before relying on scores.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- actions
PLAYBOOK = [
    ("contract", "Offer month-to-month customers a discounted 12-month plan in exchange for commitment."),
    ("data use", "Trigger a re-engagement offer (bonus data) when a customer's data use drops sharply, and check network quality in their area."),
    ("voice", "Include voice bundles in the re-engagement offer for customers whose calling has dropped."),
    ("allowance", "Right-size the plan for customers using little of their allowance so they are not paying for unused data."),
    ("tenure", "Run an onboarding call and a 60-day check-in for new customers."),
    ("bills", "Enrol customers who miss bills in Mobile Money autopay, with a small first-month credit."),
    ("late", "Send SMS reminders 3 days before the due date to customers with late payments."),
    ("autopay", "Promote autopay enrolment at every service touchpoint."),
    ("ticket", "Escalate customers with repeat or poorly rated tickets to a service-recovery team."),
    ("csat", "Call back every customer who rates support 1-2 within 48 hours."),
    ("dropped", "Prioritise network fixes where dropped calls are concentrated."),
    ("region", "Run region-specific counter-offers where competitor pressure is highest."),
]


def retention_actions(ctx: dict) -> str:
    m = ctx["model"]
    cs = m.get("current_scoring", {})
    tiers = {t["tier"]: t for t in cs.get("tiers", [])}
    seg = ctx.get("segments", {})
    lines = ["### Priority segments"]
    if "High" in tiers:
        h = tiers["High"]
        lines.append(f"The model places {_n(h['customers'])} customers in the **High** tier (average predicted risk "
                     f"{_pct(h['avg_predicted_risk_pct'])}); about {_n(h['expected_positives'])} of them are expected to leave. "
                     f"They carry {_money(cs.get('high_tier_revenue_at_risk_ghs'))} of revenue at risk over "
                     f"{_n(cs.get('revenue_horizon_months'))} months.")
    for r in seg.get("risk_by_contract_and_plan", [])[:3]:
        lines.append(f"- {r['contract_type']} / {r['plan_type']}: {_n(r['high_risk_customers'])} high-risk customers, "
                     f"{_money(r['expected_monthly_revenue_at_risk_ghs'])} expected monthly revenue at risk.")
    regs = [r for r in seg.get("risk_by_region", []) if r["region"] != "Unknown"][:3]
    if regs:
        lines.append("Regions with the most revenue at risk: " + ", ".join(
            f"{r['region']} ({_money(r['expected_monthly_revenue_at_risk_ghs'])}/month)" for r in regs) + ".")
    lines += ["", "### Recommended actions"]
    reasons = m.get("high_tier_reason_counts", [])
    used, i = set(), 1
    for r in reasons:
        label = r["reason"].lower()
        for key, action in PLAYBOOK:
            if key in label and action not in used:
                lines.append(f"{i}. {action} *(signal: {r['reason']} - {_n(r['customers'])} high-risk customers)*")
                used.add(action)
                i += 1
                break
        if i > 5:
            break
    if i == 1:
        lines.append(f"1. {m.get('campaign_assumptions', {}).get('action', 'Contact the highest-risk customers first')}.")
    lines += ["", "### How to measure success"]
    ca = m.get("campaign_assumptions", {})
    lines.append(f"- Hold out a random control group from the High tier to measure the true save rate against the planned "
                 f"{_pct(100 * ca.get('save_rate', 0), 0)}.")
    lines.append(f"- Track 90-day churn of treated versus control customers, cost per save (planned contact cost "
                 f"{_money(ca.get('contact_cost_ghs'))}) and revenue retained.")
    lines.append("- Re-score monthly and compare predicted with actual churn to confirm the model is still accurate.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- executive summary
def executive_summary(ctx: dict) -> str:
    a, m = ctx.get("analysis"), ctx.get("model")
    lines = ["### Situation"]
    if a:
        k = a["kpis"]
        lines.append(f"{_business(ctx)} serves {_n(k['active_customers'])} active customers worth "
                     f"{_money(k['monthly_recurring_revenue_ghs'])} in monthly recurring revenue. In the last 90 days "
                     f"{_n(k['churned_last_90_days'])} customers left ({_pct(k['churn_rate_90d_pct'], 2)} of the base) "
                     f"against {_n(k['new_customers_last_90_days'])} new customers.")
    lines += ["", "### Key findings"]
    if a:
        peaks = a.get("highest_churn_months_last_12m", [])
        if peaks:
            lines.append(f"- Churn peaked in {peaks[0]['month']} at {_pct(peaks[0]['churn_rate_pct'], 2)} a month.")
        shift = [r for r in a.get("regional_churn_shift", []) if r["region"] != "Unknown"]
        if shift:
            lines.append(f"- {shift[0]['region']} saw the sharpest regional spike: {_pct(shift[0]['worst_3m_avg_monthly_churn_pct'], 2)} "
                         f"monthly churn from {shift[0]['worst_3m_window']} vs {_pct(shift[0]['rest_of_country_same_window_pct'], 2)} "
                         "elsewhere.")
        con = a.get("churn_by_contract_and_plan_90d", [])
        if con:
            lines.append(f"- {con[0]['contract_type']} {con[0]['plan_type']} customers are the most likely to leave "
                         f"({_pct(con[0]['churn_rate_90d_pct'], 2)} within 90 days).")
        usage = {_strip_prefix(r["usage_trend"]): r for r in a.get("churn_by_usage_trend_90d", [])}
        if "Down 10-40%" in usage and "Stable (+/-10%)" in usage:
            lines.append(f"- Falling usage is the clearest early warning: {_pct(usage['Down 10-40%']['churn_rate_90d_pct'], 2)} "
                         f"churn vs {_pct(usage['Stable (+/-10%)']['churn_rate_90d_pct'], 2)} for stable users.")
        reasons = a.get("cancel_reasons_last_12m", [])
        if reasons:
            lines.append(f"- '{reasons[0]['cancel_reason']}' is the most common recorded cancellation reason "
                         f"({_pct(reasons[0]['share_pct'])}).")
    dqs = ctx.get("data_quality_summary")
    if dqs and dqs.get("high_severity"):
        lines.append(f"- Data quality: {dqs['checks_with_issues']} of {dqs['checks_run']} checks found issues; all are "
                     "corrected in the analytics layer, but high-severity ones should be fixed at source.")
    if m:
        best, base = _model_cmp(m)
        lines += ["", "### What the model tells us"]
        if m["task_type"] == "classification":
            lines.append(f"The {m['selected_model'].lower()} model identifies the riskiest {_pct(m['top_pct_contacted'], 0)} of "
                         f"customers who account for {_pct(100 * best['recall_at_top'])} of future churners "
                         f"({_x(best['lift_at_top'])}x random; ROC-AUC {_x(best['roc_auc'])}).")
            cs = m.get("current_scoring", {})
            if cs:
                lines.append(f"It expects about {_n(cs['expected_positives_next_90d'])} customers to leave in the next 90 days, "
                             f"with {_money(cs['expected_revenue_at_risk_ghs'])} of revenue at risk over "
                             f"{_n(cs['revenue_horizon_months'])} months.")
            c = m.get("campaign_simulation_on_test")
            if c:
                lines.append(f"A targeted retention campaign is estimated to return {_money(c['net_benefit_model_ghs'])} net "
                             f"(ROI {_x(c['roi_model'])}) versus {_money(c['net_benefit_random_ghs'])} if customers were picked at random.")
    con = (a or {}).get("churn_by_contract_and_plan_90d") or []
    start = f", starting with {con[0]['contract_type'].lower()} {con[0]['plan_type'].lower()} customers" if con else ""
    lines += ["", "### Recommendations",
              f"1. Act on the High-risk tier first{start}, keeping a control group to prove impact.",
              "2. Turn the strongest early-warning driver into an automatic trigger for intervention.",
              "3. Fix the high-severity data issues at source and re-score monthly, monitoring drift."]
    return "\n".join(lines)


# --------------------------------------------------------------------------- Q&A
TOPICS = {
    "region": ["regional_churn_shift", "risk_by_region"],
    "accra": ["regional_churn_shift", "risk_by_region"],
    "contract": ["churn_by_contract_and_plan_90d", "risk_by_contract_and_plan"],
    "plan": ["churn_by_contract_and_plan_90d", "risk_by_contract_and_plan"],
    "prepaid": ["churn_by_contract_and_plan_90d"],
    "tenure": ["churn_by_tenure_90d"],
    "new customer": ["churn_by_tenure_90d"],
    "pay": ["churn_by_payment_behaviour_90d"],
    "bill": ["churn_by_payment_behaviour_90d"],
    "support": ["churn_by_support_experience_90d", "support_by_category_last_6m"],
    "ticket": ["churn_by_support_experience_90d", "support_by_category_last_6m"],
    "csat": ["support_by_category_last_6m"],
    "usage": ["churn_by_usage_trend_90d"],
    "data": ["churn_by_usage_trend_90d"],
    "reason": ["cancel_reasons_last_12m"],
    "why": ["cancel_reasons_last_12m", "top_drivers"],
    "competitor": ["cancel_reasons_last_12m", "regional_churn_shift"],
    "trend": ["monthly_churn_trend_last_12m", "highest_churn_months_last_12m"],
    "month": ["monthly_churn_trend_last_12m", "highest_churn_months_last_12m"],
    "kpi": ["kpis"],
    "revenue": ["kpis", "current_scoring"],
    "churn rate": ["kpis", "highest_churn_months_last_12m"],
    "how many": ["kpis", "current_scoring"],
    "driver": ["top_drivers"],
    "model": ["model_comparison", "top_drivers"],
    "accura": ["model_comparison"],
    "risk": ["current_scoring", "risk_by_contract_and_plan"],
    "campaign": ["campaign_simulation_on_test", "campaign_assumptions"],
    "roi": ["campaign_simulation_on_test"],
    "drift": ["drift"],
    "quality": ["high_severity"],
}


def _find(ctx: Any, key: str) -> Any:
    if isinstance(ctx, dict):
        if key in ctx:
            return ctx[key]
        for v in ctx.values():
            found = _find(v, key)
            if found is not None:
                return found
    return None


def _render(value: Any, limit: int = 8) -> list[str]:
    if isinstance(value, list):
        out = []
        for row in value[:limit]:
            if isinstance(row, dict):
                out.append("- " + "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in row.items() if v is not None))
            else:
                out.append(f"- {row}")
        return out
    if isinstance(value, dict):
        return [f"- {k.replace('_', ' ')}: {v}" for k, v in value.items() if v is not None and not isinstance(v, (list, dict))]
    return [f"- {value}"]


def ask(ctx: dict, question: str | None) -> str:
    q = (question or "").lower()
    keys: list[str] = []
    for word, ks in TOPICS.items():
        if word in q:
            keys += [k for k in ks if k not in keys]
    found = [(k, _find(ctx, k)) for k in keys]
    found = [(k, v) for k, v in found if v]
    if not found:
        return ("I can't answer that from the analysis available in this workspace. The context covers KPIs, churn "
                "trends, regional and segment churn, payment, support and usage patterns, cancellation reasons, "
                "the model's accuracy and drivers, and the campaign estimate. Try asking about one of those, or run "
                "a new analysis that covers your question.")
    lines = ["Here is what the workspace data shows on that:"]
    for k, v in found[:3]:
        lines.append(f"\n**{k.replace('_', ' ').capitalize()}**")
        lines += _render(v)
    lines.append("\n_Mock mode returns the relevant facts verbatim; the live Snwolley agent writes a narrative answer from the same context._")
    return "\n".join(lines)


WRITERS = {
    "data_quality_brief": lambda c, q: data_quality_brief(c),
    "kpi_explainer": lambda c, q: kpi_explainer(c),
    "model_explainer": lambda c, q: model_explainer(c),
    "retention_actions": lambda c, q: retention_actions(c),
    "executive_summary": lambda c, q: executive_summary(c),
    "ask": ask,
}


def write(task: str, context: dict, question: str | None = None) -> str:
    return WRITERS[task](context, question)
