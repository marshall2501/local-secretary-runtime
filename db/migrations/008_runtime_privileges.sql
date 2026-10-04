-- Runtime table/schema DDL is complete after migrations 005-007.
-- Normal localhost runtime access is provisioned by provision_daily_runtime.py.
-- Do not add feature-specific writer roles here; external/high-impact actions are
-- controlled at the API/MCP/RITSUKO policy boundaries.
SET search_path = secretary, pg_catalog;

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
