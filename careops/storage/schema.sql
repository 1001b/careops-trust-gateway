-- CareOps Postgres schema (synthetic demo). Requires pgvector.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS providers (
    provider_id TEXT PRIMARY KEY,
    provider_name TEXT NOT NULL,
    state TEXT NOT NULL,
    active_from DATE NOT NULL,
    inactive_from DATE
);

CREATE TABLE IF NOT EXISTS provider_networks (
    provider_id TEXT NOT NULL,
    payer_network TEXT NOT NULL,
    effective_from DATE NOT NULL,
    effective_to DATE,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS appointment_slots (
    slot_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    slot_date DATE NOT NULL,
    payer_network TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE OR REPLACE VIEW gold_provider_availability_daily AS
SELECT
    s.slot_id,
    s.provider_id,
    p.provider_name,
    p.state,
    s.slot_date,
    s.payer_network,
    CASE
        WHEN s.status = 'open'
         AND s.slot_date >= p.active_from
         AND (p.inactive_from IS NULL OR s.slot_date < p.inactive_from)
         AND EXISTS (
             SELECT 1
             FROM provider_networks n
             WHERE n.provider_id = s.provider_id
               AND n.payer_network = s.payer_network
               AND n.status = 'eligible'
               AND s.slot_date >= n.effective_from
               AND (n.effective_to IS NULL OR s.slot_date <= n.effective_to)
         )
        THEN 1 ELSE 0
    END AS is_bookable
FROM appointment_slots s
JOIN providers p ON p.provider_id = s.provider_id;

CREATE TABLE IF NOT EXISTS document_chunks (
    chunk_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    path TEXT NOT NULL,
    text TEXT NOT NULL,
    effective_from DATE NOT NULL,
    effective_to DATE,
    doc_class TEXT NOT NULL,
    roles TEXT[] NOT NULL,
    authority INT NOT NULL,
    embedding vector(128)
);

CREATE INDEX IF NOT EXISTS document_chunks_doc_id_idx ON document_chunks (doc_id);
