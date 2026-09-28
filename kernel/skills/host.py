"""Small, Host-neutral capability inventory boundary for native Skills."""

from __future__ import annotations

from typing import Protocol


class HostSkillInventoryAdapter(Protocol):
    """Return attributable availability only; never claim activation or use."""

    def inventory(self) -> dict:
        """Return {adapter_id, skills:[{name, availability, provenance, revision_sha256?}]}.

        `inventory_complete` states whether absence from `skills` is evidence
        of unavailability. Each `provenance` is `ADAPTER_DISCOVERY` or
        `HOST_DECLARED`. Implementations must not expose private paths or
        opaque Host state through this boundary.
        """
        ...
