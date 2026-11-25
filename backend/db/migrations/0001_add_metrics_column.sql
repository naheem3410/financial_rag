-- Step 1.4: a fact's financial metric labels, so search can filter by metric
-- and not just company/year. Also present inside `data` JSONB automatically,
-- since Proposition.metrics serializes with the rest of the row.
ALTER TABLE propositions ADD COLUMN IF NOT EXISTS metrics TEXT[] NOT NULL DEFAULT '{}';
CREATE INDEX IF NOT EXISTS idx_propositions_metrics ON propositions USING GIN (metrics);
