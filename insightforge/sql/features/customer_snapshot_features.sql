-- =====================================================================
-- Customer feature snapshot ("gold" layer) for supervised use cases.
-- One row per customer active at :snapshot, using ONLY information
-- available on or before :snapshot (no target leakage).
-- Parameters: :ws, :snapshot, :m3_start, :m6_start, :d90_snapshot
-- Techniques: CTEs, conditional aggregation, ROW_NUMBER window, LEFT JOINs.
-- =====================================================================
WITH active AS (
    SELECT s.workspace_id, s.subscription_id, s.customer_id, s.plan_id,
           s.start_date, s.contract_type, s.autopay
    FROM v_subscriptions_clean s
    WHERE s.workspace_id = :ws
      AND s.start_date <= :snapshot
      AND (s.end_date IS NULL OR s.end_date > :snapshot)
),
usage_win AS (
    SELECT u.subscription_id,
           AVG(CASE WHEN u.usage_month >= :m3_start THEN u.data_gb_used END)                                   AS data_gb_avg_3m,
           AVG(CASE WHEN u.usage_month < :m3_start THEN u.data_gb_used END)                                    AS data_gb_avg_prev3m,
           AVG(CASE WHEN u.usage_month >= :m3_start THEN u.voice_minutes END)                                  AS voice_min_avg_3m,
           AVG(CASE WHEN u.usage_month < :m3_start THEN u.voice_minutes END)                                   AS voice_min_avg_prev3m,
           AVG(CASE WHEN u.usage_month >= :m3_start THEN u.sms_count END)                                      AS sms_avg_3m,
           SUM(CASE WHEN u.usage_month >= :m3_start THEN u.dropped_calls ELSE 0 END)                           AS dropped_calls_3m
    FROM v_usage_clean u
    WHERE u.workspace_id = :ws AND u.usage_month >= :m6_start AND u.usage_month <= :snapshot
    GROUP BY u.subscription_id
),
last_month AS (
    SELECT subscription_id, data_gb_used AS data_gb_last_month
    FROM (
        SELECT u.subscription_id, u.data_gb_used,
               ROW_NUMBER() OVER (PARTITION BY u.subscription_id ORDER BY u.usage_month DESC) AS rn
        FROM v_usage_clean u
        WHERE u.workspace_id = :ws AND u.usage_month >= :m3_start AND u.usage_month <= :snapshot
    ) x
    WHERE x.rn = 1
),
pay AS (
    SELECT p.subscription_id,
           COUNT(*)            AS bills_6m,
           SUM(p.is_late)      AS late_payments_6m,
           SUM(p.is_unpaid)    AS unpaid_bills_6m,
           SUM(CASE WHEN p.due_date > :m3_start THEN p.is_unpaid ELSE 0 END) AS unpaid_bills_3m
    FROM v_payments_clean p
    WHERE p.workspace_id = :ws AND p.due_date > :m6_start AND p.due_date <= :snapshot
    GROUP BY p.subscription_id
),
tix AS (
    SELECT t.customer_id,
           COUNT(*)                                                     AS tickets_90d,
           SUM(CASE WHEN t.category = 'Network' THEN 1 ELSE 0 END)      AS network_tickets_90d,
           SUM(CASE WHEN t.category = 'Billing' THEN 1 ELSE 0 END)      AS billing_tickets_90d,
           SUM(CASE WHEN t.csat_score <= 2 THEN 1 ELSE 0 END)           AS low_csat_tickets_90d,
           AVG(t.csat_score)                                            AS avg_csat_90d,
           AVG(t.resolution_hours)                                      AS avg_resolution_hours_90d
    FROM v_tickets_clean t
    WHERE t.workspace_id = :ws AND t.opened_date > :d90_snapshot AND t.opened_date <= :snapshot
    GROUP BY t.customer_id
)
SELECT
    a.customer_id                              AS entity_id,
    a.start_date,
    c.date_of_birth,
    COALESCE(c.region, 'Unknown')              AS region,
    c.segment,
    c.acquisition_channel,
    a.contract_type,
    CASE WHEN a.autopay THEN 1 ELSE 0 END      AS autopay,
    p.plan_type,
    p.monthly_fee,
    p.data_gb                                  AS plan_data_gb,
    uw.data_gb_avg_3m,
    uw.data_gb_avg_prev3m,
    lm.data_gb_last_month,
    uw.voice_min_avg_3m,
    uw.voice_min_avg_prev3m,
    uw.sms_avg_3m,
    COALESCE(uw.dropped_calls_3m, 0)           AS dropped_calls_3m,
    COALESCE(pay.bills_6m, 0)                  AS bills_6m,
    COALESCE(pay.late_payments_6m, 0)          AS late_payments_6m,
    COALESCE(pay.unpaid_bills_6m, 0)           AS unpaid_bills_6m,
    COALESCE(pay.unpaid_bills_3m, 0)           AS unpaid_bills_3m,
    COALESCE(tix.tickets_90d, 0)               AS tickets_90d,
    COALESCE(tix.network_tickets_90d, 0)       AS network_tickets_90d,
    COALESCE(tix.billing_tickets_90d, 0)       AS billing_tickets_90d,
    COALESCE(tix.low_csat_tickets_90d, 0)      AS low_csat_tickets_90d,
    tix.avg_csat_90d,
    tix.avg_resolution_hours_90d
FROM active a
JOIN v_customers_clean c ON c.workspace_id = a.workspace_id AND c.customer_id = a.customer_id
JOIN plans p            ON p.workspace_id = a.workspace_id AND p.plan_id = a.plan_id
LEFT JOIN usage_win uw  ON uw.subscription_id = a.subscription_id
LEFT JOIN last_month lm ON lm.subscription_id = a.subscription_id
LEFT JOIN pay           ON pay.subscription_id = a.subscription_id
LEFT JOIN tix           ON tix.customer_id = a.customer_id
ORDER BY a.customer_id;
