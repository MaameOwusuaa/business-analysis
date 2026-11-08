-- =====================================================================
-- Data-quality checks on the RAW (bronze) tables.
-- Every check returns one row: issue_count, total_rows.
-- Parameter :ws = workspace id (tenant isolation).
-- =====================================================================

-- name: customers_missing_region
-- title: Customers with no region recorded
-- table: customers
-- severity: medium
-- handling: Kept; region treated as "Unknown" category in the model.
SELECT SUM(CASE WHEN region IS NULL OR TRIM(region) = '' THEN 1 ELSE 0 END) AS issue_count,
       COUNT(*) AS total_rows
FROM customers WHERE workspace_id = :ws;

-- name: customers_missing_dob
-- title: Customers with no date of birth
-- table: customers
-- severity: low
-- handling: Age imputed with the median inside the ML pipeline; a missing-flag is added.
SELECT SUM(CASE WHEN date_of_birth IS NULL THEN 1 ELSE 0 END) AS issue_count, COUNT(*) AS total_rows
FROM customers WHERE workspace_id = :ws;

-- name: customers_nonstandard_region
-- title: Region labels that do not match the reference list (spelling / case / city names)
-- table: customers
-- severity: medium
-- handling: Standardised in v_customers_clean (e.g. 'Gt. Accra', 'kumasi' -> canonical region).
SELECT SUM(CASE WHEN region IS NOT NULL AND /*CASE_SENSITIVE*/region NOT IN
           ('Greater Accra','Ashanti','Western','Central','Eastern','Northern','Volta','Bono','Upper East','Upper West')
           THEN 1 ELSE 0 END) AS issue_count,
       COUNT(*) AS total_rows
FROM customers WHERE workspace_id = :ws;

-- name: customers_duplicate_phone
-- title: Duplicate customer registrations (same phone number, different customer id)
-- table: customers
-- severity: high
-- handling: De-duplicated in v_customers_clean - earliest record kept (ROW_NUMBER window).
SELECT COALESCE(SUM(cnt - 1), 0) AS issue_count,
       (SELECT COUNT(*) FROM customers WHERE workspace_id = :ws) AS total_rows
FROM (
    SELECT phone, COUNT(*) AS cnt
    FROM customers
    WHERE workspace_id = :ws AND phone IS NOT NULL
    GROUP BY phone
    HAVING COUNT(*) > 1
) d;

-- name: subscriptions_end_before_start
-- title: Subscriptions whose end date is before the start date
-- table: subscriptions
-- severity: high
-- handling: Quarantined (excluded) in v_subscriptions_clean; needs correction in the source system.
SELECT SUM(CASE WHEN end_date IS NOT NULL AND end_date < start_date THEN 1 ELSE 0 END) AS issue_count,
       COUNT(*) AS total_rows
FROM subscriptions WHERE workspace_id = :ws;

-- name: subscriptions_status_mismatch
-- title: Subscriptions whose status disagrees with the end date (e.g. 'active' but cancelled)
-- table: subscriptions
-- severity: high
-- handling: Status re-derived from end_date in v_subscriptions_clean.
SELECT SUM(CASE WHEN (status = 'active' AND end_date IS NOT NULL)
                  OR (status = 'cancelled' AND end_date IS NULL) THEN 1 ELSE 0 END) AS issue_count,
       COUNT(*) AS total_rows
FROM subscriptions WHERE workspace_id = :ws;

-- name: usage_duplicate_months
-- title: Duplicate usage records for the same subscription and month
-- table: monthly_usage
-- severity: medium
-- handling: De-duplicated in v_usage_clean (first record kept).
SELECT COALESCE(SUM(cnt - 1), 0) AS issue_count,
       (SELECT COUNT(*) FROM monthly_usage WHERE workspace_id = :ws) AS total_rows
