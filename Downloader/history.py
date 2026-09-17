"""Persistent Download History & Match Registry for K2 Kaggle Replay Downloader.

Stores metadata for every match replay ever downloaded (Episode ID, RNG Seed,
Teams, Rewards, Player Name/Rank, Download Timestamp) using a lightweight SQLite database.
- Records all match replays downloaded across players.
- Prevents re-downloading matches even if raw JSON files are deleted or moved.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "download_history.db"


def get_db_connection(db_path: Optional[Path | str] = None) -> sqlite3.Connection:
    """Establishes an optimized SQLite connection with WAL journal mode."""
    target = Path(db_path) if db_path else DEFAULT_DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=30.0)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[Path | str] = None) -> None:
    """Creates tables and performance indexes if they do not exist."""
    with get_db_connection(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS download_history (
                episode_id INTEGER PRIMARY KEY,
                seed INTEGER,
                competition TEXT DEFAULT 'kaggriculture',
                player_rank INTEGER,
                player_name TEXT,
                team_0 TEXT,
                team_1 TEXT,
                reward_0 REAL,
                reward_1 REAL,
                winner TEXT,
                total_steps INTEGER DEFAULT 720,
                download_timestamp TEXT,
                raw_file_exists INTEGER DEFAULT 1
            );
        """)
        # Auto-migration: Drop legacy is_formatted column and index if still present
        cursor = conn.execute("PRAGMA table_info(download_history);")
        columns = [row[1] for row in cursor.fetchall()]
        if "is_formatted" in columns:
            conn.execute("DROP INDEX IF EXISTS idx_history_formatted;")
            conn.execute("ALTER TABLE download_history DROP COLUMN is_formatted;")

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_seed
            ON download_history(seed);
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_player
            ON download_history(player_name);
        """)
        conn.commit()


def get_recorded_episode_ids(db_path: Optional[Path | str] = None) -> set[int]:
    """Fetches all recorded episode IDs as a fast Python set for O(1) in-memory lookups."""
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.execute("SELECT episode_id FROM download_history")
        return {row[0] for row in cursor.fetchall()}


def is_episode_recorded(episode_id: int | str, db_path: Optional[Path | str] = None) -> bool:
    """Checks whether a specific episode ID has ever been recorded."""
    try:
        eid = int(episode_id)
    except (ValueError, TypeError):
        return False
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        cursor = conn.execute("SELECT 1 FROM download_history WHERE episode_id = ? LIMIT 1", (eid,))
        return cursor.fetchone() is not None


def record_episode(
    episode_id: int | str,
    seed: Optional[int] = None,
    competition: str = "kaggriculture",
    player_rank: Optional[int] = None,
    player_name: Optional[str] = None,
    team_0: Optional[str] = None,
    team_1: Optional[str] = None,
    reward_0: Optional[float] = None,
    reward_1: Optional[float] = None,
    winner: Optional[str] = None,
    total_steps: int = 720,
    download_timestamp: Optional[str] = None,
    raw_file_exists: int = 1,
    db_path: Optional[Path | str] = None,
) -> bool:
    """Records a single downloaded match replay into the persistent history database."""
    try:
        eid = int(episode_id)
    except (ValueError, TypeError):
        return False

    ts = download_timestamp or dt.datetime.now(dt.timezone.utc).isoformat()
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        conn.execute("""
            INSERT INTO download_history (
                episode_id, seed, competition, player_rank, player_name,
                team_0, team_1, reward_0, reward_1, winner, total_steps,
                download_timestamp, raw_file_exists
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(episode_id) DO UPDATE SET
                seed = COALESCE(excluded.seed, download_history.seed),
                player_rank = COALESCE(excluded.player_rank, download_history.player_rank),
                player_name = COALESCE(excluded.player_name, download_history.player_name),
                team_0 = COALESCE(excluded.team_0, download_history.team_0),
                team_1 = COALESCE(excluded.team_1, download_history.team_1),
                reward_0 = COALESCE(excluded.reward_0, download_history.reward_0),
                reward_1 = COALESCE(excluded.reward_1, download_history.reward_1),
                winner = COALESCE(excluded.winner, download_history.winner),
                raw_file_exists = excluded.raw_file_exists;
        """, (
            eid, seed, competition, player_rank, player_name,
            team_0, team_1, reward_0, reward_1, winner, total_steps,
            ts, raw_file_exists
        ))
        conn.commit()
    return True


def record_episodes_batch(records: list[dict[str, Any]], db_path: Optional[Path | str] = None) -> int:
    """High-performance bulk insert of downloaded match records."""
    if not records:
        return 0

    init_db(db_path)
    now_str = dt.datetime.now(dt.timezone.utc).isoformat()
    rows = []
    for r in records:
        try:
            eid = int(r["episode_id"])
        except (KeyError, ValueError, TypeError):
            continue
        rows.append((
            eid,
            r.get("seed"),
            r.get("competition", "kaggriculture"),
            r.get("player_rank"),
            r.get("player_name"),
            r.get("team_0"),
            r.get("team_1"),
            r.get("reward_0"),
            r.get("reward_1"),
            r.get("winner"),
            r.get("total_steps", 720),
            r.get("download_timestamp", now_str),
            r.get("raw_file_exists", 1),
        ))

    if not rows:
        return 0

    with get_db_connection(db_path) as conn:
        conn.executemany("""
            INSERT INTO download_history (
                episode_id, seed, competition, player_rank, player_name,
                team_0, team_1, reward_0, reward_1, winner, total_steps,
                download_timestamp, raw_file_exists
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(episode_id) DO UPDATE SET
                seed = COALESCE(excluded.seed, download_history.seed),
                player_rank = COALESCE(excluded.player_rank, download_history.player_rank),
                player_name = COALESCE(excluded.player_name, download_history.player_name),
                team_0 = COALESCE(excluded.team_0, download_history.team_0),
                team_1 = COALESCE(excluded.team_1, download_history.team_1),
                reward_0 = COALESCE(excluded.reward_0, download_history.reward_0),
                reward_1 = COALESCE(excluded.reward_1, download_history.reward_1),
                winner = COALESCE(excluded.winner, download_history.winner),
                raw_file_exists = excluded.raw_file_exists;
        """, rows)
        conn.commit()
    return len(rows)


def mark_as_formatted(episode_ids: list[int | str], db_path: Optional[Path | str] = None) -> int:
    """Deprecated: Downloader database no longer tracks formatting or parquet conversion."""
    return 0


def mark_files_deleted(episode_ids: list[int | str], db_path: Optional[Path | str] = None) -> int:
    """Marks raw JSON replay files as deleted from disk to reflect reclaimed space."""
    if not episode_ids:
        return 0
    clean_ids = []
    for x in episode_ids:
        try:
            clean_ids.append(int(x))
        except (ValueError, TypeError):
            pass
    if not clean_ids:
        return 0

    init_db(db_path)
    with get_db_connection(db_path) as conn:
        batch_size = 900
        total_updated = 0
        for i in range(0, len(clean_ids), batch_size):
            chunk = clean_ids[i:i + batch_size]
            placeholders = ",".join("?" for _ in chunk)
            cur = conn.execute(
                f"UPDATE download_history SET raw_file_exists = 0 WHERE episode_id IN ({placeholders})",
                chunk,
            )
            total_updated += cur.rowcount
        conn.commit()
    return total_updated


def get_history_stats(db_path: Optional[Path | str] = None) -> dict[str, Any]:
    """Returns summary statistics of all downloaded match records."""
    init_db(db_path)
    with get_db_connection(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM download_history").fetchone()[0]
        unique_seeds = conn.execute("SELECT COUNT(DISTINCT seed) FROM download_history WHERE seed IS NOT NULL").fetchone()[0]
        raw_existing = conn.execute("SELECT COUNT(*) FROM download_history WHERE raw_file_exists = 1").fetchone()[0]
        players = conn.execute("SELECT COUNT(DISTINCT player_name) FROM download_history WHERE player_name IS NOT NULL").fetchone()[0]
        target_db = Path(db_path) if db_path else DEFAULT_DB_PATH
        db_size_kb = target_db.stat().st_size / 1024.0 if target_db.exists() else 0.0

    return {
        "total_episodes": total,
        "unique_seeds": unique_seeds,
        "raw_files_existing": raw_existing,
        "unique_players": players,
        "db_size_kb": db_size_kb,
        "db_path": str(target_db),
    }


def extract_header_fast(file_path: Path) -> dict[str, Any]:
    """Fast extractor: reads the first 32KB of a JSON replay to extract header info and seed."""
    meta: dict[str, Any] = {
        "episode_id": file_path.stem,
        "seed": None,
        "team_0": None,
        "team_1": None,
        "reward_0": None,
        "reward_1": None,
        "winner": None,
        "total_steps": 720,
    }

    parent_name = file_path.parent.name
    m_p = re.match(r"^(\d+)[_\s-]+(.*)$", parent_name)
    if m_p:
        meta["player_rank"] = int(m_p.group(1))
        meta["player_name"] = m_p.group(2).strip()
    else:
        meta["player_rank"] = None
        meta["player_name"] = parent_name

    try:
        with open(file_path, "rb") as f:
            chunk = f.read(32768).decode("utf-8", errors="replace")

        m_eid = re.search(r'"EpisodeId"\s*:\s*(\d+)', chunk)
        if m_eid:
            meta["episode_id"] = int(m_eid.group(1))
        elif file_path.stem.isdigit():
            meta["episode_id"] = int(file_path.stem)

        m_seed = re.search(r'"seed"\s*:\s*(\d+)', chunk)
        if m_seed:
            meta["seed"] = int(m_seed.group(1))

        m_teams = re.search(r'"TeamNames"\s*:\s*\[([^\]]+)\]', chunk)
        if m_teams:
            try:
                teams_list = json.loads("[" + m_teams.group(1) + "]")
                if len(teams_list) > 0:
                    meta["team_0"] = str(teams_list[0])
                if len(teams_list) > 1:
                    meta["team_1"] = str(teams_list[1])
            except Exception:
                pass

        m_rew = re.search(r'"rewards"\s*:\s*\[([^\]]+)\]', chunk)
        if m_rew:
            try:
                rew_list = json.loads("[" + m_rew.group(1) + "]")
                if len(rew_list) > 0 and rew_list[0] is not None:
                    meta["reward_0"] = float(rew_list[0])
                if len(rew_list) > 1 and rew_list[1] is not None:
                    meta["reward_1"] = float(rew_list[1])
            except Exception:
                pass

        r0 = meta.get("reward_0")
        r1 = meta.get("reward_1")
        if r0 is not None and r1 is not None:
            if r0 > r1:
                meta["winner"] = meta.get("team_0") or "team_0"
            elif r1 > r0:
                meta["winner"] = meta.get("team_1") or "team_1"
            else:
                meta["winner"] = "TIE"

        return meta
    except Exception:
        pass

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
        info = data.get("info", {})
        meta["episode_id"] = int(info.get("EpisodeId") or file_path.stem)
        meta["seed"] = int(info.get("seed") or data.get("configuration", {}).get("seed") or 0)
        teams = info.get("TeamNames", [])
        if len(teams) > 0:
            meta["team_0"] = str(teams[0])
        if len(teams) > 1:
            meta["team_1"] = str(teams[1])
        rewards = data.get("rewards", [0, 0])
        if len(rewards) > 0 and rewards[0] is not None:
            meta["reward_0"] = float(rewards[0])
        if len(rewards) > 1 and rewards[1] is not None:
            meta["reward_1"] = float(rewards[1])
        return meta
    except Exception:
        return meta


def migrate_existing_downloads(
    downloads_dir: Path | str,
    db_path: Optional[Path | str] = None,
    batch_size: int = 500,
) -> dict[str, Any]:
    """One-time migration: Scans existing JSON files and builds the persistent history database."""
    base = Path(downloads_dir).resolve()
    if not base.exists():
        return {"scanned": 0, "indexed": 0, "error": f"Directory not found: {base}"}

    init_db(db_path)
    existing_ids = get_recorded_episode_ids(db_path)

    all_files = [p for p in base.rglob("*.json") if p.is_file() and p.stem.isdigit()]
    total_found = len(all_files)
    to_index = [p for p in all_files if int(p.stem) not in existing_ids]

    if not to_index:
        stats = get_history_stats(db_path)
        stats["scanned"] = total_found
        stats["newly_indexed"] = 0
        return stats

    print(f"Indexing {len(to_index):,} replay files into download history registry...")
    t0 = time.time()
    batch = []
    indexed_count = 0

    for idx, fpath in enumerate(to_index, start=1):
        meta = extract_header_fast(fpath)
        if meta and meta.get("episode_id"):
            batch.append(meta)

        if len(batch) >= batch_size:
            indexed_count += record_episodes_batch(batch, db_path)
            batch.clear()
            pct = (idx / len(to_index)) * 100
            print(f"\r  Progress: [{idx:,}/{len(to_index):,}] ({pct:5.1f}%)", end="", flush=True)

    if batch:
        indexed_count += record_episodes_batch(batch, db_path)

    elapsed = time.time() - t0
    print(f"\n[OK] Indexed {indexed_count:,} replays in {elapsed:.2f}s.")
    stats = get_history_stats(db_path)
    stats["scanned"] = total_found
    stats["newly_indexed"] = indexed_count
    return stats


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Download History & Match Registry Manager")
    parser.add_argument("--migrate", default=None, help="Migrate/index existing downloads folder")
    parser.add_argument("--stats", action="store_true", help="Print registry statistics")
    args = parser.parse_args()

    if args.migrate:
        res = migrate_existing_downloads(args.migrate)
        print("Migration Result:", res)
    elif args.stats:
        stats = get_history_stats()
        print("\n" + "=" * 50)
        print("          DOWNLOAD HISTORY REGISTRY STATS")
        print("=" * 50)
        print(f" Total Matches Recorded : {stats['total_episodes']:,}")
        print(f" Unique RNG Seeds       : {stats['unique_seeds']:,}")
        print(f" Raw JSONs on Disk      : {stats['raw_files_existing']:,}")
        print(f" Unique Players Tracked : {stats['unique_players']:,}")
        print(f" Database Size          : {stats['db_size_kb']:.2f} KB")
        print("=" * 50 + "\n")
    else:
        init_db()
        print("Database initialized at:", DEFAULT_DB_PATH)
