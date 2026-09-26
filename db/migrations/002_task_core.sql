SET search_path = secretary, pg_catalog;
CREATE TABLE tasks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_task_id uuid REFERENCES tasks(id),
    request text NOT NULL,
    requested_by text NOT NULL,
    domain text NOT NULL,
    entity_id uuid REFERENCES entities(id),
    completion_criteria text NOT NULL CHECK (btrim(completion_criteria) <> ''),
    permission_scope jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(permission_scope) = 'object'),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','waiting_approval','waiting_external','paused','completed','failed','cancelled')),
    due_at timestamptz,
    next_run_at timestamptz,
    checkpoint jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(checkpoint) = 'object'),
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CHECK (parent_task_id IS DISTINCT FROM id),
    CHECK ((status = 'completed') = (completed_at IS NOT NULL))
);
CREATE INDEX tasks_resume ON tasks(status, next_run_at);
CREATE INDEX tasks_due ON tasks(due_at) WHERE status NOT IN ('completed','cancelled','failed');
CREATE TABLE task_steps (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL REFERENCES tasks(id),
    step_order integer NOT NULL CHECK (step_order >= 0),
    description text NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','waiting_approval','completed','failed','skipped')),
    checkpoint jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(checkpoint) = 'object'),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    retry_at timestamptz,
    last_error text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(task_id, step_order),
    UNIQUE(task_id, id)
);
CREATE TABLE task_dependencies (
    task_id uuid NOT NULL REFERENCES tasks(id),
    depends_on_task_id uuid NOT NULL REFERENCES tasks(id),
    PRIMARY KEY(task_id, depends_on_task_id),
    CHECK (task_id <> depends_on_task_id)
);
CREATE TABLE task_step_dependencies (
    task_id uuid NOT NULL,
    step_id uuid NOT NULL,
    depends_on_step_id uuid NOT NULL,
    PRIMARY KEY(task_id, step_id, depends_on_step_id),
    FOREIGN KEY(task_id, step_id) REFERENCES task_steps(task_id, id),
    FOREIGN KEY(task_id, depends_on_step_id) REFERENCES task_steps(task_id, id),
    CHECK (step_id <> depends_on_step_id)
);
CREATE TABLE approvals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    approval_kind text NOT NULL CHECK (approval_kind IN ('memory_write','external_action')),
    task_id uuid REFERENCES tasks(id),
    pending_claim_id uuid REFERENCES pending_claims(id),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','revoked')),
    scope jsonb NOT NULL CHECK (jsonb_typeof(scope) = 'object' AND scope <> '{}'::jsonb),
    requested_by text NOT NULL,
    decided_by text,
    reason text,
    requested_at timestamptz NOT NULL DEFAULT now(),
    decided_at timestamptz,
    expires_at timestamptz NOT NULL,
    CHECK (expires_at > requested_at),
    CHECK ((approval_kind = 'memory_write' AND pending_claim_id IS NOT NULL)
        OR (approval_kind = 'external_action' AND task_id IS NOT NULL AND pending_claim_id IS NULL)),
    CHECK ((status = 'pending' AND decided_by IS NULL AND decided_at IS NULL)
        OR (status <> 'pending' AND decided_by IS NOT NULL AND decided_at IS NOT NULL))
);
CREATE INDEX approvals_pending ON approvals(status, expires_at);
CREATE TABLE actions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id uuid NOT NULL REFERENCES tasks(id),
    step_id uuid,
    approval_id uuid REFERENCES approvals(id),
    actor text NOT NULL,
    tool text NOT NULL,
    operation text NOT NULL,
    parameters jsonb NOT NULL CHECK (jsonb_typeof(parameters) = 'object'),
    risk text NOT NULL CHECK (risk IN ('read_only','low','high')),
    authorization_basis text NOT NULL CHECK (btrim(authorization_basis) <> ''),
    status text NOT NULL DEFAULT 'planned' CHECK (status IN ('planned','running','succeeded','failed','cancelled','unknown')),
    idempotency_key text NOT NULL UNIQUE CHECK (btrim(idempotency_key) <> ''),
    reversible boolean NOT NULL DEFAULT false,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    FOREIGN KEY(task_id, step_id) REFERENCES task_steps(task_id, id),
    CHECK (risk <> 'high' OR approval_id IS NOT NULL)
);
CREATE INDEX actions_task ON actions(task_id, recorded_at);
CREATE TABLE results (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    action_id uuid NOT NULL REFERENCES actions(id),
    source_id uuid NOT NULL REFERENCES sources(id),
    outcome text NOT NULL CHECK (outcome IN ('success','failure','inconclusive')),
    summary text NOT NULL,
    evidence jsonb NOT NULL,
    error text,
    verified_by text,
    verified_at timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX results_action ON results(action_id);
CREATE TABLE audit_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    actor text NOT NULL,
    event_type text NOT NULL,
    task_id uuid REFERENCES tasks(id),
    action_id uuid REFERENCES actions(id),
    approval_id uuid REFERENCES approvals(id),
    object_type text NOT NULL,
    object_id uuid,
    details jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(details) = 'object')
);
CREATE INDEX audit_task_time ON audit_events(task_id, occurred_at);
CREATE INDEX audit_object ON audit_events(object_type, object_id, occurred_at);

CREATE FUNCTION touch_task() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    NEW.revision = OLD.revision + 1;
    RETURN NEW;
END $$;
CREATE TRIGGER task_revision BEFORE UPDATE ON tasks
FOR EACH ROW EXECUTE FUNCTION touch_task();

-- Group roles only, no application logins or passwords. Admin is provisioning-only.
CREATE ROLE secretary_reader NOLOGIN;
CREATE ROLE secretary_candidate_writer NOLOGIN;
CREATE ROLE secretary_memory_writer NOLOGIN;
CREATE ROLE secretary_task_writer NOLOGIN;
CREATE ROLE secretary_audit_writer NOLOGIN;
GRANT USAGE ON SCHEMA secretary TO secretary_reader, secretary_candidate_writer,
    secretary_memory_writer, secretary_task_writer, secretary_audit_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA secretary TO secretary_reader;
GRANT SELECT ON entities, sources, pending_claims TO secretary_candidate_writer;
-- Candidate producers cannot choose accepted/reviewer fields or update existing rows.
GRANT INSERT(entity_id, source_id, claim_type, predicate, proposed_value, confidence,
    evidence, extraction_model, prompt_version) ON pending_claims TO secretary_candidate_writer;
GRANT SELECT, INSERT, UPDATE ON entities, sources, pending_claims, claims, issues,
    hypotheses, decisions TO secretary_memory_writer;
GRANT SELECT ON current_claims, approvals TO secretary_memory_writer;
GRANT SELECT, INSERT, UPDATE ON tasks, task_steps, task_dependencies,
    task_step_dependencies, actions, results TO secretary_task_writer;
GRANT SELECT ON approvals TO secretary_task_writer;
-- Approval decisions remain admin-only until a separate trusted Policy service exists.
GRANT INSERT ON audit_events TO secretary_audit_writer;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA secretary FROM PUBLIC;
