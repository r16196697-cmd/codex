"""Build a sanitized, read-only inventory of local Skill instruction artifacts.

This hashes files but never emits absolute paths or instruction text, and it
does not execute any Skill. Unassessed criteria remain UNKNOWN and block selection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.eval.run_presence_regression_phase6 import SCREEN_CRITERIA, derive_screen_exclusion_reasons  # noqa: E402


OUTPUT = ROOT / "eval/academy/results/phase6-real-skill-static-screen.json"
SOURCES = (
    ("CODEX_SKILLS", Path.home() / ".codex/skills"),
    ("BUNDLED_PLUGIN_SKILLS", Path.home() / ".codex/plugins/cache/openai-bundled"),
    ("CURATED_PLUGIN_SKILLS", Path.home() / ".codex/plugins/cache/openai-curated-remote"),
)


def _safe_relative_id(category: str, root: Path, skill_file: Path) -> str:
    relative = skill_file.parent.relative_to(root).as_posix()
    sanitized = re.sub(r"[^A-Za-z0-9._/-]+", "_", relative).strip("/.")
    return f"{category}:{sanitized}"


def build_ledger() -> dict:
    rows = []
    for category, source_root in SOURCES:
        if not source_root.is_dir():
            raise FileNotFoundError(f"Required local instruction source unavailable: {category}")
        for skill_file in sorted(source_root.rglob("SKILL.md"), key=lambda path: path.as_posix().casefold()):
            raw = skill_file.read_bytes()
            criteria = {name: "UNKNOWN" for name in SCREEN_CRITERIA}
            # This artifact's documented workflow may write project instruction files.
            if "project-experience-curator" in skill_file.as_posix().lower():
                criteria["requires_filesystem_mutation"] = "TRUE"
            reasons = derive_screen_exclusion_reasons(criteria)
            eligible = not reasons and all(criteria[name] == "TRUE" for name in SCREEN_CRITERIA[4:]) and all(
                criteria[name] == "FALSE" for name in SCREEN_CRITERIA[:4]
            )
            rows.append({
                "artifact_id": _safe_relative_id(category, source_root, skill_file),
                "source_category": category,
                "instruction_sha256": hashlib.sha256(raw).hexdigest(),
                "criteria": criteria,
                "selection_eligible": eligible,
                "exclusion_reasons": reasons,
            })
    rows.sort(key=lambda row: row["artifact_id"])
    return {
        "artifact_kind": "READ_ONLY_STATIC_INSTRUCTION_ARTIFACT_SCREEN",
        "screening_status": "REPRODUCIBLE_STATIC_SCREEN_WITH_UNKNOWN_CRITERIA",
        "claims_boundary": [
            "This is not runtime safety verification.",
            "This does not mean a Skill is safe, evaluated, or natively loaded.",
            "UNKNOWN criteria block selection.",
        ],
        "criteria_values": ["TRUE", "FALSE", "UNKNOWN"],
        "selected_candidate_count": sum(row["selection_eligible"] for row in rows),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Write the sanitized ledger to the repository result path.")
    args = parser.parse_args()
    ledger = build_ledger()
    encoded = json.dumps(ledger, ensure_ascii=False, indent=2) + "\n"
    if args.write:
        OUTPUT.write_text(encoded, encoding="utf-8", newline="\n")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
