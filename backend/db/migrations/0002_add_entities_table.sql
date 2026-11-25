-- Step 2.1: one shared company list, built during ingestion, read by the
-- API and anything else that needs to know which companies are indexed --
-- instead of two separate registry-building code paths.
CREATE TABLE IF NOT EXISTS entities (
    company TEXT PRIMARY KEY,
    ticker TEXT,
    version INT NOT NULL DEFAULT 1
);
