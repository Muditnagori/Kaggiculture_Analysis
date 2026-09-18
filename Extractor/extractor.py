"""High-Performance Shop Sequence Move Extractor for Kaggriculture.

Supports TWO input sources:
1. Formatter2 Consolidated Parquet (B:/Replicator/Formatter2/formatted_data):
   - Consumes town.parquet, episodes.parquet, steps.parquet
   - Full match coverage across all players (34,714 unique sequences)
2. Formatter Outputs (B:/Replicator/Formatter/outputs):
   - Consumes shop_unlocked_sequence.parquet and *_moves.parquet
   - Covers 42 top player datasets

Core Requirements:
1. Tracks each particular shop unlocked sequence:
   - (PIZZA) -> 72 moves before next shop unlock
   - (PIZZA, ICE_CREAM) -> 72 moves before next shop unlock
   - ...
   - After 8th shop unlock: all moves before match end
2. Cascading Player Priority Rules (Player 1 > Player 2 > Player 3 > ...):
   - Strict hierarchical priority by rank ascending (Rank 1 > Rank 2 > ...)
   - Higher-ranked player overwrites lower-ranked player
   - Within same rank tier, highest winning reward wins
3. Well-Formatted Console & File Outputs:
   - Beautiful, aligned ASCII tables for all displays (Windows CMD cp1252 safe)
   - Atomically written, zero-corruption Parquet and CSV outputs
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

try:
    from live_rankings import load_live_player_ranks
except ImportError:
    load_live_player_ranks = None

if hasattr(sys.stdout, "reconfigure"):
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

# Canonical Shop Name Simplification Map
SHOP_NAME_MAP = {
    "PIZZA_SHOP": "PIZZA",
    "ICE_CREAM_SHOP": "ICE_CREAM",
    "SMOOTHIE_SHOP": "SMOOTHIE",
    "BAKERY": "BAKERY",
    "PET_CAFE": "PET_CAFE",
    "FARMERS_MARKET": "FARMERS_MARKET",
    "BRUNCH_SPOT": "BRUNCH_SPOT",
    "YARN_STORE": "YARN_STORE",
}

# Move-Only Columns for the Extractor (strictly the actions and moves played)
MOVE_ONLY_COLUMNS = [
    # 1. Sequence & Progress Context
    "sequence_label",             # Shop unlock sequence e.g. '(PIZZA, ICE_CREAM)'
    "sequence_length",            # Length of sequence (1 to 8)
    "step_in_sequence",           # Move number within this 72-move window (0 to 71, or 0 to 143)
    "step",                       # Match step (0 to 719)
    "day",                        # Match day (1 to 30)
    "hour",                       # Match hour (6 to 19)

    # 2. The Moves (Actions executed in this turn)
    "farmer_move",                # Main farmer move: e.g. 'WEST', 'PLANT(MELON)', 'HARVEST'
    "action_farmer_type",         # Atomic farmer action type (e.g. 'WEST', 'PLANT')
    "action_farmer_target",       # Atomic farmer target (e.g. 'MELON', '(r, c)')
    "hands_moves",                # Farmhands moves: e.g. 'WEST; WEST; PLANT(MELON); PASS'
    "action_hands_count",         # Number of active farmhands moving
    "market_moves",               # Market orders: e.g. 'BUY_SEED(MELON,7); BUY_PRODUCT(WHEAT,6)'
    "action_market_orders_count", # Number of market orders executed
    "full_move",                  # Full composite turn action: 'FARMER: ... | HANDS: ... | MARKET: ...'

    # 3. Source & Match Identification
    "player_rank",                # Rank of the player executing the move
    "player_name",                # Name of the player
    "episode_id",                 # Kaggle match ID
    "winner_final_reward",        # Final match score achieved
]


# ==============================================================================
# 1. BEAUTIFUL TABLE FORMATTING UTILITIES (ASCII & Windows CMD cp1252 Safe)
# ==============================================================================

def print_ascii_table(
    headers: list[str],
    rows: list[list[Any]],
    alignments: Optional[list[str]] = None,
    max_col_width: Optional[int] = None,
) -> None:
    """
    Renders and prints a beautifully aligned ASCII table.
    Works natively across all Windows CMD and PowerShell encodings.
    """
    if not headers or not rows:
        return

    # Convert all cells to strings
    str_rows = []
    for r in rows:
        str_r = []
        for val in r:
            s = "" if val is None else str(val)
            if max_col_width and len(s) > max_col_width:
                s = s[: max_col_width - 3] + "..."
            str_r.append(s)
        str_rows.append(str_r)

    num_cols = len(headers)
    widths = [len(str(h)) for h in headers]
    for r in str_rows:
        for i in range(min(num_cols, len(r))):
            widths[i] = max(widths[i], len(r[i]))

    # Separators
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    hdr = "| " + " | ".join(f"{h:^{w}}" for h, w in zip(headers, widths)) + " |"

    print(sep)
    print(hdr)
    print(sep)

    for r in str_rows:
        line_parts = []
        for i in range(num_cols):
            val = r[i] if i < len(r) else ""
            w = widths[i]
            align = alignments[i] if alignments and i < len(alignments) else "<"
            if align == ">":
                line_parts.append(f"{val:>{w}}")
            elif align == "^":
                line_parts.append(f"{val:^{w}}")
            else:
                line_parts.append(f"{val:<{w}}")
        print("| " + " | ".join(line_parts) + " |")

    print(sep)


def safe_atomic_write_parquet(table: pa.Table, target_file: Path) -> None:
    """Safely writes a Parquet table with temporary file and atomic swap."""
    target_file = Path(target_file).resolve()
    target_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file = target_file.parent / f"temp_{target_file.name}"
    backup_file = target_file.parent / f"backup_{target_file.name}"

    if temp_file.exists():
        try:
            temp_file.unlink()
        except Exception:
            pass

    pq.write_table(table, str(temp_file), compression="snappy")

    # Atomic swap
    if backup_file.exists():
        try:
            backup_file.unlink()
        except Exception:
            pass

    if target_file.exists():
        target_file.rename(backup_file)

    try:
        temp_file.rename(target_file)
        if backup_file.exists():
            try:
                backup_file.unlink()
            except Exception:
                pass
    except Exception as e:
        if backup_file.exists() and not target_file.exists():
            backup_file.rename(target_file)
        raise e


# ==============================================================================
# 2. ACTION FORMATTERS & HELPERS
# ==============================================================================

def format_hands_moves(raw_val: Any) -> str:
    """Formats raw hands actions into a clean, human-readable string."""
    if not raw_val or (isinstance(raw_val, float) and pd.isna(raw_val)):
        return "NONE"

    actions_list = raw_val
    if isinstance(raw_val, str):
        val_str = raw_val.strip()
        if not val_str or val_str in ("[]", "None", "NONE", "null"):
            return "NONE"
        try:
            actions_list = json.loads(val_str)
        except Exception:
            try:
                actions_list = ast.literal_eval(val_str)
            except Exception:
                return val_str

    if not isinstance(actions_list, (list, tuple)) or len(actions_list) == 0:
        return "NONE"

    parts = []
    for item in actions_list:
        if isinstance(item, (list, tuple)) and len(item) > 0:
            act_type = str(item[0])
            params = [str(x) for x in item[1:]]
            if params:
                param_str = ",".join(params)
                parts.append(f"{act_type}({param_str})")
            else:
                parts.append(act_type)
        else:
            item_str = str(item).strip()
            if item_str:
                parts.append(item_str)

    return "; ".join(parts) if parts else "NONE"


def format_farmer_move(act_type: Any, act_target: Any) -> str:
    """Formats farmer action type and target into a clean action representation."""
    t = str(act_type).strip() if act_type is not None and not pd.isna(act_type) else "PASS"
    tgt = str(act_target).strip() if act_target is not None and not pd.isna(act_target) else ""
    if tgt and tgt.upper() != "NONE" and tgt != "":
        return f"{t}({tgt})"
    return t


def format_market_orders(raw_val: Any) -> str:
    """Formats market orders into a readable string."""
    if not raw_val or (isinstance(raw_val, float) and pd.isna(raw_val)):
        return "NONE"
    if isinstance(raw_val, str):
        val_str = raw_val.strip()
        if not val_str or val_str in ("[]", "None", "NONE", "null"):
            return "NONE"
        try:
            orders = json.loads(val_str)
        except Exception:
            return val_str
    elif isinstance(raw_val, list):
        orders = raw_val
    else:
        return str(raw_val)

    if not isinstance(orders, list) or len(orders) == 0:
        return "NONE"

    parts = []
    for item in orders:
        if isinstance(item, (list, tuple)) and len(item) > 0:
            m_type = str(item[0])
            params = [str(x) for x in item[1:]]
            parts.append(f"{m_type}({','.join(params)})" if params else m_type)
        else:
            parts.append(str(item))
    return "; ".join(parts) if parts else "NONE"


def format_full_move(farmer_m: str, hands_m: str, market_m: str) -> str:
    """Combines all actions of a single step into one clean composite string."""
    parts = [f"FARMER: {farmer_m}"]
    if hands_m and hands_m not in ("NONE", "[]"):
        parts.append(f"HANDS: {hands_m}")
    if market_m and market_m not in ("NONE", "[]"):
        parts.append(f"MARKET: {market_m}")
    return " | ".join(parts)


def simplify_shop_name(name: str) -> str:
    """Converts internal shop constants to clean user-friendly names."""
    u = str(name).strip().upper()
    return SHOP_NAME_MAP.get(u, u)


def simplify_sequence(seq: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Simplifies an iterable of shop names."""
    return tuple(simplify_shop_name(s) for s in seq)


