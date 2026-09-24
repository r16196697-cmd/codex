from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def _canon(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class IndependentPurgeJournal:
    """Hash-chained append-only JSONL journal stored outside the database backup root."""

    def __init__(self, path: str | Path, data_root: str | Path):
        self.path = Path(path).expanduser().resolve()
        root = Path(data_root).expanduser().resolve()
        if self.path == root or root in self.path.parents:
            raise ValueError("purge journal must be outside the Nexus data root")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records = []
        previous = "0" * 64
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                row = json.loads(line)
            except Exception as exc:
                raise ValueError(f"purge journal corrupt at line {number}") from exc
            digest = row.pop("record_hash", None)
            if row.get("previous_hash") != previous or hashlib.sha256(_canon(row).encode("utf-8")).hexdigest() != digest:
                raise ValueError(f"purge journal hash chain invalid at line {number}")
            if row.get("sequence") != number or row.get("action") not in {"BARRIER_INSTALLED", "BARRIER_PARTIAL", "BARRIER_RELEASED"}:
                raise ValueError(f"purge journal record invalid at line {number}")
            if not isinstance(row.get("protected_refs"), list) or not row.get("barrier_id") or not row.get("plan_id"):
                raise ValueError(f"purge journal record invalid at line {number}")
            row["record_hash"] = digest
            records.append(row)
            previous = digest
        return records

    def append(self, *, action: str, barrier_id: str, plan_id: str, plan_hash: str, lineage_revision: int, protected_refs: list[str]) -> dict[str, Any]:
        if action not in {"BARRIER_INSTALLED", "BARRIER_PARTIAL", "BARRIER_RELEASED"}:
            raise ValueError("unsupported purge journal action")
        if lineage_revision < 0 or len(plan_hash) != 64:
            raise ValueError("invalid purge journal plan binding")
        prior = self.read()
        body = {"version": 1, "sequence": len(prior) + 1, "action": action, "barrier_id": barrier_id, "plan_id": plan_id, "plan_hash": plan_hash, "lineage_revision": lineage_revision, "protected_refs": sorted(set(protected_refs)), "previous_hash": prior[-1]["record_hash"] if prior else "0" * 64}
        body["record_hash"] = hashlib.sha256(_canon(body).encode("utf-8")).hexdigest()
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(_canon(body) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return body
