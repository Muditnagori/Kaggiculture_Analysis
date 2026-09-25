# SPDX-License-Identifier: Apache-2.0
"""
Extracts candidate routes from a single player's match, at EVERY possible
length -- not just a fixed 72-step window. A route can be as short as one
shop-boundary window or span the entire 720-step match; this module
generates every (start, length) combination anchored at shop-unlock
boundaries (the only points where "situation" meaningfully changes) so
none of that range is assumed away.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .fingerprint import canonical_action_string, hash_strings, significant_action_string

WINDOW_LEN = 72          # steps per shop-unlock window (3 days x 24 steps)
MAX_WINDOWS = 10         # 720 steps / 72 = 10 windows in a full match


@dataclass
class RouteCandidate:
    episode_id: str
    player_index: int
    start_step: int
    length_windows: int
    length_steps: int
    situation: tuple           # shops unlocked at start_step
    action_hash: str           # strict hash -- exact-duplicate detection
    loose_hash: str            # coarse hash -- near-duplicate clustering
    actions: list               # the actual action sequence (kept for reuse/inspection)
    player_final_reward: float
    opponent_final_reward: float


def _load_replay(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def extract_candidates(replay_path: str | Path, player_index: int) -> list[RouteCandidate]:
    replay = _load_replay(replay_path)
    steps = replay["steps"]
    n_steps = len(steps)
    episode_id = str(replay.get("id", Path(replay_path).stem))

    # Precompute per-step strings ONCE, then slice -- avoids re-serializing
    # the same step's action dict for every candidate window that includes it.
    canonical_per_step = []
    loose_per_step = []
    for step in steps:
        action = step[player_index]["action"]
        canonical_per_step.append(canonical_action_string(action))
        loose_per_step.append(significant_action_string(action))

    player_final_reward = float(steps[-1][player_index].get("reward") or 0)
    n_players = len(steps[0])
    opp_index = 1 - player_index if n_players == 2 else (player_index + 1) % n_players
    opponent_final_reward = float(steps[-1][opp_index].get("reward") or 0)

    candidates: list[RouteCandidate] = []
    n_windows_available = n_steps // WINDOW_LEN

    for start_window in range(n_windows_available):
        start_step = start_window * WINDOW_LEN
        situation = tuple(steps[start_step][player_index]["observation"]["town"]["unlocked_shops"])

        max_length = n_windows_available - start_window
        for length_windows in range(1, max_length + 1):
            end_step = start_step + length_windows * WINDOW_LEN
            canon_slice = canonical_per_step[start_step:end_step]
            loose_slice = loose_per_step[start_step:end_step]

            candidates.append(
                RouteCandidate(
                    episode_id=episode_id,
                    player_index=player_index,
                    start_step=start_step,
                    length_windows=length_windows,
                    length_steps=end_step - start_step,
                    situation=situation,
                    action_hash=hash_strings(canon_slice),
                    loose_hash=hash_strings(loose_slice),
                    actions=[step[player_index]["action"] for step in steps[start_step:end_step]],
                    player_final_reward=player_final_reward,
                    opponent_final_reward=opponent_final_reward,
                )
            )

    return candidates
