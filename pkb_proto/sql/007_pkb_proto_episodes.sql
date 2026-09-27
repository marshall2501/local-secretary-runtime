-- Fictional-only corpus receipts for the existing isolated PKB prototype DB.
-- Preserve every episode as a Source independently of interpreted Claims.
SET search_path = secretary, pg_catalog;
CREATE TABLE pkb_episode_receipts (
    episode_id text PRIMARY KEY CHECK (episode_id ~ '^(pc|rc)-[0-9]{2}$'),
    source_id uuid NOT NULL UNIQUE REFERENCES sources(id),
    payload_sha256 char(64) NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    domain text NOT NULL CHECK (domain IN ('pc', 'rc')),
    interpretation_state text NOT NULL DEFAULT 'uninterpreted'
      CHECK (interpretation_state IN ('uninterpreted','pending_review','interpreted')),
    created_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT ON pkb_episode_receipts TO secretary_pkb_proto_writer_20260927;
