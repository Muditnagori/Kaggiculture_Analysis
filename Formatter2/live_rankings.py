"""Live Leaderboard Ranking Sync for F2.

Connects to Kaggle via Downloader/auth.json and client.py, fetches real-time
competition leaderboard rankings for Kaggriculture, and updates rankings.parquet
with zero rank overlaps.
"""

from __future__ import annotations

import argparse
import datetime as dt
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
    rankings_path: Path,
    comp_id: int = 147734,
    verbose: bool = True,
) -> Dict[str, Any]:
    """
    Updates rankings.parquet with live leaderboard rankings from Kaggle.
    Preserves existing ranks as fallback if team is not found or API fails.
    """
    rankings_path = Path(rankings_path)
    if not rankings_path.exists():
        if verbose:
            print(f"[!] Warning: {rankings_path} does not exist. Skipping live rank sync.", flush=True)
        return {"success": False, "error": f"File not found: {rankings_path}"}

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
            print(f"   [!] Error writing live rankings: {e}", flush=True)
        return {"success": False, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="Fetch and update live Kaggle leaderboard rankings into rankings.parquet")
    parser.add_argument(
        "--rankings",
        "-r",
        default="formatted_data/rankings.parquet",
        help="Path to rankings.parquet",
    )
    args = parser.parse_args()

    rk_path = Path(args.rankings)
    res = update_rankings_parquet(rk_path, verbose=True)

    if res.get("success") and rk_path.exists():
        t = pq.read_table(str(rk_path))
        print("\n" + "=" * 65)
        print("                 CURRENT LIVE LEADERBOARD (F2)")
        print("=" * 65)
        for i, r in enumerate(t.to_pylist()[:15], 1):
            rk = f"#{r.get('rank'):02d}" if r.get("rank") is not None else " N/A"
            print(f"  {i:>2}. Rank {rk}: {safe_console_str(r.get('player_name')):<26} ({r.get('match_count', 0):>3} matches)")
        print("=" * 65)


if __name__ == "__main__":
    main()
