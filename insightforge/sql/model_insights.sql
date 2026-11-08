-- =====================================================================
-- Joining model output back to the business data with SQL, so the AI and
-- the dashboard can talk about segments (never individual customers).
-- Parameters: :ws, :run_id
-- =====================================================================

-- name: risk_by_segment
-- title: Predicted risk by contract and plan type
-- question: Where is the predicted risk concentrated?
WITH scored AS (
    SELECT p.entity_id, p.score, p.risk_tier, p.value, s.contract_type, pl.plan_type
    FROM predictions p
    JOIN v_subscriptions_clean s ON s.workspace_id = p.workspace_id AND s.customer_id = p.entity_id
    JOIN plans pl ON pl.workspace_id = s.workspace_id AND pl.plan_id = s.plan_id
    WHERE p.workspace_id = :ws AND p.run_id = :run_id
)
SELECT contract_type, plan_type,
       COUNT(*) AS customers,
       SUM(CASE WHEN risk_tier = 'High' THEN 1 ELSE 0 END) AS high_risk_customers,
       ROUND(100.0 * SUM(CASE WHEN risk_tier = 'High' THEN 1 ELSE 0 END) / COUNT(*), 1) AS high_risk_share_pct,
       ROUND(100.0 * AVG(score), 1) AS avg_predicted_risk_pct,
       ROUND(SUM(score * value), 0) AS expected_monthly_revenue_at_risk_ghs
FROM scored
GROUP BY contract_type, plan_type
ORDER BY expected_monthly_revenue_at_risk_ghs DESC;

-- name: risk_by_region
-- title: Predicted risk by region
-- question: Which regions need the most retention attention?
SELECT COALESCE(c.region, 'Unknown') AS region,
       COUNT(*) AS customers,
       SUM(CASE WHEN p.risk_tier = 'High' THEN 1 ELSE 0 END) AS high_risk_customers,
       ROUND(100.0 * AVG(p.score), 1) AS avg_predicted_risk_pct,
       ROUND(SUM(p.score * p.value), 0) AS expected_monthly_revenue_at_risk_ghs
FROM predictions p
JOIN v_customers_clean c ON c.workspace_id = p.workspace_id AND c.customer_id = p.entity_id
WHERE p.workspace_id = :ws AND p.run_id = :run_id
GROUP BY COALESCE(c.region, 'Unknown')
ORDER BY expected_monthly_revenue_at_risk_ghs DESC;