def format_sequence_label(seq: tuple[str, ...]) -> str:
    """Formats a sequence tuple into a readable string: e.g. (PIZZA, ICE_CREAM)."""
    return "(" + ", ".join(seq) + ")"


def parse_player_rank(folder_name: str) -> int:
    """Extracts integer rank from player directory name (e.g. '01_ymg_aq' -> 1)."""
    m = re.match(r"^(\d+)", folder_name)
    if m:
        return int(m.group(1))
    return 999


# ==============================================================================
# 3. INPUT SOURCE RESOLUTION & DETECTION
# ==============================================================================

def detect_input_source(input_path: str | Path) -> tuple[str, Path]:
    """
    Detects whether the given directory is an F2 formatted directory or Formatter directory.
    Returns: ('F2', path) or ('FORMATTER', path).
    """
    p = Path(input_path).resolve()

    if p.exists() and p.is_dir():
        if (p / "town.parquet").exists() and (p / "episodes.parquet").exists():
            return "F2", p
        if (p / "shop_unlocked_sequence.parquet").exists() or list(p.glob("*_moves.parquet")):
            return "FORMATTER", p

    candidates = [
        p,
        Path("../Formatter2/formatted_data").resolve(),
        Path("Formatter2/formatted_data").resolve(),
        Path("B:/Replicator/Formatter2/formatted_data").resolve(),
        Path("../F2/formatted_data").resolve(),
        Path("F2/formatted_data").resolve(),
        Path("B:/Replicator/F2/formatted_data").resolve(),
        Path("../Formatter/outputs").resolve(),
        Path("Formatter/outputs").resolve(),
        Path("B:/Replicator/Formatter/outputs").resolve(),
    ]

    for c in candidates:
        if c.exists() and c.is_dir():
            if (c / "town.parquet").exists() and (c / "episodes.parquet").exists():
                return "F2", c
            if (c / "shop_unlocked_sequence.parquet").exists() or list(c.glob("*_moves.parquet")):
                return "FORMATTER", c

    if (p / "town.parquet").exists():
        return "F2", p
    return "FORMATTER", p


# ==============================================================================
# 4. SEQUENCE EXTRACTION - F2 PIPELINE (HIGH-PERFORMANCE BATCH STREAMING)
# ==============================================================================

