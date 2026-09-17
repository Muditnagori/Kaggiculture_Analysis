"""High-performance Replay download manager for K2.

Organizes matches into per-player folders and creates structured ZIP archives.
Ensures ZIP archives ONLY contain the specific files requested in the current run.
Checks and skips/reuses existing matches to avoid redundant network downloads.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import os
import re
import shutil
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure K2 directory is in sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from auth import ensure_auth
    from client import (
        KaggleSession,
        determine_outcome,
    )
    from history import (
        get_recorded_episode_ids,
        record_episode,
        extract_header_fast,
        get_history_stats,
    )
except ImportError:
    from .auth import ensure_auth
    from .client import (
        KaggleSession,
        determine_outcome,
    )
    from .history import (
        get_recorded_episode_ids,
        record_episode,
        extract_header_fast,
        get_history_stats,
    )

logger = logging.getLogger("K2.downloader")


def sanitize_filename(name: str) -> str:
    """Sanitize strings for cross-platform directory and file names."""
    clean = re.sub(r'[\\/*?:"<>|\r\n\t]', "_", name).strip()
    return clean[:80] if clean else "unknown"


def make_zip_archive(files_to_pack: list[tuple[Path, str]], zip_output_path: Path) -> Path:
    """Create a ZIP archive containing ONLY the files requested for this specific download."""
    zip_output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path, arcname in files_to_pack:
            if file_path.exists() and file_path.stat().st_size > 50:
                zf.write(file_path, arcname=arcname)
    return zip_output_path


def is_valid_json_replay(p: Path) -> bool:
    """Fast check verifying file exists, has plausible size, and ends cleanly with '}'."""
    try:
        if not p.is_file():
            return False
        sz = p.stat().st_size
        if sz < 500:
            return False
        with open(p, "rb") as f:
            f.seek(max(0, sz - 4096))
            tail = f.read().rstrip()
            return tail.endswith(b"}")
    except Exception:
        return False


def find_all_cached_matches(search_dirs: list[Path]) -> dict[str, Path]:
    """Scans directories for existing valid downloaded matches mapped by episode_id."""
    cached: dict[str, Path] = {}
    for base in search_dirs:
        if not base.exists():
            continue
        try:
            for p in base.rglob("*.json"):
                if p.is_file() and p.stem.isdigit():
                    if is_valid_json_replay(p):
                        cached.setdefault(p.stem, p)
                    else:
                        # Automatically clean corrupted / truncated files
                        try:
                            p.unlink()
                        except Exception:
                            pass
        except Exception:
            pass
    return cached

def sync_existing_player_folders(comp_dir: Path, leaderboard: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Checks all existing downloaded player directories in comp_dir.
    If a player's rank on the leaderboard has changed, renames their folder to reflect their current rank,
    preventing multiple players from having the same rank prefix.
    """
    if not comp_dir.exists():
        return []

    # Build multi-key lookup from leaderboard
    lookup: dict[str, dict[str, Any]] = {}
    for team in leaderboard:
        t_name = str(team.get("team_name", "")).strip()
        t_id = str(team.get("team_id", "")).strip()
        clean_name = sanitize_filename(t_name).lower()
        if clean_name:
            lookup[clean_name] = team
        if t_name:
            lookup[t_name.lower()] = team
        if t_id:
            lookup[t_id] = team

    existing_dirs = [d for d in comp_dir.iterdir() if d.is_dir()]
    if not existing_dirs:
        return []

    print("-" * 65, flush=True)
    print("Checking ranks of existing downloaded players against live leaderboard...", flush=True)

    changes: list[dict[str, Any]] = []
    for d in existing_dirs:
        m = re.match(r"^(\d+)[_\s-]+(.*)$", d.name)
        if m:
            old_rank = int(m.group(1))
            player_suffix = m.group(2).strip()
        else:
            old_rank = None
            player_suffix = d.name.strip()

        key = player_suffix.lower()
        matched = lookup.get(key)
        if not matched:
            # Try partial search
            for k, team in lookup.items():
                if key in k or k in key:
                    matched = team
                    break

        if not matched:
            # Check inside replay json if available to extract team name
            for jf in d.glob("*.json"):
                try:
                    import json
                    with open(jf, "r", encoding="utf-8", errors="replace") as f:
                        data = json.load(f)
                    teams = data.get("info", {}).get("TeamNames", [])
                    for t in teams:
                        t_clean = sanitize_filename(str(t)).lower()
                        if t_clean in lookup:
                            matched = lookup[t_clean]
                            break
                    if matched:
                        break
                except Exception:
                    pass

        if not matched:
            print(f" • '{d.name}': Not found on active leaderboard (database preserved)", flush=True)
            continue

        new_rank = int(matched["rank"])
        sanitized_name = sanitize_filename(matched["team_name"] or f"Player_{matched['team_id']}")
        target_name = f"{new_rank:02d}_{sanitized_name}"

        if target_name == d.name:
            print(f" • '{d.name}': Rank #{new_rank:02d} unchanged", flush=True)
            continue

        # Rank has changed within Top 20!
        target_dir = comp_dir / target_name
        print(f" 🔄 Rank changed: '{d.name}' -> '{target_name}' (Rank #{old_rank if old_rank is not None else '?'} -> #{new_rank})", flush=True)

        try:
            if target_dir.exists() and target_dir != d:
                # Merge contents from old folder into target folder
                for item in d.iterdir():
                    dest = target_dir / item.name
                    if not dest.exists():
                        shutil.move(str(item), str(dest))
                    else:
                        try:
                            item.unlink()
                        except Exception:
                            pass
                try:
                    d.rmdir()
                except Exception:
                    pass
            else:
                d.rename(target_dir)

            # Also rename output file in Formatter if present ('do not delete anything')
            formatter_base = BASE_DIR.parent / "Formatter" / "outputs"
            if formatter_base.exists():
                for pf in formatter_base.glob("*_moves.parquet"):
                    m_pf = re.match(r"^(\d+)[_\s-]+(.*)_moves\.parquet$", pf.name)
                    if m_pf and m_pf.group(2).strip().lower() == player_suffix.lower():
                        new_pf_name = f"{new_rank:02d}_{m_pf.group(2).strip()}_moves.parquet"
                        new_pf = formatter_base / new_pf_name
                        if pf.name != new_pf_name and not new_pf.exists():
                            try:
                                pf.rename(new_pf)
                                print(f"    Renamed Formatter output file: '{pf.name}' ➔ '{new_pf_name}'", flush=True)
                            except Exception:
                                pass

            changes.append({
                "old_name": d.name,
                "new_name": target_name,
                "old_rank": old_rank,
                "new_rank": new_rank,
                "player": matched["team_name"],
            })
        except Exception as e:
            print(f" [!] Warning: Could not rename '{d.name}' to '{target_name}': {e}", flush=True)

    if changes:
        print(f"Updated {len(changes)} player folder(s) to match current leaderboard ranks.", flush=True)
    print("-" * 65, flush=True)
    return changes


