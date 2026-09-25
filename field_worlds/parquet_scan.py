# SPDX-License-Identifier: Apache-2.0
"""
Extracts one "world" record per match from the enriched moves-Parquet
schema (the one that carries `match_shop_sequence`, as opposed to the
plain schema found in some player files -- see field_ledger's README for
the schema mismatch this project already ran into).

match_shop_sequence is a pre-determined RNG draw for the whole match, set
before play begins and constant across every row of that episode (verified
directly against the data: it's identical at step 0 and step 719) -- so
this needs only ONE row per episode, not the full step-by-step log.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

REQUIRED_COLUMNS = {
    "episode_id", "match_shop_sequence", "player_name", "opponent_name",
    "player_final_reward", "opponent_final_reward", "match_result", "source_file",
}


@dataclass(frozen=True)
class EpisodeWorld:
    episode_id: str
    source_file: str
    player_name: str
    opponent_name: str
    shop_sequence: tuple[str, ...]
    player_final_reward: float
    opponent_final_reward: float
    match_result: str


class SchemaError(ValueError):
    """Raised when a Parquet file doesn't have the columns Field Worlds needs."""


def _parse_sequence(raw: str) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split("->") if part.strip())


def scan_enriched_parquet(path: str | Path) -> list[EpisodeWorld]:
    """Read one enriched moves-Parquet file and return one EpisodeWorld per
    episode. Raises SchemaError (not a silent empty result) if the file is
    missing the columns this needs -- e.g. it's the plain schema variant."""
    df = pd.read_parquet(path, columns=None)
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise SchemaError(f"{path}: missing required columns {sorted(missing)} -- not the enriched schema")

    one_per_episode = df.drop_duplicates(subset="episode_id", keep="first")

    records = []
    for row in one_per_episode.itertuples(index=False):
        records.append(
            EpisodeWorld(
                episode_id=str(row.episode_id),
                source_file=str(row.source_file),
                player_name=str(row.player_name),
                opponent_name=str(row.opponent_name),
                shop_sequence=_parse_sequence(row.match_shop_sequence),
                player_final_reward=float(row.player_final_reward),
                opponent_final_reward=float(row.opponent_final_reward),
                match_result=str(row.match_result),
            )
        )
    return records


@dataclass
class ScanResult:
    records: list[EpisodeWorld]
    used_files: list[str]
    skipped_files: list[tuple[str, str]]  # (path, reason)


def scan_directory(dir_path: str | Path, pattern: str = "*_moves.parquet") -> ScanResult:
    """Scan every Parquet file matching `pattern` in dir_path. Files without
    the enriched schema are skipped, not silently dropped -- reported back
    so you know exactly how much of your dataset this could actually use."""
    dir_path = Path(dir_path)
    used_files: list[str] = []
    skipped_files: list[tuple[str, str]] = []
    all_records: list[EpisodeWorld] = []

    # Deduplicate episode_ids seen ACROSS files: the same match can appear
    # in more than one player's per-player file (once per participant), and
    # counting it twice would double the apparent world frequency.
    seen_episode_ids: set[str] = set()

    for path in sorted(dir_path.glob(pattern)):
        try:
            records = scan_enriched_parquet(path)
        except SchemaError as e:
            skipped_files.append((str(path), str(e)))
            continue
        except Exception as e:  # keep scanning even if one file is corrupt
            skipped_files.append((str(path), f"unexpected error: {e}"))
            continue

        new_records = [r for r in records if r.episode_id not in seen_episode_ids]
        seen_episode_ids.update(r.episode_id for r in new_records)
        all_records.extend(new_records)
        used_files.append(str(path))

    return ScanResult(records=all_records, used_files=used_files, skipped_files=skipped_files)
