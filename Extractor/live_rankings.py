"""Live Leaderboard Ranking Sync for Extractor.

Connects to Kaggle via Downloader/auth.json and client.py, fetches real-time
competition leaderboard rankings for Kaggriculture, and maintains overlap-free
player ranks for sequence move extraction.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

# Fix Windows console UTF-8 output
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def safe_console_str(text: Any) -> str:
    """Safely converts text to a string that won't fail on Windows cmd charmaps."""
    s = str(text)
    try:
        return s.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8")
    except Exception:
        return s.encode("ascii", errors="replace").decode("ascii")


def find_auth_file() -> Optional[Path]:
    """Locates auth.json in standard workspace locations."""
    candidates = [
        Path("../Downloader/auth.json"),
        Path("Downloader/auth.json"),
        Path("../../Downloader/auth.json"),
        Path("B:/Replicator/Downloader/auth.json"),
        Path("b:/Replicator/Downloader/auth.json"),
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    return None


def find_rankings_file() -> Optional[Path]:
    """Locates rankings.parquet in standard workspace locations."""
    candidates = [
        Path("../Formatter2/formatted_data/rankings.parquet"),
        Path("Formatter2/formatted_data/rankings.parquet"),
        Path("B:/Replicator/Formatter2/formatted_data/rankings.parquet"),
        Path("../F2/formatted_data/rankings.parquet"),
        Path("F2/formatted_data/rankings.parquet"),
        Path("B:/Replicator/F2/formatted_data/rankings.parquet"),
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    return None


def fetch_live_ranks(
    auth_file: Optional[Path] = None,
    comp_id: int = 147734,
) -> Tuple[Optional[Dict[str, int]], Optional[str]]:
    """
    Fetches the live leaderboard from Kaggle.
    Returns (mapping of lowercase_name -> rank, error_message).
    """
    if auth_file is None:
        auth_file = find_auth_file()

    if not auth_file or not auth_file.exists():
        return None, f"auth.json not found (checked ../Downloader/auth.json)"

    downloader_dir = auth_file.parent
    if str(downloader_dir.resolve()) not in sys.path:
        sys.path.insert(0, str(downloader_dir.resolve()))

    try:
        from client import KaggleSession
        session = KaggleSession(str(auth_file))
        lb = session.fetch_leaderboard(comp_id)

        ranks: Dict[str, int] = {}
        for t in lb:
            name = str(t.get("team_name", "")).strip().lower()
            if name and "rank" in t and t["rank"] is not None:
                ranks[name] = int(t["rank"])

        return ranks, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def update_rankings_parquet(
    rankings_path: Optional[Path] = None,
    comp_id: int = 147734,
    verbose: bool = True,
) -> Dict[str, Any]:
    """
    Updates rankings.parquet with live leaderboard rankings from Kaggle.
    Preserves existing ranks as fallback if team is not found or API fails.
    """
    if rankings_path is None:
        rankings_path = find_rankings_file()

    if not rankings_path or not rankings_path.exists():
        if verbose:
            print(f"[!] Warning: rankings.parquet not found. Skipping live rank sync.", flush=True)
        return {"success": False, "error": "rankings.parquet not found"}

    if verbose:
        print("Checking live Kaggle leaderboard rankings...", flush=True)

    t0 = time.time()
    live_ranks, err = fetch_live_ranks(comp_id=comp_id)

    if live_ranks is None:
        if verbose:
            print(f"   [!] Note: Could not fetch live leaderboard ({err}).", flush=True)
            print("       Continuing with existing offline rankings.", flush=True)
        return {"success": False, "error": err}

    try:
        table = pq.read_table(str(rankings_path))
        rows = table.to_pylist()

        now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
        updated_count = 0
        unmatched_count = 0

        for r in rows:
            p_name = str(r.get("player_name", "")).strip()
            key = p_name.lower()

            if key in live_ranks:
                old_r = r.get("rank")
                new_r = live_ranks[key]
                if old_r != new_r:
                    r["rank"] = new_r
                    updated_count += 1
                r["last_updated"] = now_iso
            else:
                unmatched_count += 1

        # Sort by live rank ascending (nulls last), then match_count descending
        rows.sort(key=lambda r: (
            r.get("rank") if r.get("rank") is not None else 999999,
            -(r.get("match_count") or 0),
            r.get("player_name", "")
        ))

        # Re-write rankings.parquet
        updated_table = pa.Table.from_pylist(rows, schema=table.schema)
        pq.write_table(updated_table, str(rankings_path), compression="snappy")

        elapsed = time.time() - t0
        if verbose:
            print(f"   [OK] Live rankings synced in {elapsed:.2f}s! ({updated_count} ranks updated, {len(rows)} players in table)", flush=True)

        return {
            "success": True,
            "updated_count": updated_count,
            "total_players": len(rows),
            "unmatched_count": unmatched_count,
            "elapsed_s": elapsed,
        }

    except Exception as e:
        if verbose:
            print(f"   [!] Error updating {rankings_path}: {e}", flush=True)
        return {"success": False, "error": str(e)}


def load_live_player_ranks(
    rankings_path: Optional[Path] = None,
    sync_live: bool = True,
    verbose: bool = True,
) -> Dict[str, int]:
    """
    Loads normalized player ranks (lowercase player_name -> unique rank).
    Optionally syncs live Kaggle leaderboard first.
    Guarantees zero overlapping ranks across distinct players.
    """
    if rankings_path is None:
        rankings_path = find_rankings_file()

    if sync_live and rankings_path and rankings_path.exists():
        try:
            update_rankings_parquet(rankings_path=rankings_path, verbose=verbose)
        except Exception as e:
            if verbose:
                print(f"   [!] Live rank sync skipped: {e}", flush=True)

    if not rankings_path or not rankings_path.exists():
        if verbose:
            print(f"   [!] Warning: rankings.parquet not found at {rankings_path}. Using empty live ranks.", flush=True)
        return {}

    try:
        table = pq.read_table(str(rankings_path), columns=["player_name", "rank"])
        ranks: Dict[str, int] = {}
        for row in table.to_pylist():
            name = str(row.get("player_name", "")).strip().lower()
            r = row.get("rank")
            if name and r is not None:
                ranks[name] = int(r)
        return ranks
    except Exception as e:
        if verbose:
            print(f"   [!] Error loading {rankings_path}: {e}", flush=True)
        return {}


def main():
    parser = argparse.ArgumentParser(description="Sync live Kaggle leaderboard rankings for Extractor.")
    parser.add_argument(
        "--rankings-path",
        "-r",
        default=None,
        help="Path to rankings.parquet (default: auto-detected in Formatter2/formatted_data)",
    )
    parser.add_argument(
        "--comp-id",
        type=int,
        default=147734,
        help="Kaggle competition ID (default: 147734)",
    )
    args = parser.parse_args()

    r_path = Path(args.rankings_path) if args.rankings_path else find_rankings_file()
    if not r_path or not r_path.exists():
        print(f"[!] Error: rankings.parquet not found. Checked standard Formatter2 paths.", flush=True)
        sys.exit(1)

    print("=" * 70, flush=True)
    print("      LIVE KAGGLE LEADERBOARD SYNC (EXTRACTOR PIPELINE)", flush=True)
    print("=" * 70, flush=True)
    print(f"Target Rankings File : {safe_console_str(r_path.resolve())}", flush=True)
    print(f"Competition ID       : {args.comp_id}", flush=True)
    print("-" * 70, flush=True)

    res = update_rankings_parquet(rankings_path=r_path, comp_id=args.comp_id, verbose=True)

    if res["success"]:
        # Print top 15 preview
        table = pq.read_table(str(r_path))
        rows = table.to_pylist()[:15]
        print("\nTop 15 Live Leaderboard Standings (Zero Rank Overlap):", flush=True)
        print("-" * 70, flush=True)
        for r in rows:
            rk_str = f"#{r.get('rank'):02d}" if r.get('rank') is not None else " N/A"
            p_name = safe_console_str(r.get("player_name", "Unknown"))
            m_cnt = r.get("match_count", 0)
            print(f"  {rk_str} | {p_name:<30} | {m_cnt:>5} matches", flush=True)
        print("=" * 70, flush=True)


if __name__ == "__main__":
    main()
