#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Basic invariant checks for the route_catalog pipeline.

Usage:
    python tests/test_route_catalog.py --replays-dir /path/to/replays
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from route_catalog.catalog import build_catalog, load_catalog, save_catalog
from route_catalog.segment import WINDOW_LEN, extract_candidates
from route_catalog.timeline import build_timeline


def expected_candidate_count(n_windows: int) -> int:
    # sum_{start=0}^{n-1} (n - start) = n(n+1)/2
    return n_windows * (n_windows + 1) // 2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--replays-dir", required=True)
    args = parser.parse_args()
    paths = sorted(Path(args.replays_dir).glob("*.json"))
    assert paths, f"no replay files found in {args.replays_dir}"

    # --- segment.py: candidate count matches the combinatorial expectation ---
    for path in paths:
        candidates = extract_candidates(path, player_index=0)
        import json
        n_steps = len(json.loads(Path(path).read_text())["steps"])
        n_windows = n_steps // WINDOW_LEN
        expected = expected_candidate_count(n_windows)
        assert len(candidates) == expected, (
            f"{path}: got {len(candidates)} candidates, expected {expected} for {n_windows} windows"
        )
        # every candidate's length_steps must equal length_windows * WINDOW_LEN
        assert all(c.length_steps == c.length_windows * WINDOW_LEN for c in candidates)
    print(f"OK  segment.py: candidate counts match combinatorial expectation across {len(paths)} file(s)")

    # --- catalog.py: candidate total and round-trip serialization ---
    catalog = build_catalog(paths)
    assert not catalog.skipped_replays, f"unexpected skips: {catalog.skipped_replays}"
    total_expected = 0
    for path in paths:
        import json
        n_steps = len(json.loads(Path(path).read_text())["steps"])
        total_expected += 2 * expected_candidate_count(n_steps // WINDOW_LEN)  # x2 players
    assert catalog.n_candidates_seen == total_expected, (
        f"catalog saw {catalog.n_candidates_seen} candidates, expected {total_expected}"
    )
    print(f"OK  catalog.py: {catalog.n_candidates_seen} candidates folded in, matches expectation")

    tmp_path = Path("/tmp/_route_catalog_test.json")
    save_catalog(catalog, tmp_path)
    reloaded = load_catalog(tmp_path)
    assert len(reloaded.entries) == len(catalog.entries), "catalog entries changed across save/load"
    assert len(reloaded.families) == len(catalog.families), "catalog families changed across save/load"
    tmp_path.unlink()
    print("OK  catalog.py: save/load round-trips without loss")

    # --- timeline.py: no gaps, no overlaps, full coverage, no trivial self-match ---
    for path in paths:
        for player in (0, 1):
            events = build_timeline(catalog, str(path), player)
            covered = sum(e.end_step - e.start_step for e in events)
            assert covered == 720, f"{path} player {player}: timeline covers {covered} steps, not 720"
            for i in range(len(events) - 1):
                assert events[i].end_step == events[i + 1].start_step, f"{path} player {player}: gap or overlap between events"
            # the self-match bug this project specifically fixed: a route must
            # never be "chosen" using itself as its own only evidence
            for e in events:
                if e.match_type in ("exact", "loose"):
                    assert e.times_seen_elsewhere > 0, (
                        f"{path} player {player}: a {e.match_type} match was chosen with "
                        f"times_seen_elsewhere=0 -- this is the trivial self-match bug"
                    )
    print(f"OK  timeline.py: contiguous, full coverage, no trivial self-matches across {len(paths)} file(s) x 2 players")

    print("All checks passed.")


if __name__ == "__main__":
    main()