def run_f2_extraction(f2_path: Path, out_path: Path, sync_live: bool = True) -> bool:
    """
    High-performance sequence extraction directly from Formatter2 consolidated Parquet datasets:
    - town.parquet: match shop sequences and unlock steps
    - episodes.parquet: match rewards, winner determination, player priority ranks
    - steps.parquet: per-step actions, rewards, timestamps
    Uses fast PyArrow batch streaming to process matches in ~1-2 minutes.
    """
    t0 = time.time()
    print("\n" + "=" * 80)
    print("      HIGH-PERFORMANCE SEQUENCE EXTRACTION (FORMATTER2 CONSOLIDATED DATASET)")
    print("=" * 80)
    print(f" Input Directory : {f2_path}")
    print(f" Output Directory: {out_path}")
    print("-" * 80)

    # 1. Index Town Sequences
    print("[>] Step 1/4: Indexing shop unlock sequences from town.parquet...", flush=True)
    town_file = f2_path / "town.parquet"
    if not town_file.exists():
        print(f"[!] Error: town.parquet missing in {f2_path}")
        return False

    df_town = pq.read_table(
        str(town_file),
        columns=["episode_id", "step", "shop", "shop_index"]
    ).to_pandas()
    df_town = df_town.sort_values(["episode_id", "step"]).drop_duplicates(subset=["episode_id", "shop_index"])

    # 2. Episode Metadata & Live Player Priorities (Zero Overlap)
    print("[>] Step 2/4: Loading match winner & live rank metadata (zero overlap)...", flush=True)
    live_ranks: dict[str, int] = {}
    if load_live_player_ranks is not None:
        rankings_file = f2_path / "rankings.parquet"
        live_ranks = load_live_player_ranks(rankings_path=rankings_file, sync_live=sync_live, verbose=True)

    ep_file = f2_path / "episodes.parquet"
    if not ep_file.exists():
        print(f"[!] Error: episodes.parquet missing in {f2_path}")
        return False

    df_ep = pq.read_table(
        str(ep_file),
        columns=[
            "episode_id", "source_rank", "source_player_name",
            "player_0_name", "player_1_name", "player_0_reward", "player_1_reward"
        ]
    ).to_pandas()

    ep_meta: dict[int, dict[str, Any]] = {}
    for _, r in df_ep.iterrows():
        e_id = int(r["episode_id"])
        r0 = float(r.get("player_0_reward") or 0.0)
        r1 = float(r.get("player_1_reward") or 0.0)
        w_idx = 0 if r0 >= r1 else 1
        w_name = str(r["player_0_name"] if w_idx == 0 else r["player_1_name"]).strip()
        w_rew = max(r0, r1)

        # Strictly assign winning player's live rank from Kaggle leaderboard (zero overlap)
        rank = live_ranks.get(w_name.lower(), 999)

        ep_meta[e_id] = {"w_idx": w_idx, "rank": rank, "p_name": w_name, "reward": w_rew}

    seq_frequencies: Counter = Counter()
    seq_best: dict[tuple[str, ...], dict[str, Any]] = {}

    for ep_id, group in df_town.groupby("episode_id"):
        ep_int = int(ep_id)
        if ep_int not in ep_meta:
            continue
        meta = ep_meta[ep_int]
        rank = meta["rank"]
        reward = meta["reward"]
        shops = [simplify_shop_name(s) for s in group["shop"]]
        steps = [int(s) for s in group["step"]]
        total_steps = 720

        for i in range(len(steps)):
            sub_seq = tuple(shops[: i + 1])
            seq_frequencies[sub_seq] += 1
            start_step = steps[i]
            end_step = steps[i + 1] if i + 1 < len(steps) else total_steps

            if sub_seq in seq_best:
                cur = seq_best[sub_seq]
                if rank > cur["rank"]:
                    continue
                elif rank == cur["rank"] and reward <= cur["reward"]:
                    continue

            seq_best[sub_seq] = {
                "sub_seq": sub_seq,
                "seq_label": format_sequence_label(sub_seq),
                "seq_len": len(sub_seq),
                "ep_id": ep_int,
                "start_step": start_step,
                "end_step": end_step,
                "w_idx": meta["w_idx"],
                "rank": rank,
                "p_name": meta["p_name"],
                "reward": reward,
            }

    total_matches = df_town["episode_id"].nunique()
    print(f"   [OK] Discovered {len(seq_best):,} unique sequences across {total_matches:,} matches in {time.time()-t0:.2f}s.")

    # Build match step schedules for fast O(1) step routing
    match_schedule: dict[int, dict[int, Tuple[tuple[str, ...], int, dict[str, Any]]]] = defaultdict(dict)
    match_w_idx: dict[int, int] = {}

    for seg in seq_best.values():
        ep_id = seg["ep_id"]
        match_w_idx[ep_id] = seg["w_idx"]
        sub_seq = seg["sub_seq"]
        start_s = seg["start_step"]
        end_s = seg["end_step"]
        for s in range(start_s, end_s):
            match_schedule[ep_id][s] = (sub_seq, s - start_s, seg)

    needed_ep_ids = set(match_schedule.keys())
    print(f"   [OK] Scheduled move slices across {len(needed_ep_ids):,} optimal winning matches.")

    # 3. Stream steps.parquet
    print("\n[>] Step 3/4: Slicing sequence moves from steps.parquet (streaming batches)...", flush=True)
    steps_file = f2_path / "steps.parquet"
    if not steps_file.exists():
        print(f"[!] Error: steps.parquet missing in {f2_path}")
        return False

    pf = pq.ParquetFile(str(steps_file))
    seq_moves_collector: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    t_stream = time.time()
    processed_moves = 0

    for batch in pf.iter_batches(batch_size=131072, columns=["episode_id", "step", "day", "hour", "player", "action", "reward"]):
        b_eps = batch["episode_id"].to_pylist()
        unique_b_eps = set(b_eps)
        active_eps = unique_b_eps.intersection(needed_ep_ids)
        if not active_eps:
            continue

        b_steps = batch["step"].to_pylist()
        b_days = batch["day"].to_pylist()
        b_hours = batch["hour"].to_pylist()
        b_players = batch["player"].to_pylist()
        b_actions = batch["action"].to_pylist()

        for ep, st, dy, hr, pl, act_str in zip(b_eps, b_steps, b_days, b_hours, b_players, b_actions):
            if ep not in active_eps or pl != match_w_idx[ep]:
                continue
            ep_sched = match_schedule[ep]
            if st not in ep_sched:
                continue

            sub_seq, step_in_seq, seg = ep_sched[st]

            # Parse action
            f_type = "PASS"
            f_tgt = ""
            h_list = []
            m_list = []
            if act_str:
                try:
                    act_dict = json.loads(act_str)
                    f_arr = act_dict.get("farmer", ["PASS"])
                    if f_arr:
                        f_type = str(f_arr[0])
                        f_tgt = ", ".join(str(x) for x in f_arr[1:]) if len(f_arr) > 1 else ""
                    h_list = act_dict.get("hands", [])
                    m_list = act_dict.get("market", [])
                except Exception:
                    pass

            f_m = f"{f_type}({f_tgt})" if f_tgt else f_type

            # Farmhands moves
            if h_list:
                h_parts = []
                for it in h_list:
                    if isinstance(it, (list, tuple)) and it:
                        h_parts.append(f"{it[0]}({','.join(str(x) for x in it[1:])})" if len(it) > 1 else str(it[0]))
                    else:
                        h_parts.append(str(it))
                h_m = "; ".join(h_parts) if h_parts else "NONE"
            else:
                h_m = "NONE"

            # Market orders
            if m_list:
                m_parts = []
                for it in m_list:
                    if isinstance(it, (list, tuple)) and it:
                        m_parts.append(f"{it[0]}({','.join(str(x) for x in it[1:])})" if len(it) > 1 else str(it[0]))
                    else:
                        m_parts.append(str(it))
                m_m = "; ".join(m_parts) if m_parts else "NONE"
            else:
                m_m = "NONE"

            comp_parts = [f"FARMER: {f_m}"]
            if h_m != "NONE":
                comp_parts.append(f"HANDS: {h_m}")
            if m_m != "NONE":
                comp_parts.append(f"MARKET: {m_m}")
            comp_m = " | ".join(comp_parts)

            seq_moves_collector[sub_seq].append({
                "sequence_label": seg["seq_label"],
                "sequence_length": seg["seq_len"],
                "step_in_sequence": step_in_seq,
                "step": st,
                "day": dy,
                "hour": hr,
                "farmer_move": f_m,
                "action_farmer_type": f_type,
                "action_farmer_target": f_tgt,
                "hands_moves": h_m,
                "action_hands_count": len(h_list),
                "market_moves": m_m,
                "action_market_orders_count": len(m_list),
                "full_move": comp_m,
                "player_rank": seg["rank"],
                "player_name": seg["p_name"],
                "episode_id": str(ep),
                "winner_final_reward": seg["reward"],
            })
            processed_moves += 1

    print(f"   [OK] Sliced {processed_moves:,} moves for {len(seq_moves_collector):,} sequences in {time.time()-t_stream:.2f}s!")

    # 4. Compile and Save Final Datasets Atomically
    print("\n[>] Step 4/4: Compiling and saving final datasets...", flush=True)
    sorted_seq_keys = sorted(seq_best.keys(), key=lambda k: (len(k), format_sequence_label(k)))

    all_flat_moves: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    for sub_seq in sorted_seq_keys:
        seg = seq_best[sub_seq]
        moves = seq_moves_collector.get(sub_seq, [])
        moves.sort(key=lambda m: m["step_in_sequence"])
        all_flat_moves.extend(moves)

        freq = seq_frequencies.get(sub_seq, 1)
        freq_pct = round((freq / total_matches) * 100, 2) if total_matches > 0 else 0.0

        summary_rows.append({
            "sequence_label": seg["seq_label"],
            "sequence_length": seg["seq_len"],
            "player_rank": seg["rank"],
            "player_name": seg["p_name"],
            "episode_id": str(seg["ep_id"]),
            "moves_count": len(moves),
            "match_reward": seg["reward"],
            "start_step": seg["start_step"],
            "end_step": seg["end_step"],
            "frequency": freq,
            "frequency_pct": freq_pct,
        })

    moves_file = out_path / "sequence_moves.parquet"
    summary_file = out_path / "sequences_summary.parquet"

    df_all_moves = pd.DataFrame(all_flat_moves)
    safe_atomic_write_parquet(pa.Table.from_pandas(df_all_moves, preserve_index=False), moves_file)
    moves_mb = os.path.getsize(moves_file) / (1024 * 1024)

    df_summary = pd.DataFrame(summary_rows)
    safe_atomic_write_parquet(pa.Table.from_pandas(df_summary, preserve_index=False), summary_file)
    summary_mb = os.path.getsize(summary_file) / (1024 * 1024)

    # Output Reporting
    print("\n" + "=" * 80)
    print("                    EXTRACTION COMPLETE & VERIFIED")
    print("=" * 80)
    print(f" * Sequence Moves Dataset    : {moves_file.name}")
    print(f"   -> {len(df_all_moves):,} total moves | {len(df_all_moves.columns)} columns | {moves_mb:.2f} MB")
    print(f" * Sequences Summary Catalog : {summary_file.name}")
    print(f"   -> {len(df_summary):,} unique sequences | {summary_mb:.3f} MB")
    print(f" * Total Execution Time       : {time.time() - t0:.2f} seconds")

    # Table 1: Breakdown by Sequence Length
    print("\n[+] Breakdown by Sequence Length:")
    by_len = df_summary.groupby("sequence_length").size().to_dict()
    tbl_headers = ["Length", "Step Window", "Unique Sequences", "% of Catalog"]
    tbl_rows = []
    for l in sorted(by_len.keys()):
        cnt = by_len[l]
        pct = f"{(cnt / len(df_summary)) * 100:.1f}%"
        win_str = "72 moves" if l < 8 else "All remaining (144)"
        tbl_rows.append([f"Length {l}", win_str, f"{cnt:,}", pct])
    print_ascii_table(tbl_headers, tbl_rows, ["<", "<", ">", ">"])

    # Table 2: Top Source Players
    print("\n[+] Top Discovered Source Players (Priority Tier Order):")
    by_player = df_summary.groupby(["player_rank", "player_name"]).size().to_dict()
    p_headers = ["Rank", "Player Name", "Unique Sequences", "Share %"]
    p_rows = []
    for (r_val, p_val), cnt in sorted(by_player.items())[:15]:
        pct = f"{(cnt / len(df_summary)) * 100:.1f}%"
        rk_str = f"#{r_val:02d}" if r_val < 900 else "Unranked"
        p_rows.append([rk_str, safe_console_str(p_val), f"{cnt:,}", pct])
    print_ascii_table(p_headers, p_rows, ["^", "<", ">", ">"])
    print("=" * 80 + "\n")
    return True


