#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
CLI: build a Field Ledger dashboard from a raw Kaggle episode replay JSON.

Usage:
    python run_field_ledger.py path/to/replay.json --player 1 --out dashboard.html
    python run_field_ledger.py path/to/replay.json               # defaults: player 0, dashboard.html

Only needs the Python standard library. Open the resulting .html file in
any browser (it loads Chart.js from a CDN at view time).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from field_ledger.dashboard import build_dashboard_payload, write_dashboard
from field_ledger.reconstruct import load_replay, reconstruct_match, team_names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replay", type=Path, help="Path to a Kaggle episode replay JSON file")
    parser.add_argument("--player", type=int, default=0, help="Which player index is 'us' (default 0)")
    parser.add_argument("--out", type=Path, default=Path("dashboard.html"), help="Output HTML path")
    args = parser.parse_args()

    if not args.replay.exists():
        parser.error(f"replay file not found: {args.replay}")

    replay = load_replay(args.replay)
    names = team_names(replay)
    n_players = len(replay["steps"][0])
    if args.player >= n_players:
        parser.error(f"--player {args.player} out of range (this replay has {n_players} players)")

    opp_index = 1 - args.player if n_players == 2 else (args.player + 1) % n_players

    match = reconstruct_match(replay)
    records_us = match[args.player]
    records_opp = match[opp_index]

    name_us = names[args.player] if args.player < len(names) else f"player_{args.player}"
    name_opp = names[opp_index] if opp_index < len(names) else f"player_{opp_index}"

    payload = build_dashboard_payload(records_us, records_opp, name_us, name_opp)
    out_path = write_dashboard(payload, args.out)

    print(f"Wrote dashboard: {out_path.resolve()}")
    print(f"Open it in a browser: file://{out_path.resolve()}")


if __name__ == "__main__":
    main()
