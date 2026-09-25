#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Basic invariant checks for the Field Worlds pipeline.

Usage:
    python tests/test_field_worlds.py --data-dir /path/to/parquet_files [--submission /path/to/submission_dir]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from field_worlds.parquet_scan import scan_directory
from field_worlds.json_scan import scan_json_directory
from field_worlds.worlds import distinct_worlds_possible, world_frequency_table


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--format", choices=["parquet", "json"], default="parquet")
    parser.add_argument("--submission", default=None)
    parser.add_argument("--k", type=int, default=2)
    args = parser.parse_args()

    scan = scan_directory(args.data_dir) if args.format == "parquet" else scan_json_directory(args.data_dir)
    assert scan.used_files, f"no usable enriched-schema files found in {args.data_dir}"
    print(f"OK  scan: {len(scan.used_files)} used, {len(scan.skipped_files)} skipped, {len(scan.records)} episodes")

    # no duplicate episode_ids should have slipped through cross-file dedup
    ids = [r.episode_id for r in scan.records]
    assert len(ids) == len(set(ids)), "duplicate episode_id found across files -- cross-file dedup is broken"

    table = world_frequency_table(scan.records, k=args.k)
    assert 0 < len(table) <= distinct_worlds_possible(8, args.k), (
        f"world count {len(table)} outside plausible range for k={args.k}"
    )
    total_seen = sum(w.times_seen for w in table.values())
    assert total_seen == len(scan.records), (
        f"world_frequency_table lost or duplicated episodes: {total_seen} vs {len(scan.records)} records "
        "(only matches if every episode has >= k shops recorded)"
    )
    print(f"OK  world_frequency_table: {len(table)} distinct k={args.k} worlds, {total_seen} episode-observations")

    if args.submission:
        from field_worlds.cross_reference import cross_reference, load_route_db

        route_db, normalize = load_route_db(args.submission)
        cov = cross_reference(table, route_db, normalize)
        assert len(cov) == len(table), "cross_reference dropped or duplicated worlds"
        covered = sum(1 for c in cov.values() if c.has_exact_route)
        print(f"OK  cross_reference: {covered}/{len(cov)} worlds have an exact route in {args.submission}")

    print("All checks passed.")


if __name__ == "__main__":
    main()