class K2Downloader:

    """K2 Top Player Winning Match Replay Downloader with duplicate caching."""

    def __init__(
        self,
        competition: str | int = "kaggriculture",
        top_percentage: float | None = 10.0,
        num_players: int | None = 5,
        player_name: str | None = None,
        matches_per_player: int | str | None = -1,
        outcome_filter: str = "all",
        batch_size: int = 5,
        output_dir: Path | str = "downloads",
        auto_zip: bool = True,
        force: bool = False,
    ):
        self.competition_input = competition
        self.top_percentage = top_percentage
        self.num_players = num_players
        self.player_name = player_name
        self.matches_per_player = matches_per_player
        self.outcome_filter = outcome_filter
        self.batch_size = max(1, int(batch_size or 5))
        self.output_base = Path(output_dir).resolve()
        self.auto_zip = auto_zip
        self.force = force

    async def run(self) -> dict[str, Any]:
        """Execute replay fetch and download pipeline."""
        return self.run_sync()

    def run_sync(self) -> dict[str, Any]:
        """Synchronous execution of download pipeline with direct HTTP streaming."""
        auth_file = ensure_auth()
        session = KaggleSession(auth_file)

        is_all_matches = (
            self.matches_per_player in ("all", "ALL", None, -1)
            or (isinstance(self.matches_per_player, (int, float)) and self.matches_per_player <= 0)
        )
        matches_label = "ALL" if is_all_matches else str(self.matches_per_player)

        print("\n" + "=" * 65, flush=True)
        print("   K2 REPLAY DOWNLOADER — MATCH REPLAYS", flush=True)
        print("=" * 65, flush=True)
        print(f"Target Competition : {self.competition_input}", flush=True)
        if self.player_name is not None:
            print(f"Player Target      : Specific player '{self.player_name}'", flush=True)
        elif self.num_players is not None:
            print(f"Player Target      : Top {self.num_players} players (explicit count)", flush=True)
        else:
            print(f"Player Target      : Top {self.top_percentage}% of leaderboard", flush=True)
        print(f"Matches Per Player : {matches_label} (Outcome filter: {self.outcome_filter})", flush=True)
        print(f"Output Directory   : {self.output_base}", flush=True)
        print("=" * 65 + "\n", flush=True)

        # 1. Resolve competition
        comp_id, comp_slug = session.resolve_competition_id(self.competition_input)

        # 2. Fetch Leaderboard
        print(f"Fetching leaderboard for '{comp_slug}' (ID: {comp_id})...", flush=True)
        leaderboard = session.fetch_leaderboard(comp_id)
        if not leaderboard:
            print("Error: Could not retrieve leaderboard.", flush=True)
            return {"success": False, "error": "Leaderboard empty"}

        total_teams = len(leaderboard)

        # Determine players subset
        if self.player_name is not None:
            query = self.player_name.strip().lower()
            matched = None
            for p in leaderboard:
                p_name = (p.get("team_name") or "").strip().lower()
                p_id = str(p.get("team_id") or "").strip().lower()
                if query == p_name or query == p_id:
                    matched = p
                    break
            if not matched:
                for p in leaderboard:
                    p_name = (p.get("team_name") or "").strip().lower()
                    if query in p_name:
                        matched = p
                        break
            if not matched:
                print(f"Error: Player/Team '{self.player_name}' not found on leaderboard for '{comp_slug}'.", flush=True)
                return {"success": False, "error": f"Player '{self.player_name}' not found"}
            top_players = [matched]
            print(f"Total Teams on Leaderboard : {total_teams}", flush=True)
            print(f"Found Target Player        : Rank #{matched['rank']} - {matched['team_name']} (ID: {matched['team_id']})", flush=True)
        elif self.num_players is not None:
            cutoff_rank = min(total_teams, max(1, self.num_players))
            top_players = leaderboard[:cutoff_rank]
            print(f"Total Teams on Leaderboard : {total_teams}", flush=True)
            print(f"Selected Top Players       : {len(top_players)} players (ranks 1 to {cutoff_rank})", flush=True)
        else:
            pct = self.top_percentage if self.top_percentage is not None else 10.0
            cutoff_rank = max(1, math.ceil(total_teams * (pct / 100.0)))
            top_players = [p for p in leaderboard if p["rank"] <= cutoff_rank]
            print(f"Total Teams on Leaderboard : {total_teams}", flush=True)
            print(f"Top {pct:.0f}% Cutoff Rank      : #{cutoff_rank}", flush=True)
            print(f"Selected Top Players       : {len(top_players)} players", flush=True)

        print("-" * 65, flush=True)

        comp_dir = self.output_base / comp_slug
        comp_dir.mkdir(parents=True, exist_ok=True)

        # Sync existing player folder ranks with live leaderboard
        sync_existing_player_folders(comp_dir, leaderboard)

        # Index all existing files across output directory and sibling Formatter inputs
        search_paths = [
            self.output_base,
            BASE_DIR.parent / "Formatter" / "inputs",
            BASE_DIR.parent / "Formatter" / "input",
        ]
        cached_matches = find_all_cached_matches(search_paths)
        print(f"Existing cached matches found locally: {len(cached_matches)} files", flush=True)

        history_recorded_ids: set[int] = set()
        if not self.force:
            try:
                history_recorded_ids = get_recorded_episode_ids()
                h_stats = get_history_stats()
                print(f"Persistent History Registry       : {h_stats['total_episodes']:,} matches recorded", flush=True)
            except Exception as e:
                logger.warning("Could not read history database: %s", e)
        else:
            print("Force mode: Bypassing persistent history registry (will re-download all).", flush=True)

        # 3. Process each top player & collect files
        total_matches_target = 0
        already_downloaded_count = 0
        downloaded_count = 0
        failed_count = 0
        requested_files_for_zip: list[tuple[Path, str]] = []

        print(f"\nProcessing {len(top_players)} players ({matches_label} {self.outcome_filter} matches each)...\n", flush=True)
        for idx, player in enumerate(top_players, start=1):
            rank = player["rank"]
            name = sanitize_filename(player["team_name"] or f"Player_{player['team_id']}")
            folder_name = f"{rank:02d}_{name}"
            player_dir = comp_dir / folder_name
            player_dir.mkdir(parents=True, exist_ok=True)

            sub_id = player.get("best_submission_id")
            if not sub_id:
                print(f" [{idx:02d}/{len(top_players):02d}] Rank #{rank:02d} {name[:24]:<24} : No submission ID found", flush=True)
                continue

            # Fetch episodes for this player
            episodes = session.list_episodes(sub_id)
            time.sleep(0.3)  # Safe pacing between players

            # Filter by outcome
            if self.outcome_filter == "all":
                matched_eps = list(episodes)
            else:
                matched_eps = [
                    ep for ep in episodes
                    if determine_outcome(ep, sub_id) == self.outcome_filter
                ]

            matched_eps.sort(key=lambda ep: int(ep.get("id") or 0), reverse=True)

            if is_all_matches:
                chosen = matched_eps
            else:
                chosen = matched_eps[:int(self.matches_per_player)]
                if not chosen and self.outcome_filter != "all":
                    sorted_all = sorted(episodes, key=lambda ep: int(ep.get("id") or 0), reverse=True)
                    chosen = sorted_all[:int(self.matches_per_player)]

            match_ids = [str(ep["id"]) for ep in chosen]
            total_matches_target += len(match_ids)

            # Separate matches: already exists locally vs need download
            to_download: list[str] = []
            cached_for_player = 0
            new_for_player = 0

            history_for_player = 0
            for eid in match_ids:
                target_file = player_dir / f"{eid}.json"
                arcname = f"{folder_name}/{eid}.json"
                eid_int = int(eid) if eid.isdigit() else None

                # Check 1: File already exists and is valid in this player's folder
                if is_valid_json_replay(target_file):
                    already_downloaded_count += 1
                    cached_for_player += 1
                    cached_matches.setdefault(eid, target_file)
                    requested_files_for_zip.append((target_file, arcname))
                # Check 2: File exists in another player's folder or cache and is valid
                elif eid in cached_matches and is_valid_json_replay(cached_matches[eid]):
                    src_file = cached_matches[eid]
                    try:
                        shutil.copy2(src_file, target_file)
                        already_downloaded_count += 1
                        cached_for_player += 1
                        cached_matches[eid] = target_file
                        requested_files_for_zip.append((target_file, arcname))
                    except Exception:
                        to_download.append(eid)
                # Check 3: Recorded in persistent history registry (previously downloaded, raw JSON may be purged to save disk)
                elif not self.force and eid_int is not None and eid_int in history_recorded_ids:
                    already_downloaded_count += 1
                    history_for_player += 1
                else:
                    if target_file.exists():
                        try:
                            target_file.unlink()
                        except Exception:
                            pass
                    to_download.append(eid)

            # Concurrent download for remaining matches
            if to_download:
                def _download_task(eid_str: str) -> tuple[str, bool]:
                    t_file = player_dir / f"{eid_str}.json"
                    ok = session.download_replay_to_file(eid_str, t_file)
                    return eid_str, ok

                workers = min(self.batch_size, len(to_download))
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = [executor.submit(_download_task, eid) for eid in to_download]
                    for fut in as_completed(futures):
                        eid_str, success = fut.result()
                        t_file = player_dir / f"{eid_str}.json"
                        arcname = f"{folder_name}/{eid_str}.json"
                        if success and is_valid_json_replay(t_file):
                            downloaded_count += 1
                            new_for_player += 1
                            cached_matches[eid_str] = t_file
                            requested_files_for_zip.append((t_file, arcname))
                            try:
                                h_meta = extract_header_fast(t_file)
                                if h_meta and h_meta.get("episode_id"):
                                    record_episode(
                                        episode_id=h_meta["episode_id"],
                                        seed=h_meta.get("seed"),
                                        competition=comp_slug,
                                        player_rank=rank,
                                        player_name=name,
                                        team_0=h_meta.get("team_0"),
                                        team_1=h_meta.get("team_1"),
                                        reward_0=h_meta.get("reward_0"),
                                        reward_1=h_meta.get("reward_1"),
                                        winner=h_meta.get("winner"),
                                        raw_file_exists=1,
                                    )
                                    if eid_str.isdigit():
                                        history_recorded_ids.add(int(eid_str))
                            except Exception:
                                pass
                        else:
                            failed_count += 1

            summary_parts = []
            if history_for_player > 0:
                summary_parts.append(f"{history_for_player} in history")
            if cached_for_player > 0:
                summary_parts.append(f"{cached_for_player} local cache")
            if new_for_player > 0:
                summary_parts.append(f"{new_for_player} newly downloaded")
            if not summary_parts and match_ids:
                summary_parts.append("0 downloaded (failed)")
            elif not match_ids:
                summary_parts.append("0 matches found on Kaggle")

            print(
                f" [{idx:02d}/{len(top_players):02d}] Rank #{rank:02d} {name[:24]:<24} : "
                f"{len(match_ids)} matches ({', '.join(summary_parts)})",
                flush=True,
            )

        # 4. Create the ZIP Archive containing ONLY the files requested in this run
        zip_path = None
        if self.auto_zip and requested_files_for_zip:
            if self.player_name:
                zip_file = self.output_base / f"{comp_slug}_{sanitize_filename(self.player_name)}_replays.zip"
            else:
                zip_file = self.output_base / f"{comp_slug}_top_replays.zip"
            print(f"\nPackaging requested {len(requested_files_for_zip)} replays into ZIP archive: {zip_file.name}...", flush=True)
            make_zip_archive(requested_files_for_zip, zip_file)
            zip_path = str(zip_file)
            print(f"ZIP Archive created at: {zip_file}", flush=True)

        print("\n" + "=" * 65, flush=True)
        print("   DOWNLOAD COMPLETE — SUMMARY", flush=True)
        print("=" * 65, flush=True)
        print(f"Total Top Players Processed : {len(top_players)}")
        print(f"Target Matches Total        : {total_matches_target} files")
        print(f"Newly Downloaded            : {downloaded_count}")
        print(f"Already Downloaded (Cached) : {already_downloaded_count}")
        print(f"Failed / Unavailable        : {failed_count}")
        print(f"Replay Folder Location      : {comp_dir}")
        if zip_path:
            print(f"ZIP File Location           : {zip_path} (Contains exactly {len(requested_files_for_zip)} requested files)")
        print("=" * 65 + "\n", flush=True)

        return {
            "success": True,
            "competition": comp_slug,
            "top_players_count": len(top_players),
            "total_matches": total_matches_target,
            "new_downloaded": downloaded_count,
            "cached": already_downloaded_count,
            "folder": str(comp_dir),
            "zip_file": zip_path,
        }
