#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
CLI: pinpoint exactly when catalog routes were used in a specific match.

Usage:
    python run_timeline.py --catalog catalog.json --replay some_match.json --player 1 --out timeline.html
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from route_catalog.catalog import load_catalog
from route_catalog.report import build_timeline_report_payload, write_timeline_report
from route_catalog.timeline import build_timeline


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalog", type=Path, required=True, help="catalog.json from run_build_catalog.py")
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--player", type=int, default=0)
    parser.add_argument("--out", type=Path, default=Path("timeline.html"))
    args = parser.parse_args()

    catalog = load_catalog(args.catalog)
    events = build_timeline(catalog, str(args.replay), args.player)

    replay_data = json.loads(args.replay.read_text(encoding="utf-8"))
    episode_id = str(replay_data.get("id", args.replay.stem))

    payload = build_timeline_report_payload(events, episode_id, args.player)
    out_path = write_timeline_report(payload, args.out)

    print(f"Wrote timeline: {out_path.resolve()}")
    print(f"Open it in a browser: file://{out_path.resolve()}")


if __name__ == "__main__":
    main()
