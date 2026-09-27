"""Launch the local Nexus companion panel against an existing data root."""

from __future__ import annotations

import argparse
from pathlib import Path

from adapters.panel.application import open_panel_application
from adapters.panel.ui import launch_panel


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nexus-panel", description="Nexus native companion panel (ALPHA)")
    parser.add_argument("--data-root", required=True, type=Path, help="Existing Nexus data root; the panel will not initialize a database")
    parser.add_argument("--policy", type=Path, help="Optional existing Nexus policy JSON")
    parser.add_argument("--independent-purge-journal", type=Path, help="Optional configured independent purge journal")
    args = parser.parse_args(argv)
    application = open_panel_application(
        args.data_root,
        policy_path=args.policy,
        independent_purge_journal_path=args.independent_purge_journal,
    )
    try:
        launch_panel(application.view_model)
    finally:
        application.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
