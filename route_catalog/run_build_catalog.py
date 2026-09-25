#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
CLI: build a route catalog from a directory of raw Kaggle episode replay
JSON files.

Usage:
    python run_build_catalog.py --replays-dir /path/to/replays --catalog-out catalog.json --report-out catalog_report.html
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from route_catalog.catalog import build_catalog, save_catalog
from route_catalog.report import build_catalog_report_payload, write_catalog_report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--replays-dir", type=Path, required=True)
    parser.add_argument("--pattern", default="*.json")
    parser.add_argument("--catalog-out", type=Path, default=Path("catalog.json"))
    parser.add_argument("--report-out", type=Path, default=Path("catalog_report.html"))
    args = parser.parse_args()

    paths = sorted(args.replays_dir.glob(args.pattern))
    if not paths:
        parser.error(f"no files found in {args.replays_dir} matching {args.pattern}")

    print(f"building catalog from {len(paths)} file(s)...", file=sys.stderr)
    catalog = build_catalog(paths)

    print(f"scanned {catalog.n_replays_scanned}, skipped {len(catalog.skipped_replays)}, "
          f"{catalog.n_candidates_seen} candidates, {len(catalog.entries)} distinct exact routes", file=sys.stderr)
    if catalog.skipped_replays:
        for path, reason in catalog.skipped_replays[:10]:
            print(f"  skipped {path}: {reason}", file=sys.stderr)

    save_catalog(catalog, args.catalog_out)
    print(f"Wrote catalog: {args.catalog_out.resolve()}")

    payload = build_catalog_report_payload(catalog)
    report_path = write_catalog_report(payload, args.report_out)
    print(f"Wrote report: {report_path.resolve()}")
    print(f"Open it in a browser: file://{report_path.resolve()}")


if __name__ == "__main__":
    main()
