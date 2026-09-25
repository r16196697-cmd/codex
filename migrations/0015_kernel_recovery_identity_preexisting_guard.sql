-- A v13 database may contain ordinary authority facts using the identity
-- introduced by 0013. Refuse upgrade rather than rewriting those facts.
-- The migration runner wraps this guard and its version record in one transaction.
CREATE TABLE kernel_recovery_v15_preexisting_guard (
    must_be_zero INTEGER NOT NULL CHECK (must_be_zero = 0)
);

INSERT INTO kernel_recovery_v15_preexisting_guard(must_be_zero)
SELECT 1 WHERE EXISTS (
    SELECT 1 FROM delegation_grants
    WHERE issued_by = 'nexus-core-recovery'
       OR granted_to = 'nexus-core-recovery'
    UNION ALL
    SELECT 1 FROM trust_anchors
    WHERE principal_id = 'nexus-core-recovery'
    UNION ALL
    SELECT 1 FROM tasks
    WHERE requester_id = 'nexus-core-recovery'
);

DROP TABLE kernel_recovery_v15_preexisting_guard;
