"""Export Top N Player Moves to Single Parquet Files (from existing formatted_data).

This utility reads ALREADY FORMATTED datasets in F2/formatted_data/
(rankings.parquet, episodes.parquet, and steps.parquet) and creates 1 Parquet file
per player containing all moves across all matches for that specific player.

No raw JSON files are parsed or re-formatted.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
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


def sanitize_filename(name: str) -> str:
    """Sanitize player name to create safe filename on Windows."""
    # Replace invalid filename characters with underscore
    clean = re.sub(r'[\\/*?:"<>| \t\n\r]+', '_', name)
    # Remove emoji / non-ascii for file path compatibility
    clean = re.sub(r'[^\w\-.]', '', clean)
    clean = clean.strip('_')
    return clean or "unknown_player"


OUTPUT_SCHEMA = pa.schema([
    ("episode_id", pa.int64()),
    ("step", pa.int32()),
    ("day", pa.int32()),
    ("hour", pa.int32()),
    ("player_rank", pa.int32()),
    ("player_name", pa.string()),
    ("player_index", pa.int32()),
    ("action", pa.string()),
    ("reward", pa.float64()),
    ("opponent_name", pa.string()),
    ("player_final_reward", pa.float64()),
    ("opponent_final_reward", pa.float64()),
    ("match_result", pa.string()),
    ("source_file", pa.string()),
])


def get_top_players(rankings_path: Path, top_n: int) -> List[Dict[str, Any]]:
    """Loads rankings.parquet and returns the top N players by rank."""
    if not rankings_path.exists():
        raise FileNotFoundError(f"Rankings file not found: {rankings_path}")

    table = pq.read_table(str(rankings_path))
    rows = table.to_pylist()

    # Sort by rank ascending (None ranks to end), then match_count desc
    rows.sort(key=lambda r: (
        r.get("rank") if r.get("rank") is not None else 999999,
        -(r.get("match_count") or 0),
        r.get("player_name", "")
    ))

    return rows[:top_n]


def load_episodes_metadata(episodes_path: Path) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, List[Tuple[int, int]]]]:
    """
    Loads episodes.parquet and builds:
      1. ep_info: episode_id -> {player_0_name, player_1_name, r0, r1, status0, status1}
      2. player_episodes: player_name -> list of (episode_id, player_idx)
    """
    if not episodes_path.exists():
        raise FileNotFoundError(f"Episodes file not found: {episodes_path}")

    table = pq.read_table(str(episodes_path), columns=[
        "episode_id", "player_0_name", "player_1_name",
        "player_0_reward", "player_1_reward",
        "player_0_status", "player_1_status",
    ])

    ep_info: Dict[int, Dict[str, Any]] = {}
    player_episodes: Dict[str, List[Tuple[int, int]]] = defaultdict(list)

    for row in table.to_pylist():
        eid = row.get("episode_id")
        if not eid:
            continue
        p0 = row.get("player_0_name") or ""
        p1 = row.get("player_1_name") or ""
        r0 = float(row.get("player_0_reward") or 0.0)
        r1 = float(row.get("player_1_reward") or 0.0)

        ep_info[eid] = {
            "p0": p0,
            "p1": p1,
            "r0": r0,
            "r1": r1,
        }

        if p0:
            player_episodes[p0].append((eid, 0))
        if p1:
            player_episodes[p1].append((eid, 1))

    return ep_info, player_episodes


def extract_player_moves(
    steps_path: Path,
    player_name: str,
    player_rank: Optional[int],
    episodes_list: List[Tuple[int, int]],
    ep_info: Dict[int, Dict[str, Any]],
    output_file: Path,
) -> Dict[str, Any]:
    """
    Extracts all moves for a given player across all their matches using PyArrow dataset filtering.
    Writes a single consolidated Parquet file for this player.
    """
    t0 = time.time()
    if not episodes_list:
        return {"rows": 0, "file_size_mb": 0.0, "time_s": 0.0, "unique_matches": 0}

    # Separate episode IDs by player index (0 vs 1)
    p0_eids = [eid for eid, idx in episodes_list if idx == 0]
    p1_eids = [eid for eid, idx in episodes_list if idx == 1]
    unique_eids = set(p0_eids).union(set(p1_eids))

    # Build PyArrow compute filter
    dataset = ds.dataset(str(steps_path), format="parquet")
    filt_expr = None

    if p0_eids and p1_eids:
        filt_p0 = pc.is_in(ds.field("episode_id"), pa.array(p0_eids, type=pa.int64())) & (ds.field("player") == 0)
        filt_p1 = pc.is_in(ds.field("episode_id"), pa.array(p1_eids, type=pa.int64())) & (ds.field("player") == 1)
        filt_expr = filt_p0 | filt_p1
    elif p0_eids:
        filt_expr = pc.is_in(ds.field("episode_id"), pa.array(p0_eids, type=pa.int64())) & (ds.field("player") == 0)
    elif p1_eids:
        filt_expr = pc.is_in(ds.field("episode_id"), pa.array(p1_eids, type=pa.int64())) & (ds.field("player") == 1)

    steps_table = dataset.to_table(filter=filt_expr)
    num_rows = steps_table.num_rows

    if num_rows == 0:
        return {"rows": 0, "file_size_mb": 0.0, "time_s": time.time() - t0, "unique_matches": len(unique_eids)}

    # Convert columns efficiently to add player context
    ep_col = steps_table.column("episode_id").to_pylist()
    player_idx_col = steps_table.column("player").to_pylist()

    # Precompute match result & opponent per row
    ranks = [player_rank] * num_rows
    pnames = [player_name] * num_rows
    opponents: List[str] = []
    p_rewards: List[float] = []
    opp_rewards: List[float] = []
    results: List[str] = []

    for eid, pidx in zip(ep_col, player_idx_col):
        meta = ep_info.get(eid)
        if meta:
            if pidx == 0:
                opp = meta["p1"]
                my_r = meta["r0"]
                opp_r = meta["r1"]
            else:
                opp = meta["p0"]
                my_r = meta["r1"]
                opp_r = meta["r0"]

            if my_r > opp_r:
                res = "WIN"
            elif my_r < opp_r:
                res = "LOSS"
            else:
                res = "TIE"
        else:
            opp = "Unknown"
            my_r = 0.0
            opp_r = 0.0
            res = "UNKNOWN"

        opponents.append(opp)
        p_rewards.append(my_r)
        opp_rewards.append(opp_r)
        results.append(res)

    # Build final Arrow Table matching schema
    final_arrays = [
        steps_table.column("episode_id"),
        steps_table.column("step"),
        steps_table.column("day"),
        steps_table.column("hour"),
        pa.array(ranks, type=pa.int32()),
        pa.array(pnames, type=pa.string()),
        steps_table.column("player"),  # player_index
        steps_table.column("action"),
        steps_table.column("reward"),
        pa.array(opponents, type=pa.string()),
        pa.array(p_rewards, type=pa.float64()),
        pa.array(opp_rewards, type=pa.float64()),
        pa.array(results, type=pa.string()),
        steps_table.column("source_file"),
    ]

    out_table = pa.Table.from_arrays(final_arrays, schema=OUTPUT_SCHEMA)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(out_table, str(output_file), compression="snappy")

    file_size_mb = output_file.stat().st_size / (1024 * 1024)
    elapsed = time.time() - t0

    return {
        "rows": num_rows,
        "file_size_mb": file_size_mb,
        "time_s": elapsed,
        "unique_matches": len(unique_eids),
    }


def export_top_players(
    formatted_data_dir: Path,
    output_dir: Path,
    top_n: int = 3,
    sync_live: bool = True,
) -> None:
    """Main coordinator function to export moves for top N players."""
    rankings_path = formatted_data_dir / "rankings.parquet"
    episodes_path = formatted_data_dir / "episodes.parquet"
    steps_path = formatted_data_dir / "steps.parquet"

    print("=" * 70, flush=True)
    print("      EXPORT TOP PLAYERS MOVES TO PARQUET (FROM FORMATTED DATA)", flush=True)
    print("=" * 70, flush=True)
    print(f"Data Source   : {safe_console_str(formatted_data_dir.resolve())}", flush=True)
    print(f"Target Output : {safe_console_str(output_dir.resolve())}", flush=True)
    print(f"Top Players   : Top {top_n} players by rank", flush=True)
    print("-" * 70, flush=True)

    # 1. Validate required files exist
    for p in [rankings_path, episodes_path, steps_path]:
        if not p.exists():
            print(f"[!] Error: Required file missing: {p}", flush=True)
            print("    Please run the main formatter first to create formatted_data/.", flush=True)
            sys.exit(1)

    # 1.5. Automatically sync live leaderboard ranks (from Kaggle API if credentials exist)
    if sync_live:
        try:
            from live_rankings import update_rankings_parquet
            update_rankings_parquet(rankings_path, verbose=True)
        except Exception as e:
            print(f"   [!] Live ranking sync skipped: {e}", flush=True)

    # 2. Get top N players
    top_players = get_top_players(rankings_path, top_n)
    if not top_players:
        print("[!] No players found in rankings.parquet.", flush=True)
        return

    print(f"Found {len(top_players)} top players to export:\n", flush=True)
    for i, p in enumerate(top_players, 1):
        rk_val = f"#{p.get('rank'):02d}" if p.get('rank') is not None else " N/A"
        print(f"  {i}. Rank {rk_val}: {safe_console_str(p['player_name']):<25} ({p.get('match_count', 0)} matches)", flush=True)
    print("-" * 70, flush=True)

    # 3. Load episode match index
    print("Indexing matches from episodes.parquet...", flush=True)
    t_idx_start = time.time()
    ep_info, player_episodes = load_episodes_metadata(episodes_path)
    print(f"   Indexed {len(ep_info):,} matches across all players in {time.time() - t_idx_start:.2f}s.\n", flush=True)

    # 4. Process each player
    output_dir.mkdir(parents=True, exist_ok=True)
    total_start = time.time()
    exported_count = 0

    for i, p in enumerate(top_players, 1):
        p_name = p["player_name"]
        p_rank = p.get("rank")
        ep_list = player_episodes.get(p_name, [])

        safe_name = sanitize_filename(p_name)
        rank_prefix = f"{p_rank:02d}" if p_rank is not None else "00"
        out_filename = f"{rank_prefix}_{safe_name}_moves.parquet"
        out_filepath = output_dir / out_filename

        print(f"[{i}/{len(top_players)}] Exporting moves for '{safe_console_str(p_name)}' (Rank #{p_rank})...", flush=True)

        res = extract_player_moves(
            steps_path=steps_path,
            player_name=p_name,
            player_rank=p_rank,
            episodes_list=ep_list,
            ep_info=ep_info,
            output_file=out_filepath,
        )

        exported_count += 1
        print(f"   [OK] Saved: {out_filename}", flush=True)
        print(f"        Rows: {res['rows']:,} moves | Matches: {res['unique_matches']:,} | Size: {res['file_size_mb']:.2f} MB | Time: {res['time_s']:.2f}s\n", flush=True)

    total_time = time.time() - total_start
    print("=" * 70, flush=True)
    print(f"Successfully exported {exported_count} player parquet files in {total_time:.2f}s.", flush=True)
    print(f"Output folder: {safe_console_str(output_dir.resolve())}", flush=True)
    print("=" * 70, flush=True)


def main():
    parser = argparse.ArgumentParser(
        description="Export all moves of top N players to single Parquet files from existing formatted_data."
    )
    parser.add_argument(
        "--top",
        "-n",
        type=int,
        default=None,
        help="Number of top ranked players to export (e.g. 3 for top 3)",
    )
    parser.add_argument(
        "--input-dir",
        "-i",
        default="formatted_data",
        help="Directory containing existing formatted parquet files (default: formatted_data)",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default="player_moves",
        help="Directory to save the single player parquet files (default: player_moves)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Prompt user interactively for the number of top players",
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip auto-syncing live Kaggle leaderboard rankings",
    )

    args = parser.parse_args()

    # Determine input directory
    in_dir = Path(args.input_dir)
    if not in_dir.exists():
        candidates = [
            Path("formatted_data"),
            Path("F2/formatted_data"),
            Path("../formatted_data"),
        ]
        for c in candidates:
            if c.exists():
                in_dir = c
                break

    out_dir = Path(args.output_dir)

    top_count = args.top
    if top_count is None or args.interactive:
        # Prompt interactively
        print("=" * 60)
        print("          EXPORT TOP PLAYERS MOVES PARQUET")
        print("=" * 60)
        user_in = input("Enter number of top players to export [default: 3]: ").strip()
        if not user_in:
            top_count = 3
        else:
            try:
                top_count = int(user_in)
                if top_count <= 0:
                    print("[!] Value must be > 0. Using default of 3.")
                    top_count = 3
            except ValueError:
                print("[!] Invalid number entered. Using default of 3.")
                top_count = 3

    export_top_players(
        formatted_data_dir=in_dir,
        output_dir=out_dir,
        top_n=top_count,
        sync_live=not args.no_sync,
    )


if __name__ == "__main__":
    main()
