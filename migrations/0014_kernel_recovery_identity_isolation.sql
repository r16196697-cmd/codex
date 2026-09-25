-- The kernel recovery SERVICE identity is not a participant in ordinary
-- delegation or user-created task authority, including direct SQL writes.
CREATE TRIGGER kernel_recovery_no_delegation_grant
BEFORE INSERT ON delegation_grants
WHEN NEW.issued_by = 'nexus-core-recovery'
  OR NEW.granted_to = 'nexus-core-recovery'
BEGIN
    SELECT RAISE(ABORT, 'KERNEL_RECOVERY_IDENTITY_RESERVED');
END;

CREATE TRIGGER kernel_recovery_no_trust_anchor
BEFORE INSERT ON trust_anchors
WHEN NEW.principal_id = 'nexus-core-recovery'
BEGIN
    SELECT RAISE(ABORT, 'KERNEL_RECOVERY_IDENTITY_RESERVED');
END;

CREATE TRIGGER kernel_recovery_no_task_requester
BEFORE INSERT ON tasks
WHEN NEW.requester_id = 'nexus-core-recovery'
BEGIN
    SELECT RAISE(ABORT, 'KERNEL_RECOVERY_IDENTITY_RESERVED');
END;
