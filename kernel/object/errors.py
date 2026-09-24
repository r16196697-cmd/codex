"""Stable, payload-free error types for object and storage invariants."""


class NexusStoreError(Exception):
    """Base class for deterministic store errors."""


class CommandConflict(NexusStoreError):
    """A command_id was reused with a different canonical request."""


class ConcurrentModification(NexusStoreError):
    """A compare-and-swap expected revision was stale."""


class IntegrityMismatch(NexusStoreError):
    """Stored bytes do not match the declared SHA-256 integrity value."""


class LineageCycle(NexusStoreError):
    """A lineage edge would create a cycle or self-loop."""


class ObjectNotFound(NexusStoreError):
    """The requested object identity is not present."""


class PurgeBarrierActive(NexusStoreError):
    """A write referencing an object protected by an active barrier was denied."""


class PurgedObject(NexusStoreError):
    """Payload access was attempted for a purged object."""


class MigrationError(NexusStoreError):
    """A schema migration is missing, changed, or inconsistent."""


class WriterAlreadyRunning(NexusStoreError):
    """Another process owns the single-writer lease for this data root."""