# ==============================================================================
# 5. SEQUENCE EXTRACTION - FORMATTER PIPELINE
# ==============================================================================

def run_formatter_extraction(formatter_path: Path, out_path: Path, sync_live: bool = True) -> bool:
    """Extracts sequences from Formatter folder (shop_unlocked_sequence + *_moves)."""
    t0 = time.time()
    print("\n" + "=" * 80)
    print("        HIGH-PERFORMANCE SEQUENCE EXTRACTION (FORMATTER DATASET)")
    print("=" * 80)
    print(f" Input Directory : {formatter_path}")
    print(f" Output Directory: {out_path}")
    print("-" * 80)

    # Load live rankings if available
    live_ranks: dict[str, int] = {}
    if load_live_player_ranks is not None:
        live_ranks = load_live_player_ranks(sync_live=sync_live, verbose=True)

    seq_file = formatter_path / "shop_unlocked_sequence.parquet"
    if not seq_file.exists():
        print(f"[!] shop_unlocked_sequence.parquet not found in: {formatter_path}")
        return False

    print(f"[>] Loading shop unlock sequences from {seq_file.name}...")
    df_seq = pq.read_table(str(seq_file)).to_pandas()
    print(f"   [OK] Loaded {len(df_seq):,} match sequences.")

    seq_lookup: dict[str, dict[str, Any]] = {}
    seq_frequencies: Counter = Counter()
    total_matches = len(df_seq)

    for _, row in df_seq.iterrows():
        ep_id = str(row["episode_id"])
        raw_shops = list(row["shop_sequence"])
        simplified_shops = [simplify_shop_name(s) for s in raw_shops]
        unlock_steps = [int(s) for s in list(row["unlock_steps"])]
        total_steps = int(row.get("total_steps", 720) or 720)
        winner = str(row.get("winner", ""))
        seq_lookup[ep_id] = {
            "shops": simplified_shops,
            "unlock_steps": unlock_steps,
            "total_steps": total_steps,
            "winner": winner,
        }
        for i in range(len(unlock_steps)):
            sub_seq = tuple(simplified_shops[: i + 1])
            seq_frequencies[sub_seq] += 1

    player_datasets: list[tuple[int, str, Path]] = []
    for f in formatter_path.glob("*_moves.parquet"):
        clean_name = f.stem[:-6] if f.stem.endswith("_moves") else f.stem
        raw_pname = re.sub(r"^\d+_", "", clean_name).replace("_", " ").strip()
        live_r = live_ranks.get(raw_pname.lower())
        rank = live_r if live_r is not None else parse_player_rank(clean_name)
        player_datasets.append((rank, clean_name, f))

    for d in formatter_path.iterdir():
        if d.is_dir() and (d / "winning_agent_details.parquet").exists():
            clean_name = d.name
            raw_pname = re.sub(r"^\d+_", "", clean_name).replace("_", " ").strip()
            live_r = live_ranks.get(raw_pname.lower())
            rank = live_r if live_r is not None else parse_player_rank(clean_name)
            if not any(ds[1] == clean_name for ds in player_datasets):
                player_datasets.append((rank, clean_name, d / "winning_agent_details.parquet"))

    player_datasets.sort(key=lambda x: (x[0], x[1]))
    if not player_datasets:
        print(f"[!] No player moves parquet files found in {formatter_path}")
        return False

    catalog: dict[tuple[str, ...], dict[str, Any]] = {}

    for rank, p_name, win_file in player_datasets:
        print(f"[>] Processing Rank #{rank:02d} - '{p_name}'...")
        df_win = pq.read_table(str(win_file)).to_pandas()

        for ep_id, group in df_win.groupby("episode_id", sort=False):
            ep_id_str = str(ep_id)
            if ep_id_str not in seq_lookup:
                continue

            ep_info = seq_lookup[ep_id_str]
            shops = ep_info["shops"]
            unlock_steps = ep_info["unlock_steps"]
            total_steps = ep_info["total_steps"]

            group = group.sort_values("step")
            reward = float(group["winner_final_reward"].iloc[0]) if "winner_final_reward" in group.columns else 0.0

            for i in range(len(unlock_steps)):
                sub_seq = tuple(shops[: i + 1])
                seq_label = format_sequence_label(sub_seq)
                start_step = unlock_steps[i]
                end_step = unlock_steps[i + 1] if i + 1 < len(unlock_steps) else total_steps

                if sub_seq in catalog:
                    existing = catalog[sub_seq]
                    if rank > existing["player_rank"]:
                        continue
                    elif rank == existing["player_rank"] and reward <= existing["reward"]:
                        continue

                moves_slice = group[(group["step"] >= start_step) & (group["step"] < end_step)].copy()
                if moves_slice.empty:
                    continue

                moves_slice["sequence_label"] = seq_label
                moves_slice["sequence_length"] = len(sub_seq)
                moves_slice["step_in_sequence"] = moves_slice["step"] - start_step
                moves_slice["player_rank"] = rank
                moves_slice["player_name"] = p_name

                farmer_moves = [
                    format_farmer_move(t, tgt)
                    for t, tgt in zip(moves_slice["action_farmer_type"], moves_slice["action_farmer_target"])
                ]
                moves_slice["farmer_move"] = farmer_moves

                if "action_hands_raw" in moves_slice.columns:
                    hands_moves = [format_hands_moves(h) for h in moves_slice["action_hands_raw"]]
                else:
                    hands_moves = ["NONE"] * len(moves_slice)
                moves_slice["hands_moves"] = hands_moves

                market_moves = [
                    str(m) if m and not pd.isna(m) else "NONE"
                    for m in moves_slice.get("market_orders_summary", ["NONE"] * len(moves_slice))
                ]
                moves_slice["market_moves"] = market_moves

                moves_slice["full_move"] = [
                    format_full_move(f, h, m)
                    for f, h, m in zip(farmer_moves, hands_moves, market_moves)
                ]

                slice_cols = [c for c in MOVE_ONLY_COLUMNS if c in moves_slice.columns]
                moves_slice = moves_slice[slice_cols]

                catalog[sub_seq] = {
                    "sequence_key": sub_seq,
                    "sequence_label": seq_label,
                    "sequence_length": len(sub_seq),
                    "player_rank": rank,
                    "player_name": p_name,
                    "episode_id": ep_id_str,
                    "reward": reward,
                    "start_step": start_step,
                    "end_step": end_step,
                    "moves_count": len(moves_slice),
                    "moves_df": moves_slice,
                }

    # Save Formatter Catalog
    out_path.mkdir(parents=True, exist_ok=True)
    sorted_catalog = sorted(
        catalog.values(),
        key=lambda s: (s["sequence_length"], s["sequence_label"]),
    )

    all_moves_dfs: list[pd.DataFrame] = []
    summary_rows: list[dict[str, Any]] = []

    for seg in sorted_catalog:
        all_moves_dfs.append(seg["moves_df"])
        sub_key = seg.get("sequence_key", ())
        freq = seq_frequencies.get(sub_key, 1)
        freq_pct = round((freq / total_matches) * 100, 2) if total_matches > 0 else 0.0
        summary_rows.append({
            "sequence_label": seg["sequence_label"],
            "sequence_length": seg["sequence_length"],
            "player_rank": seg["player_rank"],
            "player_name": seg["player_name"],
            "episode_id": seg["episode_id"],
            "moves_count": seg["moves_count"],
            "match_reward": seg["reward"],
            "start_step": seg["start_step"],
            "end_step": seg["end_step"],
            "frequency": freq,
            "frequency_pct": freq_pct,
        })

    master_moves_df = pd.concat(all_moves_dfs, ignore_index=True) if all_moves_dfs else pd.DataFrame()
    final_cols = [c for c in MOVE_ONLY_COLUMNS if c in master_moves_df.columns]
    master_moves_df = master_moves_df[final_cols]

    moves_file = out_path / "sequence_moves.parquet"
    summary_file = out_path / "sequences_summary.parquet"

    moves_table = pa.Table.from_pandas(master_moves_df, preserve_index=False)
    safe_atomic_write_parquet(moves_table, moves_file)
    moves_mb = os.path.getsize(moves_file) / (1024 * 1024)

    summary_df = pd.DataFrame(summary_rows)
    summary_table = pa.Table.from_pandas(summary_df, preserve_index=False)
    safe_atomic_write_parquet(summary_table, summary_file)
    summary_mb = os.path.getsize(summary_file) / (1024 * 1024)

    print("\n" + "=" * 80)
    print("                    EXTRACTION COMPLETE & VERIFIED")
    print("=" * 80)
    print(f" * Sequence Moves Dataset    : {moves_file.name}")
    print(f"   -> {len(master_moves_df):,} total moves | {len(master_moves_df.columns)} columns | {moves_mb:.2f} MB")
    print(f" * Sequences Summary Catalog : {summary_file.name}")
    print(f"   -> {len(summary_df):,} unique sequences | {summary_mb:.3f} MB")
    print(f" * Total Execution Time       : {time.time() - t0:.2f} seconds")

    # Table 1: Breakdown by Sequence Length
    print("\n[+] Breakdown by Sequence Length:")
    by_len = summary_df.groupby("sequence_length").size().to_dict()
    tbl_headers = ["Length", "Step Window", "Unique Sequences", "% of Catalog"]
    tbl_rows = []
    for l in sorted(by_len.keys()):
        cnt = by_len[l]
        pct = f"{(cnt / len(summary_df)) * 100:.1f}%"
        win_str = "72 moves" if l < 8 else "All remaining (144)"
        tbl_rows.append([f"Length {l}", win_str, f"{cnt:,}", pct])
    print_ascii_table(tbl_headers, tbl_rows, ["<", "<", ">", ">"])

    # Table 2: Top Source Players
    print("\n[+] Top Discovered Source Players (Priority Tier Order):")
    by_player = summary_df.groupby(["player_rank", "player_name"]).size().to_dict()
    p_headers = ["Rank", "Player Name", "Unique Sequences", "Share %"]
    p_rows = []
    for (r_val, p_val), cnt in sorted(by_player.items())[:15]:
        pct = f"{(cnt / len(summary_df)) * 100:.1f}%"
        rk_str = f"#{r_val:02d}" if r_val < 900 else "Unranked"
        p_rows.append([rk_str, safe_console_str(p_val), f"{cnt:,}", pct])
    print_ascii_table(p_headers, p_rows, ["^", "<", ">", ">"])
    print("=" * 80 + "\n")
    return True