FROM (
    SELECT subscription_id, usage_month, COUNT(*) AS cnt
    FROM monthly_usage
    WHERE workspace_id = :ws
    GROUP BY subscription_id, usage_month
    HAVING COUNT(*) > 1
) d;

-- name: usage_negative_or_missing
-- title: Usage readings that are negative or missing
-- table: monthly_usage
-- severity: medium
-- handling: Negative values set to NULL; missing values imputed in the ML pipeline.
SELECT SUM(CASE WHEN data_gb_used IS NULL OR data_gb_used < 0 THEN 1 ELSE 0 END) AS issue_count,
       COUNT(*) AS total_rows
FROM monthly_usage WHERE workspace_id = :ws;

-- name: usage_implausible
-- title: Usage readings above 5x the plan allowance (meter / mediation errors)
-- table: monthly_usage
-- severity: low
-- handling: Set to NULL in v_usage_clean.
SELECT SUM(CASE WHEN u.data_gb_used > 5 * p.data_gb THEN 1 ELSE 0 END) AS issue_count,
       COUNT(*) AS total_rows
FROM monthly_usage u
JOIN subscriptions s ON s.workspace_id = u.workspace_id AND s.subscription_id = u.subscription_id
JOIN plans p ON p.workspace_id = s.workspace_id AND p.plan_id = s.plan_id
WHERE u.workspace_id = :ws;

-- name: payments_double_posted
-- title: Bills posted more than once for the same subscription and month
-- table: payments
-- severity: high
-- handling: De-duplicated in v_payments_clean - otherwise revenue would be overstated.
SELECT COALESCE(SUM(cnt - 1), 0) AS issue_count,
       (SELECT COUNT(*) FROM payments WHERE workspace_id = :ws) AS total_rows
FROM (
    SELECT subscription_id, billing_month, COUNT(*) AS cnt
    FROM payments
    WHERE workspace_id = :ws
    GROUP BY subscription_id, billing_month
    HAVING COUNT(*) > 1
) d;

-- name: payments_inconsistent
-- title: Payments dated before the billing month, or paid amount > 3x the amount due
-- table: payments
-- severity: medium
-- handling: Flagged (has_inconsistency) and overpayments capped at amount due in v_payments_clean.
SELECT SUM(CASE WHEN (paid_date IS NOT NULL AND paid_date < billing_month)
                  OR amount_paid > 3 * amount_due THEN 1 ELSE 0 END) AS issue_count,
       COUNT(*) AS total_rows
FROM payments WHERE workspace_id = :ws;

-- name: tickets_invalid_csat
-- title: Support tickets with CSAT outside the valid 1-5 scale
-- table: support_tickets
-- severity: low
-- handling: Set to NULL in v_tickets_clean.
SELECT SUM(CASE WHEN csat_score IS NOT NULL AND (csat_score < 1 OR csat_score > 5) THEN 1 ELSE 0 END) AS issue_count,
       COUNT(*) AS total_rows
FROM support_tickets WHERE workspace_id = :ws;

-- name: tickets_negative_resolution
-- title: Support tickets with a negative resolution time
-- table: support_tickets
-- severity: low
-- handling: Set to NULL in v_tickets_clean.
SELECT SUM(CASE WHEN resolution_hours < 0 THEN 1 ELSE 0 END) AS issue_count, COUNT(*) AS total_rows
FROM support_tickets WHERE workspace_id = :ws;

-- name: orphan_usage
-- title: Usage records with no matching subscription (referential integrity)
-- table: monthly_usage
-- severity: high
-- handling: Excluded by the inner joins in the silver views.
SELECT COUNT(*) AS issue_count,
       (SELECT COUNT(*) FROM monthly_usage WHERE workspace_id = :ws) AS total_rows
FROM monthly_usage u
LEFT JOIN subscriptions s
       ON s.workspace_id = u.workspace_id AND s.subscription_id = u.subscription_id
WHERE u.workspace_id = :ws AND s.subscription_id IS NULL;
