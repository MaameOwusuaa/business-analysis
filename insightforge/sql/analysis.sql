-- =====================================================================
-- Business analysis queries (run on the cleaned silver views).
-- Parameters (computed by insightforge.analysis.analysis_params):
--   :ws                workspace id
--   :as_of             data cut-off date (e.g. 2026-09-30)
--   :d90_start         as_of - 90 days
--   :snapshot          last fully-labelled snapshot (as_of - 3 months)
--   :horizon_end       snapshot + 90 days (outcome window end)
--   :m3_start/:m6_start  first day of the month 3 / 6 months before the snapshot month
--   :t3/:t12/:t24      start dates that bound tenure bands at the snapshot
--   :trend_start       first month shown in trend charts
--   :month_after_as_of first day of the month after as_of
-- Metadata keys: title, question, chart (kind|x|y|color), insight
-- =====================================================================

-- name: kpi_summary
-- title: Headline KPIs
-- question: How big is the active base, what does it earn per month, and how fast is it shrinking?
-- chart: kpi
WITH subs AS (
    SELECT s.start_date, s.end_date, s.cancel_reason, p.monthly_fee
    FROM v_subscriptions_clean s
    JOIN plans p ON p.workspace_id = s.workspace_id AND p.plan_id = s.plan_id
    WHERE s.workspace_id = :ws
),
k AS (
    SELECT
        SUM(CASE WHEN start_date <= :as_of AND (end_date IS NULL OR end_date > :as_of) THEN 1 ELSE 0 END) AS active_customers,
        SUM(CASE WHEN start_date <= :as_of AND (end_date IS NULL OR end_date > :as_of) THEN monthly_fee ELSE 0 END) AS mrr_ghs,
        SUM(CASE WHEN start_date <= :d90_start AND (end_date IS NULL OR end_date > :d90_start) THEN 1 ELSE 0 END) AS active_90d_ago,
        SUM(CASE WHEN end_date > :d90_start AND end_date <= :as_of THEN 1 ELSE 0 END) AS churned_last_90d,
        SUM(CASE WHEN end_date > :d90_start AND end_date <= :as_of AND cancel_reason = 'Non-payment' THEN 1 ELSE 0 END) AS involuntary_last_90d,
        SUM(CASE WHEN start_date > :d90_start AND start_date <= :as_of THEN 1 ELSE 0 END) AS new_last_90d
    FROM subs
)
SELECT
    active_customers,
    ROUND(mrr_ghs, 0) AS mrr_ghs,
    ROUND(1.0 * mrr_ghs / NULLIF(active_customers, 0), 2) AS arpu_ghs,
    churned_last_90d,
    involuntary_last_90d,
    new_last_90d,
    ROUND(100.0 * churned_last_90d / NULLIF(active_90d_ago, 0), 2) AS churn_rate_90d_pct,
    new_last_90d - churned_last_90d AS net_adds_90d
FROM k;

