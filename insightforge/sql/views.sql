-- =====================================================================
-- Silver layer: cleaned, de-duplicated, PII-minimised views over the raw
-- tables. Portable across MySQL 8 / MariaDB 10.2+ and SQLite 3.25+.
-- Statements are separated by a line holding a double semicolon.
-- =====================================================================

DROP VIEW IF EXISTS v_tickets_clean
;;
DROP VIEW IF EXISTS v_payments_clean
;;
DROP VIEW IF EXISTS v_usage_clean
;;
DROP VIEW IF EXISTS v_subscriptions_clean
;;
DROP VIEW IF EXISTS v_customers_clean
;;

-- Customers: one row per real person (duplicates share a phone number; keep the
-- earliest record), region labels standardised, PII columns (name, phone,
-- email) deliberately excluded so downstream analytics and the AI layer never
-- see them.
CREATE VIEW v_customers_clean AS
SELECT
    r.workspace_id,
    r.customer_id,
    r.gender,
    r.date_of_birth,
    r.segment,
    r.acquisition_channel,
    r.signup_date,
    CASE
        WHEN LOWER(TRIM(r.region)) IN ('greater accra', 'gt. accra', 'g. accra', 'accra') THEN 'Greater Accra'
        WHEN LOWER(TRIM(r.region)) IN ('ashanti', 'ashanti region', 'kumasi') THEN 'Ashanti'
        WHEN LOWER(TRIM(r.region)) = 'western' THEN 'Western'
        WHEN LOWER(TRIM(r.region)) = 'central' THEN 'Central'
        WHEN LOWER(TRIM(r.region)) = 'eastern' THEN 'Eastern'
        WHEN LOWER(TRIM(r.region)) = 'northern' THEN 'Northern'
        WHEN LOWER(TRIM(r.region)) = 'volta' THEN 'Volta'
        WHEN LOWER(TRIM(r.region)) = 'bono' THEN 'Bono'
        WHEN LOWER(TRIM(r.region)) = 'upper east' THEN 'Upper East'
        WHEN LOWER(TRIM(r.region)) = 'upper west' THEN 'Upper West'
        ELSE NULL
    END AS region
FROM (
    SELECT c.*,
           ROW_NUMBER() OVER (
               PARTITION BY c.workspace_id, COALESCE(c.phone, c.customer_id)
               ORDER BY c.signup_date, c.customer_id
           ) AS rn
    FROM customers c
) r
WHERE r.rn = 1
;;

-- Subscriptions: records whose end_date precedes start_date are quarantined
-- (excluded); status is re-derived from end_date because the raw status column
-- is not always kept in sync.
CREATE VIEW v_subscriptions_clean AS
SELECT
    s.workspace_id,
    s.subscription_id,
    s.customer_id,
    s.plan_id,
    s.start_date,
    s.end_date,
    CASE WHEN s.end_date IS NULL THEN 'active' ELSE 'cancelled' END AS status,
    s.contract_type,
    s.autopay,
    s.cancel_reason
FROM subscriptions s
WHERE s.end_date IS NULL OR s.end_date >= s.start_date
;;

-- Usage: one row per subscription-month (duplicates removed), negative and
-- physically implausible readings (> 5x the plan allowance) set to NULL.
CREATE VIEW v_usage_clean AS
SELECT
    d.workspace_id,
    d.subscription_id,
    d.usage_month,
    CASE
        WHEN d.data_gb_used < 0 THEN NULL
        WHEN d.data_gb_used > 5 * p.data_gb THEN NULL
        ELSE d.data_gb_used
    END AS data_gb_used,
    d.voice_minutes,
    d.sms_count,
    d.dropped_calls
FROM (
    SELECT u.*,
           ROW_NUMBER() OVER (
               PARTITION BY u.workspace_id, u.subscription_id, u.usage_month
               ORDER BY u.usage_id
           ) AS rn
    FROM monthly_usage u
) d
JOIN subscriptions s
  ON s.workspace_id = d.workspace_id AND s.subscription_id = d.subscription_id
JOIN plans p
  ON p.workspace_id = s.workspace_id AND p.plan_id = s.plan_id
WHERE d.rn = 1
;;

-- Payments: one bill per subscription-month (double-posted rows removed),
-- with late / unpaid flags and a data-consistency flag.
CREATE VIEW v_payments_clean AS
SELECT
    d.workspace_id,
    d.subscription_id,
    d.billing_month,
    d.due_date,
    d.amount_due,
    CASE WHEN d.amount_paid > 3 * d.amount_due THEN d.amount_due ELSE d.amount_paid END AS amount_paid,
    d.paid_date,
    d.payment_method,
    CASE WHEN d.paid_date IS NULL OR COALESCE(d.amount_paid, 0) = 0 THEN 1 ELSE 0 END AS is_unpaid,
    CASE WHEN d.paid_date IS NOT NULL AND d.paid_date > d.due_date THEN 1 ELSE 0 END AS is_late,
    CASE WHEN d.paid_date IS NOT NULL AND d.paid_date < d.billing_month THEN 1
         WHEN d.amount_paid > 3 * d.amount_due THEN 1
         ELSE 0 END AS has_inconsistency
FROM (
    SELECT p.*,
           ROW_NUMBER() OVER (
               PARTITION BY p.workspace_id, p.subscription_id, p.billing_month
               ORDER BY p.payment_id
           ) AS rn
    FROM payments p
) d
WHERE d.rn = 1
;;

-- Tickets: out-of-range CSAT (valid 1-5) and negative resolution times nulled.
CREATE VIEW v_tickets_clean AS
SELECT
    t.workspace_id,
    t.ticket_id,
    t.customer_id,
    t.opened_date,
    t.category,
    CASE WHEN t.resolution_hours < 0 THEN NULL ELSE t.resolution_hours END AS resolution_hours,
    CASE WHEN t.csat_score BETWEEN 1 AND 5 THEN t.csat_score ELSE NULL END AS csat_score
FROM support_tickets t
;;