def run_sequence_extraction(
    input_dir: str = "../Formatter2/formatted_data",
    output_dir: str = ".",
    sync_live: bool = True,
) -> bool:
    """Dispatches extraction to F2 pipeline or Formatter pipeline based on input directory."""
    mode, resolved_path = detect_input_source(input_dir)
    out_p = Path(output_dir).resolve()
    if mode == "F2":
        return run_f2_extraction(resolved_path, out_p, sync_live=sync_live)
    else:
        return run_formatter_extraction(resolved_path, out_p, sync_live=sync_live)


# ==============================================================================
# 6. FREQUENCY ANALYSIS (Option 4)
# ==============================================================================

def count_unique_sequence_frequency(
    input_source: Optional[str | Path] = None,
    extractor_dir: str | Path = ".",
    length: Optional[int] = None,
    min_length: int = 1,
    max_length: int = 8,
    top_n: Optional[int] = 200,
    save_parquet: bool = True,
    save_csv: bool = True,
    output_filename: str = "sequence_frequencies.parquet",
    verbose: bool = True,
) -> pd.DataFrame:
    """
    Counts and arranges all unique sequences from most frequent to least frequent.
    Supports both F2 datasets (town.parquet) and Formatter datasets (shop_unlocked_sequence.parquet).
    """
    base = Path(extractor_dir).resolve()
    summary_file = base / "sequences_summary.parquet"

    total_matches = 0
    freq_counter: Dict[str, int] = defaultdict(int)
    source_name = "Unknown"

    candidates = []
    if input_source:
        candidates.append(Path(input_source).resolve())
        candidates.append(Path(input_source).resolve() / "town.parquet")
        candidates.append(Path(input_source).resolve() / "shop_unlocked_sequence.parquet")

    candidates.extend([
        Path("B:/Replicator/Formatter2/formatted_data/town.parquet"),
        base / "../Formatter2/formatted_data/town.parquet",
        Path("B:/Replicator/F2/formatted_data/town.parquet"),
        base / "../F2/formatted_data/town.parquet",
        Path("B:/Replicator/Formatter/outputs/shop_unlocked_sequence.parquet"),
        base / "../Formatter/outputs/shop_unlocked_sequence.parquet",
    ])

    matched_src: Optional[Path] = None
    for cand in candidates:
        if cand.exists() and cand.is_file():
            matched_src = cand
            break
        elif cand.exists() and cand.is_dir():
            if (cand / "town.parquet").exists():
                matched_src = cand / "town.parquet"
                break
            elif (cand / "shop_unlocked_sequence.parquet").exists():
                matched_src = cand / "shop_unlocked_sequence.parquet"
                break

    if matched_src and matched_src.name == "town.parquet":
        source_name = f"F2 town.parquet ({matched_src.parent.name})"
        df_town = pq.read_table(str(matched_src), columns=["episode_id", "step", "shop", "shop_index"]).to_pandas()
        df_town = df_town.sort_values(["episode_id", "step"]).drop_duplicates(subset=["episode_id", "shop_index"])
        total_matches = df_town["episode_id"].nunique()
        for _, group in df_town.groupby("episode_id", sort=False):
            raw_shops = list(group["shop"])
            simplified = [simplify_shop_name(s) for s in raw_shops]
            for i in range(len(simplified)):
                sub = tuple(simplified[: i + 1])
                lbl = format_sequence_label(sub)
                freq_counter[lbl] += 1

    elif matched_src and matched_src.name == "shop_unlocked_sequence.parquet":
        source_name = f"Formatter ({matched_src.name})"
        df_raw = pq.read_table(str(matched_src)).to_pandas()
        total_matches = len(df_raw)
        for _, row in df_raw.iterrows():
            raw_shops = list(row.get("shop_sequence", []))
            simplified = [simplify_shop_name(s) for s in raw_shops]
            for i in range(len(simplified)):
                sub = tuple(simplified[: i + 1])
                lbl = format_sequence_label(sub)
                freq_counter[lbl] += 1

    # Load summary if available for additional discovered sequences
    df_summary: Optional[pd.DataFrame] = None
    if summary_file.exists():
        try:
            df_summary = pq.read_table(str(summary_file)).to_pandas()
            if total_matches == 0:
                total_matches = df_summary["episode_id"].nunique()
        except Exception:
            pass

    # Build rows
    rows = []
    if df_summary is not None and not df_summary.empty:
        for _, row in df_summary.iterrows():
            lbl = str(row["sequence_label"])
            l_val = int(row["sequence_length"])
            cnt = freq_counter.get(lbl, int(row.get("frequency", 1)))
            pct = round((cnt / total_matches) * 100, 2) if total_matches > 0 else 0.0
            rows.append({
                "sequence_label": lbl,
                "sequence_length": l_val,
                "frequency": cnt,
                "frequency_pct": pct,
            })
    elif freq_counter:
        for lbl, cnt in freq_counter.items():
            parts = [p.strip() for p in lbl.strip("()").split(",") if p.strip()]
            l_val = len(parts)
            pct = round((cnt / total_matches) * 100, 2) if total_matches > 0 else 0.0
            rows.append({
                "sequence_label": lbl,
                "sequence_length": l_val,
                "frequency": cnt,
                "frequency_pct": pct,
            })
    else:
        print("[!] No sequence data found to compute frequencies.")
        return pd.DataFrame()

    df_res = pd.DataFrame(rows)
    df_res = df_res.drop_duplicates(subset=["sequence_label"])

    if length is not None:
        df_res = df_res[df_res["sequence_length"] == length].copy()
    else:
        df_res = df_res[(df_res["sequence_length"] >= min_length) & (df_res["sequence_length"] <= max_length)].copy()

    df_res = df_res.sort_values(by=["frequency", "sequence_length", "sequence_label"], ascending=[False, True, True]).reset_index(drop=True)
    df_res["popularity_rank"] = df_res.index + 1

    ordered_cols = ["popularity_rank", "sequence_label", "sequence_length", "frequency", "frequency_pct"]
    df_res = df_res[ordered_cols]

    # Atomic saves
    if save_parquet:
        out_f = base / output_filename
        safe_atomic_write_parquet(pa.Table.from_pandas(df_res, preserve_index=False), out_f)
        if verbose:
            print(f" [OK] All {len(df_res):,} unique sequences arranged and saved to: {out_f.name}")

    if save_csv:
        out_csv = base / output_filename.replace(".parquet", ".csv")
        temp_csv = base / f"temp_{out_csv.name}"
        df_res.to_csv(temp_csv, index=False, encoding="utf-8")
        if out_csv.exists():
            try:
                out_csv.unlink()
            except Exception:
                pass
        temp_csv.rename(out_csv)
        if verbose:
            print(f" [OK] Exported full CSV for easy viewing: {out_csv.name}")

    # Beautiful Console Reporting
    if verbose:
        print("\n" + "=" * 80)
        print("        ALL UNIQUE SEQUENCES ARRANGE-BY-FREQUENCY ANALYSIS")
        print("=" * 80)
        print(f" * Active Data Source        : {source_name}")
        print(f" * Total Matches Analyzed    : {total_matches:,}")
        print(f" * Total Unique Sequences    : {len(df_res):,}")

        # Summary Breakdown Table by Length
        print("\n[+] Breakdown by Sequence Length (Lengths 1 to 8):")
        all_summary_lens = sorted(df_res["sequence_length"].unique())
        len_headers = ["Length", "Unique Sequences", "Total Occurrences", "Avg Frequency", "Most Popular Sequence"]
        len_rows = []
        for l_curr in all_summary_lens:
            sub_df = df_res[df_res["sequence_length"] == l_curr]
            u_count = len(sub_df)
            t_occ = sub_df["frequency"].sum()
            avg_f = t_occ / u_count if u_count > 0 else 0
            top_row = sub_df.iloc[0]
            top_info = f"{top_row['sequence_label']} ({top_row['frequency']:,})"
            len_rows.append([
                f"Length {l_curr}",
                f"{u_count:,}",
                f"{t_occ:,}",
                f"{avg_f:,.1f}",
                top_info
            ])
        print_ascii_table(len_headers, len_rows, ["<", ">", ">", ">", "<"], max_col_width=35)

        # Ranked Top Sequences Table
        display_count = min(top_n, len(df_res)) if top_n is not None else len(df_res)
        print(f"\n[+] Top {display_count:,} Most Frequent Unique Sequences (Arranged Highest to Lowest):")

        disp_headers = ["Rank", "Sequence Label", "Length", "Frequency", "Freq %"]
        disp_rows = []
        for _, r in df_res.head(display_count).iterrows():
            disp_rows.append([
                f"#{int(r['popularity_rank']):02d}",
                str(r["sequence_label"]),
                str(int(r["sequence_length"])),
                f"{int(r['frequency']):,}",
                f"{float(r['frequency_pct']):.2f}%"
            ])

        print_ascii_table(disp_headers, disp_rows, ["^", "<", "^", ">", ">"])
        print("=" * 80)
        if display_count < len(df_res):
            print(f" [i] Displaying top {display_count} of {len(df_res):,} arranged sequences.")
            print(f"     Full ranked table of all {len(df_res):,} sequences is saved in:")
            print(f"     -> {output_filename}")
            print(f"     -> {output_filename.replace('.parquet', '.csv')}")
        print("=" * 80 + "\n")

    return df_res


