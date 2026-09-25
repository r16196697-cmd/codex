-- Bind newly created PurgePlans to one Task. Existing plans retain NULL and
-- are rejected for execution until explicitly replanned under the bound schema.
ALTER TABLE purge_plan_records ADD COLUMN task_id TEXT REFERENCES tasks(task_id);
CREATE INDEX purge_plan_records_task_idx ON purge_plan_records(task_id, plan_id);
