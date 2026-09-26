#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Comprehensive Player-Wise Replay Analyzer & Text Report Generator.

Aggregates all 4 analysis pillars across player replay collections:
1. Economic & Wealth Accounting (field_ledger)
2. Opening Strategy & Action Breakdown (Extractor)
3. Game World & RNG Performance (field_worlds)
4. Route & Tactical Action Fingerprinting (route_catalog)
Plus advanced strategic indicators: Land expansion, Animal economics,
Financial risk, Labor movement efficiency, and Tactical archetypes.

Outputs a comprehensive, beginner-friendly .txt report per player.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import concurrent.futures
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

# Add repository root to path
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from field_ledger.reconstruct import StepRecord, reconstruct_match, team_names
from field_ledger.analytics import totals_by_category, match_summary
from route_catalog.fingerprint import canonical_action_string, significant_action_string, hash_strings
from strategy_decoder import (
    DecodedGame,
    extract_decoded_game,
    load_reference_routes,
    decode_script_identification,
    decode_route_selection,
    decode_script_overlays,
    decode_invariants,
    decode_field_and_labor,
    decode_selling_and_market,
    decode_opponent_interaction,
    decode_eras_and_outcomes,
    export_strategy_artifacts,
    render_extended_section_6,
    render_extended_section_9,
    render_section_11,
    render_section_12,
    render_section_13,
)

WINDOW_LEN = 72  # 3 days x 24 steps per shop-unlock window


def normalize_shop_name(raw: str) -> str:
    """Normalize full shop name to clean identifier."""
    raw = raw.strip().upper()
    mapping = {
        "PIZZA_SHOP": "PIZZA",
        "ICE_CREAM_SHOP": "ICE_CREAM",
        "SMOOTHIE_SHOP": "SMOOTHIE",
        "BAKERY": "BAKERY",
        "PET_CAFE": "PET_CAFE",
        "FARMERS_MARKET": "FARMERS_MARKET",
        "BRUNCH_SPOT": "BRUNCH_SPOT",
        "YARN_STORE": "YARN_STORE",
    }
    return mapping.get(raw, raw)


@dataclass
class MatchAnalysisResult:
    episode_id: str
    file_name: str
    player_name: str
    opponent_name: str
    player_index: int
    opponent_index: int
    player_reward: float
    opponent_reward: float
    result: str  # WIN, LOSS, TIE
    margin: float
    final_cash_player: float
    final_cash_opp: float
    revenue_by_cat: Dict[str, float]
    spend_by_cat: Dict[str, float]
    total_revenue: float
    total_spend: float
    total_hands_max: int
    avg_idle_hands: float
    total_idle_steps: int
    shop_sequence: List[str]
    opening_crops_planted: List[str]
    opening_animals_bought: List[str]
    opening_market_buys: List[str]
    opening_hands_hired: int
    opening_pastures_built: int
    route_hashes: List[Tuple[int, int, str, str]]  # (window_start, window_len, strict_hash, loose_hash)
    
    # Advanced Strategic Variables
    min_cash_dip: float
    p1_spend: float
    p1_rev: float
    p2_spend: float
    p2_rev: float
    p3_spend: float
    p3_rev: float
    growth_phase_avg_cash: float
    quadrant_unlock_steps: Dict[int, int]  # {2: step, 3: step, 4: step}
    final_quadrants_count: int
    tiles_crop_count: int
    tiles_livestock_count: int
    tiles_empty_count: int
    last_investment_step: int
    animal_spend_by_type: Dict[str, float]
    first_animal_step: Optional[int]
    hand_move_count: int
    hand_productive_count: int
    hands_day_3: int
    hands_day_10: int
    hands_day_20: int
    hands_day_30: int
    first_sale_step: Optional[int]
    sell_steps_count: int
    peak_revenue_day: int
    peak_revenue_amount: float


def determine_player_seat(replay: dict, target_player_name: str, folder_hint: str) -> int:
    """Finds which seat (0 or 1) corresponds to the target player in this replay."""
    names = replay.get("info", {}).get("TeamNames", [])
    if not names:
        return 0

    for i, name in enumerate(names):
        if name == target_player_name:
            return i

    cleaned_folder = folder_hint
    if "_" in folder_hint:
        cleaned_folder = folder_hint.split("_", 1)[1].strip()

    for i, name in enumerate(names):
        if name.lower() == cleaned_folder.lower():
            return i
        if cleaned_folder.lower() in name.lower() or name.lower() in cleaned_folder.lower():
            return i

    return 0


