#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
CLI: build a Field Worlds report from a directory of enriched moves-Parquet
files, optionally cross-referenced against a submission's own route_db.

Usage:
    python run_field_worlds.py --data-dir /path/to/parquet_files --k 2 --out report.html
    python run_field_worlds.py --data-dir /path/to/parquet_files --k 2 \\
        --submission /path/to/submission_dir --out report.html
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from field_worlds.cross_reference import cross_reference, load_route_db
from field_worlds.parquet_scan import scan_directory
from field_worlds.json_scan import scan_json_directory
from field_worlds.report import build_report_payload, write_report
from field_worlds.worlds import world_frequency_table

DEFAULT_N_SHOP_TYPES = 8  # matches this game's known shop count; override with --n-shop-types if that changes


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, required=True, help="Directory of replay files")
    parser.add_argument("--format", choices=["parquet", "json"], default="parquet",
                         help="'parquet' for enriched moves-Parquet files, 'json' for raw Kaggle episode replay JSON files")
    parser.add_argument("--pattern", default=None, help="Glob pattern (defaults to *_moves.parquet or *.json depending on --format)")
    parser.add_argument("--player-index", type=int, default=0, help="For --format json: which seat to record the world/outcome from (default 0)")
    parser.add_argument("--k", type=int, default=2, help="World prefix length (default 2, matching the reference notebook's own definition)")
    parser.add_argument("--n-shop-types", type=int, default=DEFAULT_N_SHOP_TYPES)
    parser.add_argument("--submission", type=Path, default=None, help="Optional: cross-reference against this submission's route_db")
    parser.add_argument("--out", type=Path, default=Path("field_worlds_report.html"))
    args = parser.parse_args()

    scan = scan_directory(args.data_dir, args.pattern or "*_moves.parquet") if args.format == "parquet" \
        else scan_json_directory(args.data_dir, args.pattern or "*.json", player_index=args.player_index)
    if not scan.used_files:
        parser.error(
            f"no usable files found in {args.data_dir} matching {args.pattern} "
            f"(found {len(scan.skipped_files)} file(s), all skipped -- see reasons above)"
        )
    print(f"scanned {len(scan.used_files)} file(s), {len(scan.skipped_files)} skipped, "
          f"{len(scan.records)} episodes pooled", file=sys.stderr)

    table = world_frequency_table(scan.records, k=args.k)

    coverage = None
    if args.submission:
        print(f"cross-referencing against submission at {args.submission}...", file=sys.stderr)
        route_db, normalize = load_route_db(str(args.submission))
        coverage = cross_reference(table, route_db, normalize)

    payload = build_report_payload(scan, table, args.k, args.n_shop_types, coverage)
    out_path = write_report(payload, args.out)

    print(f"\nWrote report: {out_path.resolve()}")
    print(f"Open it in a browser: file://{out_path.resolve()}")


if __name__ == "__main__":
    main()
