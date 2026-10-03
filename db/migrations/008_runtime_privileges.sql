-- Production group-role boundaries for schemas promoted after migration 004.
SET search_path = secretary, pg_catalog;

CREATE ROLE secretary_finance_writer NOLOGIN;
CREATE ROLE secretary_magi_settings_writer NOLOGIN;
CREATE ROLE secretary_connection_writer NOLOGIN;
CREATE ROLE secretary_billing_writer NOLOGIN;

GRANT USAGE ON SCHEMA secretary
    TO secretary_finance_writer,
       secretary_magi_settings_writer,
       secretary_connection_writer,
       secretary_billing_writer;

REVOKE ALL ON
    pkb_input_receipts,
    pkb_correction_receipts,
    pkb_pending_intake,
    entity_relations,
    pkb_memory_intakes,
    pkb_memory_candidate_receipts,
    finance_import_batches,
    finance_accounts,
    finance_categories,
    finance_transactions,
    finance_transaction_versions,
    llm_profiles,
    magi_member_assignments,
    service_connections,
    service_billing_profiles
FROM PUBLIC;

GRANT SELECT, INSERT ON
    pkb_input_receipts,
    pkb_correction_receipts,
    pkb_memory_intakes,
    pkb_memory_candidate_receipts
TO secretary_memory_writer;

GRANT SELECT, INSERT, UPDATE ON
    pkb_pending_intake,
    entity_relations
TO secretary_memory_writer;

GRANT SELECT, INSERT, UPDATE ON
    finance_import_batches,
    finance_accounts,
    finance_categories,
    finance_transactions,
    finance_transaction_versions
TO secretary_finance_writer;

GRANT SELECT, INSERT, UPDATE, DELETE ON
    llm_profiles,
    magi_member_assignments
TO secretary_magi_settings_writer;

GRANT SELECT, INSERT, UPDATE, DELETE ON
    service_connections
TO secretary_connection_writer;

GRANT SELECT, INSERT, UPDATE, DELETE ON
    service_billing_profiles
TO secretary_billing_writer;