def analyze_single_replay(replay_path: Path, target_player_name: str, folder_hint: str) -> Tuple[Optional[MatchAnalysisResult], Optional[DecodedGame]]:
    """Processes a single replay JSON into a complete MatchAnalysisResult and DecodedGame."""
    try:
        content = replay_path.read_text(encoding="utf-8")
        replay = json.loads(content)
        if not isinstance(replay, dict):
            return None, None
    except Exception:
        return None, None

    steps = replay.get("steps", [])
    if not steps or len(steps) < 2:
        return None, None

    decoded_game = extract_decoded_game(replay, replay_path.name, target_player_name, folder_hint)

    n_players = len(steps[0])
    player_idx = determine_player_seat(replay, target_player_name, folder_hint)
    opp_idx = 1 - player_idx if n_players == 2 else (player_idx + 1) % n_players

    names = team_names(replay)
    p_name = names[player_idx] if player_idx < len(names) else f"player_{player_idx}"
    o_name = names[opp_idx] if opp_idx < len(names) else f"player_{opp_idx}"

    try:
        match_ledger = reconstruct_match(replay)
        p_records = match_ledger[player_idx]
        o_records = match_ledger[opp_idx]
    except Exception:
        return None, decoded_game

    summary = match_summary(p_records, o_records)
    p_reward = summary["final_projected_us"]
    o_reward = summary["final_projected_opp"]
    margin = p_reward - o_reward

    if p_reward > o_reward:
        res = "WIN"
    elif p_reward < o_reward:
        res = "LOSS"
    else:
        res = "TIE"

    rev_cat = totals_by_category(p_records, "revenue_by_category")
    spd_cat = totals_by_category(p_records, "spend_by_category")

    # Hands analysis
    total_hands_max = max(r.total_hands for r in p_records) if p_records else 0
    avg_idle_hands = sum(r.idle_hands for r in p_records) / len(p_records) if p_records else 0.0
    total_idle_steps = sum(1 for r in p_records if r.idle_hands > 0)

    # Shop Sequence (at final step)
    final_obs = steps[-1][player_idx].get("observation", {})
    town = final_obs.get("town", {})
    raw_shops = town.get("unlocked_shops", [])
    shop_seq = [normalize_shop_name(s) for s in raw_shops]

    # Opening Moves (First 72 turns / Days 1-3)
    opening_crops: List[str] = []
    opening_animals: List[str] = []
    opening_buys: List[str] = []
    opening_hired = 0
    opening_pastures = 0

    first_window_steps = min(72, len(steps) - 1)
    for t in range(first_window_steps):
        k = t + 1
        action = steps[k][player_idx].get("action", {})
        if not action:
            continue

        farmer_act = action.get("farmer")
        if isinstance(farmer_act, list) and farmer_act:
            cmd = str(farmer_act[0]).upper()
            if cmd == "PLANT" and len(farmer_act) > 1:
                opening_crops.append(str(farmer_act[1]).upper())
            elif cmd == "BUILD_PASTURE":
                opening_pastures += 1

        hands_act = action.get("hands")
        if isinstance(hands_act, list):
            for h in hands_act:
                if isinstance(h, list) and h:
                    cmd = str(h[0]).upper()
                    if cmd == "PLANT" and len(h) > 1:
                        opening_crops.append(str(h[1]).upper())

        market_orders = action.get("market")
        if isinstance(market_orders, list):
            for order in market_orders:
                if isinstance(order, list) and order:
                    mtype = str(order[0]).upper()
                    if mtype == "HIRE":
                        opening_hired += 1
                    elif mtype == "BUY_ANIMAL" and len(order) > 1:
                        qty = order[2] if len(order) > 2 else 1
                        opening_animals.append(f"{order[1]} (x{qty})")
                    elif mtype == "BUY_SEED" and len(order) > 1:
                        qty = order[2] if len(order) > 2 else 1
                        opening_buys.append(f"SEED:{order[1]} (x{qty})")
                    elif mtype == "BUY_PRODUCT" and len(order) > 1:
                        qty = order[2] if len(order) > 2 else 1
                        opening_buys.append(f"PRODUCT:{order[1]} (x{qty})")

    # Financial Phase Spends & Cash Min
    min_cash_dip = min(r.cash for r in p_records) if p_records else 0.0
    p1_spend = sum(sum(r.spend_by_category.values()) for r in p_records[:240])
    p1_rev = sum(sum(r.revenue_by_category.values()) for r in p_records[:240])
    p2_spend = sum(sum(r.spend_by_category.values()) for r in p_records[240:480])
    p2_rev = sum(sum(r.revenue_by_category.values()) for r in p_records[240:480])
    p3_spend = sum(sum(r.spend_by_category.values()) for r in p_records[480:])
    p3_rev = sum(sum(r.revenue_by_category.values()) for r in p_records[480:])

    # Cash held during growth phase (Days 1-20 / first 480 steps)
    growth_cash_list = [r.cash for r in p_records[:480]]
    growth_phase_avg_cash = sum(growth_cash_list) / len(growth_cash_list) if growth_cash_list else 0.0

    # Selling timing & cadence
    first_sale_step: Optional[int] = None
    sell_steps_count = 0
    daily_revenue: Dict[int, float] = defaultdict(float)

    for t, r in enumerate(p_records):
        rev_t = sum(r.revenue_by_category.values())
        if rev_t > 0:
            if first_sale_step is None:
                first_sale_step = t
            sell_steps_count += 1
            day_num = (t // 24) + 1
            daily_revenue[day_num] += rev_t

    peak_revenue_day = max(daily_revenue.items(), key=lambda x: x[1])[0] if daily_revenue else 1
    peak_revenue_amount = max(daily_revenue.values()) if daily_revenue else 0.0

    # Land & Quadrant Expansion tracking
    quadrant_unlock_steps: Dict[int, int] = {}
    prev_quad_count = 1
    final_quadrants_count = 1

    # Worker actions & movement efficiency
    hand_move_count = 0
    hand_productive_count = 0
    last_investment_step = 0
    first_animal_step = None
    animal_spend_by_type: Dict[str, float] = defaultdict(float)

    for k in range(1, len(steps)):
        t = k - 1
        p_step = steps[k][player_idx]
        obs = steps[t][player_idx].get("observation", {})
        farms = obs.get("farms", [])
        if player_idx < len(farms):
            quads = farms[player_idx].get("unlocked_quadrants", [])
            q_count = len(quads) if isinstance(quads, list) else 1
            if q_count > prev_quad_count:
                for q_num in range(prev_quad_count + 1, q_count + 1):
                    if q_num not in quadrant_unlock_steps:
                        quadrant_unlock_steps[q_num] = t
                prev_quad_count = q_count
            final_quadrants_count = max(final_quadrants_count, q_count)

        act = p_step.get("action", {})
        if not act:
            continue

        # Farmer action
        farmer_act = act.get("farmer")
        if isinstance(farmer_act, list) and farmer_act:
            cmd = str(farmer_act[0]).upper()
            if cmd in ("PLANT", "BUILD_PASTURE", "BUILD_COOP"):
                last_investment_step = t

        # Hands action
        hands_act = act.get("hands", [])
        if isinstance(hands_act, list):
            for h in hands_act:
                if isinstance(h, list) and h:
                    cmd = str(h[0]).upper()
                    if cmd in ("NORTH", "SOUTH", "EAST", "WEST", "MOVE"):
                        hand_move_count += 1
                    elif cmd not in ("PASS",):
                        hand_productive_count += 1
                    if cmd in ("PLANT", "BUILD_PASTURE", "BUILD_COOP"):
                        last_investment_step = t

        # Market action
        market_act = act.get("market", [])
        if isinstance(market_act, list):
            for m in market_act:
                if isinstance(m, list) and m:
                    mcmd = str(m[0]).upper()
                    if mcmd in ("BUY_SEED", "BUY_ANIMAL", "BUY_LAND"):
                        last_investment_step = t
                    if mcmd == "BUY_ANIMAL" and len(m) > 1:
                        if first_animal_step is None:
                            first_animal_step = t
                        anim = str(m[1]).upper()
                        qty = m[2] if len(m) > 2 else 1
                        cost_est = 400.0 if anim == "COW" else (250.0 if anim in ("SHEEP", "GOOSE") else 200.0)
                        animal_spend_by_type[anim] += cost_est * qty

    # Tile Allocation snapshot (at step 500 or final step)
    snapshot_step = min(500, len(steps) - 1)
    tiles_crop_count = 0
    tiles_livestock_count = 0
    tiles_empty_count = 0
    farms_snap = steps[snapshot_step][player_idx].get("observation", {}).get("farms", [])
    if player_idx < len(farms_snap):
        tiles_grid = farms_snap[player_idx].get("tiles", [])
        for row in tiles_grid:
            for cell in row:
                if isinstance(cell, dict):
                    kind = str(cell.get("kind") or cell.get("type", "")).upper()
                    if "PLANT" in kind:
                        tiles_crop_count += 1
                    elif "PASTURE" in kind or "COOP" in kind:
                        tiles_livestock_count += 1
                    elif kind in ("DIRT", "EMPTY", "NONE"):
                        tiles_empty_count += 1
                    else:
                        tiles_empty_count += 1

    # Hand counts at milestone days
    def get_hands_count_at_step(target_step: int) -> int:
        idx = min(target_step, len(steps) - 1)
        farms_at_idx = steps[idx][player_idx].get("observation", {}).get("farms", [])
        if player_idx < len(farms_at_idx):
            return len(farms_at_idx[player_idx].get("hands", []))
        return 0

    hands_day_3 = get_hands_count_at_step(71)
    hands_day_10 = get_hands_count_at_step(239)
    hands_day_20 = get_hands_count_at_step(479)
    hands_day_30 = get_hands_count_at_step(719)

    # Route Candidate Slicing across all window lengths (up to 720 steps / 10 windows)
    canonical_per_step = []
    loose_per_step = []
    for k in range(1, len(steps)):
        act = steps[k][player_idx].get("action", {})
        canonical_per_step.append(canonical_action_string(act))
        loose_per_step.append(significant_action_string(act))

    n_steps = len(steps)
    n_windows = n_steps // WINDOW_LEN
    route_hashes: List[Tuple[int, int, str, str]] = []
    for start_w in range(n_windows):
        start_step = start_w * WINDOW_LEN
        for length_w in range(1, n_windows - start_w + 1):
            end_step = start_step + length_w * WINDOW_LEN
            c_hash = hash_strings(canonical_per_step[start_step:end_step])
            l_hash = hash_strings(loose_per_step[start_step:end_step])
            route_hashes.append((start_w, length_w, c_hash, l_hash))

    ep_id = str(replay.get("id") or replay_path.stem)

    analysis_res = MatchAnalysisResult(
        episode_id=ep_id,
        file_name=replay_path.name,
        player_name=p_name,
        opponent_name=o_name,
        player_index=player_idx,
        opponent_index=opp_idx,
        player_reward=p_reward,
        opponent_reward=o_reward,
        result=res,
        margin=margin,
        final_cash_player=summary["final_cash_us"],
        final_cash_opp=summary["final_cash_opp"],
        revenue_by_cat=rev_cat,
        spend_by_cat=spd_cat,
        total_revenue=summary["total_revenue_us"],
        total_spend=summary["total_spend_us"],
        total_hands_max=total_hands_max,
        avg_idle_hands=avg_idle_hands,
        total_idle_steps=total_idle_steps,
        shop_sequence=shop_seq,
        opening_crops_planted=opening_crops,
        opening_animals_bought=opening_animals,
        opening_market_buys=opening_buys,
        opening_hands_hired=opening_hired,
        opening_pastures_built=opening_pastures,
        route_hashes=route_hashes,
        min_cash_dip=min_cash_dip,
        p1_spend=p1_spend,
        p1_rev=p1_rev,
        p2_spend=p2_spend,
        p2_rev=p2_rev,
        p3_spend=p3_spend,
        p3_rev=p3_rev,
        growth_phase_avg_cash=growth_phase_avg_cash,
        quadrant_unlock_steps=quadrant_unlock_steps,
        final_quadrants_count=final_quadrants_count,
        tiles_crop_count=tiles_crop_count,
        tiles_livestock_count=tiles_livestock_count,
        tiles_empty_count=tiles_empty_count,
        last_investment_step=last_investment_step,
        animal_spend_by_type=dict(animal_spend_by_type),
        first_animal_step=first_animal_step,
        hand_move_count=hand_move_count,
        hand_productive_count=hand_productive_count,
        hands_day_3=hands_day_3,
        hands_day_10=hands_day_10,
        hands_day_20=hands_day_20,
        hands_day_30=hands_day_30,
        first_sale_step=first_sale_step,
        sell_steps_count=sell_steps_count,
        peak_revenue_day=peak_revenue_day,
        peak_revenue_amount=peak_revenue_amount,
    )
    return analysis_res, decoded_game


def process_player_folder(folder_path: Path, max_workers: int = 4) -> Tuple[str, List[MatchAnalysisResult], List[DecodedGame]]:
    """Analyzes all replay JSON files in a player folder."""
    replays = sorted([
        f for f in folder_path.glob("*.json")
        if not f.name.startswith(f"{folder_path.name}_") and not f.name.endswith(("_clusters.json", "_investments.json"))
    ])
    if not replays:
        return folder_path.name, [], []

    folder_name = folder_path.name
    player_name = folder_name.split("_", 1)[1] if "_" in folder_name else folder_name

    results: List[MatchAnalysisResult] = []
    decoded_games: List[DecodedGame] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(analyze_single_replay, rep_path, player_name, folder_name): rep_path
            for rep_path in replays
        }
        for future in concurrent.futures.as_completed(futures):
            res, dgame = future.result()
            if res:
                results.append(res)
            if dgame:
                decoded_games.append(dgame)

    results.sort(key=lambda r: r.episode_id)
    decoded_games.sort(key=lambda g: g.episode_id)
    return folder_name, results, decoded_games


