# SPDX-License-Identifier: Apache-2.0
"""
Replays a real Kaggle episode JSON through a freshly loaded, instrumented
submission, turn by turn, exactly as the live competition harness would
call it -- so route-selection state builds up naturally rather than being
guessed at a single timestep (see the field_ledger project's README for
why this matters: the saved replay JSON doesn't include a 'step' field,
so it must be supplied from the entry's position in the sequence).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .instrument import RouteEvent, instrument
from .submission_loader import load_submission


@dataclass
class MatchRouteLog:
    replay_path: str
    player_index: int
    team_name: str
    opponent_name: str
    events: list[RouteEvent]
    final_money: float
    final_reward: float
    won: bool | None  # None if it was a tie or reward comparison is ambiguous


def _load_replay(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def reset_route_state(module) -> None:
    """Reset a loaded submission module's route-selection globals to their
    startup values, so it can be reused for a fresh match without a full
    reimport (reimporting re-reads and re-decompresses the whole route_db
    from disk, which gets expensive across many replays)."""
    module._current_shops = None
    module._current_route_id = None
    module._route_start_step = None
    module._current_day = None


def walk_loaded_replay(module, replay_path: str | Path, player_index: int, events: list[RouteEvent]) -> MatchRouteLog:
    """Same as walk_replay, but takes an already-loaded + already-instrumented
    module (see submission_loader.load_submission + instrument.instrument)
    and an events list to append this match's RouteEvents into. Caller is
    responsible for calling reset_route_state(module) and clearing/slicing
    `events` between matches -- see walk_many_replays for the common case."""
    replay = _load_replay(replay_path)
    steps = replay["steps"]
    names = replay.get("info", {}).get("TeamNames", [])
    n_players = len(steps[0])
    opp_index = 1 - player_index if n_players == 2 else (player_index + 1) % n_players

    start = len(events)
    for i, step in enumerate(steps):
        obs = dict(step[player_index]["observation"])
        obs["step"] = i
        module.agent(obs, replay.get("configuration"))
    this_match_events = list(events[start:])

    final_entry = steps[-1][player_index]
    final_money = float(final_entry["observation"]["farms"][player_index].get("money", 0) or 0)
    final_reward = float(final_entry.get("reward") or 0)
    opp_reward = float(steps[-1][opp_index].get("reward") or 0)

    won = None
    if final_reward != opp_reward:
        won = final_reward > opp_reward

    return MatchRouteLog(
        replay_path=str(replay_path),
        player_index=player_index,
        team_name=names[player_index] if player_index < len(names) else f"player_{player_index}",
        opponent_name=names[opp_index] if opp_index < len(names) else f"player_{opp_index}",
        events=this_match_events,
        final_money=final_money,
        final_reward=final_reward,
        won=won,
    )


def walk_many_replays(submission_dir: str | Path, replay_specs: list[tuple[str, int]]) -> list[MatchRouteLog]:
    """Efficient batch path: loads and instruments the submission ONCE,
    then replays each (path, player_index) spec against it, resetting
    route-selection state between matches. Use this instead of calling
    walk_replay() in a loop once you're past a handful of replays."""
    module = load_submission(submission_dir)
    events = instrument(module)

    logs = []
    for path, player_index in replay_specs:
        reset_route_state(module)
        logs.append(walk_loaded_replay(module, path, player_index, events))
    return logs


def walk_replay(submission_dir: str | Path, replay_path: str | Path, player_index: int) -> MatchRouteLog:
    """Run one player's full match through a fresh, instrumented copy of
    the submission and return its route-selection log + outcome.

    A fresh submission module is loaded per call so each match starts from
    clean route-selection state (module globals aren't shared across
    matches)."""
    module = load_submission(submission_dir)
    events = instrument(module)

    replay = _load_replay(replay_path)
    steps = replay["steps"]
    names = replay.get("info", {}).get("TeamNames", [])
    n_players = len(steps[0])
    opp_index = 1 - player_index if n_players == 2 else (player_index + 1) % n_players

    for i, step in enumerate(steps):
        obs = dict(step[player_index]["observation"])
        obs["step"] = i  # supplied live by the real harness; absent from saved replay JSON
        module.agent(obs, replay.get("configuration"))

    final_entry = steps[-1][player_index]
    final_money = float(final_entry["observation"]["farms"][player_index].get("money", 0) or 0)
    final_reward = float(final_entry.get("reward") or 0)
    opp_reward = float(steps[-1][opp_index].get("reward") or 0)

    won = None
    if final_reward != opp_reward:
        won = final_reward > opp_reward

    return MatchRouteLog(
        replay_path=str(replay_path),
        player_index=player_index,
        team_name=names[player_index] if player_index < len(names) else f"player_{player_index}",
        opponent_name=names[opp_index] if opp_index < len(names) else f"player_{opp_index}",
        events=events,
        final_money=final_money,
        final_reward=final_reward,
        won=won,
    )
