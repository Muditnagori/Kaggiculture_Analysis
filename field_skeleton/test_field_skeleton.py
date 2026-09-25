#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Basic invariant checks for the Field Skeleton pipeline.

Usage:
    python tests/test_field_skeleton.py --submission /path/to/submission_dir \\
        --replay replay1.json:1 [--replay replay2.json:0 ...]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from field_skeleton.aggregate import aggregate
from field_skeleton.route_log import walk_replay
from field_skeleton.static_analysis import analyze_route_db


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--submission", required=True)
    parser.add_argument("--replay", action="append", required=True, dest="replays")
    args = parser.parse_args()

    static_stats = analyze_route_db(args.submission)
    assert static_stats.n_routes > 0, "route_db reports zero routes"
    assert sum(static_stats.routes_per_sequence_length.values()) == static_stats.n_routes, (
        "per-sequence-length route counts don't sum to the DB's total route count -- "
        "static_analysis is miscounting somewhere"
    )
    print(f"OK  static analysis: {static_stats.n_routes} routes, "
          f"coverage by k = {static_stats.routes_per_sequence_length}")

    logs = []
    for raw in args.replays:
        path, idx = raw.rsplit(":", 1)
        log = walk_replay(args.submission, path, int(idx))
        assert log.events, f"no route-selection events captured for {path}:{idx} -- instrumentation may be broken"
        for ev in log.events:
            assert ev.day >= 2, f"unexpected day-1 route event in {path}"
            assert ev.match_type in ("exact",) or ev.match_type.startswith("same_step") or ev.match_type == "none", (
                f"unrecognized match_type {ev.match_type!r} in {path}"
            )
        print(f"OK  {path} (player {idx}): {len(log.events)} route events, won={log.won}")
        logs.append(log)

    result = aggregate(logs)
    total_record = result["overall_wins"] + result["overall_losses"] + result["overall_unknown"]
    assert total_record == len(logs), "aggregate() lost or duplicated a match in the win/loss/unknown tally"
    print(f"OK  aggregate: {result['n_matches']} matches, "
          f"{len(result['world_table'])} distinct worlds observed")
    print("All checks passed.")


if __name__ == "__main__":
    main()
