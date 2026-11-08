-- Target: customer active at :snapshot cancels within the following 90 days.
-- Parameters: :ws, :snapshot, :horizon_end
SELECT
    s.customer_id AS entity_id,
    CASE WHEN s.end_date IS NOT NULL AND s.end_date > :snapshot AND s.end_date <= :horizon_end
         THEN 1 ELSE 0 END AS target
FROM v_subscriptions_clean s
WHERE s.workspace_id = :ws
  AND s.start_date <= :snapshot
  AND (s.end_date IS NULL OR s.end_date > :snapshot);
