"""High-Performance Parquet Extractor for Kaggle Simulation Replays.

Streamlined & Focused Parquet Extractor:
1. Generates the Shop Unlocked Sequence of ALL matches in ONE single Parquet file:
   outputs/shop_unlocked_sequence.parquet
2. Generates flat per-player moves files directly in outputs/ without subfolders:
   outputs/<rank>_<name>_moves.parquet (e.g. outputs/01_Mengfei Li_moves.parquet)
   (Contains all moves, inventories, shed goods, farm tiles, and details of the winning agent)
3. Consumes replays directly from Downloader folder (replay_downloader/downloads/kaggriculture).
4. No redundant, fragmented, or cluttering subfolders.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import multiprocessing
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import pyarrow as pa
import pyarrow.parquet as pq

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def sanitize_name(name: str) -> str:
    """Sanitizes names for safe filesystem output."""
    clean = re.sub(r'[\\/*?:"<>|\r\n\t]', "_", name).strip()
    clean = re.sub(r"_+", "_", clean)
    return clean.strip("_") or "player_data"


sanitize_folder_name = sanitize_name


def parse_player_rank_and_name(identifier: str) -> tuple[Optional[int], str]:
    """
    Extracts (rank, player_name) from a player directory or parquet filename.
    Examples:
      '04_3정훈_moves.parquet' -> (4, '3정훈')
      '02_3정훈'               -> (2, '3정훈')
      '01_Mengfei Li'          -> (1, 'Mengfei Li')
      'shop_unlocked_sequence' -> (None, 'shop_unlocked_sequence')
    """
    clean = re.sub(r"(_moves)?\.parquet$", "", identifier.strip(), flags=re.IGNORECASE)
    m = re.match(r"^(\d+)[_\s-]+(.*)$", clean)
    if m:
        return (int(m.group(1)), m.group(2).strip())
    return (None, clean)


def fetch_live_leaderboard_ranks() -> dict[str, int]:
    """
    Attempts to fetch live leaderboard rankings for teams from Kaggle.
    Returns mapping of lowercase_team_name -> current_rank.
    """
    ranks: dict[str, int] = {}
    auth_candidates = [
        Path("../replay_downloader/auth.json"),
        Path("replay_downloader/auth.json"),
        Path("../../replay_downloader/auth.json"),
    ]
    auth_file = None
    for cand in auth_candidates:
        if cand.exists():
            auth_file = cand
            break

    if not auth_file:
        return ranks

    try:
        downloader_dir = auth_file.parent
        if str(downloader_dir.resolve()) not in sys.path:
            sys.path.insert(0, str(downloader_dir.resolve()))
        from client import KaggleSession
        session = KaggleSession(auth_file)
        comp_id, _ = session.resolve_competition_id("kaggriculture")
        lb = session.fetch_leaderboard(comp_id)
        for t in lb:
            name = str(t.get("team_name", "")).strip().lower()
            if name and "rank" in t:
                ranks[name] = int(t["rank"])
    except Exception:
        pass
    return ranks


def sync_output_file_ranks(
    output_dir: str,
    player_groups: dict[str, list[str]],
    download_dir: Optional[str] = None,
) -> list[tuple[str, str]]:
    """
    Checks all existing output files in output_dir.
    Modifies the name of each player's moves file to their CURRENT rank (matching download folder / live leaderboard).
    Never deletes any files or folders ('do not delete anything').
    Returns a list of (old_filename, new_filename) for all renames performed.
    """
    if not os.path.exists(output_dir):
        return []

    # 1. Map normalized player name -> (target_rank, target_name) from download folder
    target_players: dict[str, tuple[int, str]] = {}
    for g in player_groups.keys():
        r, name = parse_player_rank_and_name(g)
        if r is not None:
            target_players[name.lower()] = (r, name)

    # 2. For any existing output files not in download folder, fetch live leaderboard rank
    existing_entries = [e for e in os.listdir(output_dir) if e.endswith("_moves.parquet")]
    missing_players = []
    for entry in existing_entries:
        curr_rank, curr_name = parse_player_rank_and_name(entry)
        if curr_name.lower() not in target_players:
            missing_players.append(curr_name)

    if missing_players:
        live_ranks = fetch_live_leaderboard_ranks()
        for p_name in missing_players:
            l_rank = live_ranks.get(p_name.lower())
            if l_rank is not None:
                target_players[p_name.lower()] = (l_rank, p_name)

    renames: list[tuple[str, str]] = []
    for entry in existing_entries:
        curr_rank, curr_name = parse_player_rank_and_name(entry)
        matched = target_players.get(curr_name.lower())
        if not matched:
            continue

        target_rank, target_name = matched
        if target_rank is not None and curr_rank != target_rank:
            new_filename = f"{target_rank:02d}_{sanitize_name(target_name)}_moves.parquet"
            if entry != new_filename:
                renames.append((entry, new_filename))

    if not renames:
        return []

    print("-" * 65)
    print("Checking player ranks in output files against current rankings...")
    for old_name, new_name in renames:
        old_path = os.path.join(output_dir, old_name)
        new_path = os.path.join(output_dir, new_name)
        if not os.path.exists(old_path):
            continue

        if os.path.exists(new_path) and old_path != new_path:
            # If target already exists, do not overwrite or delete.
            tmp_name = f"{old_name}.rank_synced"
            try:
                os.rename(old_path, os.path.join(output_dir, tmp_name))
                print(f" 🔄 Player rank changed: '{old_name}' -> '{new_name}' (target existed, preserved as '{tmp_name}')")
            except Exception as e:
                print(f" [!] Warning: Could not rename '{old_name}': {e}")
        else:
            try:
                os.rename(old_path, new_path)
                print(f" 🔄 Player rank changed: Renamed output file '{old_name}' ➔ '{new_name}'")
            except Exception as e:
                print(f" [!] Warning: Could not rename '{old_name}' to '{new_name}': {e}")

    print("-" * 65)
    return renames


def extract_shop_unlocked_sequence(data: dict[str, Any], filename: str) -> dict[str, Any]:
    """
    Extracts the chronological shop unlocked sequence from a match into one consolidated record.
    """
    ep_id = str(data.get("info", {}).get("EpisodeId", filename.replace(".json", "")))
    seed = int(data.get("configuration", {}).get("seed") or data.get("info", {}).get("seed") or 0)
    teams = data.get("info", {}).get("TeamNames", ["Player 0", "Player 1"])
    team_0 = str(teams[0]) if len(teams) > 0 else "Player 0"
    team_1 = str(teams[1]) if len(teams) > 1 else "Player 1"
    rewards = data.get("rewards", [0, 0])
    r0 = float(rewards[0]) if len(rewards) > 0 and rewards[0] is not None else 0.0
    r1 = float(rewards[1]) if len(rewards) > 1 and rewards[1] is not None else 0.0

    winner = "TIE"
    if r0 > r1:
        winner = team_0
    elif r1 > r0:
        winner = team_1

    steps = data.get("steps", [])
    total_steps = len(steps)

    prev_shops: list[str] = []
    shop_sequence: list[str] = []
    unlock_steps: list[int] = []
    unlock_days: list[int] = []
    unlock_hours: list[int] = []

    for step_idx, step_data in enumerate(steps):
        if not step_data or not isinstance(step_data, list):
            continue
        obs = step_data[0].get("observation", {}) if isinstance(step_data[0], dict) else {}
        town = obs.get("town", {}) if isinstance(obs, dict) else {}
        unlocked = town.get("unlocked_shops", []) if isinstance(town, dict) else []

        if isinstance(unlocked, list) and len(unlocked) > len(prev_shops):
            day = int(obs.get("day", 0))
            hour = int(obs.get("hour", 0))
            for new_shop in unlocked[len(prev_shops):]:
                shop_sequence.append(str(new_shop))
                unlock_steps.append(step_idx)
                unlock_days.append(day)
                unlock_hours.append(hour)
            prev_shops = list(unlocked)

    seq_str = " -> ".join(shop_sequence) if shop_sequence else "NONE"

    match_row: dict[str, Any] = {
        "episode_id": ep_id,
        "source_file": filename,
        "seed": seed,
        "team_0": team_0,
        "team_1": team_1,
        "reward_0": r0,
        "reward_1": r1,
        "winner": winner,
        "total_steps": total_steps,
        "total_shops_unlocked": len(shop_sequence),
        "shop_sequence": shop_sequence,
        "shop_sequence_str": seq_str,
        "unlock_steps": unlock_steps,
        "unlock_days": unlock_days,
        "unlock_hours": unlock_hours,
    }

    for pos in range(1, 11):
        idx = pos - 1
        match_row[f"shop_{pos}"] = shop_sequence[idx] if idx < len(shop_sequence) else ""
        match_row[f"step_{pos}"] = unlock_steps[idx] if idx < len(unlock_steps) else -1
        match_row[f"day_{pos}"] = unlock_days[idx] if idx < len(unlock_days) else -1

    return match_row


def extract_winning_agent_details(data: dict[str, Any], filename: str) -> list[dict[str, Any]]:
    """
    Extracts complete step-by-step trajectory (all moves, inventories, shed goods,
    tiles, finances, and market orders) of the WINNING AGENT of this match.
    """
    ep_id = str(data.get("info", {}).get("EpisodeId", filename.replace(".json", "")))
    seed = int(data.get("configuration", {}).get("seed") or data.get("info", {}).get("seed") or 0)
    teams = data.get("info", {}).get("TeamNames", ["Player 0", "Player 1"])
    team_0 = str(teams[0]) if len(teams) > 0 else "Player 0"
    team_1 = str(teams[1]) if len(teams) > 1 else "Player 1"
    rewards = data.get("rewards", [0, 0])
    r0 = float(rewards[0]) if len(rewards) > 0 and rewards[0] is not None else 0.0
    r1 = float(rewards[1]) if len(rewards) > 1 and rewards[1] is not None else 0.0

    # Determine winning player index
    winner_idx = 0 if r0 >= r1 else 1
    winner_team = team_0 if winner_idx == 0 else team_1
    opponent_team = team_1 if winner_idx == 0 else team_0
    winner_final_reward = r0 if winner_idx == 0 else r1
    opponent_final_reward = r1 if winner_idx == 0 else r0

    steps = data.get("steps", [])
    winning_agent_rows: list[dict[str, Any]] = []

    for step_idx, step_data in enumerate(steps):
        if not step_data or not isinstance(step_data, list) or len(step_data) <= winner_idx:
            continue

        player_turn = step_data[winner_idx]
        if not isinstance(player_turn, dict):
            continue

        obs = player_turn.get("observation", {}) if isinstance(player_turn, dict) else {}
        act = player_turn.get("action", {}) if isinstance(player_turn, dict) else {}

        farms = obs.get("farms", []) if isinstance(obs, dict) else []
        my_farm = farms[winner_idx] if len(farms) > winner_idx and isinstance(farms[winner_idx], dict) else {}
        priv = obs.get("private", {}) if isinstance(obs, dict) else {}
        seeds = priv.get("seeds", {}) if isinstance(priv, dict) else {}
        shed = priv.get("shed", {}) if isinstance(priv, dict) else {}
        inventories = priv.get("inventories", []) if isinstance(priv, dict) else []

        day = int(obs.get("day", 0))
        hour = int(obs.get("hour", 0))
        reward_at_step = float(player_turn.get("reward") or 0.0)
        status_at_step = str(player_turn.get("status") or "")

        # 1. Moves / Actions
        farmer_act = act.get("farmer", []) if isinstance(act, dict) else []
        hands_act = act.get("hands", []) if isinstance(act, dict) else []
        market_act = act.get("market", []) if isinstance(act, dict) else []

        farmer_act_type = str(farmer_act[0]) if isinstance(farmer_act, list) and len(farmer_act) > 0 else "NONE"
        farmer_act_target = str(farmer_act[1]) if isinstance(farmer_act, list) and len(farmer_act) > 1 else ""

        buy_prod_cnt = 0
        sell_cnt = 0
        hire_cnt = 0
        buy_seed_cnt = 0
        buy_anim_cnt = 0
        buy_land_cnt = 0
        market_summaries: list[str] = []

        if isinstance(market_act, list):
            for order in market_act:
                if isinstance(order, list) and len(order) > 0:
                    cmd = str(order[0]).upper()
                    if cmd == "BUY_PRODUCT":
                        buy_prod_cnt += 1
                        market_summaries.append(f"BUY_PRODUCT({order[1] if len(order)>1 else ''},{order[2] if len(order)>2 else ''})")
                    elif cmd == "SELL":
                        sell_cnt += 1
                        market_summaries.append(f"SELL({order[1] if len(order)>1 else ''},{order[2] if len(order)>2 else ''})")
                    elif cmd == "HIRE":
                        hire_cnt += 1
                        market_summaries.append("HIRE")
                    elif cmd == "BUY_SEED":
                        buy_seed_cnt += 1
                        market_summaries.append(f"BUY_SEED({order[1] if len(order)>1 else ''},{order[2] if len(order)>2 else ''})")
                    elif cmd == "BUY_ANIMAL":
                        buy_anim_cnt += 1
                        market_summaries.append(f"BUY_ANIMAL({order[1] if len(order)>1 else ''},{order[2] if len(order)>2 else ''})")
                    elif cmd == "BUY_LAND":
                        buy_land_cnt += 1
                        market_summaries.append("BUY_LAND")

        # 2. Positions
        farmer_pos = my_farm.get("farmer", [-1, -1]) if isinstance(my_farm, dict) else [-1, -1]
        farmer_r = int(farmer_pos[0]) if len(farmer_pos) > 0 and farmer_pos[0] is not None else -1
        farmer_c = int(farmer_pos[1]) if len(farmer_pos) > 1 and farmer_pos[1] is not None else -1

        hands_pos = my_farm.get("hands", []) if isinstance(my_farm, dict) else []
        unlocked_quads = my_farm.get("unlocked_quadrants", [0]) if isinstance(my_farm, dict) else [0]

        # 3. Inventories
        farmer_inv = inventories[0] if len(inventories) > 0 and isinstance(inventories[0], dict) else {}
        farmer_holding_item = "NONE"
        farmer_holding_qty = 0
        if farmer_inv:
            first_item = next(iter(farmer_inv.items()))
            farmer_holding_item = str(first_item[0])
            farmer_holding_qty = int(first_item[1]) if first_item[1] is not None else 0

        hands_invs = inventories[1:] if len(inventories) > 1 else []

        # Seeds
        seed_carrot = int(seeds.get("CARROT") or 0) if isinstance(seeds, dict) else 0
        seed_melon = int(seeds.get("MELON") or 0) if isinstance(seeds, dict) else 0
        seed_strawberry = int(seeds.get("STRAWBERRY") or 0) if isinstance(seeds, dict) else 0
        seed_tomato = int(seeds.get("TOMATO") or 0) if isinstance(seeds, dict) else 0
        seed_wheat = int(seeds.get("WHEAT") or 0) if isinstance(seeds, dict) else 0
        total_seeds = seed_carrot + seed_melon + seed_strawberry + seed_tomato + seed_wheat

        # Shed Goods
        shed_wheat = int(shed.get("WHEAT") or 0) if isinstance(shed, dict) else 0
        shed_tomato = int(shed.get("TOMATO") or 0) if isinstance(shed, dict) else 0
        shed_carrot = int(shed.get("CARROT") or 0) if isinstance(shed, dict) else 0
        shed_melon = int(shed.get("MELON") or 0) if isinstance(shed, dict) else 0
        shed_strawberry = int(shed.get("STRAWBERRY") or 0) if isinstance(shed, dict) else 0
        shed_cow = int(shed.get("COW") or 0) if isinstance(shed, dict) else 0
        shed_sheep = int(shed.get("SHEEP") or 0) if isinstance(shed, dict) else 0
        shed_goose = int(shed.get("GOOSE") or 0) if isinstance(shed, dict) else 0
        shed_milk = int(shed.get("MILK") or 0) if isinstance(shed, dict) else 0
        shed_wool = int(shed.get("WOOL") or 0) if isinstance(shed, dict) else 0
        shed_egg = int(shed.get("EGG") or 0) if isinstance(shed, dict) else 0
        shed_fertilizer = int(shed.get("FERTILIZER") or 0) if isinstance(shed, dict) else 0
        total_shed_items = sum([
            shed_wheat, shed_tomato, shed_carrot, shed_melon, shed_strawberry,
            shed_cow, shed_sheep, shed_goose, shed_milk, shed_wool, shed_egg, shed_fertilizer
        ])

        # 4. Tiles & Aggregates
        tiles = my_farm.get("tiles", []) if isinstance(my_farm, dict) else []
        locked_count = 0
        plant_count = 0
        watered_count = 0
        pasture_count = 0
        coop_count = 0
        weed_count = 0
        cow_count = 0
        sheep_count = 0
        goose_count = 0
        total_yield = 0
        crop_counts: dict[str, int] = defaultdict(int)

        if isinstance(tiles, list):
            for row in tiles:
                if isinstance(row, list):
                    for cell in row:
                        if cell == "LOCKED":
                            locked_count += 1
                        elif isinstance(cell, dict):
                            kind = cell.get("kind", "")
                            if kind == "PLANT":
                                plant_count += 1
                                if cell.get("watered_today"):
                                    watered_count += 1
                                c_name = cell.get("crop", "UNKNOWN")
                                crop_counts[c_name] += 1
                                total_yield += int(cell.get("yield_units") or 0)
                            elif kind == "PASTURE":
                                pasture_count += 1
                                animal = cell.get("animal", "")
                                if animal == "COW":
                                    cow_count += 1
                                elif animal == "SHEEP":
                                    sheep_count += 1
                                total_yield += int(cell.get("yield_units") or 0)
                            elif kind == "COOP":
                                coop_count += 1
                                animal = cell.get("animal", "")
                                if animal == "GOOSE":
                                    goose_count += 1
                                total_yield += int(cell.get("yield_units") or 0)
                            elif kind == "WEED":
                                weed_count += 1

        # 5. Market & Town Environment
        shared_market = step_data[0].get("observation", {}).get("market", {}) if len(step_data) > 0 and isinstance(step_data[0], dict) else {}
        prices = shared_market.get("prices", {}) if isinstance(shared_market, dict) else {}
        town = obs.get("town", {}) if isinstance(obs, dict) else {}
        unlocked_shops = town.get("unlocked_shops", []) if isinstance(town, dict) else []

        row: dict[str, Any] = {
            # Match & Agent Meta
            "episode_id": ep_id,
            "step": step_idx,
            "day": day,
            "hour": hour,
            "winner_idx": winner_idx,
            "winner_team": winner_team,
            "opponent_team": opponent_team,
            "winner_final_reward": winner_final_reward,
            "opponent_final_reward": opponent_final_reward,
            "step_reward": reward_at_step,
            "status": status_at_step,
            "seed": seed,
            "money": float(my_farm.get("money") or 0.0) if isinstance(my_farm, dict) else 0.0,
            "remaining_time": float(obs.get("remainingOverageTime") or 0.0) if isinstance(obs, dict) else 0.0,
            "hires_today": int(my_farm.get("hires_today") or 0) if isinstance(my_farm, dict) else 0,
            "unlocked_quadrants_count": len(unlocked_quads) if isinstance(unlocked_quads, list) else 1,
            "unlocked_quadrants": json.dumps(unlocked_quads),

            # Farmer Moves & Actions
            "action_farmer_type": farmer_act_type,
            "action_farmer_target": farmer_act_target,
            "action_farmer_raw": json.dumps(farmer_act, ensure_ascii=False),

            # Hands Moves & Actions
            "action_hands_count": len(hands_act) if isinstance(hands_act, list) else 0,
            "action_hands_raw": json.dumps(hands_act, ensure_ascii=False),

            # Market Actions & Counts
            "action_market_orders_count": len(market_act) if isinstance(market_act, list) else 0,
            "market_buy_product_count": buy_prod_cnt,
            "market_sell_count": sell_cnt,
            "market_hire_count": hire_cnt,
            "market_buy_seed_count": buy_seed_cnt,
            "market_buy_animal_count": buy_anim_cnt,
            "market_buy_land_count": buy_land_cnt,
            "action_market_raw": json.dumps(market_act, ensure_ascii=False),
            "market_orders_summary": "; ".join(market_summaries) if market_summaries else "NONE",
            "action_full_raw": json.dumps(act, ensure_ascii=False),

            # Positions
            "farmer_r": farmer_r,
            "farmer_c": farmer_c,
            "hands_count": len(hands_pos) if isinstance(hands_pos, list) else 0,
            "hands_positions_raw": json.dumps(hands_pos),

            # Farmer Held Inventory
            "farmer_holding_item": farmer_holding_item,
            "farmer_holding_qty": farmer_holding_qty,
            "hands_inventories_raw": json.dumps(hands_invs, ensure_ascii=False),

            # Seeds
            "seed_CARROT": seed_carrot,
            "seed_MELON": seed_melon,
            "seed_STRAWBERRY": seed_strawberry,
            "seed_TOMATO": seed_tomato,
            "seed_WHEAT": seed_wheat,
            "total_seeds": total_seeds,

            # Shed Inventory
            "shed_WHEAT": shed_wheat,
            "shed_TOMATO": shed_tomato,
            "shed_CARROT": shed_carrot,
            "shed_MELON": shed_melon,
            "shed_STRAWBERRY": shed_strawberry,
            "shed_COW": shed_cow,
            "shed_SHEEP": shed_sheep,
            "shed_GOOSE": shed_goose,
            "shed_MILK": shed_milk,
            "shed_WOOL": shed_wool,
            "shed_EGG": shed_egg,
            "shed_FERTILIZER": shed_fertilizer,
            "total_shed_items": total_shed_items,

            # Tiles
            "locked_tiles": locked_count,
            "active_plants": plant_count,
            "watered_plants": watered_count,
            "active_pastures": pasture_count,
            "active_coops": coop_count,
            "active_weeds": weed_count,
            "cows_count": cow_count,
            "sheeps_count": sheep_count,
            "geese_count": goose_count,
            "planted_WHEAT": crop_counts["WHEAT"],
            "planted_TOMATO": crop_counts["TOMATO"],
            "planted_CARROT": crop_counts["CARROT"],
            "planted_MELON": crop_counts["MELON"],
            "planted_STRAWBERRY": crop_counts["STRAWBERRY"],
            "total_yield_ready": total_yield,

            # Market & Town Environment
            "price_WHEAT": float(prices.get("WHEAT", 0.0)) if isinstance(prices, dict) else 0.0,
            "price_TOMATO": float(prices.get("TOMATO", 0.0)) if isinstance(prices, dict) else 0.0,
            "price_CARROT": float(prices.get("CARROT", 0.0)) if isinstance(prices, dict) else 0.0,
            "price_MELON": float(prices.get("MELON", 0.0)) if isinstance(prices, dict) else 0.0,
            "price_STRAWBERRY": float(prices.get("STRAWBERRY", 0.0)) if isinstance(prices, dict) else 0.0,
            "price_MILK": float(prices.get("MILK", 0.0)) if isinstance(prices, dict) else 0.0,
            "price_WOOL": float(prices.get("WOOL", 0.0)) if isinstance(prices, dict) else 0.0,
            "price_EGG": float(prices.get("EGG", 0.0)) if isinstance(prices, dict) else 0.0,
            "unlocked_shops_count": len(unlocked_shops) if isinstance(unlocked_shops, list) else 0,
            "town_unlocked_shops": json.dumps(unlocked_shops),
            "latest_unlocked_shop": str(unlocked_shops[-1]) if unlocked_shops else "NONE",
        }
        winning_agent_rows.append(row)

    return winning_agent_rows


def extract_episode_essential_tables(filepath: str) -> dict[str, Any]:
    """
    Parses a single episode JSON and generates ONLY the essential tables:
    - shop_unlocked_sequence: 1 row for the match
    - winning_agent_details: 720 rows for the winning agent
    """
    tables: dict[str, Any] = {
        "shop_unlocked_sequence": [],
        "winning_agent_details": [],
    }

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except Exception as e:
        print(f"\n[!] Error reading {filepath}: {e}")
        return tables

    filename = os.path.basename(filepath)

    # 1. Shop Unlocked Sequence
    shop_match_row = extract_shop_unlocked_sequence(data, filename)
    tables["shop_unlocked_sequence"].append(shop_match_row)

    # 2. Winning Agent Full Details
    win_rows = extract_winning_agent_details(data, filename)
    tables["winning_agent_details"].extend(win_rows)

    return tables


def merge_or_write_parquet(
    rows: list[dict[str, Any]],
    output_path: str,
    dedup_key: str = "episode_id",
) -> Optional[Tuple[str, int, int, float, int, int]]:
    """
    Appends new rows to an existing Parquet table, strictly deduplicating by dedup_key.
    If output_path does not exist, writes a new Snappy Parquet file.
    Returns: (filename, total_rows, ncols, size_mb, newly_added_rows, skipped_duplicates)
    """
    if not rows:
        return None

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    out_file = Path(output_path)

    if out_file.exists():
        try:
            existing_table = pq.read_table(str(out_file))
            existing_ids = set()
            if dedup_key in existing_table.column_names:
                existing_ids = {str(x) for x in existing_table[dedup_key].to_pylist()}

            filtered_rows = [r for r in rows if str(r.get(dedup_key, "")) not in existing_ids]
            skipped = len(rows) - len(filtered_rows)

            if not filtered_rows:
                file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
                return (out_file.name, len(existing_table), len(existing_table.schema), file_size_mb, 0, skipped)

            new_table = pa.Table.from_pylist(filtered_rows, schema=existing_table.schema)
            combined_table = pa.concat_tables([existing_table, new_table])
            pq.write_table(combined_table, output_path, compression="snappy")

            file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
            return (out_file.name, len(combined_table), len(combined_table.schema), file_size_mb, len(filtered_rows), skipped)
        except Exception as e:
            print(f" [!] Warning: Could not merge into {output_path} ({e}); writing fresh file...")

    table = pa.Table.from_pylist(rows)
    pq.write_table(table, output_path, compression="snappy")
    file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
    schema = pq.read_schema(output_path)
    return (out_file.name, len(rows), len(schema), file_size_mb, len(rows), 0)


write_table_to_parquet = merge_or_write_parquet


def discover_player_groups(input_dir: str) -> dict[str, list[str]]:
    """
    Discovers all JSON files grouped by player directory.
    Supports:
      - replay_downloader/downloads/kaggriculture (or ../replay_downloader/downloads/kaggriculture)
      - replay_downloader/downloads (auto drills down into kaggriculture subfolder if present)
      - inputs
    """
    input_path = Path(input_dir)
    if not input_path.exists():
        candidates = [
            Path("../replay_downloader/downloads/kaggriculture"),
            Path("../replay_downloader/downloads"),
            Path("../../replay_downloader/downloads/kaggriculture"),
            Path("../../replay_downloader/downloads"),
            Path("replay_downloader/downloads/kaggriculture"),
            Path("replay_downloader/downloads"),
            Path("inputs"),
            Path("input"),
        ]
        for c in candidates:
            if c.exists():
                input_path = c
                break
        else:
            return {}

    # If input_path has a 'kaggriculture' child directory with player folders, drill down
    if (input_path / "kaggriculture").is_dir():
        child_dirs = [d for d in (input_path / "kaggriculture").iterdir() if d.is_dir()]
        if child_dirs:
            input_path = input_path / "kaggriculture"

    groups: dict[str, list[str]] = defaultdict(list)
    valid_exts = {".json", ".jsonl", ".ndjson"}

    # 1. Subdirectories (player folders, e.g. '01_Mengfei Li', '02_自己找差距')
    subdirs = [d for d in input_path.iterdir() if d.is_dir()]
    for subdir in subdirs:
        sub_files = []
        for ext in ["*.json", "*.jsonl", "*.ndjson", "**/*.json", "**/*.jsonl", "**/*.ndjson"]:
            sub_files.extend([str(f) for f in subdir.glob(ext) if f.is_file()])
        sub_files = sorted(list(set(sub_files)))
        if sub_files:
            groups[subdir.name] = sub_files

    # 2. Root-level files (if any standalone replays)
    root_files = [str(f) for f in input_path.iterdir() if f.is_file() and f.suffix.lower() in valid_exts]
    if root_files:
        groups["player_main" if groups else "player_1"] = sorted(root_files)

    return dict(groups)


def extract_single_match_to_parquet(match_path_or_id: str, output_dir: str = "outputs") -> bool:
    """
    Extracts all moves, inventory, and details of the winning agent of ONE match into ONE Parquet file.
    """
    p = Path(match_path_or_id)
    if not p.exists():
        candidates = list(Path(".").rglob(f"*{match_path_or_id}*.json"))
        if candidates:
            p = candidates[0]
        else:
            print(f"[!] Replay match file not found for: {match_path_or_id}")
            return False

    print(f"\n▶ Extracting WINNING AGENT details for match: {p.name}")
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[!] Error loading {p}: {e}")
        return False

    ep_id = str(data.get("info", {}).get("EpisodeId", p.stem))
    win_rows = extract_winning_agent_details(data, p.name)
    if not win_rows:
        print("[!] No winning agent data could be extracted.")
        return False

    out_file = os.path.join(output_dir, f"{ep_id}_winning_agent.parquet")
    meta = write_table_to_parquet(win_rows, out_file)
    if meta:
        fname, nrows, ncols, sz_mb, *_ = meta
        print(f" [OK] Wrote WINNING AGENT Parquet: {out_file}")
        print(f"      {fname} -> {nrows:,} steps | {ncols} columns | {sz_mb:.3f} MB")
        return True
    return False


def run_parquet_conversion(
    input_dir: str = "../replay_downloader/downloads/kaggriculture",
    output_dir: str = "outputs",
    max_workers: Optional[int] = None,
    clean_downloads: bool = False,
) -> bool:
    """
    Executes the streamlined JSON to Parquet conversion:
      - outputs/shop_unlocked_sequence.parquet (ALL matches in ONE Parquet file)
      - outputs/<rank>_<name>_moves.parquet (flat per-player file, NO subfolders)
    """
    # Auto-resolve inputs vs downloads folder
    if not Path(input_dir).exists():
        candidates = [
            "../replay_downloader/downloads/kaggriculture",
            "../replay_downloader/downloads",
            "../../replay_downloader/downloads/kaggriculture",
            "../../replay_downloader/downloads",
            "replay_downloader/downloads/kaggriculture",
            "replay_downloader/downloads",
            "inputs",
            "input",
        ]
        for cand in candidates:
            if Path(cand).exists():
                input_dir = cand
                break

    player_groups = discover_player_groups(input_dir)
    total_files = sum(len(fl) for fl in player_groups.values())

    if not player_groups or total_files == 0:
        print(f"\n[!] No JSON simulation files found in '{input_dir}'.")
        print("    Please check the Downloader folder or place replay files in the input directory.")
        return False

    print("\n" + "=" * 80)
    print("        🚀 KAGGLE SIMULATION DATA -> PARQUET EXTRACTOR")
    print("=" * 80)
    print(f" • Input Directory : {os.path.abspath(input_dir)}")
    print(f" • Output Directory: {os.path.abspath(output_dir)}")
    print(f" • Detected Players: {len(player_groups)} ({', '.join(player_groups.keys())})")
    print(f" • Total JSON Files: {total_files:,}")
    print("=" * 80 + "\n")

    num_workers = max_workers or min(4, max(1, multiprocessing.cpu_count() - 1))
    start_time = time.time()

    all_shop_rows: list[dict[str, Any]] = []
    overall_written_files = 0
    total_output_mb = 0.0

    os.makedirs(output_dir, exist_ok=True)

    # Synchronize output file names if any player rank has changed ('do not delete anything')
    sync_output_file_ranks(output_dir, player_groups)

    # Process each player group directly into <rank>_<name>_moves.parquet using persistent worker pool
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        for group_name, files in player_groups.items():
            sanitized_group = sanitize_name(group_name)
            out_parquet_name = f"{sanitized_group}_moves.parquet"
            player_pq = os.path.join(output_dir, out_parquet_name)

            print(f"▶ Processing Player: '{group_name}' ({len(files)} matches)")
            player_win_rows: list[dict[str, Any]] = []

            futures = [executor.submit(extract_episode_essential_tables, fp) for fp in files]
            processed = 0
            for fut in as_completed(futures):
                res = fut.result()
                shop_rows = res.get("shop_unlocked_sequence", [])
                win_rows = res.get("winning_agent_details", [])

                if shop_rows:
                    all_shop_rows.extend(shop_rows)
                if win_rows:
                    player_win_rows.extend(win_rows)

                processed += 1
                pct = (processed / len(files)) * 100
                print(f"\r   Extracting: [{processed:,}/{len(files):,}] ({pct:5.1f}%)", end="", flush=True)

            print(f"\n   [OK] Player '{group_name}' records parsed.")

            # Write ONLY the player moves parquet file directly into output_dir (NO player subfolder)
            if player_win_rows:
                meta = write_table_to_parquet(player_win_rows, player_pq)
                if meta:
                    fname, nrows, ncols, sz_mb, *extra = meta
                    newly_added = extra[0] if len(extra) >= 1 else nrows
                    skipped = extra[1] if len(extra) >= 2 else 0
                    total_output_mb += sz_mb
                    overall_written_files += 1
                    stats_msg = f" (+{newly_added:,} new, {skipped:,} skipped)" if extra else ""
                    print(f"   💾 Saved Player Moves Parquet: {player_pq}")
                    print(f"      • {fname:<28} -> {nrows:>7,} steps | {ncols:>2} cols | {sz_mb:5.2f} MB{stats_msg}")
            print()

    # Write the SINGLE consolidated shop unlocked sequence file for all matches
    if all_shop_rows:
        shop_pq = os.path.join(output_dir, "shop_unlocked_sequence.parquet")
        meta = write_table_to_parquet(all_shop_rows, shop_pq)
        if meta:
            fname, nrows, ncols, sz_mb, *extra = meta
            newly_added = extra[0] if len(extra) >= 1 else nrows
            skipped = extra[1] if len(extra) >= 2 else 0
            total_output_mb += sz_mb
            overall_written_files += 1
            stats_msg = f" (+{newly_added:,} new, {skipped:,} skipped)" if extra else ""
            print("=" * 80)
            print(f"▶ 🛒 SHOP UNLOCKED SEQUENCE OF ALL MATCHES -> {shop_pq}")
            print(f"   • {fname:<28} -> {nrows:>8,} matches | {ncols:>2} cols | {sz_mb:6.2f} MB{stats_msg}")
            print("=" * 80)

        # Optional post-formatting cleanup to save disk space
    if clean_downloads and overall_written_files > 0:
        print("\n" + "-" * 80)
        print("PURGING PROCESSED RAW JSON REPLAYS TO RECLAIM DISK SPACE...")
        deleted_count = 0
        deleted_eids = []
        for g_name, fpaths in player_groups.items():
            for fp in fpaths:
                p = Path(fp)
                if p.exists():
                    try:
                        deleted_eids.append(p.stem)
                        p.unlink()
                        deleted_count += 1
                    except Exception:
                        pass
        print(f"   Reclaimed disk space! Deleted {deleted_count:,} raw JSON files.")
        # Mark as deleted in history
        for cand_db in [Path("../replay_downloader/download_history.db"), Path("replay_downloader/download_history.db"), Path("../../replay_downloader/download_history.db")]:
            if cand_db.exists():
                try:
                    from history import mark_files_deleted
                    mark_files_deleted(deleted_eids, db_path=cand_db)
                    break
                except Exception:
                    pass
        print("-" * 80)

    total_time = time.time() - start_time
    print("\n" + "=" * 80)
    print("                         📊 CONVERSION SUMMARY")
    print("=" * 80)
    print(f" • Total Players Converted      : {len(player_groups)}")
    print(f" • Total JSON Replays Parsed    : {total_files:,}")
    print(f" • Total Parquet Files Written  : {overall_written_files}")
    print(f" • Total Compressed Output Size : {total_output_mb:.2f} MB")
    print(f" • Time Elapsed                 : {total_time:.2f} seconds")
    print(f" • Shop Sequence File           : {os.path.abspath(os.path.join(output_dir, 'shop_unlocked_sequence.parquet'))}")
    print("=" * 80)
    print(" 🎉 SUCCESS! Extraction finished successfully.\n")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract Kaggle simulation JSON replays into clean, important Parquet tables."
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="../replay_downloader/downloads/kaggriculture",
        help="Input directory (default: ../replay_downloader/downloads/kaggriculture)",
    )
    parser.add_argument("-o", "--output", default="outputs", help="Output directory (default: outputs)")
    parser.add_argument("--match", default=None, help="Extract a single match replay file or episode ID into one parquet file")
    parser.add_argument("-w", "--workers", type=int, default=None, help="Number of worker processes (default: auto)")
    parser.add_argument("--clean-downloads", "--clean", action="store_true", help="Safely delete processed raw JSON files after successful Parquet formatting to reclaim disk space")
    args = parser.parse_args()

    if args.match:
        extract_single_match_to_parquet(args.match, output_dir=args.output)
    else:
        run_parquet_conversion(
            input_dir=args.input,
            output_dir=args.output,
            max_workers=args.workers,
            clean_downloads=args.clean_downloads,
        )