def format_player_text_report(
    folder_name: str,
    results: List[MatchAnalysisResult],
    decoded_games: Optional[List[DecodedGame]] = None,
    out_dir: Optional[Path] = None,
) -> str:
    """Renders a comprehensive, beautifully structured ASCII text report."""
    if not results:
        return f"===============================================================================\nPLAYER ANALYSIS REPORT: {folder_name}\n===============================================================================\nNo valid match replays found in folder.\n"

    player_name = results[0].player_name or (folder_name.split("_", 1)[1] if "_" in folder_name else folder_name)
    total_matches = len(results)

    # Strategy Decoder Execution
    script_res = None
    route_res = None
    overlay_res = None
    inv_res = None
    field_res = None
    selling_res = None
    opp_res = None
    era_res = None

    if decoded_games:
        ref_routes, ref_msg = load_reference_routes()
        script_res = decode_script_identification(decoded_games, ref_routes)
        route_res = decode_route_selection(decoded_games, script_res)
        overlay_res = decode_script_overlays(decoded_games, script_res)
        inv_res = decode_invariants(decoded_games, script_res)
        field_res = decode_field_and_labor(decoded_games)
        selling_res = decode_selling_and_market(decoded_games, script_res)
        opp_res = decode_opponent_interaction(decoded_games)
        era_res = decode_eras_and_outcomes(decoded_games, script_res)

        if out_dir:
            export_strategy_artifacts(
                out_dir=out_dir,
                player_prefix=folder_name,
                script_res=script_res,
                route_res=route_res,
                overlay_res=overlay_res,
                selling_res=selling_res,
                field_res=field_res,
                opp_res=opp_res,
                era_res=era_res,
                inv_res=inv_res,
            )

    # 1. Match Win / Loss Statistics
    wins = sum(1 for r in results if r.result == "WIN")
    losses = sum(1 for r in results if r.result == "LOSS")
    ties = sum(1 for r in results if r.result == "TIE")
    win_rate = (wins / total_matches) * 100.0 if total_matches > 0 else 0.0

    avg_player_score = sum(r.player_reward for r in results) / total_matches
    avg_opp_score = sum(r.opponent_reward for r in results) / total_matches
    avg_margin = sum(r.margin for r in results) / total_matches
    max_score = max(r.player_reward for r in results)
    min_score = min(r.player_reward for r in results)

    # 2. Financial & Economic Performance
    avg_cash = sum(r.final_cash_player for r in results) / total_matches
    avg_revenue = sum(r.total_revenue for r in results) / total_matches
    avg_spend = sum(r.total_spend for r in results) / total_matches
    avg_profit = avg_revenue - avg_spend

    all_spend_cats: Dict[str, float] = defaultdict(float)
    for r in results:
        for cat, val in r.spend_by_cat.items():
            all_spend_cats[cat] += val

    all_rev_cats: Dict[str, float] = defaultdict(float)
    for r in results:
        for cat, val in r.revenue_by_cat.items():
            all_rev_cats[cat] += val

    # Financial Risk & Phase Spends
    avg_min_cash = sum(r.min_cash_dip for r in results) / total_matches
    avg_growth_cash = sum(r.growth_phase_avg_cash for r in results) / total_matches
    avg_p1_spend = sum(r.p1_spend for r in results) / total_matches
    avg_p1_rev = sum(r.p1_rev for r in results) / total_matches
    avg_p2_spend = sum(r.p2_spend for r in results) / total_matches
    avg_p2_rev = sum(r.p2_rev for r in results) / total_matches
    avg_p3_spend = sum(r.p3_spend for r in results) / total_matches
    avg_p3_rev = sum(r.p3_rev for r in results) / total_matches

    p1_reinvest_pct = (avg_p1_spend / (avg_p1_rev + 3000.0) * 100.0)  # starting money $3000
    p2_reinvest_pct = (avg_p2_spend / avg_p2_rev * 100.0) if avg_p2_rev > 0 else 0.0
    p3_reinvest_pct = (avg_p3_spend / avg_p3_rev * 100.0) if avg_p3_rev > 0 else 0.0

    # Land & Quadrants
    avg_final_quads = sum(r.final_quadrants_count for r in results) / total_matches
    quad2_steps = [r.quadrant_unlock_steps[2] for r in results if 2 in r.quadrant_unlock_steps]
    quad3_steps = [r.quadrant_unlock_steps[3] for r in results if 3 in r.quadrant_unlock_steps]
    quad4_steps = [r.quadrant_unlock_steps[4] for r in results if 4 in r.quadrant_unlock_steps]

    avg_quad2_turn = (sum(quad2_steps) / len(quad2_steps)) if quad2_steps else None
    avg_quad3_turn = (sum(quad3_steps) / len(quad3_steps)) if quad3_steps else None
    avg_quad4_turn = (sum(quad4_steps) / len(quad4_steps)) if quad4_steps else None

    avg_crop_tiles = sum(r.tiles_crop_count for r in results) / total_matches
    avg_livestock_tiles = sum(r.tiles_livestock_count for r in results) / total_matches
    avg_empty_tiles = sum(r.tiles_empty_count for r in results) / total_matches
    total_active_tiles = avg_crop_tiles + avg_livestock_tiles + avg_empty_tiles
    crop_tile_pct = (avg_crop_tiles / total_active_tiles * 100.0) if total_active_tiles > 0 else 0.0
    livestock_tile_pct = (avg_livestock_tiles / total_active_tiles * 100.0) if total_active_tiles > 0 else 0.0
    empty_tile_pct = (avg_empty_tiles / total_active_tiles * 100.0) if total_active_tiles > 0 else 0.0

    # Market Timing, Selling Cadence & Liquidation
    avg_last_invest_turn = sum(r.last_investment_step for r in results) / total_matches
    avg_liquidation_day = (avg_last_invest_turn / 24.0) + 1.0

    first_sale_steps = [r.first_sale_step for r in results if r.first_sale_step is not None]
    avg_first_sale_turn = (sum(first_sale_steps) / len(first_sale_steps)) if first_sale_steps else None
    avg_first_sale_day = (avg_first_sale_turn / 24.0 + 1.0) if avg_first_sale_turn is not None else None

    avg_sell_turns = sum(r.sell_steps_count for r in results) / total_matches
    sell_turn_pct = (avg_sell_turns / 720.0) * 100.0
    avg_rev_per_sale_turn = (avg_revenue / avg_sell_turns) if avg_sell_turns > 0 else 0.0

    if avg_sell_turns > 120:
        cadence_style = "CONTINUOUS SELLER (Sells frequently in small batches as crops are picked)"
    elif avg_sell_turns <= 45:
        cadence_style = "BATCH HOARDER (Stores goods in shed and executes large periodic bulk dumps)"
    else:
        cadence_style = "BALANCED CADENCE (Periodic batch sales every few days)"

    p1_rev_share = (avg_p1_rev / avg_revenue * 100.0) if avg_revenue > 0 else 0.0
    p2_rev_share = (avg_p2_rev / avg_revenue * 100.0) if avg_revenue > 0 else 0.0
    p3_rev_share = (avg_p3_rev / avg_revenue * 100.0) if avg_revenue > 0 else 0.0

    avg_peak_rev_day = sum(r.peak_revenue_day for r in results) / total_matches
    avg_peak_rev_amount = sum(r.peak_revenue_amount for r in results) / total_matches

    # Animal & Livestock Economics
    animal_prods = {"MILK", "WOOL", "EGG", "FERTILIZER"}
    animal_rev_total = sum(all_rev_cats.get(p, 0.0) for p in animal_prods)
    crop_rev_total = sum(v for k, v in all_rev_cats.items() if k not in animal_prods)
    total_goods_rev = animal_rev_total + crop_rev_total
    animal_rev_pct = (animal_rev_total / total_goods_rev * 100.0) if total_goods_rev > 0 else 0.0
    crop_rev_pct = (crop_rev_total / total_goods_rev * 100.0) if total_goods_rev > 0 else 0.0

    animal_spend_agg: Dict[str, float] = defaultdict(float)
    for r in results:
        for a, cost in r.animal_spend_by_type.items():
            animal_spend_agg[a] += cost

    first_animal_steps = [r.first_animal_step for r in results if r.first_animal_step is not None]
    avg_first_animal_step = (sum(first_animal_steps) / len(first_animal_steps)) if first_animal_steps else None

    # Labor Efficiency & Movement Pathfinding
    avg_max_hands = sum(r.total_hands_max for r in results) / total_matches
    avg_idle_hands = sum(r.avg_idle_hands for r in results) / total_matches
    avg_idle_steps = sum(r.total_idle_steps for r in results) / total_matches
    avg_idle_percent = (avg_idle_steps / 720.0) * 100.0

    total_hand_moves = sum(r.hand_move_count for r in results)
    total_hand_prod = sum(r.hand_productive_count for r in results)
    total_hand_acts = total_hand_moves + total_hand_prod
    pathfinding_waste_pct = (total_hand_moves / total_hand_acts * 100.0) if total_hand_acts > 0 else 0.0

    avg_hands_d3 = sum(r.hands_day_3 for r in results) / total_matches
    avg_hands_d10 = sum(r.hands_day_10 for r in results) / total_matches
    avg_hands_d20 = sum(r.hands_day_20 for r in results) / total_matches
    avg_hands_d30 = sum(r.hands_day_30 for r in results) / total_matches
    worker_density = (avg_max_hands / (avg_final_quads * 25.0)) if avg_final_quads > 0 else 0.0

    # Archetype Classifier
    if animal_rev_pct > 50.0:
        archetype = "RANCHING & LIVESTOCK HEAVY (Dominant revenue from Cows/Pastures/Wool/Milk)"
    else:
        crop_revenues = {k: v for k, v in all_rev_cats.items() if k not in animal_prods}
        top_crop, top_crop_val = max(crop_revenues.items(), key=lambda x: x[1]) if crop_revenues else ("NONE", 0.0)
        top_crop_pct = (top_crop_val / crop_rev_total * 100.0) if crop_rev_total > 0 else 0.0
        if top_crop_pct > 65.0:
            archetype = f"MONOCULTURE RUSHER ({top_crop} Dominant — {top_crop_pct:.1f}% of crop income)"
        elif len([k for k, v in crop_revenues.items() if v / crop_rev_total > 0.15]) >= 3:
            archetype = "DIVERSIFIED MULTI-SHOP TRADER (Balanced portfolio matching varied shop unlocks)"
        else:
            archetype = "BALANCED AGRO-INDUSTRIAL (Mixed crop rotation with selective animal support)"

    # Opening Strategy (Extractor)
    first_crops_count = Counter()
    for r in results:
        if r.opening_crops_planted:
            first_crops_count[r.opening_crops_planted[0]] += 1
        else:
            first_crops_count["NONE (Animals/Other)"] += 1

    all_opening_animals = Counter()
    for r in results:
        for a in r.opening_animals_bought:
            all_opening_animals[a] += 1

    all_opening_buys = Counter()
    for r in results:
        for b in r.opening_market_buys:
            all_opening_buys[b] += 1

    early_hires = Counter()
    for r in results:
        early_hires[r.opening_hands_hired] += 1

    early_pastures = Counter()
    for r in results:
        early_pastures[r.opening_pastures_built] += 1

    # Game World & RNG Analysis (field_worlds)
    world_k2_count = Counter()
    world_k2_wins = Counter()
    world_k3_count = Counter()
    world_k3_wins = Counter()

    for r in results:
        if len(r.shop_sequence) >= 2:
            w2 = f"{r.shop_sequence[0]} -> {r.shop_sequence[1]}"
            world_k2_count[w2] += 1
            if r.result == "WIN":
                world_k2_wins[w2] += 1
        if len(r.shop_sequence) >= 3:
            w3 = f"{r.shop_sequence[0]} -> {r.shop_sequence[1]} -> {r.shop_sequence[2]}"
            world_k3_count[w3] += 1
            if r.result == "WIN":
                world_k3_wins[w3] += 1

    # Route Catalog & Multi-Horizon Scripting Analysis (route_catalog)
    strict_by_horizon: Dict[Tuple[int, int], Counter] = defaultdict(Counter)
    loose_by_horizon: Dict[Tuple[int, int], Counter] = defaultdict(Counter)

    for r in results:
        for start_w, len_w, c_hash, l_hash in r.route_hashes:
            strict_by_horizon[(start_w, len_w)][c_hash] += 1
            loose_by_horizon[(start_w, len_w)][l_hash] += 1

    longest_recurring_turns = 0
    longest_recurring_instances = 0
    longest_recurring_pos = (0, 0)
    for (start_w, len_w), counts in strict_by_horizon.items():
        for chash, occ in counts.items():
            if occ >= 2:
                turns = len_w * WINDOW_LEN
                if turns > longest_recurring_turns:
                    longest_recurring_turns = turns
                    longest_recurring_instances = occ
                    longest_recurring_pos = (start_w, len_w)

    key_horizons = [
        (1, "Days 1-3   ( 72 turns / 1 window ) - Pre-Shop Opening"),
        (2, "Days 1-6   (144 turns / 2 windows) - Shop 1 Unlocked"),
        (3, "Days 1-9   (216 turns / 3 windows) - Shop 2 Unlocked"),
        (5, "Days 1-15  (360 turns / 5 windows) - Mid-Match Halfway"),
        (10, "Days 1-30  (720 turns / 10 win)    - Full Match Playbook"),
    ]

    w0_strict = strict_by_horizon[(0, 1)]
    w0_most_common_strict = w0_strict.most_common(1)[0][1] if w0_strict else 0
    w0_strict_consistency_pct = (w0_most_common_strict / total_matches) * 100.0 if total_matches > 0 else 0.0

    w0_loose = loose_by_horizon[(0, 1)]
    w0_most_common_loose = w0_loose.most_common(1)[0][1] if w0_loose else 0
    w0_loose_consistency_pct = (w0_most_common_loose / total_matches) * 100.0 if total_matches > 0 else 0.0

    full_strict = strict_by_horizon[(0, 10)]
    full_top_occ = full_strict.most_common(1)[0][1] if full_strict else 0

    if full_top_occ >= 2 or longest_recurring_turns >= 504:
        bot_style = "PRE-COMPILED END-TO-END PLAYBOOKS (Executes fixed long-horizon / 720-step trees)"
    elif w0_strict_consistency_pct > 70:
        bot_style = "RADIX ROUTE TREE (Fixed Day 1 script, branches adaptively as shops unlock)"
    elif w0_loose_consistency_pct > 60:
        bot_style = "STRATEGICALLY CONSISTENT (Follows shared macro tactics with localized coordinate adjustments)"
    else:
        bot_style = "HIGHLY ADAPTIVE / REACTIVE (Generates dynamic moves on the fly)"

    # Build ASCII report
    lines: List[str] = []
    lines.append("=" * 80)
    lines.append(f"  KAGGRICULTURE PLAYER STRATEGY & PERFORMANCE REPORT: {player_name}")
    lines.append(f"  Directory: {folder_name} | Matches Analyzed: {total_matches}")
    lines.append("=" * 80)
    lines.append("")

    # Section 1: Executive Summary
    lines.append("-" * 80)
    lines.append("1. EXECUTIVE SUMMARY & WIN/LOSS RECORD")
    lines.append("-" * 80)
    lines.append(f"  * Total Matches:     {total_matches}")
    lines.append(f"  * Record:            {wins} Wins / {losses} Losses / {ties} Ties")
    lines.append(f"  * Win Rate:          {win_rate:.1f}%")
    lines.append(f"  * Avg Player Score:  ${avg_player_score:,.2f}  (Opponent Avg: ${avg_opp_score:,.2f})")
    lines.append(f"  * Avg Win Margin:    {'+' if avg_margin>=0 else ''}${avg_margin:,.2f}")
    lines.append(f"  * Score Range:       Min ${min_score:,.2f} | Max ${max_score:,.2f}")
    lines.append(f"  * Strategic Profile: {archetype}")
    lines.append("")

    # Section 2: Financial & Economics
    lines.append("-" * 80)
    lines.append("2. FINANCIAL & ECONOMIC PERFORMANCE (field_ledger)")
    lines.append("-" * 80)
    lines.append(f"  * Average Final Cash in Hand:  ${avg_cash:,.2f}")
    lines.append(f"  * Average Gross Revenue:       ${avg_revenue:,.2f}")
    lines.append(f"  * Average Total Spending:      ${avg_spend:,.2f}")
    lines.append(f"  * Average Net Farm Profit:     ${avg_profit:,.2f}")
    lines.append("")
    lines.append("  [Financial Risk & Budget Management]")
    lines.append(f"    - Minimum Cash Dip (Bankruptcy Margin):  ${avg_min_cash:,.2f}")
    lines.append(f"    - Cash Idle Drag (Avg Cash in Days 1-20): ${avg_growth_cash:,.2f}")
    lines.append(f"    - Phase 1 Reinvestment (Days 1-10)     : ${avg_p1_spend:,.0f} spend vs ${avg_p1_rev:,.0f} rev ({p1_reinvest_pct:.1f}% rate)")
    lines.append(f"    - Phase 2 Reinvestment (Days 11-20)    : ${avg_p2_spend:,.0f} spend vs ${avg_p2_rev:,.0f} rev ({p2_reinvest_pct:.1f}% rate)")
    lines.append(f"    - Phase 3 Reinvestment (Days 21-30)    : ${avg_p3_spend:,.0f} spend vs ${avg_p3_rev:,.0f} rev ({p3_reinvest_pct:.1f}% rate)")
    lines.append("")
    lines.append("  [Spending Breakdown by Category (Avg per Match)]")
    if all_spend_cats:
        for cat, total_val in sorted(all_spend_cats.items(), key=lambda x: x[1], reverse=True):
            avg_val = total_val / total_matches
            pct = (avg_val / avg_spend * 100.0) if avg_spend > 0 else 0.0
            lines.append(f"    - {cat:<22}: ${avg_val:>10,.2f} ({pct:>5.1f}%)")
    else:
        lines.append("    (No spending recorded)")

    lines.append("")
    lines.append("  [Revenue Breakdown by Category (Avg per Match)]")
    if all_rev_cats:
        for cat, total_val in sorted(all_rev_cats.items(), key=lambda x: x[1], reverse=True):
            avg_val = total_val / total_matches
            pct = (avg_val / avg_revenue * 100.0) if avg_revenue > 0 else 0.0
            lines.append(f"    - {cat:<22}: ${avg_val:>10,.2f} ({pct:>5.1f}%)")
    else:
        lines.append("    (No revenue recorded)")
    lines.append("")

    # Section 3: Land & Tile Expansion
    lines.append("-" * 80)
    lines.append("3. LAND & TILE EXPANSION STRATEGY")
    lines.append("-" * 80)
    lines.append(f"  * Average Unlocked Quadrants:    {avg_final_quads:.1f} / 4 plots ({avg_final_quads*25:.0f} / 100 tiles)")
    lines.append(
        f"  * Expansion Velocity:            "
        f"Plot 2: {f'Turn {avg_quad2_turn:.0f} (Day {avg_quad2_turn/24+1:.1f})' if avg_quad2_turn else 'Not Bought'} | "
        f"Plot 3: {f'Turn {avg_quad3_turn:.0f} (Day {avg_quad3_turn/24+1:.1f})' if avg_quad3_turn else 'Not Bought'} | "
        f"Plot 4: {f'Turn {avg_quad4_turn:.0f} (Day {avg_quad4_turn/24+1:.1f})' if avg_quad4_turn else 'Not Bought'}"
    )
    lines.append(
        f"  * Land Allocation Ratio:         "
        f"Crops: {crop_tile_pct:.1f}% ({avg_crop_tiles:.1f} tiles) | "
        f"Pasture/Coop: {livestock_tile_pct:.1f}% ({avg_livestock_tiles:.1f} tiles) | "
        f"Empty/Idle: {empty_tile_pct:.1f}% ({avg_empty_tiles:.1f} tiles)"
    )
    lines.append("")

    # Section 4: Market Timing & Liquidation
    lines.append("-" * 80)
    lines.append("4. MARKET TIMING & LIQUIDATION DISCIPLINE")
    lines.append("-" * 80)
    lines.append(f"  * First Sale Milestone:          {f'Turn {avg_first_sale_turn:.0f} (Day {avg_first_sale_day:.1f})' if avg_first_sale_turn is not None else 'No Sales'}")
    lines.append(f"  * Selling Cadence & Style:       {avg_sell_turns:.1f} selling turns ({sell_turn_pct:.1f}% of match) | Avg ${avg_rev_per_sale_turn:,.2f} per selling turn")
    lines.append(f"                                   -> {cadence_style}")
    lines.append(
        f"  * Phase-by-Phase Sales Revenue:  "
        f"Days 1-10: ${avg_p1_rev:,.0f} ({p1_rev_share:.1f}%) | "
        f"Days 11-20: ${avg_p2_rev:,.0f} ({p2_rev_share:.1f}%) | "
        f"Days 21-30: ${avg_p3_rev:,.0f} ({p3_rev_share:.1f}%)"
    )
    lines.append(f"  * Peak Revenue Day:              Day {avg_peak_rev_day:.1f} (Avg ${avg_peak_rev_amount:,.2f} liquidated on peak day)")
    lines.append(f"  * Final Investment/Planting Turn: Turn {avg_last_invest_turn:.0f} (Day {avg_liquidation_day:.1f})")
    lines.append(f"  * Harvest-to-Cash Liquidation Window: {720 - avg_last_invest_turn:.0f} turns ({30.0 - avg_liquidation_day:.1f} days before match end)")
    lines.append("")

    # Section 5: Animal & Livestock Economics
    lines.append("-" * 80)
    lines.append("5. ANIMAL & LIVESTOCK ECONOMICS")
    lines.append("-" * 80)
    lines.append(f"  * Livestock Adoption Milestone:   {f'Turn {avg_first_animal_step:.0f} (Day {avg_first_animal_step/24+1:.1f})' if avg_first_animal_step else 'No Animals Raised'}")
    lines.append(f"  * Animal vs Crop Income Share:    Animal Products: {animal_rev_pct:.1f}% (${animal_rev_total/total_matches:,.2f}) vs Crops: {crop_rev_pct:.1f}% (${crop_rev_total/total_matches:,.2f})")
    lines.append("  [Livestock Purchasing Breakdown (Avg Spend per Match)]")
    if animal_spend_agg:
        for anim, total_c in sorted(animal_spend_agg.items(), key=lambda x: x[1], reverse=True):
            avg_c = total_c / total_matches
            lines.append(f"    - {anim:<22}: ${avg_c:>10,.2f}")
    else:
        lines.append("    (No animal purchases recorded)")
    lines.append("")

    # Section 6: Labor Efficiency & Pathfinding
    lines.append("-" * 80)
    lines.append("6. LABOR EFFICIENCY & WORKER COORDINATION")
    lines.append("-" * 80)
    lines.append(f"  * Peak Active Farmhands (Max):    {avg_max_hands:.1f} hands (Worker Density: {worker_density:.2f} hands/tile)")
    lines.append(f"  * Average Idle Hands per Turn:    {avg_idle_hands:.2f} hands")
    lines.append(f"  * Turns with Idle Workers:        {avg_idle_steps:.1f} / 720 turns ({avg_idle_percent:.1f}%)")
    lines.append(f"  * Pathfinding Transit Waste:      {pathfinding_waste_pct:.1f}% of actions moving vs {100-pathfinding_waste_pct:.1f}% productive tasks")
    lines.append(
        f"  * Hiring Ramp-Up Curve:           "
        f"Day 3: {avg_hands_d3:.1f} hands -> "
        f"Day 10: {avg_hands_d10:.1f} hands -> "
        f"Day 20: {avg_hands_d20:.1f} hands -> "
        f"Day 30: {avg_hands_d30:.1f} hands"
    )
    lines.append("")
    if field_res:
        lines.extend(render_extended_section_6(field_res))

    # Section 7: Opening Strategy (Extractor)
    lines.append("-" * 80)
    lines.append("7. OPENING STRATEGY & EARLY GAME CHOICES (Extractor)")
    lines.append("-" * 80)
    lines.append("  [Day 1 First Crop Planted]")
    for crop, count in first_crops_count.most_common(5):
        pct = (count / total_matches) * 100.0
        bar = "#" * max(1, int(pct / 4))
        lines.append(f"    - {crop:<22}: {count:>3} matches ({pct:>5.1f}%) | {bar}")

    lines.append("")
    lines.append("  [Early Animal Purchases (First 72 Turns)]")
    if all_opening_animals:
        for animal, count in all_opening_animals.most_common(6):
            lines.append(f"    - {animal:<22}: bought in {count} instances")
    else:
        lines.append("    (No early animal purchases)")

    lines.append("")
    lines.append("  [Early Market Purchases (Seeds / Products)]")
    if all_opening_buys:
        for buy_item, count in all_opening_buys.most_common(6):
            lines.append(f"    - {buy_item:<22}: bought in {count} instances")
    else:
        lines.append("    (No early seed/product buys)")

    lines.append("")
    lines.append("  [Early Farmhand Hiring (First 72 Turns)]")
    for hires, count in sorted(early_hires.items()):
        pct = (count / total_matches) * 100.0
        lines.append(f"    - Hired {hires} workers: {count:>3} matches ({pct:>5.1f}%)")

    lines.append("")
    lines.append("  [Early Pastures Built (First 72 Turns)]")
    for pastures, count in sorted(early_pastures.items()):
        pct = (count / total_matches) * 100.0
        lines.append(f"    - Built {pastures} pastures: {count:>3} matches ({pct:>5.1f}%)")
    lines.append("")

    # Section 8: Game Worlds (field_worlds)
    lines.append("-" * 80)
    lines.append("8. GAME WORLD & RNG FREQUENCIES (field_worlds)")
    lines.append("-" * 80)
    lines.append("  [Top 2-Shop Opening Sequences (k=2 Worlds)]")
    lines.append("  " + f"{'World (Shop 1 -> Shop 2)':<40} {'Matches':<10} {'Win Rate':<10}")
    lines.append("  " + "-" * 62)
    for w2, count in world_k2_count.most_common(10):
        w_wins = world_k2_wins[w2]
        w_pct = (w_wins / count) * 100.0
        lines.append(f"  {w2:<40} {count:>7}    {w_pct:>6.1f}% ({w_wins}/{count})")

    lines.append("")
    lines.append("  [Top 3-Shop Sequences (k=3 Worlds)]")
    lines.append("  " + f"{'World (Shop 1 -> Shop 2 -> Shop 3)':<50} {'Matches':<10} {'Win Rate':<10}")
    lines.append("  " + "-" * 72)
    for w3, count in world_k3_count.most_common(8):
        w_wins = world_k3_wins[w3]
        w_pct = (w_wins / count) * 100.0
        lines.append(f"  {w3:<50} {count:>7}    {w_pct:>6.1f}% ({w_wins}/{count})")
    lines.append("")

    # Section 9: Route & Scripting Patterns (route_catalog & strategy_decoder)
    lines.append("-" * 80)
    lines.append("9. ROUTE & SCRIPTING PATTERNS (route_catalog)")
    lines.append("-" * 80)
    lines.append(f"  * Bot Architecture Assessment: {bot_style}")
    if longest_recurring_turns > 0:
        w_start, w_len = longest_recurring_pos
        start_day = w_start * 3 + 1
        end_day = (w_start + w_len) * 3
        lines.append(
            f"  * Longest Reused Action Route: {longest_recurring_turns} turns ({w_len} windows / Days {start_day}-{end_day}) "
            f"— repeated identically in {longest_recurring_instances} matches"
        )
    else:
        lines.append("  * Longest Reused Action Route: No multi-match identical route segments found")

    phases = [
        (0, 1, "Phase 1: Steps 0-71   (Days 1-3)   - Pre-Shop Opening"),
        (1, 1, "Phase 2: Steps 72-143  (Days 4-6)   - Shop 1 Reveal & Initial Orders"),
        (2, 1, "Phase 3: Steps 144-215 (Days 7-9)   - Shop 2 Reveal & Branching"),
        (3, 4, "Phase 4: Steps 216-503 (Days 10-21) - Mid-Game Production Engine"),
        (7, 2, "Phase 5: Steps 504-647 (Days 22-27) - Late-Game Harvest & Saturation"),
        (9, 1, "Phase 6: Steps 648-719 (Days 28-30) - Terminal Liquidation & End"),
    ]

    for start_w, len_w, label in phases:
        strict_c = strict_by_horizon[(start_w, len_w)]
        loose_c = loose_by_horizon[(start_w, len_w)]
        top_s = strict_c.most_common(1)[0][1] if strict_c else 0
        top_s_pct = (top_s / total_matches * 100.0) if total_matches > 0 else 0.0
        top_l = loose_c.most_common(1)[0][1] if loose_c else 0
        top_l_pct = (top_l / total_matches * 100.0) if total_matches > 0 else 0.0
        lines.append(f"  * {label}: {top_s_pct:>5.1f}% top exact ({top_s:>3}/{total_matches}) | {len(strict_c):>3} unique scripts | {top_l_pct:>5.1f}% macro match")

    lines.append("")
    lines.append("  [Key Phase Segmentation (Route Consistency Across 6 Lifecycle Stages)]")
    lines.append("  " + f"{'Phase / Step Range (Days)':<50} {'Unique Scripts':<16} {'Top Exact Match':<16} {'Top Macro Match':<16}")
    lines.append("  " + "-" * 98)
    for start_w, len_w, label in phases:
        strict_c = strict_by_horizon[(start_w, len_w)]
        loose_c = loose_by_horizon[(start_w, len_w)]
        unique_strict = len(strict_c)
        top_strict_occ = strict_c.most_common(1)[0][1] if strict_c else 0
        top_strict_pct = (top_strict_occ / total_matches * 100.0) if total_matches > 0 else 0.0
        top_loose_occ = loose_c.most_common(1)[0][1] if loose_c else 0
        top_loose_pct = (top_loose_occ / total_matches * 100.0) if total_matches > 0 else 0.0
        lines.append(
            f"  {label:<50} {unique_strict:>7} variants  "
            f"{top_strict_pct:>5.1f}% ({top_strict_occ:>3}/{total_matches})  "
            f"{top_loose_pct:>5.1f}% ({top_loose_occ:>3}/{total_matches})"
        )

    lines.append("")
    lines.append("  [Multi-Horizon Route Consistency by Strategic Milestone]")

    horizon_groups = [
        ("Anchor A: From Match Start (Day 1 / Cumulative Horizons)", [
            (0, 1, "Days 1-3   (Steps 0-71 / 1 window ) - Pre-Shop Opening"),
            (0, 2, "Days 1-6   (Steps 0-143 / 2 windows) - Shop 1 Revealed"),
            (0, 3, "Days 1-9   (Steps 0-215 / 3 windows) - Shop 2 Revealed"),
            (0, 5, "Days 1-15  (Steps 0-359 / 5 windows) - Halfway Mid-Match"),
            (0, 7, "Days 1-21  (Steps 0-503 / 7 windows) - Core Production Cycle"),
            (0, 10, "Days 1-30  (Steps 0-719 / 10 win)   - Full Match Playbook"),
        ]),
        ("Anchor B: From 1st Shop Reveal (Days 4 & 7 Horizons)", [
            (1, 1, "Days 4-6   (Steps 72-143 / 1 window ) - Shop 1 Immediate Window"),
            (1, 4, "Days 4-15  (Steps 72-359 / 4 windows) - Shop 1 to Mid-Match"),
            (1, 9, "Days 4-30  (Steps 72-719 / 9 windows) - Shop 1 to Match End"),
            (2, 3, "Days 7-15  (Steps 144-359 / 3 windows) - Shop 2 Reveal to Mid-Match"),
            (2, 8, "Days 7-30  (Steps 144-719 / 8 windows) - Shop 2 Reveal to Match End"),
        ]),
        ("Anchor C: From 2nd Shop Reveal & Mid-Game (Day 10 Horizons)", [
            (3, 3, "Days 10-18 (Steps 216-431 / 3 windows) - Post-Shop 2 Production Engine"),
            (3, 7, "Days 10-30 (Steps 216-719 / 7 windows) - Post-Shop 2 to Match End"),
            (6, 4, "Days 19-30 (Steps 432-719 / 4 windows) - Late Match Final Third"),
        ]),
    ]

    for grp_title, horizons in horizon_groups:
        lines.append(f"    * {grp_title}")
        lines.append("      " + f"{'Horizon / Time Window':<48} {'Unique Scripts':<16} {'Top Exact Match':<16} {'Top Macro Match':<16}")
        lines.append("      " + "-" * 96)
        for start_w, len_w, label in horizons:
            strict_c = strict_by_horizon[(start_w, len_w)]
            loose_c = loose_by_horizon[(start_w, len_w)]
            unique_strict = len(strict_c)
            top_strict_occ = strict_c.most_common(1)[0][1] if strict_c else 0
            top_strict_pct = (top_strict_occ / total_matches * 100.0) if total_matches > 0 else 0.0
            top_loose_occ = loose_c.most_common(1)[0][1] if loose_c else 0
            top_loose_pct = (top_loose_occ / total_matches * 100.0) if total_matches > 0 else 0.0
            lines.append(
                f"      {label:<48} {unique_strict:>7} variants  "
                f"{top_strict_pct:>5.1f}% ({top_strict_occ:>3}/{total_matches})  "
                f"{top_loose_pct:>5.1f}% ({top_loose_occ:>3}/{total_matches})"
            )
        lines.append("")

    # Strategy Decoder Section 9 Sub-blocks (A, B, C, H)
    if script_res and route_res and overlay_res and inv_res:
        lines.extend(render_extended_section_9(script_res, route_res, overlay_res, inv_res, total_matches))

    # Section 10: Match-by-Match Log
    lines.append("-" * 80)
    lines.append("10. MATCH-BY-MATCH SUMMARY LOG")
    lines.append("-" * 80)
    lines.append(f"  {'Episode ID':<12} {'Opponent':<22} {'Result':<6} {'Score':>10} {'Opp Score':>10} {'Margin':>9} {'Final Cash':>11}")
    lines.append("  " + "-" * 86)
    for r in results:
        sign = "+" if r.margin >= 0 else ""
        lines.append(
            f"  {r.episode_id:<12} {r.opponent_name[:20]:<22} {r.result:<6} "
            f"${r.player_reward:>9,.0f} ${r.opponent_reward:>9,.0f} {sign+f'${r.margin:,.0f}':>9} "
            f"${r.final_cash_player:>10,.0f}"
        )

    lines.append("")

    # Section 11: Selling Pattern & Market Trading Moves
    if selling_res:
        lines.extend(render_section_11(selling_res))

    # Section 12: Opponent Interaction
    if opp_res:
        lines.extend(render_section_12(opp_res))

    # Section 13: Eras & Outcomes
    if era_res:
        lines.extend(render_section_13(era_res))

    lines.append("=" * 80)
    lines.append("  END OF REPORT")
    lines.append("=" * 80)
    lines.append("")

    return "\n".join(lines)


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=REPO_ROOT / "replay_downloader" / "downloads" / "kaggriculture",
        help="Path to folder containing player directories",
    )
    parser.add_argument(
        "--player",
        type=str,
        default=None,
        help="Specific player folder name (e.g. '01_Boey', 'Boey') or 'all' to process all players (default: all)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Optional directory to save reports. Defaults to inside each player folder.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel worker processes (default 4)",
    )
    args = parser.parse_args()

    if not args.data_dir.exists():
        print(f"Error: Data directory not found: {args.data_dir}")
        sys.exit(1)

    player_folders = [f for f in sorted(args.data_dir.iterdir()) if f.is_dir()]
    if args.player and args.player.lower() != "all":
        player_folders = [f for f in player_folders if f.name == args.player or args.player.lower() in f.name.lower()]
        if not player_folders:
            print(f"Error: No folder matching player '{args.player}' found in {args.data_dir}")
            sys.exit(1)

    print(f"Starting Player-Wise Replay Analysis on {len(player_folders)} players...")
    start_time = time.time()

    for p_folder in player_folders:
        t0 = time.time()
        print(f"Processing player: {p_folder.name} ...", end=" ", flush=True)
        folder_name, results, decoded_games = process_player_folder(p_folder, max_workers=args.workers)
        
        target_out_dir = args.out_dir if args.out_dir else p_folder
        report_text = format_player_text_report(folder_name, results, decoded_games=decoded_games, out_dir=target_out_dir)

        if args.out_dir:
            args.out_dir.mkdir(parents=True, exist_ok=True)
            report_file = args.out_dir / f"{folder_name}_report.txt"
        else:
            report_file = p_folder / "player_analysis_report.txt"

        report_file.write_text(report_text, encoding="utf-8")
        elapsed = time.time() - t0
        print(f"Done ({len(results)} matches in {elapsed:.1f}s) -> Wrote: {report_file.name}")

    total_time = time.time() - start_time
    print(f"\nAll player reports generated successfully in {total_time:.1f}s!")


if __name__ == "__main__":
    run()
