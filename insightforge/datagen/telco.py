"""Reproducible synthetic data for a Ghanaian mobile & fibre operator ("Kasa Telecom").

Business scenario
-----------------
Kasa Telecom sells monthly bundles (prepaid bundles, postpaid plans and home
fibre). Customers are billed monthly, pay mostly via Mobile Money, raise support
tickets and generate usage. The operator wants to know who is about to leave.

The simulation is customer-month based. Each month an active customer:

* generates usage (data, voice, SMS) and dropped calls driven by the regional
  network quality,
* may raise support tickets (network, billing, data speed, ...) with a
  resolution time and an optional satisfaction (CSAT) score,
* receives a bill and pays on time, late, or not at all,
* accumulates "dissatisfaction" from bad experiences, which - together with
  contract type, tenure, autopay, price sensitivity and two market events -
  drives a monthly churn hazard.

Market events baked in (so the analytics have something real to find):

* October 2025: prepaid bundle prices rise 15%.
* April-July 2026: a competitor runs an aggressive promotion in Greater Accra.

Voluntary churners usually disengage (usage declines) in the 2-3 months before
they leave; three consecutive unpaid bills trigger involuntary churn.

Realistic imperfections are then injected: duplicate customer registrations,
inconsistent region spellings, missing values, double-posted payments, negative
or implausible usage readings, out-of-range CSAT scores and a few subscriptions
whose dates are inconsistent.

Everything is driven by a single ``numpy.random.Generator`` seeded from
``seed`` so the dataset is exactly reproducible.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- reference data
PLANS = [
    # plan_id, name, type, current monthly fee (GHS), data allowance GB
    ("P01", "Kasa Lite Bundle", "Prepaid", 40.0, 5),
    ("P02", "Kasa Plus Bundle", "Prepaid", 69.0, 12),
    ("P03", "Kasa Max Bundle", "Prepaid", 115.0, 25),
    ("P04", "Postpaid Basic", "Postpaid", 120.0, 20),
    ("P05", "Postpaid Pro", "Postpaid", 199.0, 50),
    ("P06", "Postpaid Unlimited", "Postpaid", 320.0, 150),
    ("P07", "Home Fibre 50Mbps", "Fibre", 380.0, 500),
    ("P08", "Home Fibre 100Mbps", "Fibre", 550.0, 1000),
]
PREPAID_INCREASE = 1.15  # applied from October 2025
PRICE_RISE_MONTH = date(2025, 10, 1)
PROMO_START, PROMO_END = date(2026, 4, 1), date(2026, 7, 1)

REGIONS = {
    # region: (share of customers, network badness 0-1, cities)
    "Greater Accra": (0.34, 0.20, ["Accra", "Tema", "Madina", "Kasoa"]),
    "Ashanti": (0.20, 0.25, ["Kumasi", "Obuasi", "Ejisu"]),
    "Western": (0.08, 0.38, ["Takoradi", "Tarkwa"]),
    "Central": (0.07, 0.35, ["Cape Coast", "Winneba"]),
    "Eastern": (0.08, 0.42, ["Koforidua", "Nkawkaw"]),
    "Northern": (0.06, 0.62, ["Tamale", "Yendi"]),
    "Volta": (0.05, 0.48, ["Ho", "Hohoe"]),
    "Bono": (0.05, 0.45, ["Sunyani", "Berekum"]),
    "Upper East": (0.04, 0.66, ["Bolgatanga", "Navrongo"]),
    "Upper West": (0.03, 0.70, ["Wa"]),
}
REGION_VARIANTS = {
    "Greater Accra": ["greater accra", "GREATER ACCRA", "Gt. Accra", "Accra", "Greater Accra "],
    "Ashanti": ["ashanti", "ASHANTI", "Ashanti Region", "Kumasi"],
}
CHANNELS = (["Retail Shop", "Agent", "Online", "Referral", "Telesales"], [0.30, 0.30, 0.20, 0.10, 0.10])
PLAN_MIX = {
    "Consumer": [0.25, 0.25, 0.13, 0.14, 0.10, 0.04, 0.06, 0.03],
    "SME": [0.02, 0.05, 0.08, 0.20, 0.25, 0.15, 0.15, 0.10],
}
CONTRACT_MIX = {
    "Prepaid": ([0.90, 0.10, 0.00]),
    "Postpaid": ([0.30, 0.45, 0.25]),
    "Fibre": ([0.25, 0.45, 0.30]),
}
CONTRACTS = ["Month-to-month", "12-month", "24-month"]
PAY_METHODS = (["Mobile Money", "Card", "Bank Transfer", "Cash (Agent)"], [0.68, 0.12, 0.08, 0.12])
TICKET_CATS = ["Network", "Billing", "Data Speed", "Device", "Account"]

FIRST_NAMES = [
    "Kwame", "Kofi", "Kwabena", "Kwaku", "Yaw", "Kwasi", "Kojo", "Ama", "Akua", "Abena", "Esi",
    "Afia", "Adwoa", "Yaa", "Akosua", "Efua", "Nana", "Selorm", "Edem", "Elikem", "Dzifa",
    "Mawuli", "Fuseini", "Abdul", "Amina", "Zainab", "Ibrahim", "Fatima", "Mohammed", "Rahinatu",
    "Emmanuel", "Grace", "Daniel", "Joyce", "Samuel", "Gifty", "Isaac", "Mercy", "Michael",
    "Comfort", "Prince", "Priscilla", "Eric", "Linda", "Richard", "Vida", "Benjamin", "Evelyn",
]
LAST_NAMES = [
    "Mensah", "Owusu", "Boateng", "Asante", "Osei", "Appiah", "Agyeman", "Ofori", "Amoah",
    "Darko", "Addo", "Tetteh", "Quaye", "Lamptey", "Annan", "Acheampong", "Frimpong", "Badu",
    "Gyamfi", "Nkrumah", "Sarpong", "Danso", "Opoku", "Kumi", "Adjei", "Antwi", "Amponsah",
    "Agbeko", "Dogbe", "Kpodo", "Mahama", "Iddrisu", "Alhassan", "Abubakar", "Seidu", "Yakubu",
]


@dataclass(frozen=True)
class GeneratorConfig:
    n_customers: int = 8000
    seed: int = 42
    start: date = date(2023, 1, 1)
    as_of: date = date(2026, 9, 30)
    imperfections: bool = True


def _month_starts(start: date, end: date) -> list[date]:
    months, d = [], date(start.year, start.month, 1)
    while d <= end:
        months.append(d)
        d = date(d.year + (d.month // 12), d.month % 12 + 1, 1)
    return months


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + np.exp(-x))


def generate(config: GeneratorConfig | None = None) -> dict[str, pd.DataFrame]:
    """Return the synthetic dataset as a dict of DataFrames (no workspace_id)."""
    cfg = config or GeneratorConfig()
    rng = np.random.default_rng(cfg.seed)
    months = _month_starts(cfg.start, cfg.as_of)
    n_months = len(months)

    plans_df = pd.DataFrame(PLANS, columns=["plan_id", "plan_name", "plan_type", "monthly_fee", "data_gb"])
    plan_by_id = {p[0]: p for p in PLANS}

    region_names = list(REGIONS)
    region_p = np.array([REGIONS[r][0] for r in region_names])
    region_p = region_p / region_p.sum()

    # Signup month weights grow over time (business is growing); a block of
    # customers already exists when the data warehouse starts.
    signup_w = np.linspace(1.0, 2.2, n_months - 1)
    signup_w[0] += 25  # legacy base loaded in the first month
    signup_w = signup_w / signup_w.sum()

    customers, subs, usage, payments, tickets = [], [], [], [], []
    ticket_seq = pay_seq = usage_seq = 0
    used_phones: set[str] = set()

    for i in range(cfg.n_customers):
        cid = f"C{i + 1:05d}"
        sid = f"S{i + 1:06d}"
        seg = "SME" if rng.random() < 0.15 else "Consumer"
        region = rng.choice(region_names, p=region_p)
        _, q_region, cities = REGIONS[region]
        city = rng.choice(cities)
        channel = rng.choice(CHANNELS[0], p=CHANNELS[1])
        gender = rng.choice(["Male", "Female"], p=[0.52, 0.48])
        age = int(np.clip(rng.normal(34, 10), 18, 72))
        m0 = int(rng.choice(n_months - 1, p=signup_w))
        signup = months[m0] + timedelta(days=int(rng.integers(0, 28)))
        dob = date(signup.year - age, int(rng.integers(1, 13)), int(rng.integers(1, 29)))
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        phone = None
        while phone is None or phone in used_phones:
            phone = "+233" + rng.choice(["24", "54", "55", "59", "20", "50", "27"]) + f"{rng.integers(0, 10**7):07d}"
        used_phones.add(phone)
        email = f"{first.lower()}.{last.lower()}{rng.integers(1, 999)}@example.com"

        plan_id = PLANS[int(rng.choice(8, p=PLAN_MIX[seg]))][0]
        _, _, ptype, fee_now, plan_gb = plan_by_id[plan_id]
        contract = rng.choice(CONTRACTS, p=CONTRACT_MIX[ptype])
        autopay = bool(rng.random() < (0.25 if ptype == "Prepaid" else 0.55))

        # latent traits
        util = float(np.clip(rng.beta(4, 3) * 1.15, 0.05, 1.3))  # share of allowance used
        price_sens = float(np.clip(rng.beta(2, 3) + (0.12 if ptype == "Prepaid" else 0) - (0.1 if seg == "SME" else 0) + (0.08 if age < 26 else 0), 0, 1))
        stress = float(rng.beta(1.5, 5))
        q = float(np.clip(q_region + rng.normal(0, 0.1), 0, 1))
        voice_need = float(rng.lognormal(np.log(180), 0.5))
        sms_need = float(rng.lognormal(np.log(25), 0.6))

        customers.append(
            dict(customer_id=cid, full_name=f"{first} {last}", phone=phone, email=email, gender=gender,
                 date_of_birth=dob, region=region, city=city, segment=seg,
                 acquisition_channel=channel, signup_date=signup)
        )

        dissat = 0.0
        unpaid_streak = 0
        churn_idx, churn_type = None, None
        month_events = []  # per active month: (idx, dropped)
        for k in range(m0, n_months):
            m = months[k]
            tenure = k - m0
            dropped = int(rng.poisson(1 + 10 * q))
            # ---- support tickets
            lam = {
                "Network": 0.03 + 0.20 * q,
                "Billing": 0.02 + 0.10 * stress + (0.06 if ptype == "Prepaid" and m == PRICE_RISE_MONTH else 0),
                "Data Speed": 0.02 + 0.07 * q,
                "Device": 0.015,
                "Account": 0.015,
            }
            n_net = n_bill = n_speed = bad_csat = 0
            for cat, rate in lam.items():
                for _ in range(int(rng.poisson(rate))):
                    ticket_seq += 1
                    hours = float(rng.lognormal(np.log(10 + 40 * q if cat in ("Network", "Data Speed") else 8), 0.8))
                    csat = None
                    if rng.random() > 0.3:  # 30% never answer the survey
                        csat = int(np.clip(round(4.4 - 0.015 * hours + rng.normal(0, 0.8)), 1, 5))
                        bad_csat += csat <= 2
                    n_net += cat == "Network"
                    n_bill += cat == "Billing"
                    n_speed += cat == "Data Speed"
                    tickets.append(
                        dict(ticket_id=f"T{ticket_seq:07d}", customer_id=cid,
                             opened_date=m + timedelta(days=int(rng.integers(0, 28))), category=cat,
                             resolution_hours=round(hours, 1), csat_score=csat)
                    )
            # ---- billing
            fee = fee_now if (ptype != "Prepaid" or m >= PRICE_RISE_MONTH) else round(fee_now / PREPAID_INCREASE)
            due = _add_months(m, 1) + timedelta(days=6)
            p_unpaid = (0.01 + 0.05 * stress) if autopay else (0.02 + 0.14 * stress)
            p_late = 0.03 if autopay else (0.08 + 0.30 * stress)
            if unpaid_streak:  # financial distress tends to persist
                p_unpaid = 0.35
            r = rng.random()
            pay_seq += 1
            method = "Mobile Money" if autopay else rng.choice(PAY_METHODS[0], p=PAY_METHODS[1])
            if due > cfg.as_of:  # bill not yet due at data cut-off
                paid, paid_date, late, unpaid = None, None, 0, 0
            elif r < p_unpaid:
                paid, paid_date, late, unpaid = 0.0, None, 0, 1
            elif r < p_unpaid + p_late:
                paid, paid_date, late, unpaid = fee, due + timedelta(days=int(rng.integers(2, 35))), 1, 0
            else:
                paid, paid_date, late, unpaid = fee, due - timedelta(days=int(rng.integers(0, 12))), 0, 0
            if paid_date and paid_date > cfg.as_of:
                paid, paid_date, late = None, None, 0
            payments.append(
                dict(payment_id=f"P{pay_seq:08d}", subscription_id=sid, billing_month=m, due_date=due,
                     amount_due=float(fee), amount_paid=paid, paid_date=paid_date,
                     payment_method=None if paid_date is None else method)
            )
            unpaid_streak = unpaid_streak + 1 if unpaid else 0

            # ---- dissatisfaction & churn hazard
            dissat = (0.65 * dissat + 0.5 * n_net + 0.5 * n_bill + 0.35 * n_speed + 0.6 * bad_csat
                      + 0.25 * late + 0.5 * unpaid + 0.04 * max(0, dropped - 6))
            logit = (-5.25
                     + {"Month-to-month": 1.0, "12-month": 0.0, "24-month": -0.7}[contract]
                     + (0.5 if tenure < 4 else 0.25 if tenure < 12 else -0.3 if tenure >= 24 else 0)
                     - 0.45 * autopay
                     + 1.0 * price_sens
                     + 0.9 * dissat
                     - 0.35 * (seg == "SME")
                     - 0.2 * (channel == "Referral")
                     - 0.2 * (ptype == "Fibre")
                     + 0.4 * (util < 0.3))
            if ptype == "Prepaid" and PRICE_RISE_MONTH <= m < _add_months(PRICE_RISE_MONTH, 6):
                logit += 0.8 * price_sens
            if region == "Greater Accra" and PROMO_START <= m <= PROMO_END:
                logit += 1.6 * price_sens
            month_events.append((k, dropped))
            if tenure > 0 and rng.random() < _sigmoid(logit):
                churn_idx, churn_type = k, "voluntary"
                break
            if unpaid_streak >= 3:
                churn_idx, churn_type = k, "involuntary"
                break

        # ---- subscription record
        end_date, reason, status = None, None, "active"
        if churn_idx is not None:
            cm = months[churn_idx]
            end_date = min(cm + timedelta(days=int(rng.integers(0, 28))), cfg.as_of)
            status = "cancelled"
            if churn_type == "involuntary":
                reason = "Non-payment"
            elif rng.random() < 0.2:
                reason = None  # not captured by the call-centre
            else:
                opts = ["Price", "Poor network", "Poor customer service", "Moved to competitor", "Relocation", "Other"]
                w = np.array([0.6 + 2 * price_sens, 0.3 + 2 * q, 0.3 + min(dissat, 2), 0.5, 0.35, 0.3])
                if region == "Greater Accra" and PROMO_START <= cm <= PROMO_END:
                    w[3] += 2.5
                reason = rng.choice(opts, p=w / w.sum())
        start = max(signup, months[m0])
        subs.append(
            dict(subscription_id=sid, customer_id=cid, plan_id=plan_id, start_date=start, end_date=end_date,
                 status=status, contract_type=contract, autopay=autopay, cancel_reason=reason)
        )

        # ---- usage (generated after we know whether / when the customer leaves)
        decays = churn_type == "voluntary" and rng.random() < 0.72
        for k, dropped in month_events:
            factor = 1.0
            if churn_idx is not None:
                gap = churn_idx - k
                if decays and gap <= 3:
                    factor = {3: 0.88, 2: 0.72, 1: 0.55, 0: 0.4}[gap]
                elif gap == 0:
                    factor = 0.5
            usage_seq += 1
            usage.append(
                dict(usage_id=f"U{usage_seq:08d}", subscription_id=sid, usage_month=months[k],
                     data_gb_used=round(util * plan_gb * float(rng.lognormal(0, 0.22)) * factor, 2),
                     voice_minutes=round(voice_need * float(rng.lognormal(0, 0.25)) * factor, 1),
                     sms_count=int(rng.poisson(sms_need * factor)), dropped_calls=dropped)
            )

    data = {
        "plans": plans_df,
        "customers": pd.DataFrame(customers),
        "subscriptions": pd.DataFrame(subs),
        "monthly_usage": pd.DataFrame(usage),
        "payments": pd.DataFrame(payments),
        "support_tickets": pd.DataFrame(tickets),
    }
    if cfg.imperfections:
        data = inject_imperfections(data, np.random.default_rng(cfg.seed + 1))
    return data


def inject_imperfections(data: dict[str, pd.DataFrame], rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    """Add the kind of mess real operational systems contain."""
    c = data["customers"].copy()
    n = len(c)
    # missing values
    c.loc[rng.random(n) < 0.03, "region"] = None
    c.loc[rng.random(n) < 0.04, "date_of_birth"] = None
    c.loc[rng.random(n) < 0.12, "email"] = None
    # inconsistent region labels
    idx = np.where((rng.random(n) < 0.10) & c["region"].notna())[0]
    for i in idx:
        reg = c.at[i, "region"]
        variants = REGION_VARIANTS.get(reg, [reg.lower(), reg.upper(), reg + " "])
        c.at[i, "region"] = variants[int(rng.integers(len(variants)))]
    # duplicate registrations (same phone, new id, no subscription)
    dup_src = c.sample(frac=0.015, random_state=int(rng.integers(1e9))).copy()
    dup_src["customer_id"] = [f"C9{j:04d}" for j in range(len(dup_src))]
    dup_src["full_name"] = dup_src["full_name"].str.upper()
    dup_src["signup_date"] = dup_src["signup_date"].apply(lambda d: d + timedelta(days=int(rng.integers(0, 4))))
    data["customers"] = pd.concat([c, dup_src], ignore_index=True)

    s = data["subscriptions"].copy()
    cancelled = s.index[s["end_date"].notna()].to_numpy()
    bad_dates = rng.choice(cancelled, size=max(1, int(0.004 * len(cancelled))), replace=False)
    s.loc[bad_dates, "end_date"] = s.loc[bad_dates, "start_date"].apply(lambda d: d - timedelta(days=int(rng.integers(30, 400))))
    stale = rng.choice(np.setdiff1d(cancelled, bad_dates), size=max(1, int(0.006 * len(cancelled))), replace=False)
    s.loc[stale, "status"] = "active"
    data["subscriptions"] = s

    u = data["monthly_usage"].copy()
    m = len(u)
    u.loc[rng.random(m) < 0.003, "data_gb_used"] *= -1
    u.loc[rng.random(m) < 0.005, "data_gb_used"] = None
    u.loc[rng.random(m) < 0.001, "data_gb_used"] *= 60
    dups = u.sample(frac=0.008, random_state=int(rng.integers(1e9))).copy()
    dups["usage_id"] = [f"UD{j:07d}" for j in range(len(dups))]
    data["monthly_usage"] = pd.concat([u, dups], ignore_index=True)

    p = data["payments"].copy()
    k = len(p)
    early = (rng.random(k) < 0.002) & p["paid_date"].notna()
    p.loc[early, "paid_date"] = p.loc[early, "paid_date"].apply(lambda d: d - timedelta(days=365))
    over = (rng.random(k) < 0.0015) & p["amount_paid"].fillna(0).gt(0)
    p.loc[over, "amount_paid"] = p.loc[over, "amount_paid"] * 10
    pdups = p.sample(frac=0.005, random_state=int(rng.integers(1e9))).copy()
    pdups["payment_id"] = [f"PD{j:07d}" for j in range(len(pdups))]
    data["payments"] = pd.concat([p, pdups], ignore_index=True)

    t = data["support_tickets"].copy()
    r = len(t)
    t["csat_score"] = t["csat_score"].astype("object")
    bad_csat = rng.random(r) < 0.006
    t.loc[bad_csat, "csat_score"] = rng.choice([0, 6, 10], size=int(bad_csat.sum()))
    t.loc[rng.random(r) < 0.003, "resolution_hours"] *= -1
    data["support_tickets"] = t
    return data


def write_csvs(data: dict[str, pd.DataFrame], out_dir: Path, config: GeneratorConfig) -> dict:
    """Write one CSV per table plus a manifest with row counts and checksums."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"generator": "kasa_telecom_v1", "seed": config.seed, "n_customers": config.n_customers,
                "start": str(config.start), "as_of": str(config.as_of), "tables": {}}
    for name, df in data.items():
        path = out_dir / f"{name}.csv"
        df.to_csv(path, index=False)
        manifest["tables"][name] = {"rows": len(df), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest
