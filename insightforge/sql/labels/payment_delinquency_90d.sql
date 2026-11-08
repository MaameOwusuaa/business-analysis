-- Target: customer active at :snapshot pays at least one bill late, or misses one,
-- among bills falling due in the following 90 days.
-- Parameters: :ws, :snapshot, :horizon_end
SELECT
    s.customer_id AS entity_id,
    COALESCE(MAX(CASE WHEN p.is_late = 1 OR p.is_unpaid = 1 THEN 1 ELSE 0 END), 0) AS target
FROM v_subscriptions_clean s
LEFT JOIN v_payments_clean p
       ON p.workspace_id = s.workspace_id
      AND p.subscription_id = s.subscription_id
      AND p.due_date > :snapshot
      AND p.due_date <= :horizon_end
WHERE s.workspace_id = :ws
  AND s.start_date <= :snapshot
  AND (s.end_date IS NULL OR s.end_date > :snapshot)
GROUP BY s.customer_id;
