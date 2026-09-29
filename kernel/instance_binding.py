"""Canonical identities shared by instance bootstrap and storage checks."""

from __future__ import annotations

import hashlib
import json
from typing import Any


BOOTSTRAP_PROTOCOL_VERSION = "NEXUS_INSTANCE_BOOTSTRAP_V1"


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def parse_json_object(raw: bytes) -> dict[str, Any]:
    """Parse a JSON object without duplicate keys or non-standard constants."""
    def pairs_hook(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value

    def reject_constant(_value: str):
        raise ValueError("non-standard JSON constant")

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs_hook, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError("JSON document must be an object")
    return value


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def policy_sha256(policy: dict[str, Any]) -> str:
    return sha256_hex(canonical_json_bytes(policy))


def request_sha256(request: dict[str, Any]) -> str:
    return sha256_hex(canonical_json_bytes(request))