-- name: monthly_churn_trend
-- title: Monthly churn rate trend
-- question: Is churn getting better or worse, and when did it change?
-- chart: line|month_start|churn_rate_pct
-- insight: Uses LEAD to build month boundaries, conditional aggregation for the counts, LAG for month-on-month change and a 3-month moving average window.
WITH months AS (
    SELECT DISTINCT usage_month AS m
    FROM monthly_usage
    WHERE workspace_id = :ws AND usage_month >= :trend_start
),
bounds AS (
    SELECT m, COALESCE(LEAD(m) OVER (ORDER BY m), :month_after_as_of) AS m_next
    FROM months
),
agg AS (
    SELECT
        b.m AS month_start,
        SUM(CASE WHEN s.start_date < b.m AND (s.end_date IS NULL OR s.end_date >= b.m) THEN 1 ELSE 0 END) AS active_at_start,
        SUM(CASE WHEN s.start_date >= b.m AND s.start_date < b.m_next THEN 1 ELSE 0 END) AS new_customers,
        SUM(CASE WHEN s.end_date >= b.m AND s.end_date < b.m_next THEN 1 ELSE 0 END) AS churned,
        SUM(CASE WHEN s.end_date >= b.m AND s.end_date < b.m_next AND s.cancel_reason = 'Non-payment' THEN 1 ELSE 0 END) AS churned_involuntary
    FROM bounds b
    CROSS JOIN v_subscriptions_clean s
    WHERE s.workspace_id = :ws
    GROUP BY b.m
)
SELECT
    month_start,
    active_at_start,
    new_customers,
    churned,
    churned_involuntary,
    ROUND(100.0 * churned / NULLIF(active_at_start, 0), 2) AS churn_rate_pct,
    ROUND(100.0 * churned / NULLIF(active_at_start, 0)
          - LAG(100.0 * churned / NULLIF(active_at_start, 0)) OVER (ORDER BY month_start), 2) AS mom_change_pp,
    ROUND(AVG(100.0 * churned / NULLIF(active_at_start, 0))
          OVER (ORDER BY month_start ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2) AS churn_rate_3m_avg
FROM agg
ORDER BY month_start;

-- name: monthly_churn_by_region
-- title: Monthly churn rate by region
-- question: Is the churn movement national or concentrated in particular regions?
-- chart: line|month_start|churn_rate_pct|region
WITH months AS (
    SELECT DISTINCT usage_month AS m
    FROM monthly_usage
    WHERE workspace_id = :ws AND usage_month >= :trend_start
),
bounds AS (
    SELECT m, COALESCE(LEAD(m) OVER (ORDER BY m), :month_after_as_of) AS m_next FROM months
),
subs AS (
    SELECT s.start_date, s.end_date, COALESCE(c.region, 'Unknown') AS region
    FROM v_subscriptions_clean s
    JOIN v_customers_clean c ON c.workspace_id = s.workspace_id AND c.customer_id = s.customer_id
    WHERE s.workspace_id = :ws
)
SELECT
    b.m AS month_start,
    s.region,
    SUM(CASE WHEN s.start_date < b.m AND (s.end_date IS NULL OR s.end_date >= b.m) THEN 1 ELSE 0 END) AS active_at_start,
    SUM(CASE WHEN s.end_date >= b.m AND s.end_date < b.m_next THEN 1 ELSE 0 END) AS churned,
    ROUND(100.0 * SUM(CASE WHEN s.end_date >= b.m AND s.end_date < b.m_next THEN 1 ELSE 0 END)
          / NULLIF(SUM(CASE WHEN s.start_date < b.m AND (s.end_date IS NULL OR s.end_date >= b.m) THEN 1 ELSE 0 END), 0), 2) AS churn_rate_pct
FROM bounds b
CROSS JOIN subs s
GROUP BY b.m, s.region
ORDER BY b.m, s.region;

-- name: churn_by_contract_plan
-- title: 90-day churn by contract and plan type
-- question: Which contract and product combinations lose the most customers?
-- chart: bar|contract_type|churn_rate_90d_pct|plan_type
-- insight: Population = customers active at the snapshot; outcome = cancelled within the next 90 days.
SELECT
    s.contract_type,
    p.plan_type,
    COUNT(*) AS customers,
    SUM(CASE WHEN s.end_date > :snapshot AND s.end_date <= :horizon_end THEN 1 ELSE 0 END) AS churned_90d,
    ROUND(100.0 * SUM(CASE WHEN s.end_date > :snapshot AND s.end_date <= :horizon_end THEN 1 ELSE 0 END) / COUNT(*), 2) AS churn_rate_90d_pct,
    ROUND(SUM(p.monthly_fee), 0) AS mrr_ghs
FROM v_subscriptions_clean s
JOIN plans p ON p.workspace_id = s.workspace_id AND p.plan_id = s.plan_id
WHERE s.workspace_id = :ws
  AND s.start_date <= :snapshot
  AND (s.end_date IS NULL OR s.end_date > :snapshot)
GROUP BY s.contract_type, p.plan_type
ORDER BY churn_rate_90d_pct DESC;

-- name: churn_by_tenure
-- title: 90-day churn by tenure band
-- question: Are new customers more likely to leave than established ones?
-- chart: bar|tenure_band|churn_rate_90d_pct
WITH base AS (
    SELECT
        CASE
            WHEN s.start_date > :t3 THEN '1. 0-3 months'
            WHEN s.start_date > :t12 THEN '2. 4-12 months'
            WHEN s.start_date > :t24 THEN '3. 13-24 months'
            ELSE '4. 24+ months'
        END AS tenure_band,
        CASE WHEN s.end_date > :snapshot AND s.end_date <= :horizon_end THEN 1 ELSE 0 END AS churned
    FROM v_subscriptions_clean s
    WHERE s.workspace_id = :ws
      AND s.start_date <= :snapshot
      AND (s.end_date IS NULL OR s.end_date > :snapshot)
)
SELECT tenure_band, COUNT(*) AS customers, SUM(churned) AS churned_90d,
       ROUND(100.0 * SUM(churned) / COUNT(*), 2) AS churn_rate_90d_pct
FROM base
GROUP BY tenure_band
ORDER BY tenure_band;

-- name: payment_behaviour_vs_churn
-- title: Payment behaviour (last 6 months) vs 90-day churn
-- question: Do late or missed payments signal that a customer is about to leave?
-- chart: bar|payment_profile|churn_rate_90d_pct
WITH active AS (
    SELECT s.subscription_id,
           CASE WHEN s.end_date > :snapshot AND s.end_date <= :horizon_end THEN 1 ELSE 0 END AS churned
    FROM v_subscriptions_clean s
    WHERE s.workspace_id = :ws AND s.start_date <= :snapshot
      AND (s.end_date IS NULL OR s.end_date > :snapshot)
),
pay AS (
    SELECT p.subscription_id, SUM(p.is_late) AS late_bills, SUM(p.is_unpaid) AS unpaid_bills
    FROM v_payments_clean p
    WHERE p.workspace_id = :ws AND p.due_date > :m6_start AND p.due_date <= :snapshot
    GROUP BY p.subscription_id
)
SELECT
    CASE
        WHEN COALESCE(pay.unpaid_bills, 0) > 0 THEN '3. Missed at least one bill'
        WHEN COALESCE(pay.late_bills, 0) >= 2 THEN '2. Paid late 2+ times'
        WHEN COALESCE(pay.late_bills, 0) = 1 THEN '1. Paid late once'
        ELSE '0. Always on time'
    END AS payment_profile,
    COUNT(*) AS customers,
    SUM(a.churned) AS churned_90d,
    ROUND(100.0 * SUM(a.churned) / COUNT(*), 2) AS churn_rate_90d_pct
FROM active a
LEFT JOIN pay ON pay.subscription_id = a.subscription_id
GROUP BY 1
ORDER BY 1;

-- name: support_experience_vs_churn
-- title: Support experience (90 days before snapshot) vs 90-day churn
-- question: Do customers who struggle with support leave more often?
-- chart: bar|support_profile|churn_rate_90d_pct
WITH active AS (
    SELECT s.customer_id,
           CASE WHEN s.end_date > :snapshot AND s.end_date <= :horizon_end THEN 1 ELSE 0 END AS churned
    FROM v_subscriptions_clean s
    WHERE s.workspace_id = :ws AND s.start_date <= :snapshot
      AND (s.end_date IS NULL OR s.end_date > :snapshot)
),
tix AS (
    SELECT t.customer_id,
           COUNT(*) AS tickets,
           SUM(CASE WHEN t.csat_score <= 2 THEN 1 ELSE 0 END) AS unhappy_tickets
    FROM v_tickets_clean t
    WHERE t.workspace_id = :ws AND t.opened_date > :d90_snapshot AND t.opened_date <= :snapshot
    GROUP BY t.customer_id
)
SELECT
    CASE
        WHEN tix.customer_id IS NULL THEN '0. No tickets'
        WHEN tix.unhappy_tickets > 0 THEN '3. Gave a CSAT of 1-2'
        WHEN tix.tickets >= 2 THEN '2. 2+ tickets'
        ELSE '1. One ticket'
    END AS support_profile,
    COUNT(*) AS customers,
    SUM(a.churned) AS churned_90d,
    ROUND(100.0 * SUM(a.churned) / COUNT(*), 2) AS churn_rate_90d_pct
FROM active a
LEFT JOIN tix ON tix.customer_id = a.customer_id
GROUP BY 1
ORDER BY 1;

-- name: usage_decline_early_warning
-- title: Usage trend (last 3 vs prior 3 months) vs 90-day churn
-- question: Does falling data usage give an early warning before customers leave?
-- chart: bar|usage_trend|churn_rate_90d_pct
WITH active AS (
    SELECT s.subscription_id,
           CASE WHEN s.end_date > :snapshot AND s.end_date <= :horizon_end THEN 1 ELSE 0 END AS churned
    FROM v_subscriptions_clean s
    WHERE s.workspace_id = :ws AND s.start_date <= :m6_start
      AND (s.end_date IS NULL OR s.end_date > :snapshot)
),
u AS (
    SELECT u.subscription_id,
           AVG(CASE WHEN u.usage_month >= :m3_start THEN u.data_gb_used END) AS recent,
           AVG(CASE WHEN u.usage_month < :m3_start THEN u.data_gb_used END) AS prior
    FROM v_usage_clean u
    WHERE u.workspace_id = :ws AND u.usage_month >= :m6_start AND u.usage_month <= :snapshot
    GROUP BY u.subscription_id
),
banded AS (
    SELECT a.churned,
           CASE
               WHEN u.prior IS NULL OR u.prior = 0 OR u.recent IS NULL THEN '9. Not enough history'
               WHEN u.recent / u.prior < 0.6 THEN '1. Down 40%+'
               WHEN u.recent / u.prior < 0.9 THEN '2. Down 10-40%'
               WHEN u.recent / u.prior <= 1.1 THEN '3. Stable (+/-10%)'
               ELSE '4. Up 10%+'
           END AS usage_trend
    FROM active a
    LEFT JOIN u ON u.subscription_id = a.subscription_id
)
SELECT usage_trend, COUNT(*) AS customers, SUM(churned) AS churned_90d,
       ROUND(100.0 * SUM(churned) / COUNT(*), 2) AS churn_rate_90d_pct
FROM banded
GROUP BY usage_trend
ORDER BY usage_trend;

-- name: cancel_reasons
-- title: Recorded cancellation reasons (last 12 months)
-- question: What do leaving customers tell us, and how much revenue walks out with each reason?
-- chart: bar|cancel_reason|share_pct
-- insight: Share of total computed with a window SUM() OVER ().
SELECT
    COALESCE(s.cancel_reason, 'Not captured') AS cancel_reason,
    COUNT(*) AS cancellations,
    ROUND(SUM(p.monthly_fee), 0) AS lost_mrr_ghs,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS share_pct
FROM v_subscriptions_clean s
JOIN plans p ON p.workspace_id = s.workspace_id AND p.plan_id = s.plan_id
WHERE s.workspace_id = :ws AND s.end_date > :d365_start AND s.end_date <= :as_of
GROUP BY COALESCE(s.cancel_reason, 'Not captured')
ORDER BY cancellations DESC;

-- name: support_by_category
-- title: Support tickets by category (last 6 months)
-- question: Where is the support experience weakest?
-- chart: bar|category|avg_csat
SELECT
    t.category,
    COUNT(*) AS tickets,
    ROUND(AVG(t.resolution_hours), 1) AS avg_resolution_hours,
    ROUND(AVG(t.csat_score), 2) AS avg_csat,
    ROUND(100.0 * SUM(CASE WHEN t.csat_score IS NULL THEN 1 ELSE 0 END) / COUNT(*), 1) AS no_survey_pct
FROM v_tickets_clean t
WHERE t.workspace_id = :ws AND t.opened_date > :d180_start AND t.opened_date <= :as_of
GROUP BY t.category
ORDER BY avg_csat;
