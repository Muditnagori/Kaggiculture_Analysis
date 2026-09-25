#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Basic invariant checks for the Field Ledger pipeline. Not a full unit-test
suite -- just enough to catch obvious breakage (crashes, negative cash,
out-of-range tile/hand counts) when you point this at new replay files.

Usage:
    python tests/test_reconstruct.py path/to/replay1.json [path/to/replay2.json ...]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from field_ledger.reconstruct import load_replay, reconstruct_match


def check_replay(path: str) -> None:
    replay = load_replay(path)
    match = reconstruct_match(replay)
    n_steps = len(replay["steps"])

    for player, records in match.items():
        assert len(records) == n_steps, f"{path}: player {player} record count mismatch"
        for i, r in enumerate(records):
            assert r.step == i, f"{path}: step index mismatch at {i}"
            assert r.cash >= -0.01, f"{path}: negative cash at step {i}, player {player}"
            assert r.total_unlocked_tiles >= r.empty_tiles >= 0, f"{path}: tile count invalid at step {i}"
            assert r.total_hands >= r.idle_hands >= 0, f"{path}: hand count invalid at step {i}"
            assert r.projected_value == r.cash + r.shed_value + r.growing_value, (
                f"{path}: projected_value doesn't match its components at step {i}"
            )

    print(f"OK  {path}  ({n_steps} steps x {len(match)} players)")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    for path in sys.argv[1:]:
        check_replay(path)
    print("All checks passed.")


if __name__ == "__main__":
    main()
