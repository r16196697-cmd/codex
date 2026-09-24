"""Credential Broker port; deployments provide a reviewed OS-key-store adapter."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CredentialBroker(Protocol):
    def resolve(self, credential_ref: str) -> object:
        """Return an ephemeral credential handle, never a serializable secret."""
        ...
