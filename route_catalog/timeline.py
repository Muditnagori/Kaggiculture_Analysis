# SPDX-License-Identifier: Apache-2.0
"""
Given a built catalog and a specific replay, finds exactly which catalog
route (if any) was in use at every point in the match, and for how long.

At each shop-boundary start, prefers the LONGEST catalog match available
(a match to a 3-window route is more informative than the same steps also
trivially matching a 1-window route, since the shorter one is a subset).
Falls back through: exact match -> loose-family match -> "no known route"
(gap) so gaps are visible, not silently skipped.
"""
from __future__ import annotations

from dataclasses import dataclass

from .catalog import Catalog
from .segment import WINDOW_LEN, extract_candidates


@dataclass
class TimelineEvent:
    start_step: int
    end_step: int
    length_windows: int
    situation: tuple
    match_type: str          # "exact" | "loose" | "none"
    action_hash: str | None
    times_seen_elsewhere: int  # how many OTHER occurrences this route/family has in the catalog


def build_timeline(catalog: Catalog, replay_path: str, player_index: int) -> list[TimelineEvent]:
    candidates = extract_candidates(replay_path, player_index)

    # Group candidates by start_step so we can try longest-first at each start.
    by_start: dict[int, list] = {}
    for c in candidates:
        by_start.setdefault(c.start_step, []).append(c)
    for start in by_start:
        by_start[start].sort(key=lambda c: -c.length_windows)  # longest first

    n_windows = max(by_start) // WINDOW_LEN + 1 if by_start else 0
    events: list[TimelineEvent] = []
    covered_until_window = 0  # next window index not yet covered by an event

    starts_sorted = sorted(by_start.keys())
    for start_step in starts_sorted:
        start_window = start_step // WINDOW_LEN
        if start_window < covered_until_window:
            continue  # already covered by a longer route claimed from an earlier start

        chosen = None
        match_type = "none"
        times_seen_elsewhere = 0

        for c in by_start[start_step]:
            exact = catalog.lookup_exact(c.situation, c.length_windows, c.action_hash)
            if exact is None:
                continue
            # Require recurrence BEYOND this exact occurrence itself -- every
            # candidate trivially has an entry for itself in any catalog that
            # includes this replay's own data, so "an entry exists" is not
            # evidence of a real recurring route; "times_seen_elsewhere > 0"
            # is what actually means "this route was also used somewhere else".
            elsewhere = sum(
                1 for o in exact.occurrences
                if not (o.episode_id == c.episode_id and o.player_index == c.player_index and o.start_step == c.start_step)
            )
            if elsewhere > 0:
                chosen = c
                match_type = "exact"
                times_seen_elsewhere = elsewhere
                break

        if chosen is None:
            for c in by_start[start_step]:
                family = catalog.lookup_loose(c.situation, c.length_windows, c.loose_hash)
                if family is None:
                    continue
                # Same self-exclusion logic as above, approximated at the
                # family level: family.total_times_seen counts every
                # candidate ever folded in, including this one, so
                # subtracting 1 estimates "seen elsewhere". This is an
                # approximation (families don't track per-occurrence
                # identity the way strict entries do) but is consistent
                # with how times_seen_elsewhere is reported everywhere else.
                elsewhere = family.total_times_seen - 1
                if elsewhere > 0:
                    chosen = c
                    match_type = "loose"
                    times_seen_elsewhere = elsewhere
                    break

        if chosen is None:
            # No known route at any length starting here -- record a
            # single-window gap and move on one window at a time.
            shortest = min(by_start[start_step], key=lambda c: c.length_windows)
            events.append(
                TimelineEvent(
                    start_step=start_step,
                    end_step=start_step + WINDOW_LEN,
                    length_windows=1,
                    situation=shortest.situation,
                    match_type="none",
                    action_hash=None,
                    times_seen_elsewhere=0,
                )
            )
            covered_until_window = start_window + 1
            continue

        events.append(
            TimelineEvent(
                start_step=chosen.start_step,
                end_step=chosen.start_step + chosen.length_steps,
                length_windows=chosen.length_windows,
                situation=chosen.situation,
                match_type=match_type,
                action_hash=chosen.action_hash,
                times_seen_elsewhere=times_seen_elsewhere,
            )
        )
        covered_until_window = start_window + chosen.length_windows

    return events