# ==============================================================================
# 7. QUERY SEQUENCE (Option 2)
# ==============================================================================

def query_sequence(sequence_query: str, extractor_dir: str = ".") -> None:
    """Queries and displays moves for a specific shop sequence with beautiful formatting."""
    base = Path(extractor_dir).resolve()
    moves_file = base / "sequence_moves.parquet"
    summary_file = base / "sequences_summary.parquet"

    if not summary_file.exists():
        print(f"[!] Extractor dataset not found in {base}. Please run extraction first (Option 1).")
        return

    try:
        df_summary = pq.read_table(str(summary_file)).to_pandas()
    except Exception as e:
        print(f"[!] Error opening sequences_summary.parquet: {e}")
        print("    Please re-run Option 1 (Run Full Sequence Extraction) to generate fresh datasets.")
        return

    clean = sequence_query.strip().strip("()").replace(" ", "")
    query_parts = [simplify_shop_name(p) for p in clean.split(",") if p]
    target_label = format_sequence_label(tuple(query_parts))

    print("\n" + "=" * 80)
    print(f"                    QUERY SEQUENCE: {target_label}")
    print("=" * 80)

    match = df_summary[df_summary["sequence_label"] == target_label]
    if match.empty:
        match = df_summary[df_summary["sequence_label"].str.contains(target_label.strip("()"), case=False, na=False)]

    if match.empty:
        print(f"[!] Sequence '{target_label}' was not found in the dataset.")
        print("\nAvailable sample sequences of this length:")
        same_len = df_summary[df_summary["sequence_length"] == len(query_parts)]
        for s in same_len["sequence_label"].head(8):
            print(f"   * {s}")
        print("=" * 80 + "\n")
        return

    row = match.iloc[0]
    print(f" * Sequence Label      : {row['sequence_label']}")
    print(f" * Discovered Player   : Rank #{row['player_rank']} ({row['player_name']})")
    print(f" * Match ID            : {row['episode_id']}")
    print(f" * Match Win Reward    : {row['match_reward']:,.1f}")
    print(f" * Move Window         : {row['moves_count']} moves (Match Steps {row['start_step']} to {row['end_step'] - 1})")
    print(f" * Catalog Frequency   : {int(row.get('frequency', 1)):,} occurrences ({float(row.get('frequency_pct', 0.0)):.2f}% of matches)")

    if not moves_file.exists():
        print(f"\n[!] Note: sequence_moves.parquet not found. Run Option 1 to generate turn moves.")
        print("=" * 80 + "\n")
        return

    try:
        df_moves = pq.read_table(str(moves_file)).to_pandas()
        moves = df_moves[df_moves["sequence_label"] == row["sequence_label"]]
    except Exception as e:
        print(f"\n[!] Could not read sequence_moves.parquet ({e}). Please re-run Option 1.")
        print("=" * 80 + "\n")
        return

    if moves.empty:
        print(f"\n[!] No moves recorded for sequence {row['sequence_label']}.")
        print("=" * 80 + "\n")
        return

    display_moves = moves.head(15)
    print(f"\n[+] Move Details (First {len(display_moves)} of {len(moves)} steps):")
    m_headers = ["Seq Step", "Match Step", "Hour", "Farmer Action", "Farmhands Moves", "Market Orders"]
    m_rows = []
    for _, mr in display_moves.iterrows():
        m_rows.append([
            f"{int(mr['step_in_sequence']):02d}",
            f"{int(mr['step']):03d}",
            f"{int(mr['hour']):02d}:00",
            str(mr.get("farmer_move", "PASS")),
            str(mr.get("hands_moves", "NONE")),
            str(mr.get("market_moves", "NONE")),
        ])

    print_ascii_table(m_headers, m_rows, ["^", "^", "^", "<", "<", "<"], max_col_width=25)
    if len(moves) > 15:
        print(f" [i] Displaying first 15 of {len(moves)} moves. All moves available in sequence_moves.parquet.")
    print("=" * 80 + "\n")


