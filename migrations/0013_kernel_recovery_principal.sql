-- Infrastructure identity introduced by this software version, not a
-- reconstructed historical actor or a user trust anchor.
INSERT INTO principals(principal_id, principal_type, status)
VALUES ('nexus-core-recovery', 'SERVICE', 'ACTIVE');

CREATE TRIGGER kernel_recovery_principal_no_update
BEFORE UPDATE ON principals
WHEN OLD.principal_id = 'nexus-core-recovery'
BEGIN
    SELECT RAISE(ABORT, 'KERNEL_RECOVERY_PRINCIPAL_IMMUTABLE');
END;

CREATE TRIGGER kernel_recovery_principal_no_delete
BEFORE DELETE ON principals
WHEN OLD.principal_id = 'nexus-core-recovery'
BEGIN
    SELECT RAISE(ABORT, 'KERNEL_RECOVERY_PRINCIPAL_IMMUTABLE');
END;
