-- Prototype-only finance schema for MoneyForward CSV imports.
-- Stores only structured records in the isolated prototype DB.
BEGIN;

DO $guard$
BEGIN
  IF current_database() <> 'secretary_pkb_proto_20260927' THEN
    RAISE EXCEPTION 'Refusing unexpected database';
  END IF;
END $guard$;

CREATE TABLE secretary.finance_import_batches (
  id uuid PRIMARY KEY,
  source_system text NOT NULL CHECK (source_system = 'moneyforward_me'),
  source_filename text NOT NULL CHECK (btrim(source_filename) <> ''),
  source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
  row_count integer NOT NULL CHECK (row_count >= 0),
  imported_at timestamptz NOT NULL DEFAULT now(),
  status text NOT NULL CHECK (status IN ('committed')),
  UNIQUE (source_system, source_sha256)
);

CREATE TABLE secretary.finance_accounts (
  id uuid PRIMARY KEY,
  source_system text NOT NULL CHECK (source_system = 'moneyforward_me'),
  external_name text NOT NULL CHECK (btrim(external_name) <> ''),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_system, external_name)
);

CREATE TABLE secretary.finance_categories (
  id uuid PRIMARY KEY,
  source_system text NOT NULL CHECK (source_system = 'moneyforward_me'),
  major_name text NOT NULL,
  minor_name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_system, major_name, minor_name)
);

CREATE TABLE secretary.finance_transactions (
  id uuid PRIMARY KEY,
  source_system text NOT NULL CHECK (source_system = 'moneyforward_me'),
  external_id text NOT NULL CHECK (btrim(external_id) <> ''),
  transaction_date date NOT NULL,
  description text NOT NULL,
  amount_jpy bigint NOT NULL,
  account_id uuid REFERENCES secretary.finance_accounts(id),
  category_id uuid REFERENCES secretary.finance_categories(id),
  memo text NOT NULL DEFAULT '',
  is_transfer boolean NOT NULL,
  calculation_target boolean NOT NULL,
  current_content_hash text NOT NULL CHECK (current_content_hash ~ '^[0-9a-f]{64}$'),
  first_seen_batch_id uuid NOT NULL REFERENCES secretary.finance_import_batches(id),
  last_seen_batch_id uuid NOT NULL REFERENCES secretary.finance_import_batches(id),
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_system, external_id)
);

CREATE TABLE secretary.finance_transaction_versions (
  id uuid PRIMARY KEY,
  transaction_id uuid NOT NULL REFERENCES secretary.finance_transactions(id) ON DELETE CASCADE,
  source_system text NOT NULL CHECK (source_system = 'moneyforward_me'),
  external_id text NOT NULL,
  source_batch_id uuid NOT NULL REFERENCES secretary.finance_import_batches(id),
  content_hash text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
  payload jsonb NOT NULL,
  observed_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source_system, external_id, content_hash)
);

CREATE INDEX finance_transactions_date_idx
  ON secretary.finance_transactions(transaction_date DESC);
CREATE INDEX finance_transactions_account_idx
  ON secretary.finance_transactions(account_id, transaction_date DESC);
CREATE INDEX finance_transactions_category_idx
  ON secretary.finance_transactions(category_id, transaction_date DESC);

GRANT SELECT, INSERT, UPDATE ON
  secretary.finance_import_batches,
  secretary.finance_accounts,
  secretary.finance_categories,
  secretary.finance_transactions,
  secretary.finance_transaction_versions
TO secretary_pkb_proto_writer_20260927;

INSERT INTO secretary.schema_migrations(version, sha256)
VALUES ('016_pkb_proto_finance.sql',
        'PENDING_SHA_REPLACED_BY_APPLIER');

COMMIT;