# ==============================================================================
# 8. VIEW STATISTICS (Option 3)
# ==============================================================================

def show_statistics(extractor_dir: str = ".") -> None:
    """Displays summary statistics of extracted sequences with formatted ASCII tables."""
    base = Path(extractor_dir).resolve()
    summary_file = base / "sequences_summary.parquet"
    if not summary_file.exists():
        print(f"[!] {summary_file.name} not found in {base}. Please run extraction first (Option 1).")
        return

    try:
        df = pq.read_table(str(summary_file)).to_pandas()
    except Exception as e:
        print(f"[!] Error opening sequences_summary.parquet: {e}")
        return

    print("\n" + "=" * 80)
    print("                      SEQUENCE SUMMARY STATISTICS")
    print("=" * 80)
    print(f" * Total Unique Sequences in Catalog : {len(df):,}")

    # Table 1: Length breakdown
    print("\n[+] Unique Sequences by Length:")
    l_headers = ["Length", "Step Window", "Unique Sequences", "Share %"]
    l_rows = []
    for length, count in df.groupby("sequence_length").size().items():
        moves = "72 moves" if length < 8 else "All remaining (144)"
        pct = f"{(count / len(df)) * 100:.1f}%"
        l_rows.append([f"Length {length}", moves, f"{count:,}", pct])
    print_ascii_table(l_headers, l_rows, ["<", "<", ">", ">"])

    # Table 2: Source Player breakdown
    print("\n[+] Source Players (Priority Hierarchy):")
    p_headers = ["Rank", "Player Name", "Sequences Discovered", "Share %"]
    p_rows = []
    for (rank, name), count in df.groupby(["player_rank", "player_name"]).size().items():
        pct = f"{(count / len(df)) * 100:.1f}%"
        p_rows.append([f"#{rank:02d}", str(name), f"{count:,}", pct])
    print_ascii_table(p_headers, p_rows, ["^", "<", ">", ">"])
    print("=" * 80 + "\n")


