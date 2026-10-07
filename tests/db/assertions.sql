\set ON_ERROR_STOP on
BEGIN;
SET search_path = secretary, pg_catalog;
DO $review_test$
DECLARE e uuid; s uuid; p uuid; t uuid; other_task uuid; step uuid; c uuid;
BEGIN
    INSERT INTO entities(name, entity_type, domain) VALUES ('Fictional PC','device','pc') RETURNING id INTO e;
    INSERT INTO sources(source_type,uri,citation,retrieved_at) VALUES ('user_statement','test:fixture','Synthetic fixture',now()) RETURNING id INTO s;
    INSERT INTO pending_claims(entity_id,source_id,claim_type,predicate,proposed_value,evidence,extraction_model,prompt_version)
    VALUES (e,s,'attribute','ram_gb','16','Synthetic statement','fake-model','fixture-v1') RETURNING id INTO p;
    BEGIN
        INSERT INTO claims(entity_id,source_id,pending_claim_id,claim_type,predicate,value,evidence,origin,verification_status,valid_from)
        VALUES (e,s,p,'attribute','ram_gb','16','Synthetic statement','reviewed_extraction','verified',now());
        RAISE EXCEPTION 'TEST FAILED: pending candidate promoted';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE 'TEST FAILED:%' THEN RAISE; END IF;
    END;
    UPDATE pending_claims SET review_status='accepted', reviewer='test-human', reviewed_at=now() WHERE id=p;
    INSERT INTO claims(entity_id,source_id,pending_claim_id,claim_type,predicate,value,evidence,origin,verification_status,valid_from,valid_to)
    VALUES (e,s,p,'attribute','ram_gb','16','Synthetic statement','reviewed_extraction','verified','2020-01-01','2021-01-01') RETURNING id INTO c;
    IF EXISTS (SELECT 1 FROM current_claims WHERE id=c) THEN RAISE EXCEPTION 'Expired claim visible as current'; END IF;
    INSERT INTO tasks(request,requested_by,domain,completion_criteria,status,checkpoint,next_run_at)
    VALUES ('Fictional diagnosis','test','pc','Verify synthetic result','waiting_approval','{"next_step":"diagnose"}',now()) RETURNING id INTO t;
    INSERT INTO tasks(request,requested_by,domain,completion_criteria)
    VALUES ('Fictional shopping','test','shopping','Compare fictional items') RETURNING id INTO other_task;
    INSERT INTO task_steps(task_id,step_order,description) VALUES (t,0,'Read-only diagnostic') RETURNING id INTO step;
    BEGIN
        INSERT INTO actions(task_id,step_id,actor,tool,operation,parameters,risk,authorization_basis,idempotency_key)
        VALUES (other_task,step,'test','fake','read','{}','read_only','test scope','cross-task');
        RAISE EXCEPTION 'Cross-task step accepted';
    EXCEPTION WHEN foreign_key_violation THEN NULL; END;
    BEGIN
        INSERT INTO actions(task_id,actor,tool,operation,parameters,risk,authorization_basis,idempotency_key)
        VALUES (t,'test','fake','change','{}','high','none','high-risk');
        RAISE EXCEPTION 'High-risk action without approval accepted';
    EXCEPTION WHEN check_violation THEN NULL; END;
    BEGIN
        INSERT INTO claims(entity_id,source_id,claim_type,predicate,value,evidence,origin,verification_status,valid_from,valid_to)
        VALUES (e,s,'fact','x','1','fixture','user_explicit','verified',now(),now()-interval '1 day');
        RAISE EXCEPTION 'Invalid validity accepted';
    EXCEPTION WHEN check_violation THEN NULL; END;
    UPDATE tasks SET checkpoint='{"next_step":"resume"}' WHERE id=t;
    IF (SELECT revision FROM tasks WHERE id=t) <> 1 THEN RAISE EXCEPTION 'Revision not incremented'; END IF;
    IF has_table_privilege('secretary_candidate_writer','secretary.claims','INSERT') OR
       has_column_privilege('secretary_candidate_writer','secretary.pending_claims','review_status','INSERT') OR
       has_table_privilege('secretary_task_writer','secretary.approvals','UPDATE') OR
       has_table_privilege('secretary_audit_writer','secretary.audit_events','DELETE') THEN
        RAISE EXCEPTION 'Role privilege boundary failed';
    END IF;
    IF NOT has_column_privilege('secretary_review_writer',
              'secretary.approvals', 'approval_kind', 'INSERT')
       OR has_column_privilege('secretary_review_writer',
              'secretary.approvals', 'task_id', 'INSERT')
       OR has_table_privilege('secretary_candidate_writer',
              'secretary.approvals', 'INSERT')
       OR has_table_privilege('secretary_review_writer',
              'secretary.approvals', 'UPDATE') THEN
        RAISE EXCEPTION 'Memory-only review role boundary failed';
    END IF;
END $review_test$;

DO $promotion_test$
BEGIN
    IF to_regclass('secretary.entity_relations') IS NULL
       OR to_regclass('secretary.pkb_pending_intake') IS NULL
       OR to_regclass('secretary.pkb_memory_intakes') IS NULL
       OR to_regclass('secretary.finance_transactions') IS NULL
       OR to_regclass('secretary.service_connections') IS NULL
       OR to_regclass('secretary.llm_profiles') IS NULL
       OR to_regclass('secretary.magi_member_assignments') IS NULL
       OR to_regclass('secretary.service_billing_profiles') IS NULL THEN
        RAISE EXCEPTION 'Production promotion schema is incomplete';
    END IF;

    IF to_regclass('secretary.pkb_episode_receipts') IS NOT NULL THEN
        RAISE EXCEPTION 'Prototype episode receipt table leaked into production schema';
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='secretary'
          AND table_name='llm_profiles'
          AND column_name IN ('provider','endpoint','credential_env')
    ) THEN
        RAISE EXCEPTION 'Legacy LLM connection snapshot columns leaked into production schema';
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname = ANY(ARRAY[
            'secretary_finance_writer',
            'secretary_magi_settings_writer',
            'secretary_connection_writer',
            'secretary_billing_writer'
        ])
    ) THEN
        RAISE EXCEPTION 'Legacy feature-specific runtime role leaked into fresh production cluster';
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_roles
        WHERE rolname='secretary_pkb_proto_writer_20260927'
    ) THEN
        RAISE EXCEPTION 'Prototype writer role leaked into production cluster';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM information_schema.table_privileges
        WHERE table_schema='secretary'
          AND table_name IN (
              'finance_transactions',
              'llm_profiles',
              'magi_member_assignments',
              'service_connections',
              'service_billing_profiles'
          )
          AND grantee='PUBLIC'
          AND privilege_type IN ('SELECT','INSERT','UPDATE','DELETE')
    ) THEN
        RAISE EXCEPTION 'PUBLIC privilege leaked into protected runtime tables';
    END IF;
END $promotion_test$;
ROLLBACK;
-- Persistent synthetic checkpoint checked after restart and restore.
INSERT INTO secretary.tasks(id,request,requested_by,domain,completion_criteria,status,checkpoint,next_run_at)
VALUES ('00000000-0000-4000-8000-000000000001','Synthetic resume test','test','pc','Verify restart and restore',
    'waiting_approval','{"next_step":"diagnose"}','2030-01-01');
