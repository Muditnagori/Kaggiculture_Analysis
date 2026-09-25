# SPDX-License-Identifier: Apache-2.0
"""
Extracts one "world" record per match directly from raw Kaggle episode
replay JSON files -- the same format field_ledger and field_skeleton use.

Verified against real data: obs['town']['unlocked_shops'] grows in unlock
order across the match and, by the final step, contains the complete
shop-unlock sequence -- content-identical to the Parquet mining pipeline's
`match_shop_sequence` column (checked directly: both list PET_CAFE,
BRUNCH_SPOT, SMOOTHIE_SHOP, ... in the same order). This means Field
Worlds can run straight off raw replay JSON, without needing the
Parquet-extraction step first.
"""
from __future__ import annotations

import json
from pathlib import Path

from .parquet_scan import EpisodeWorld, ScanResult


def _load_replay(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def scan_replay_json(path: str | Path, player_index: int = 0) -> EpisodeWorld | None:
    """Extract one EpisodeWorld from a single raw replay JSON. Returns None
    if the match never got far enough to unlock any shops (nothing to
    record) rather than raising -- a short/aborted match isn't a schema
    error, just an empty world."""
    path = Path(path)
    replay = _load_replay(path)
    steps = replay["steps"]
    n_players = len(steps[0])
    opp_index = 1 - player_index if n_players == 2 else (player_index + 1) % n_players

    final_shops = steps[-1][player_index]["observation"]["town"]["unlocked_shops"]
    if not final_shops:
        return None

    names = replay.get("info", {}).get("TeamNames", [])
    player_reward = float(steps[-1][player_index].get("reward") or 0)
    opp_reward = float(steps[-1][opp_index].get("reward") or 0)
    if player_reward > opp_reward:
        result = "WIN"
    elif player_reward < opp_reward:
        result = "LOSS"
    else:
        result = "TIE"

    return EpisodeWorld(
        episode_id=str(replay.get("id", path.stem)),
        source_file=path.name,
        player_name=names[player_index] if player_index < len(names) else f"player_{player_index}",
        opponent_name=names[opp_index] if opp_index < len(names) else f"player_{opp_index}",
        shop_sequence=tuple(final_shops),
        player_final_reward=player_reward,
        opponent_final_reward=opp_reward,
        match_result=result,
    )


def scan_json_directory(dir_path: str | Path, pattern: str = "*.json", player_index: int = 0) -> ScanResult:
    """Scan every replay JSON matching `pattern` in dir_path. Files that
    fail to parse or aren't episode replays are skipped and reported, same
    contract as parquet_scan.scan_directory."""
    dir_path = Path(dir_path)
    used_files: list[str] = []
    skipped_files: list[tuple[str, str]] = []
    records: list[EpisodeWorld] = []
    seen_episode_ids: set[str] = set()

    for path in sorted(dir_path.glob(pattern)):
        try:
            record = scan_replay_json(path, player_index=player_index)
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            skipped_files.append((str(path), f"not a recognizable episode replay: {e}"))
            continue
        except Exception as e:
            skipped_files.append((str(path), f"unexpected error: {e}"))
            continue

        if record is None:
            skipped_files.append((str(path), "match never unlocked any shops (too short / aborted)"))
            continue
        if record.episode_id in seen_episode_ids:
            continue  # same match seen twice under a different filename
        seen_episode_ids.add(record.episode_id)
        records.append(record)
        used_files.append(str(path))

    return ScanResult(records=records, used_files=used_files, skipped_files=skipped_files)