# ==============================================================================
# 9. MAIN CLI INTERFACE
# ==============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract specific shop unlock sequences and moves from F2 or Formatter datasets."
    )
    parser.add_argument(
        "-i", "--input",
        default="../Formatter2/formatted_data",
        help="Input directory (default: ../Formatter2/formatted_data; also accepts ../Formatter/outputs)",
    )
    parser.add_argument(
        "-o", "--output",
        default=".",
        help="Output directory for sequence parquet datasets (default: current dir)",
    )
    parser.add_argument(
        "-q", "--query",
        default=None,
        help="Query a specific sequence (e.g. '(PIZZA, ICE_CREAM)' or 'PIZZA')",
    )
    parser.add_argument(
        "-s", "--stats",
        action="store_true",
        help="Show statistics of extracted sequences",
    )
    parser.add_argument(
        "-f", "--frequency",
        action="store_true",
        help="Count and display unique sequence frequencies across all matches",
    )
    parser.add_argument(
        "--length",
        type=int,
        default=None,
        help="Filter frequency analysis to a specific sequence length (1-8)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=200,
        help="Number of top frequent sequences to display (default: 200)",
    )
    parser.add_argument(
        "--formatter2", "--f2",
        action="store_true",
        dest="formatter2",
        help="Explicitly use Formatter2 consolidated Parquet datasets as input source",
    )
    parser.add_argument(
        "--formatter",
        action="store_true",
        help="Explicitly use Formatter outputs as input source",
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip auto-syncing live Kaggle leaderboard rankings",
    )
    args = parser.parse_args()

    input_src = args.input
    if args.formatter2:
        input_src = "../Formatter2/formatted_data"
    elif args.formatter:
        input_src = "../Formatter/outputs"

    if args.frequency:
        count_unique_sequence_frequency(
            input_source=input_src,
            extractor_dir=args.output,
            length=args.length,
            top_n=args.top,
            save_parquet=True,
            save_csv=True,
        )
    elif args.stats:
        show_statistics(extractor_dir=args.output)
    elif args.query:
        query_sequence(args.query, extractor_dir=args.output)
    else:
        run_sequence_extraction(input_dir=input_src, output_dir=args.output, sync_live=not args.no_sync)
