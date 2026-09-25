#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
CLI: build a Field Skeleton report for a submission.

Usage:
    python run_field_skeleton.py --submission /path/to/submission_dir \\
        --replay replay1.json:1 --replay replay2.json:0 \\
        --out report.html

Each --replay is "path:player_index" -- player_index is which seat in that
replay file was played by the submission you're analyzing.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from field_skeleton.aggregate import aggregate
from field_skeleton.report import build_report_payload, write_report
from field_skeleton.route_log import walk_replay
from field_skeleton.static_analysis import analyze_route_db


def parse_replay_arg(raw: str) -> tuple[str, int]:
    if ":" not in raw:
        raise argparse.ArgumentTypeError(f"expected path:player_index, got {raw!r}")
    path, idx = raw.rsplit(":", 1)
    try:
        return path, int(idx)
    except ValueError:
        raise argparse.ArgumentTypeError(f"player_index must be an integer, got {idx!r}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--submission", type=Path, required=True, help="Directory with main.py/route_compressor.py/route_db/")
    parser.add_argument(
        "--replay", action="append", type=parse_replay_arg, default=[], dest="replays",
        help="path:player_index, repeatable -- one per (replay, seat) the submission played",
    )
    parser.add_argument("--out", type=Path, default=Path("field_skeleton_report.html"))
    args = parser.parse_args()

    if not args.replays:
        parser.error("at least one --replay path:player_index is required")

    static_stats = analyze_route_db(str(args.submission))

    from field_skeleton.route_log import walk_many_replays
    print(f"replaying {len(args.replays)} match(es) against submission at {args.submission}...", file=sys.stderr)
    logs = walk_many_replays(str(args.submission), args.replays)

    dynamic_stats = aggregate(logs)
    payload = build_report_payload(static_stats, dynamic_stats)
    out_path = write_report(payload, args.out)

    print(f"\nWrote report: {out_path.resolve()}")
    print(f"Open it in a browser: file://{out_path.resolve()}")


if __name__ == "__main__":
    main()
