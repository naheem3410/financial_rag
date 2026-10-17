-- Step 3.1: the document card, for papers uploaded outside the financial
-- filings pipeline (policies, procedures, guides, FAQs, forms/notices).
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    product_area TEXT,
    audience TEXT NOT NULL DEFAULT 'everyone',
    effective_date DATE NOT NULL,
    doc_version_label TEXT,
    file_name TEXT NOT NULL,
    uploader TEXT,
    kind TEXT,
    page_count INT,
    upload_date TIMESTAMPTZ NOT NULL DEFAULT now(),
    version INT NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_documents_title_type ON documents (title, doc_type);
