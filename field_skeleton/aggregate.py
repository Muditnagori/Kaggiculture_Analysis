# SPDX-License-Identifier: Apache-2.0
"""Aggregates MatchRouteLog objects (one per match) into summary tables:
which worlds (day + shop sequence) trigger which route, how often, and
with what real record -- plus a match-type (exact/fallback/none) breakdown."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .route_log import MatchRouteLog


@dataclass
class WorldStats:
    day: int
    lookup_shops: tuple
    route_id: int | None
    match_type: str
    times_seen: int = 0
    wins: int = 0
    losses: int = 0
    ties_or_unknown: int = 0
    replay_paths: list = field(default_factory=list)


def aggregate(logs: list[MatchRouteLog]) -> dict:
    world_table: dict[tuple, WorldStats] = {}
    match_type_counts: dict[str, int] = defaultdict(int)
    overall_wins = overall_losses = overall_unknown = 0

    for log in logs:
        for ev in log.events:
            key = (ev.day, ev.lookup_shops, ev.route_id)
            if key not in world_table:
                world_table[key] = WorldStats(
                    day=ev.day, lookup_shops=ev.lookup_shops,
                    route_id=ev.route_id, match_type=ev.match_type,
                )
            ws = world_table[key]
            ws.times_seen += 1
            ws.replay_paths.append(log.replay_path)
            if log.won is True:
                ws.wins += 1
            elif log.won is False:
                ws.losses += 1
            else:
                ws.ties_or_unknown += 1
            match_type_counts[ev.match_type] += 1

        if log.won is True:
            overall_wins += 1
        elif log.won is False:
            overall_losses += 1
        else:
            overall_unknown += 1

    return {
        "n_matches": len(logs),
        "overall_wins": overall_wins,
        "overall_losses": overall_losses,
        "overall_unknown": overall_unknown,
        "world_table": sorted(world_table.values(), key=lambda w: (-w.times_seen, w.day)),
        "match_type_counts": dict(match_type_counts),
    }
