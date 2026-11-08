import pandas as pd

from insightforge.datagen.telco import GeneratorConfig, generate


def test_generation_is_reproducible():
    a = generate(GeneratorConfig(n_customers=600, seed=11))
    b = generate(GeneratorConfig(n_customers=600, seed=11))
    for name in a:
        pd.testing.assert_frame_equal(a[name], b[name])


def test_different_seed_gives_different_data():
    a = generate(GeneratorConfig(n_customers=600, seed=1))["monthly_usage"]
    b = generate(GeneratorConfig(n_customers=600, seed=2))["monthly_usage"]
    assert not a["data_gb_used"].head(200).equals(b["data_gb_used"].head(200))


def test_imperfections_are_injected():
    d = generate(GeneratorConfig(n_customers=1500, seed=3))
    c = d["customers"]
    assert c["region"].isna().any()
    assert c["phone"].duplicated().any()  # duplicate registrations
    u = d["monthly_usage"]
    assert (u["data_gb_used"] < 0).any()
    assert u.duplicated(["subscription_id", "usage_month"]).any()
    assert d["payments"].duplicated(["subscription_id", "billing_month"]).any()


def test_clean_mode_has_no_injected_issues():
    d = generate(GeneratorConfig(n_customers=600, seed=3, imperfections=False))
    assert not d["customers"]["phone"].duplicated().any()
    assert not (d["monthly_usage"]["data_gb_used"] < 0).any()


def test_referential_integrity():
    d = generate(GeneratorConfig(n_customers=800, seed=5))
    subs = set(d["subscriptions"]["subscription_id"])
    assert set(d["monthly_usage"]["subscription_id"]) <= subs
    assert set(d["payments"]["subscription_id"]) <= subs
    assert set(d["subscriptions"]["customer_id"]) <= set(d["customers"]["customer_id"])
