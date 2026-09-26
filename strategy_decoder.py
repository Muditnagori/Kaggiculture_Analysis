#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Strategy Decoder & Deep Replay Behavioral Analyzer for Kaggriculture.

Decodes player strategies across games:
- A. Script Identification (Trunk, Branching, Clustering, Fingerprints)
- B. Route Selection (Shop routing tables, LOO key predictability, unmapped pairs)
- C. Script Overlays (Field/Market deviation rates, categorization, calendars, conditional triggers)
- D. Selling Patterns (Heatmaps, liquidation shares, presale rates, price realization, shed overflow)
- E. Market Moves (Opening orders, wheat arbitrage/topups, queue saturation, fertilizer flow)
- F. Field & Labor (Hire schedule, planting calendar, animal skip rates, layout fingerprints)
- G. Opponent Interaction (Farm mirroring, mirror bot performance, rival-conditioned counters)
- H. Strategic Invariants (Top action invariants, route invariants, derived rule invariants)
- I. Eras & Outcomes (UUIDv1 chronological timeline, performance by route/shop, loss divergence)
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import uuid

# =============================================================================
# CONFIGURABLE THRESHOLDS & CONSTANTS
# =============================================================================
DEFAULT_MISMATCH_TOLERANCE: float = 0.10    # 10% mismatch tolerance (90% agreement for cluster on days 6-26)
CLUSTER_START_STEP: int = 144               # Day 6 start
CLUSTER_END_STEP: int = 648                 # Day 27 start (steps 144..647 = 504 steps)
PUBLIC_ROUTE_MATCH_HIGH: float = 0.95       # >= 95% = Public route
PUBLIC_ROUTE_MATCH_LOW: float = 0.90        # < 90% = Own custom route (90-95% = Modified public)
INVARIANT_THRESHOLD: float = 0.95           # >= 95% occurrence across games
MIRROR_MIN_OCCUPIED_TILES: int = 8          # Minimum occupied tiles required on either side
MIRROR_SIMILARITY_THRESHOLD: float = 0.90   # >= 90% tile agreement = mirror day
MIRROR_MIN_DAYS: int = 6                    # >= 6 mirror days = Mirror Opponent
LOSS_DIVERGENCE_MARGIN: float = 5000.0      # Opponent cash lead threshold in losses
STEPS_PER_DAY: int = 24
TOTAL_STEPS: int = 720
TOTAL_DAYS: int = 30
MAX_MARKET_ORDERS: int = 10


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class DecodedStep:
    step: int
    day: int
    hour: int
    player_farmer_act: List[Any]
    player_hands_act: List[List[Any]]
    player_market_act: List[List[Any]]
    opp_farmer_act: List[Any]
    opp_hands_act: List[List[Any]]
    opp_market_act: List[List[Any]]
    player_money: float
    opp_money: float
    player_shed: Dict[str, int]
    market_prices: Dict[str, float]
    unlocked_shops: List[str]
    player_tiles: List[List[Any]]
    opp_tiles: List[List[Any]]
    player_quads: List[int]
    opp_quads: List[int]
    field_canonical: str
    market_canonical: str


@dataclass
class DecodedGame:
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
    timestamp: Optional[datetime]
    steps: List[DecodedStep]
    field_fingerprints: List[str]  # 30 daily hashes
    unlocked_shops: List[str]
    shop_pair: Tuple[str, str]
    shop_triplet: Tuple[str, str, str]


# =============================================================================
# HELPER CANONICALIZERS & HASHING
# =============================================================================

def canonical_action_field(farmer_act: Any, hands_act: Any) -> str:
    """Produces a deterministic canonical string for FIELD actions (farmer + hands), sorting hands for stability."""
    f_str = ""
    if isinstance(farmer_act, list) and farmer_act:
        f_str = ":".join(str(x).upper() for x in farmer_act)
    elif farmer_act:
        f_str = str(farmer_act).upper()
    else:
        f_str = "PASS"

    h_parts = []
    if isinstance(hands_act, list):
        for h in hands_act:
            if isinstance(h, list) and h:
                h_parts.append(":".join(str(x).upper() for x in h))
            elif h:
                h_parts.append(str(h).upper())
    # Canonicalize hands by sorting to avoid indexing ordering noise
    h_parts.sort()
    h_str = ";".join(h_parts) if h_parts else "NONE"
    return f"F[{f_str}]|H[{h_str}]"


def canonical_action_market(market_act: Any) -> str:
    """Produces a deterministic canonical string for MARKET actions."""
    if not isinstance(market_act, list) or not market_act:
        return "M[]"
    m_parts = []
    for order in market_act:
        if isinstance(order, list) and order:
            m_parts.append(":".join(str(x).upper() for x in order))
        elif order:
            m_parts.append(str(order).upper())
    return f"M[{';'.join(m_parts)}]"


def compute_day_fingerprint(steps_field_canonical: List[str]) -> str:
    """Hashes 24 FIELD steps of a single day into an 8-character hex fingerprint."""
    raw = "|".join(steps_field_canonical)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]


def uuid1_to_datetime(u_str: str) -> Optional[datetime]:
    """Extracts UTC timestamp from a UUIDv1 string."""
    try:
        u = uuid.UUID(u_str)
        if u.version == 1:
            timestamp = (u.time - 0x01b21dd213814000) / 1e7
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except Exception:
        pass
    return None


def normalize_shop_name(raw: str) -> str:
    raw = str(raw).strip().upper()
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


# =============================================================================
# LIBRARY ROUTES LOADER
# =============================================================================

def load_reference_routes(search_dirs: Optional[List[Path]] = None) -> Tuple[Dict[str, List[Dict[str, Any]]], str]:
    """
    Loads and normalizes public reference routes if present.
    Returns:
        (routes_dict, status_message)
    """
    if search_dirs is None:
        search_dirs = [
            Path("data/reference_routes"),
            Path("reference_routes"),
            Path("../data/reference_routes"),
            Path("../../data/reference_routes"),
        ]

    routes: Dict[str, List[Dict[str, Any]]] = {}
    found_files = []

    for d in search_dirs:
        tapes_path = d / "route_tapes_decoded.json"
        full_path = d / "routes_full_13x719.json"

        if tapes_path.exists():
            try:
                content = json.loads(tapes_path.read_text(encoding="utf-8"))
                actions_pool = content.get("actions", [])
                raw_routes = content.get("routes", {})
                for r_id, step_indices in raw_routes.items():
                    step_acts = []
                    for idx in step_indices:
                        if isinstance(idx, int) and 0 <= idx < len(actions_pool):
                            step_acts.append(actions_pool[idx])
                        elif isinstance(idx, dict):
                            step_acts.append(idx)
                        else:
                            step_acts.append({"farmer": ["PASS"], "hands": [], "market": []})
                    while len(step_acts) < TOTAL_STEPS:
                        step_acts.append({"farmer": ["PASS"], "hands": [], "market": []})
                    routes[f"tape_{r_id}"] = step_acts[:TOTAL_STEPS]
                found_files.append(tapes_path.name)
            except Exception:
                pass

        if full_path.exists():
            try:
                content = json.loads(full_path.read_text(encoding="utf-8"))
                for r_id, step_acts in content.items():
                    acts = list(step_acts)
                    while len(acts) < TOTAL_STEPS:
                        acts.append({"farmer": ["PASS"], "hands": [], "market": []})
                    routes[f"full_{r_id}"] = acts[:TOTAL_STEPS]
                found_files.append(full_path.name)
            except Exception:
                pass

    if routes:
        msg = f"Loaded {len(routes)} reference routes from: {', '.join(found_files)}"
    else:
        msg = "Library files not found in data/reference_routes (skipped)"

    return routes, msg


# =============================================================================
# GAME EXTRACTION
# =============================================================================

def extract_decoded_game(replay_data: Dict[str, Any], file_name: str, target_player_name: str, folder_hint: str) -> Optional[DecodedGame]:
    """Converts raw replay JSON into a complete DecodedGame object, correcting replay step offset."""
    steps_data = replay_data.get("steps", [])
    if not steps_data or len(steps_data) < 2:
        return None

    names = replay_data.get("info", {}).get("TeamNames", [])
    player_idx = 0
    if names:
        for i, name in enumerate(names):
            if name == target_player_name:
                player_idx = i
                break
        else:
            cleaned_folder = folder_hint.split("_", 1)[1].strip() if "_" in folder_hint else folder_hint
            for i, name in enumerate(names):
                if name.lower() == cleaned_folder.lower() or cleaned_folder.lower() in name.lower() or name.lower() in cleaned_folder.lower():
                    player_idx = i
                    break

    n_players = len(steps_data[0])
    opp_idx = 1 - player_idx if n_players == 2 else (player_idx + 1) % n_players

    p_name = names[player_idx] if player_idx < len(names) else f"player_{player_idx}"
    o_name = names[opp_idx] if opp_idx < len(names) else f"player_{opp_idx}"

    rewards = replay_data.get("rewards", [0, 0])
    if not rewards or len(rewards) < 2:
        final_p_reward = steps_data[-1][player_idx].get("reward", 0.0) or 0.0
        final_o_reward = steps_data[-1][opp_idx].get("reward", 0.0) or 0.0
    else:
        final_p_reward = rewards[player_idx] or 0.0
        final_o_reward = rewards[opp_idx] or 0.0

    margin = final_p_reward - final_o_reward
    if final_p_reward > final_o_reward:
        res = "WIN"
    elif final_p_reward < final_o_reward:
        res = "LOSS"
    else:
        res = "TIE"

    ep_id = str(replay_data.get("id") or replay_data.get("info", {}).get("EpisodeId") or Path(file_name).stem)
    ts = uuid1_to_datetime(ep_id)

    decoded_steps: List[DecodedStep] = []
    daily_field_canonicals: List[List[str]] = [[] for _ in range(TOTAL_DAYS)]

    # Kaggle Replays: actions executed for step t are logged at steps_data[t + 1]
    for k in range(1, len(steps_data)):
        t = k - 1
        if t >= TOTAL_STEPS:
            break
        day = t // STEPS_PER_DAY
        hour = t % STEPS_PER_DAY

        p_turn = steps_data[k][player_idx] if player_idx < len(steps_data[k]) else {}
        o_turn = steps_data[k][opp_idx] if opp_idx < len(steps_data[k]) else {}

        p_act = p_turn.get("action", {}) or {}
        o_act = o_turn.get("action", {}) or {}

        p_f = p_act.get("farmer", []) or []
        p_h = p_act.get("hands", []) or []
        p_m = p_act.get("market", []) or []

        o_f = o_act.get("farmer", []) or []
        o_h = o_act.get("hands", []) or []
        o_m = o_act.get("market", []) or []

        f_canon = canonical_action_field(p_f, p_h)
        m_canon = canonical_action_market(p_m)
        if day < TOTAL_DAYS:
            daily_field_canonicals[day].append(f_canon)

        # Pre-action observation (state going into step t)
        pre_turn = steps_data[t][player_idx] if player_idx < len(steps_data[t]) else {}
        p_obs = pre_turn.get("observation", {}) or {}
        p_farms = p_obs.get("farms", []) or []
        p_farm = p_farms[player_idx] if player_idx < len(p_farms) else {}
        o_farm = p_farms[opp_idx] if opp_idx < len(p_farms) else {}

        p_money = float(p_farm.get("money", 0.0) or 0.0)
        o_money = float(o_farm.get("money", 0.0) or 0.0)

        p_shed = p_obs.get("private", {}).get("shed", {}) or {}
        m_prices = p_obs.get("market", {}).get("prices", {}) or {}
        u_shops = [normalize_shop_name(s) for s in p_obs.get("town", {}).get("unlocked_shops", []) or []]

        p_tiles = p_farm.get("tiles", []) or []
        o_tiles = o_farm.get("tiles", []) or []

        p_quads = p_farm.get("unlocked_quadrants", [1]) or [1]
        o_quads = o_farm.get("unlocked_quadrants", [1]) or [1]

        decoded_steps.append(DecodedStep(
            step=t,
            day=day,
            hour=hour,
            player_farmer_act=p_f,
            player_hands_act=p_h,
            player_market_act=p_m,
            opp_farmer_act=o_f,
            opp_hands_act=o_h,
            opp_market_act=o_m,
            player_money=p_money,
            opp_money=o_money,
            player_shed=p_shed,
            market_prices=m_prices,
            unlocked_shops=u_shops,
            player_tiles=p_tiles,
            opp_tiles=o_tiles,
            player_quads=p_quads,
            opp_quads=o_quads,
            field_canonical=f_canon,
            market_canonical=m_canon,
        ))

    daily_fingerprints = []
    for d in range(TOTAL_DAYS):
        day_steps = daily_field_canonicals[d]
        if day_steps:
            daily_fingerprints.append(compute_day_fingerprint(day_steps))
        else:
            daily_fingerprints.append("00000000")

    # Get final unlocked shops from final step
    final_obs = steps_data[-1][player_idx].get("observation", {}) if steps_data else {}
    final_shops = [normalize_shop_name(s) for s in final_obs.get("town", {}).get("unlocked_shops", []) or []]
    if not final_shops and decoded_steps:
        final_shops = decoded_steps[-1].unlocked_shops

    s1 = final_shops[0] if len(final_shops) >= 1 else "NONE"
    s2 = final_shops[1] if len(final_shops) >= 2 else "NONE"
    s3 = final_shops[2] if len(final_shops) >= 3 else "NONE"

    return DecodedGame(
        episode_id=ep_id,
        file_name=file_name,
        player_name=p_name,
        opponent_name=o_name,
        player_index=player_idx,
        opponent_index=opp_idx,
        player_reward=final_p_reward,
        opponent_reward=final_o_reward,
        result=res,
        margin=margin,
        timestamp=ts,
        steps=decoded_steps,
        field_fingerprints=daily_fingerprints,
        unlocked_shops=final_shops,
        shop_pair=(s1, s2),
        shop_triplet=(s1, s2, s3),
    )


# =============================================================================
# A. SCRIPT IDENTIFICATION (Section 9)
# =============================================================================

@dataclass
class ClusterInfo:
    cluster_id: int
    cluster_name: str
    game_indices: List[int]
    match_count: int
    match_share_pct: float
    base_field_steps: List[str]
    base_market_steps: List[str]
    base_actions: List[Dict[str, Any]]
    public_route_name: Optional[str] = None
    public_match_pct: float = 0.0
    public_match_category: str = "Own Route"


@dataclass
class ScriptIdentificationResult:
    common_trunk_length: int
    branch_steps: List[int]
    endgame_shared_from_step: Optional[int]
    clusters: List[ClusterInfo]
    game_cluster_map: Dict[str, int]
    library_status_msg: str
    daily_fingerprints_mode: List[str]


def decode_script_identification(
    games: List[DecodedGame],
    reference_routes: Dict[str, List[Dict[str, Any]]],
    tolerance: float = DEFAULT_MISMATCH_TOLERANCE,
) -> ScriptIdentificationResult:
    """
    Computes common trunk length, branching steps, route clusters (days 6-26 with >=90% similarity),
    public route library alignment, and endgame convergence.
    """
    n_games = len(games)
    if n_games == 0:
        return ScriptIdentificationResult(0, [], None, [], {}, "No games", [])

    # Common trunk length from step 0 across all games
    common_trunk_length = 0
    for t in range(TOTAL_STEPS):
        first_act = games[0].steps[t].field_canonical if t < len(games[0].steps) else ""
        all_same = all(
            (t < len(g.steps) and g.steps[t].field_canonical == first_act)
            for g in games
        )
        if all_same:
            common_trunk_length += 1
        else:
            break

    eval_steps_count = CLUSTER_END_STEP - CLUSTER_START_STEP

    game_routes = [
        [g.steps[t].field_canonical for t in range(CLUSTER_START_STEP, min(CLUSTER_END_STEP, len(g.steps)))]
        for g in games
    ]

    assigned_cluster = [-1] * n_games
    clusters_list: List[List[int]] = []

    for i in range(n_games):
        if assigned_cluster[i] != -1:
            continue
        cur_cluster = [i]
        assigned_cluster[i] = len(clusters_list)

        for j in range(i + 1, n_games):
            if assigned_cluster[j] != -1:
                continue
            matches = sum(1 for k in range(eval_steps_count) if k < len(game_routes[i]) and k < len(game_routes[j]) and game_routes[i][k] == game_routes[j][k])
            match_ratio = matches / eval_steps_count if eval_steps_count > 0 else 1.0
            if match_ratio >= (1.0 - tolerance):
                cur_cluster.append(j)
                assigned_cluster[j] = assigned_cluster[i]

        clusters_list.append(cur_cluster)

    clusters_list.sort(key=lambda c: len(c), reverse=True)

    cluster_infos: List[ClusterInfo] = []
    game_cluster_map: Dict[str, int] = {}

    ref_field_canonicals: Dict[str, List[str]] = {}
    for r_name, r_acts in reference_routes.items():
        ref_field_canonicals[r_name] = [
            canonical_action_field(act.get("farmer", []), act.get("hands", []))
            for act in r_acts
        ]

    for c_idx, member_indices in enumerate(clusters_list):
        c_id = c_idx + 1
        c_name = f"Cluster {c_id}"
        m_count = len(member_indices)
        m_share = (m_count / n_games) * 100.0

        for m_i in member_indices:
            game_cluster_map[games[m_i].episode_id] = c_id

        base_field = []
        base_market = []
        base_raw_actions = []

        for t in range(TOTAL_STEPS):
            f_votes = Counter(games[idx].steps[t].field_canonical for idx in member_indices if t < len(games[idx].steps))
            m_votes = Counter(games[idx].steps[t].market_canonical for idx in member_indices if t < len(games[idx].steps))
            top_f = f_votes.most_common(1)[0][0] if f_votes else "PASS"
            top_m = m_votes.most_common(1)[0][0] if m_votes else "M[]"
            base_field.append(top_f)
            base_market.append(top_m)

            sample_g = next((games[idx] for idx in member_indices if t < len(games[idx].steps) and games[idx].steps[t].field_canonical == top_f), games[member_indices[0]])
            if t < len(sample_g.steps):
                base_raw_actions.append({
                    "farmer": sample_g.steps[t].player_farmer_act,
                    "hands": sample_g.steps[t].player_hands_act,
                    "market": sample_g.steps[t].player_market_act,
                })
            else:
                base_raw_actions.append({"farmer": ["PASS"], "hands": [], "market": []})

        best_ref_name = None
        best_match_pct = 0.0

        if ref_field_canonicals:
            for r_name, r_field in ref_field_canonicals.items():
                match_count = sum(1 for t in range(TOTAL_STEPS) if t < len(base_field) and t < len(r_field) and base_field[t] == r_field[t])
                pct = (match_count / TOTAL_STEPS) * 100.0
                if pct > best_match_pct:
                    best_match_pct = pct
                    best_ref_name = r_name

            if best_match_pct >= PUBLIC_ROUTE_MATCH_HIGH * 100.0:
                match_cat = f"Public Route ({best_ref_name})"
            elif best_match_pct < PUBLIC_ROUTE_MATCH_LOW * 100.0:
                match_cat = "Own Route"
            else:
                match_cat = f"Modified Public ({best_ref_name})"
        else:
            match_cat = "Skipped (no library files)"

        cluster_infos.append(ClusterInfo(
            cluster_id=c_id,
            cluster_name=c_name,
            game_indices=member_indices,
            match_count=m_count,
            match_share_pct=m_share,
            base_field_steps=base_field,
            base_market_steps=base_market,
            base_actions=base_raw_actions,
            public_route_name=best_ref_name,
            public_match_pct=best_match_pct,
            public_match_category=match_cat,
        ))

    # Branch steps: pairwise first step where each pair of clusters differs
    cluster_div_steps = []
    for i in range(len(cluster_infos)):
        for j in range(i + 1, len(cluster_infos)):
            c1_f = cluster_infos[i].base_field_steps
            c2_f = cluster_infos[j].base_field_steps
            for t in range(TOTAL_STEPS):
                if c1_f[t] != c2_f[t]:
                    cluster_div_steps.append(t)
                    break
    branch_steps = sorted(set(cluster_div_steps))

    # Endgame convergence: step after which all games converge again
    endgame_shared_from_step = None
    for t in range(TOTAL_STEPS - 1, -1, -1):
        first_act = games[0].steps[t].field_canonical if t < len(games[0].steps) else ""
        if all(t < len(g.steps) and g.steps[t].field_canonical == first_act for g in games):
            endgame_shared_from_step = t
        else:
            break

    daily_mode = []
    for d in range(TOTAL_DAYS):
        d_counts = Counter(g.field_fingerprints[d] for g in games)
        daily_mode.append(d_counts.most_common(1)[0][0] if d_counts else "00000000")

    lib_msg = "Library loaded" if reference_routes else "Library files not found (skipped)"

    return ScriptIdentificationResult(
        common_trunk_length=common_trunk_length,
        branch_steps=branch_steps,
        endgame_shared_from_step=endgame_shared_from_step,
        clusters=cluster_infos,
        game_cluster_map=game_cluster_map,
        library_status_msg=lib_msg,
        daily_fingerprints_mode=daily_mode,
    )


# =============================================================================
# B. ROUTE SELECTION (Section 9 Sub-block)
# =============================================================================

@dataclass
class ShopRouteEntry:
    shop1: str
    shop2: str
    cluster_id: int
    cluster_name: str
    matches: int
    wins: int
    losses: int
    win_rate: float
    avg_margin: float


@dataclass
class RouteSelectionResult:
    table: List[ShopRouteEntry]
    router_consistency_pct: float
    router_key_accuracies: Dict[str, float]
    majority_baseline_accuracy: float
    best_predictive_key: str
    route_decision_step: int
    unmapped_pairs: List[Tuple[str, str]]
    default_cluster_id: int


def decode_route_selection(
    games: List[DecodedGame],
    script_res: ScriptIdentificationResult,
) -> RouteSelectionResult:
    """
    Decodes shop routing table and evaluates Leave-One-Out (LOO) key predictability vs majority baseline.
    """
    n_games = len(games)
    if n_games == 0 or not script_res.clusters:
        return RouteSelectionResult([], 0.0, {}, 0.0, "None", 0, [], 1)

    default_cluster_id = script_res.clusters[0].cluster_id

    pair_cluster_games: Dict[Tuple[str, str], Dict[int, List[DecodedGame]]] = defaultdict(lambda: defaultdict(list))

    for g in games:
        c_id = script_res.game_cluster_map.get(g.episode_id, default_cluster_id)
        pair_cluster_games[g.shop_pair][c_id].append(g)

    table: List[ShopRouteEntry] = []
    for pair, c_dict in pair_cluster_games.items():
        s1, s2 = pair
        for c_id, g_list in c_dict.items():
            cnt = len(g_list)
            wins = sum(1 for g in g_list if g.result == "WIN")
            losses = sum(1 for g in g_list if g.result == "LOSS")
            wr = (wins / cnt) * 100.0 if cnt > 0 else 0.0
            avg_m = sum(g.margin for g in g_list) / cnt if cnt > 0 else 0.0
            c_name = f"Cluster {c_id}"
            table.append(ShopRouteEntry(
                shop1=s1,
                shop2=s2,
                cluster_id=c_id,
                cluster_name=c_name,
                matches=cnt,
                wins=wins,
                losses=losses,
                win_rate=wr,
                avg_margin=avg_m,
            ))

    table.sort(key=lambda x: (x.matches, x.win_rate), reverse=True)

    consistent_count = 0
    for pair, c_dict in pair_cluster_games.items():
        max_in_pair = max(len(g_list) for g_list in c_dict.values())
        consistent_count += max_in_pair
    router_consistency_pct = (consistent_count / n_games) * 100.0 if n_games > 0 else 0.0

    # Leave-One-Out Cross-Validation (LOO-CV) to prevent overfitting
    def eval_loo_accuracy(key_fn) -> float:
        correct = 0
        for i, g_test in enumerate(games):
            train_counts: Dict[Any, Counter] = defaultdict(Counter)
            global_train_counts: Counter = Counter()
            for j, g_train in enumerate(games):
                if j == i:
                    continue
                k = key_fn(g_train)
                c_id = script_res.game_cluster_map.get(g_train.episode_id, default_cluster_id)
                train_counts[k][c_id] += 1
                global_train_counts[c_id] += 1

            test_key = key_fn(g_test)
            actual_c = script_res.game_cluster_map.get(g_test.episode_id, default_cluster_id)

            if test_key in train_counts and train_counts[test_key]:
                predicted_c = train_counts[test_key].most_common(1)[0][0]
            elif global_train_counts:
                predicted_c = global_train_counts.most_common(1)[0][0]
            else:
                predicted_c = default_cluster_id

            if predicted_c == actual_c:
                correct += 1

        return (correct / n_games) * 100.0 if n_games > 0 else 0.0

    # Majority class baseline accuracy
    majority_correct = 0
    for i, g_test in enumerate(games):
        global_train_counts: Counter = Counter()
        for j, g_train in enumerate(games):
            if j == i:
                continue
            c_id = script_res.game_cluster_map.get(g_train.episode_id, default_cluster_id)
            global_train_counts[c_id] += 1
        predicted_c = global_train_counts.most_common(1)[0][0] if global_train_counts else default_cluster_id
        actual_c = script_res.game_cluster_map.get(g_test.episode_id, default_cluster_id)
        if predicted_c == actual_c:
            majority_correct += 1
    baseline_acc = (majority_correct / n_games) * 100.0 if n_games > 0 else 0.0

    acc_1 = eval_loo_accuracy(lambda g: g.shop_pair[0])
    acc_2_ord = eval_loo_accuracy(lambda g: g.shop_pair)
    acc_2_unord = eval_loo_accuracy(lambda g: tuple(sorted(g.shop_pair)))
    acc_3 = eval_loo_accuracy(lambda g: g.shop_triplet)

    accuracies = {
        "Majority Baseline (Always Top Cluster)": baseline_acc,
        "First 1 Shop (Shop 1)": acc_1,
        "First 2 Ordered (Shop 1 -> Shop 2)": acc_2_ord,
        "First 2 Unordered {Shop 1, Shop 2}": acc_2_unord,
        "First 3 Shops (Shop 1 -> Shop 2 -> Shop 3)": acc_3,
    }
    # Best predictive key amongst shop features
    shop_accs = {k: v for k, v in accuracies.items() if "Baseline" not in k}
    best_key = max(shop_accs.items(), key=lambda x: x[1])[0]

    route_decision_step = 0
    if len(script_res.clusters) > 1:
        for t in range(TOTAL_STEPS):
            acts_at_t = set(c.base_field_steps[t] for c in script_res.clusters)
            if len(acts_at_t) > 1:
                route_decision_step = t
                break
    else:
        route_decision_step = script_res.common_trunk_length

    unmapped_pairs: List[Tuple[str, str]] = []
    for pair, c_dict in pair_cluster_games.items():
        mode_c = max(c_dict.items(), key=lambda x: len(x[1]))[0]
        if mode_c == default_cluster_id and len(script_res.clusters) > 1:
            unmapped_pairs.append(pair)

    return RouteSelectionResult(
        table=table,
        router_consistency_pct=router_consistency_pct,
        router_key_accuracies=accuracies,
        majority_baseline_accuracy=baseline_acc,
        best_predictive_key=best_key,
        route_decision_step=route_decision_step,
        unmapped_pairs=unmapped_pairs,
        default_cluster_id=default_cluster_id,
    )


# =============================================================================
# C. OVERLAYS ON TOP OF THE SCRIPT
# =============================================================================

@dataclass
class ConditionalInvestmentEvent:
    episode_id: str
    step: int
    day: int
    hour: int
    category: str
    action_str: str
    cash: float
    prices: Dict[str, float]
    shops: List[str]


@dataclass
class ScriptOverlayResult:
    avg_deviation_rate_field: float
    avg_deviation_rate_market: float
    deviation_type_counts: Dict[str, int]
    deviation_calendar: Dict[str, List[Tuple[int, int, int]]]
    active_windows_summary: Dict[str, str]
    conditional_investments: List[ConditionalInvestmentEvent]
    investment_trigger_ranges: Dict[str, str]
    reactivity_score: float


def decode_script_overlays(
    games: List[DecodedGame],
    script_res: ScriptIdentificationResult,
) -> ScriptOverlayResult:
    """
    Diffs each game against its cluster's base script to isolate adaptive overlays.
    """
    n_games = len(games)
    if n_games == 0 or not script_res.clusters:
        return ScriptOverlayResult(0.0, 0.0, {}, {}, {}, [], {}, 0.0)

    cluster_by_id = {c.cluster_id: c for c in script_res.clusters}
    default_cluster = script_res.clusters[0]

    field_diffs_total = 0
    market_diffs_total = 0
    total_steps_evaluated = n_games * TOTAL_STEPS

    cat_counts: Dict[str, int] = defaultdict(int)
    cat_day_hour: Dict[str, Counter] = defaultdict(Counter)
    cond_investments: List[ConditionalInvestmentEvent] = []

    for g in games:
        c_id = script_res.game_cluster_map.get(g.episode_id, default_cluster.cluster_id)
        base = cluster_by_id.get(c_id, default_cluster)

        for t in range(min(TOTAL_STEPS, len(g.steps))):
            s = g.steps[t]
            day = s.day
            hour = s.hour

            base_f_canon = base.base_field_steps[t]
            base_m_canon = base.base_market_steps[t]

            # Field diff
            f_diff = (s.field_canonical != base_f_canon)
            if f_diff:
                field_diffs_total += 1
                p_f_str = ":".join(str(x).upper() for x in s.player_farmer_act) if s.player_farmer_act else "PASS"
                if "FEED" in base_f_canon and ("PASS" in s.field_canonical or "FEED" not in s.field_canonical):
                    cat_counts["FEED->PASS"] += 1
                    cat_day_hour["FEED->PASS"][(day, hour)] += 1
                elif any(cmd in ("BUILD_PASTURE", "BUILD_COOP", "PLACE") for cmd in p_f_str.split(":")):
                    cat_counts["animal bought/placed"] += 1
                    cat_day_hour["animal bought/placed"][(day, hour)] += 1
                    cond_investments.append(ConditionalInvestmentEvent(
                        g.episode_id, t, day, hour, "ANIMAL_FIELD", p_f_str, s.player_money, dict(s.market_prices), list(s.unlocked_shops)
                    ))
                elif "PLANT:TOMATO" in p_f_str:
                    cat_counts["other"] += 1
                    cond_investments.append(ConditionalInvestmentEvent(
                        g.episode_id, t, day, hour, "TOMATO_PLANT", p_f_str, s.player_money, dict(s.market_prices), list(s.unlocked_shops)
                    ))
                else:
                    cat_counts["other"] += 1
                    cat_day_hour["other"][(day, hour)] += 1

            # Market diff
            m_diff = (s.market_canonical != base_m_canon)
            if m_diff:
                market_diffs_total += 1

                g_orders = s.player_market_act or []
                base_raw_m = base.base_actions[t].get("market", []) or []

                g_orders_norm = [tuple(str(x).upper() for x in o) for o in g_orders if o]
                b_orders_norm = [tuple(str(x).upper() for x in o) for o in base_raw_m if o]

                g_types = [o[0] for o in g_orders_norm]
                b_types = [o[0] for o in b_orders_norm]

                g_sells = {o[1]: (int(o[2]) if len(o) > 2 and str(o[2]).isdigit() else 1) for o in g_orders_norm if o[0] == "SELL" and len(o) > 1}
                b_sells = {o[1]: (int(o[2]) if len(o) > 2 and str(o[2]).isdigit() else 1) for o in b_orders_norm if o[0] == "SELL" and len(o) > 1}

                g_wheat_buys = sum(int(o[2]) if len(o) > 2 and str(o[2]).isdigit() else 1 for o in g_orders_norm if "BUY" in o[0] and len(o) > 1 and "WHEAT" in o[1])
                b_wheat_buys = sum(int(o[2]) if len(o) > 2 and str(o[2]).isdigit() else 1 for o in b_orders_norm if "BUY" in o[0] and len(o) > 1 and "WHEAT" in o[1])

                if set(g_orders_norm) == set(b_orders_norm) and g_orders_norm != b_orders_norm:
                    cat_counts["orders reordered"] += 1
                    cat_day_hour["orders reordered"][(day, hour)] += 1
                elif any(o[0] == "BUY_LAND" for o in g_orders_norm) and not any(o[0] == "BUY_LAND" for o in b_orders_norm):
                    cat_counts["land bought"] += 1
                    cat_day_hour["land bought"][(day, hour)] += 1
                    cond_investments.append(ConditionalInvestmentEvent(
                        g.episode_id, t, day, hour, "LAND_PURCHASE", "BUY_LAND", s.player_money, dict(s.market_prices), list(s.unlocked_shops)
                    ))
                elif any(o[0] == "BUY_ANIMAL" for o in g_orders_norm) and not any(o[0] == "BUY_ANIMAL" for o in b_orders_norm):
                    cat_counts["animal bought/placed"] += 1
                    cat_day_hour["animal bought/placed"][(day, hour)] += 1
                    cond_investments.append(ConditionalInvestmentEvent(
                        g.episode_id, t, day, hour, "BUY_ANIMAL", str(g_orders), s.player_money, dict(s.market_prices), list(s.unlocked_shops)
                    ))
                elif g_types.count("HIRE") > b_types.count("HIRE"):
                    cat_counts["extra HIRE"] += 1
                    cat_day_hour["extra HIRE"][(day, hour)] += 1
                elif g_sells and not b_sells:
                    cat_counts["SELL added"] += 1
                    cat_day_hour["SELL added"][(day, hour)] += 1
                elif g_sells and b_sells and g_sells != b_sells:
                    cat_counts["SELL qty changed"] += 1
                    cat_day_hour["SELL qty changed"][(day, hour)] += 1
                elif g_wheat_buys > b_wheat_buys:
                    cat_counts["BUY WHEAT added"] += 1
                    cat_day_hour["BUY WHEAT added"][(day, hour)] += 1
                elif g_wheat_buys < b_wheat_buys:
                    cat_counts["BUY WHEAT reduced"] += 1
                    cat_day_hour["BUY WHEAT reduced"][(day, hour)] += 1
                elif any("TOMATO" in str(o) for o in g_orders_norm) and not any("TOMATO" in str(o) for o in b_orders_norm):
                    cat_counts["other"] += 1
                    cond_investments.append(ConditionalInvestmentEvent(
                        g.episode_id, t, day, hour, "TOMATO_BUY", str(g_orders), s.player_money, dict(s.market_prices), list(s.unlocked_shops)
                    ))
                else:
                    cat_counts["other"] += 1
                    cat_day_hour["other"][(day, hour)] += 1

    avg_dev_field = (field_diffs_total / total_steps_evaluated) * 100.0 if total_steps_evaluated > 0 else 0.0
    avg_dev_market = (market_diffs_total / total_steps_evaluated) * 100.0 if total_steps_evaluated > 0 else 0.0

    active_windows: Dict[str, str] = {}
    cal_dict: Dict[str, List[Tuple[int, int, int]]] = {}

    for cat, dh_counts in cat_day_hour.items():
        sorted_entries = sorted(dh_counts.items(), key=lambda x: x[1], reverse=True)
        cal_dict[cat] = [(d, h, cnt) for (d, h), cnt in sorted_entries]

        days = [d + 1 for (d, h) in dh_counts.keys()]
        hours = [h for (d, h) in dh_counts.keys()]
        if days and hours:
            min_d, max_d = min(days), max(days)
            common_h = Counter(hours).most_common(2)
            h_str = ", ".join(f"hr {h}" for h, _ in common_h)
            active_windows[cat] = f"Days {min_d}–{max_d} (most active: {h_str})"
        else:
            active_windows[cat] = "None"

    trigger_ranges: Dict[str, str] = {}
    if cond_investments:
        by_type: Dict[str, List[ConditionalInvestmentEvent]] = defaultdict(list)
        for ev in cond_investments:
            by_type[ev.category].append(ev)

        for inv_type, ev_list in by_type.items():
            min_cash = min(e.cash for e in ev_list)
            max_cash = max(e.cash for e in ev_list)
            steps = [e.step for e in ev_list]
            min_step, max_step = min(steps), max(steps)
            trigger_ranges[inv_type] = (
                f"{len(ev_list)} events | Steps {min_step}–{max_step} (Days {min_step//24+1}–{max_step//24+1}) | "
                f"Cash range: ${min_cash:,.0f} – ${max_cash:,.0f}"
            )

    pair_groups: Dict[Tuple[str, str], List[DecodedGame]] = defaultdict(list)
    for g in games:
        pair_groups[g.shop_pair].append(g)

    pairwise_diffs = []
    for pair, g_list in pair_groups.items():
        if len(g_list) >= 2:
            for i in range(len(g_list)):
                for j in range(i + 1, len(g_list)):
                    steps_len = min(len(g_list[i].steps), len(g_list[j].steps))
                    if steps_len > 0:
                        diff_steps = sum(
                            1 for t in range(steps_len)
                            if (g_list[i].steps[t].field_canonical != g_list[j].steps[t].field_canonical or
                                g_list[i].steps[t].market_canonical != g_list[j].steps[t].market_canonical)
                        )
                        pairwise_diffs.append(diff_steps / steps_len)

    reactivity_score = (sum(pairwise_diffs) / len(pairwise_diffs) * 100.0) if pairwise_diffs else 0.0

    return ScriptOverlayResult(
        avg_deviation_rate_field=avg_dev_field,
        avg_deviation_rate_market=avg_dev_market,
        deviation_type_counts=dict(cat_counts),
        deviation_calendar=cal_dict,
        active_windows_summary=active_windows,
        conditional_investments=cond_investments,
        investment_trigger_ranges=trigger_ranges,
        reactivity_score=reactivity_score,
    )


# =============================================================================
# D & E. SELLING & MARKET PATTERNS (Section 11)
# =============================================================================

@dataclass
class SellingMarketResult:
    sell_heatmap: Dict[str, Dict[Tuple[int, int], float]]
    top_heatmap_cells: List[Tuple[str, int, int, float]]
    first_sale_steps: Dict[str, Optional[int]]
    last_sale_steps: Dict[str, Optional[int]]
    terminal_liquidation_share_d30: float
    terminal_liquidation_share_step718: float
    sell_precedes_buy_pct: float
    sells_sorted_by_value_pct: float
    sell_split_pattern: Dict[str, str]
    realised_vs_day_price_pct: Dict[str, float]
    presale_rate_pct: float
    presale_price_advantage: float
    overnight_shed_max_avg: float
    overflow_units_lost_total: float
    opening_orders: List[Tuple[int, List[str], int, float]]
    wheat_round_trips_count: int
    wheat_round_trip_profit_est: float
    wheat_topups_by_day: Dict[int, int]
    full_queue_turns_pct: float
    fertilizer_bought: float
    fertilizer_sold: float
    fertilizer_used: float


def decode_selling_and_market(
    games: List[DecodedGame],
    script_res: ScriptIdentificationResult,
) -> SellingMarketResult:
    """
    Decodes selling timing, price capture, order prioritization, and market flows.
    """
    n_games = len(games)
    if n_games == 0:
        return SellingMarketResult(
            {}, [], {}, {}, 0.0, 0.0, 0.0, 0.0, {}, {}, 0.0, 0.0, 0.0, 0.0, [], 0, 0.0, {}, 0.0, 0.0, 0.0, 0.0
        )

    sell_heatmap: Dict[str, Dict[Tuple[int, int], float]] = defaultdict(lambda: defaultdict(float))
    first_sale_tracker: Dict[str, List[int]] = defaultdict(list)
    last_sale_tracker: Dict[str, List[int]] = defaultdict(list)

    total_rev_all = 0.0
    rev_d30_all = 0.0
    rev_step718_all = 0.0

    turns_with_mixed_orders = 0
    turns_sells_precede = 0
    turns_with_multi_sells = 0
    turns_sells_sorted = 0

    product_sale_turns: Dict[str, List[int]] = defaultdict(list)
    realized_price_acc: Dict[str, List[Tuple[float, float]]] = defaultdict(list)

    presale_opportunities = 0
    presale_player_first = 0
    presale_price_diffs: List[float] = []

    overnight_shed_maxes: List[int] = []
    overflow_lost_total = 0.0

    opening_orders_tracker: Dict[int, Counter] = {0: Counter(), 1: Counter(), 2: Counter()}

    wheat_round_trips = 0
    wheat_rt_profit = 0.0
    wheat_topups: Dict[int, int] = defaultdict(int)
    full_queue_count = 0

    fertilizer_bought = 0.0
    fertilizer_sold = 0.0
    fertilizer_used = 0.0

    default_base = script_res.clusters[0] if script_res.clusters else None

    for g in games:
        g_rev = 0.0
        g_rev_d30 = 0.0
        g_rev_718 = 0.0
        g_max_shed_night = 0

        player_sold_prod_day: Dict[Tuple[str, int], int] = {}
        opp_sold_prod_day: Dict[Tuple[str, int], int] = {}
        daily_prices: Dict[Tuple[str, int], List[float]] = defaultdict(list)

        for t, s in enumerate(g.steps):
            day = s.day
            hour = s.hour

            for prod, pr in s.market_prices.items():
                daily_prices[(prod, day)].append(float(pr))

            if hour == 23:
                shed_cnt = sum(s.player_shed.values()) if s.player_shed else 0
                if shed_cnt > g_max_shed_night:
                    g_max_shed_night = shed_cnt
                if shed_cnt > 100:
                    overflow_lost_total += (shed_cnt - 100)

            if t in (0, 1, 2):
                ord_str = "; ".join(canonical_action_market([o]).replace("M[", "").replace("]", "") for o in s.player_market_act) if s.player_market_act else "NONE"
                opening_orders_tracker[t][ord_str] += 1

            if len(s.player_market_act) >= MAX_MARKET_ORDERS:
                full_queue_count += 1

            if s.player_farmer_act and len(s.player_farmer_act) > 0 and s.player_farmer_act[0] == "FERTILIZE":
                fertilizer_used += 1
            for h in s.player_hands_act:
                if h and len(h) > 0 and h[0] == "FERTILIZE":
                    fertilizer_used += 1

            market_orders = s.player_market_act or []
            sells_in_turn = []
            buys_hires_in_turn = []
            turn_wheat_buy = 0
            turn_wheat_sell = 0

            for o_idx, order in enumerate(market_orders):
                if not isinstance(order, list) or not order:
                    continue
                cmd = str(order[0]).upper()
                item = str(order[1]).upper() if len(order) > 1 else ""
                qty = float(order[2]) if len(order) > 2 and str(order[2]).replace('.', '', 1).isdigit() else 1.0

                if cmd == "SELL":
                    sells_in_turn.append((o_idx, item, qty))
                    sell_heatmap[item][(day, hour)] += qty
                    first_sale_tracker[item].append(t)
                    last_sale_tracker[item].append(t)
                    product_sale_turns[item].append(t)

                    price_now = float(s.market_prices.get(item, 0.0))
                    sale_val = price_now * qty
                    g_rev += sale_val
                    if day == 29:
                        g_rev_d30 += sale_val
                    if t in (718, 719):
                        g_rev_718 += sale_val

                    if (item, day) not in player_sold_prod_day:
                        player_sold_prod_day[(item, day)] = t

                    if item == "WHEAT":
                        turn_wheat_sell += qty
                    elif item == "FERTILIZER":
                        fertilizer_sold += qty

                elif "BUY" in cmd or cmd == "HIRE":
                    buys_hires_in_turn.append((o_idx, cmd, item, qty))
                    if item == "WHEAT":
                        turn_wheat_buy += qty
                    elif item == "FERTILIZER":
                        fertilizer_bought += qty

            for o_ord in s.opp_market_act:
                if isinstance(o_ord, list) and o_ord and str(o_ord[0]).upper() == "SELL" and len(o_ord) > 1:
                    o_item = str(o_ord[1]).upper()
                    if (o_item, day) not in opp_sold_prod_day:
                        opp_sold_prod_day[(o_item, day)] = t

            if sells_in_turn and buys_hires_in_turn:
                turns_with_mixed_orders += 1
                max_sell_idx = max(x[0] for x in sells_in_turn)
                min_buy_idx = min(x[0] for x in buys_hires_in_turn)
                if max_sell_idx < min_buy_idx:
                    turns_sells_precede += 1

            if len(sells_in_turn) >= 2:
                turns_with_multi_sells += 1
                prices = [float(s.market_prices.get(x[1], 0.0)) for x in sells_in_turn]
                if prices == sorted(prices, reverse=True):
                    turns_sells_sorted += 1

            if turn_wheat_buy > 0 and turn_wheat_sell > 0:
                wheat_round_trips += 1
                w_price = float(s.market_prices.get("WHEAT", 25.0))
                wheat_rt_profit += (min(turn_wheat_buy, turn_wheat_sell) * (w_price * 0.1))

            if default_base:
                base_raw_m = default_base.base_actions[t].get("market", []) if t < len(default_base.base_actions) else []
                b_wheat = sum(int(o[2]) if len(o) > 2 and str(o[2]).isdigit() else 1 for o in base_raw_m if len(o) > 1 and "WHEAT" in str(o[1]).upper() and "BUY" in str(o[0]).upper())
                if turn_wheat_buy > b_wheat:
                    wheat_topups[day] += int(turn_wheat_buy - b_wheat)

        overnight_shed_maxes.append(g_max_shed_night)
        total_rev_all += g_rev
        rev_d30_all += g_rev_d30
        rev_step718_all += g_rev_718

        for (prod, d), p_step in player_sold_prod_day.items():
            if (prod, d) in opp_sold_prod_day:
                presale_opportunities += 1
                o_step = opp_sold_prod_day[(prod, d)]
                if p_step < o_step:
                    presale_player_first += 1
                    p_price = float(g.steps[p_step].market_prices.get(prod, 0.0))
                    o_price = float(g.steps[o_step].market_prices.get(prod, 0.0))
                    presale_price_diffs.append(p_price - o_price)

        for (prod, d), pr_list in daily_prices.items():
            if (prod, d) in player_sold_prod_day and pr_list:
                avg_pr = sum(pr_list) / len(pr_list)
                realized_pr = float(g.steps[player_sold_prod_day[(prod, d)]].market_prices.get(prod, avg_pr))
                realized_price_acc[prod].append((realized_pr, avg_pr))

    term_liq_d30 = (rev_d30_all / total_rev_all * 100.0) if total_rev_all > 0 else 0.0
    term_liq_718 = (rev_step718_all / total_rev_all * 100.0) if total_rev_all > 0 else 0.0

    sell_precede_pct = (turns_sells_precede / turns_with_mixed_orders * 100.0) if turns_with_mixed_orders > 0 else 100.0
    sells_sorted_pct = (turns_sells_sorted / turns_with_multi_sells * 100.0) if turns_with_multi_sells > 0 else 100.0

    first_sale_res = {p: min(steps) if steps else None for p, steps in first_sale_tracker.items()}
    last_sale_res = {p: max(steps) if steps else None for p, steps in last_sale_tracker.items()}

    all_heatmap_cells = []
    for prod, dh_map in sell_heatmap.items():
        for (d, h), qty in dh_map.items():
            all_heatmap_cells.append((prod, d + 1, h, qty / n_games))
    all_heatmap_cells.sort(key=lambda x: x[3], reverse=True)
    top_cells = all_heatmap_cells[:8]

    sell_split_pattern = {}
    for prod, t_list in product_sale_turns.items():
        avg_turns_per_game = len(t_list) / n_games
        if avg_turns_per_game <= 15:
            sell_split_pattern[prod] = "Single Block / Bulk Dumps (Periodic)"
        else:
            sell_split_pattern[prod] = "Continuous / Spread across turns"

    realized_pct_map = {}
    for prod, pr_pairs in realized_price_acc.items():
        if pr_pairs:
            mean_real = sum(x[0] for x in pr_pairs) / len(pr_pairs)
            mean_day = sum(x[1] for x in pr_pairs) / len(pr_pairs)
            realized_pct_map[prod] = (mean_real / mean_day * 100.0) if mean_day > 0 else 100.0

    presale_pct = (presale_player_first / presale_opportunities * 100.0) if presale_opportunities > 0 else 0.0
    presale_diff_avg = (sum(presale_price_diffs) / len(presale_price_diffs)) if presale_price_diffs else 0.0

    avg_shed_max = sum(overnight_shed_maxes) / len(overnight_shed_maxes) if overnight_shed_maxes else 0.0

    opening_orders_list = []
    for step_num in (0, 1, 2):
        c = opening_orders_tracker[step_num]
        for ord_s, count in c.most_common(2):
            pct = (count / n_games) * 100.0
            opening_orders_list.append((step_num, [ord_s], count, pct))

    full_queue_pct = (full_queue_count / (n_games * TOTAL_STEPS) * 100.0) if n_games > 0 else 0.0

    return SellingMarketResult(
        sell_heatmap=sell_heatmap,
        top_heatmap_cells=top_cells,
        first_sale_steps=first_sale_res,
        last_sale_steps=last_sale_res,
        terminal_liquidation_share_d30=term_liq_d30,
        terminal_liquidation_share_step718=term_liq_718,
        sell_precedes_buy_pct=sell_precede_pct,
        sells_sorted_by_value_pct=sells_sorted_pct,
        sell_split_pattern=sell_split_pattern,
        realised_vs_day_price_pct=realized_pct_map,
        presale_rate_pct=presale_pct,
        presale_price_advantage=presale_diff_avg,
        overnight_shed_max_avg=avg_shed_max,
        overflow_units_lost_total=overflow_lost_total / n_games,
        opening_orders=opening_orders_list,
        wheat_round_trips_count=int(wheat_round_trips / n_games),
        wheat_round_trip_profit_est=wheat_rt_profit / n_games,
        wheat_topups_by_day={d: int(cnt / n_games) for d, cnt in sorted(wheat_topups.items())},
        full_queue_turns_pct=full_queue_pct,
        fertilizer_bought=fertilizer_bought / n_games,
        fertilizer_sold=fertilizer_sold / n_games,
        fertilizer_used=fertilizer_used / n_games,
    )


# =============================================================================
# F. FIELD AND LABOR (Section 6 Extension)
# =============================================================================

@dataclass
class FieldLaborResult:
    hire_steps: List[int]
    hands_by_day: List[float]
    planting_calendar: Dict[str, List[Tuple[int, Tuple[int, int]]]]
    land_purchases: List[Tuple[int, int]]
    animal_purchases: List[Tuple[int, str, Tuple[int, int]]]
    feed_skip_rates: Dict[str, float]
    layout_fingerprints_d6: Counter
    layout_fingerprints_d12: Counter
    layout_fingerprints_d20: Counter


def decode_field_and_labor(games: List[DecodedGame]) -> FieldLaborResult:
    """
    Decodes labor hiring exact schedule, planting timeline, pasture placement,
    feed skip rates per animal type, and layout fingerprints.
    """
    n_games = len(games)
    if n_games == 0:
        return FieldLaborResult([], [], {}, [], [], {}, Counter(), Counter(), Counter())

    hire_steps_counter = Counter()
    hands_daily_acc: List[List[int]] = [[] for _ in range(TOTAL_DAYS)]

    planting_tracker: Dict[str, List[Tuple[int, Tuple[int, int]]]] = defaultdict(list)
    land_purchases_tracker: List[Tuple[int, int]] = []
    animal_purchases_tracker: List[Tuple[int, str, Tuple[int, int]]] = []

    animal_total_days: Dict[str, int] = defaultdict(int)
    animal_skipped_days: Dict[str, int] = defaultdict(int)

    fp_d6 = Counter()
    fp_d12 = Counter()
    fp_d20 = Counter()

    for g in games:
        seen_quads = {1}
        for t, s in enumerate(g.steps):
            day = s.day
            hour = s.hour

            h_cnt = len(s.player_hands_act) if s.player_hands_act else 0
            hands_daily_acc[day].append(h_cnt)

            for m in s.player_market_act:
                if isinstance(m, list) and m:
                    mcmd = str(m[0]).upper()
                    if mcmd == "HIRE":
                        hire_steps_counter[t] += 1
                    elif mcmd == "BUY_ANIMAL" and len(m) > 1:
                        anim = str(m[1]).upper()
                        animal_purchases_tracker.append((t, anim, (0, 0)))
                    elif mcmd == "BUY_LAND":
                        for q in s.player_quads:
                            if q not in seen_quads:
                                seen_quads.add(q)
                                land_purchases_tracker.append((t, q))

            p_f = s.player_farmer_act or []
            if len(p_f) > 0:
                cmd = str(p_f[0]).upper()
                if cmd == "PLANT" and len(p_f) > 1:
                    crop = str(p_f[1]).upper()
                    planting_tracker[crop].append((t, (0, 0)))
                elif cmd in ("BUILD_PASTURE", "BUILD_COOP") and len(p_f) > 1:
                    anim = str(p_f[1]).upper() if len(p_f) > 1 else "PASTURE"
                    animal_purchases_tracker.append((t, anim, (0, 0)))

            for h in s.player_hands_act:
                if isinstance(h, list) and h:
                    cmd = str(h[0]).upper()
                    if cmd == "PLANT" and len(h) > 1:
                        crop = str(h[1]).upper()
                        planting_tracker[crop].append((t, (0, 0)))

            # Animal feeding: inspect animal tiles at end of day (hour 23)
            if hour == 23 and s.player_tiles:
                for row in s.player_tiles:
                    for cell in row:
                        if isinstance(cell, dict) and cell.get("kind") == "PASTURE":
                            anim = cell.get("animal")
                            if anim:
                                anim_str = str(anim).upper()
                                animal_total_days[anim_str] += 1
                                if not cell.get("fed_today", False):
                                    animal_skipped_days[anim_str] += 1

            if t == 143:
                fp = summarize_tile_layout(s.player_tiles)
                fp_d6[fp] += 1
            elif t == 287:
                fp = summarize_tile_layout(s.player_tiles)
                fp_d12[fp] += 1
            elif t == 479:
                fp = summarize_tile_layout(s.player_tiles)
                fp_d20[fp] += 1

    hire_steps_list = sorted([step for step, _ in hire_steps_counter.most_common(10)])

    hands_by_day = [
        (sum(daily_list) / len(daily_list)) if daily_list else 0.0
        for daily_list in hands_daily_acc
    ]

    feed_skip_rates = {}
    for anim, total_days in animal_total_days.items():
        skips = animal_skipped_days.get(anim, 0)
        feed_skip_rates[anim] = (skips / total_days * 100.0) if total_days > 0 else 0.0

    return FieldLaborResult(
        hire_steps=hire_steps_list,
        hands_by_day=hands_by_day,
        planting_calendar=planting_tracker,
        land_purchases=land_purchases_tracker,
        animal_purchases=animal_purchases_tracker,
        feed_skip_rates=feed_skip_rates,
        layout_fingerprints_d6=fp_d6,
        layout_fingerprints_d12=fp_d12,
        layout_fingerprints_d20=fp_d20,
    )


def summarize_tile_layout(tiles_grid: List[List[Any]]) -> str:
    """Creates a concise summary of a farm's tile layout."""
    counts = Counter()
    for row in tiles_grid:
        for cell in row:
            if isinstance(cell, dict):
                k = str(cell.get("crop") or cell.get("kind") or cell.get("type", "")).upper()
                counts[k] += 1
            elif cell == "LOCKED":
                counts["LOCKED"] += 1
            else:
                counts["DIRT"] += 1
    parts = [f"{k}:{v}" for k, v in sorted(counts.items()) if v > 0]
    return ";".join(parts)


# =============================================================================
# G. OPPONENT INTERACTION (Section 12)
# =============================================================================

@dataclass
class OpponentInteractionResult:
    daily_mirror_similarities: List[float]
    mirror_matches_count: int
    mirror_win_rate: float
    mirror_avg_margin: float
    non_mirror_matches_count: int
    non_mirror_win_rate: float
    non_mirror_avg_margin: float
    opponent_conditioned_deviations: Dict[str, str]


def decode_opponent_interaction(games: List[DecodedGame]) -> OpponentInteractionResult:
    """
    Decodes farm mirror similarity against opponents, mirror win rate,
    and opponent-conditioned opening deviations.
    """
    n_games = len(games)
    if n_games == 0:
        return OpponentInteractionResult([], 0, 0.0, 0.0, 0, 0.0, 0.0, {})

    daily_sims: List[List[float]] = [[] for _ in range(TOTAL_DAYS)]
    mirror_games: List[DecodedGame] = []
    non_mirror_games: List[DecodedGame] = []
    opp_early_acts: Dict[str, List[str]] = defaultdict(list)

    for g in games:
        # Check if match is self-play vs mirror bot opponent
        is_self_play = (g.player_name.lower() == g.opponent_name.lower())
        mirror_days_count = 0
        for d in range(TOTAL_DAYS):
            step_idx = min((d + 1) * STEPS_PER_DAY - 1, len(g.steps) - 1)
            s = g.steps[step_idx]

            p_grid = s.player_tiles
            o_grid = s.opp_tiles

            occupied_count = 0
            matching_count = 0

            for r in range(min(len(p_grid), len(o_grid))):
                for c in range(min(len(p_grid[r]), len(o_grid[r]))):
                    p_cell = p_grid[r][c]
                    o_cell = o_grid[r][c]

                    p_occ = (isinstance(p_cell, dict) and p_cell.get("kind") not in ("DIRT", "EMPTY", None))
                    o_occ = (isinstance(o_cell, dict) and o_cell.get("kind") not in ("DIRT", "EMPTY", None))

                    if p_occ or o_occ:
                        occupied_count += 1
                        p_kind = str(p_cell.get("crop") or p_cell.get("kind") if isinstance(p_cell, dict) else "").upper()
                        o_kind = str(o_cell.get("crop") or o_cell.get("kind") if isinstance(o_cell, dict) else "").upper()
                        if p_kind == o_kind:
                            matching_count += 1

            if occupied_count >= MIRROR_MIN_OCCUPIED_TILES:
                sim = matching_count / occupied_count
                daily_sims[d].append(sim)
                if sim >= MIRROR_SIMILARITY_THRESHOLD:
                    mirror_days_count += 1
            else:
                daily_sims[d].append(0.0)

        if mirror_days_count >= MIRROR_MIN_DAYS:
            mirror_games.append(g)
        else:
            non_mirror_games.append(g)

        early_sig = "|".join(g.steps[t].field_canonical for t in range(min(10, len(g.steps))))
        opp_early_acts[g.opponent_name].append(early_sig)

    avg_daily_sims = [
        (sum(s_list) / len(s_list)) if s_list else 0.0
        for s_list in daily_sims
    ]

    m_cnt = len(mirror_games)
    m_wins = sum(1 for g in mirror_games if g.result == "WIN")
    m_wr = (m_wins / m_cnt * 100.0) if m_cnt > 0 else 0.0
    m_margin = (sum(g.margin for g in mirror_games) / m_cnt) if m_cnt > 0 else 0.0

    nm_cnt = len(non_mirror_games)
    nm_wins = sum(1 for g in non_mirror_games if g.result == "WIN")
    nm_wr = (nm_wins / nm_cnt * 100.0) if nm_cnt > 0 else 0.0
    nm_margin = (sum(g.margin for g in non_mirror_games) / nm_cnt) if nm_cnt > 0 else 0.0

    opp_deviations = {}
    overall_mode_sig = Counter(
        "|".join(g.steps[t].field_canonical for t in range(min(10, len(g.steps))))
        for g in games
    ).most_common(1)[0][0]

    for opp_name, sig_list in opp_early_acts.items():
        if len(sig_list) >= 2:
            unique_sigs = set(sig_list)
            if len(unique_sigs) == 1 and list(unique_sigs)[0] != overall_mode_sig:
                opp_deviations[opp_name] = f"Executes dedicated counter opening in {len(sig_list)} matches"

    return OpponentInteractionResult(
        daily_mirror_similarities=avg_daily_sims,
        mirror_matches_count=m_cnt,
        mirror_win_rate=m_wr,
        mirror_avg_margin=m_margin,
        non_mirror_matches_count=nm_cnt,
        non_mirror_win_rate=nm_wr,
        non_mirror_avg_margin=nm_margin,
        opponent_conditioned_deviations=opp_deviations,
    )


# =============================================================================
# H. STRATEGIC INVARIANTS (Section 9)
# =============================================================================

@dataclass
class InvariantRule:
    rule_description: str
    occurrence_count: int
    frequency_pct: float


@dataclass
class InvariantResult:
    invariant_actions_top30: List[Tuple[int, str, str, float]]
    total_invariant_actions_count: int
    invariants_by_route: Dict[str, int]
    derived_market_rules: List[InvariantRule]


def decode_invariants(
    games: List[DecodedGame],
    script_res: ScriptIdentificationResult,
) -> InvariantResult:
    """
    Identifies deterministic invariant actions and derives invariant data rules (>=95%).
    """
    n_games = len(games)
    if n_games == 0:
        return InvariantResult([], 0, {}, [])

    unit_step_counts: Dict[Tuple[int, str, str], int] = defaultdict(int)

    for g in games:
        for t, s in enumerate(g.steps):
            f_cmd = ":".join(str(x).upper() for x in s.player_farmer_act) if s.player_farmer_act else "PASS"
            unit_step_counts[(t, "farmer", f_cmd)] += 1

            for h_i, h in enumerate(s.player_hands_act):
                h_cmd = ":".join(str(x).upper() for x in h) if h else "PASS"
                unit_step_counts[(t, f"hand_{h_i}", h_cmd)] += 1

            for m_i, m in enumerate(s.player_market_act):
                m_cmd = ":".join(str(x).upper() for x in m) if m else "NONE"
                unit_step_counts[(t, f"market_{m_i}", m_cmd)] += 1

    all_invariants = []
    for (step, unit, cmd), count in unit_step_counts.items():
        pct = (count / n_games) * 100.0
        if pct >= INVARIANT_THRESHOLD * 100.0:
            all_invariants.append((step, unit, cmd, pct))

    all_invariants.sort(key=lambda x: (x[0], x[1]))
    top30 = all_invariants[:30]
    total_invariants_count = len(all_invariants)

    invariants_by_route = {}
    for c in script_res.clusters:
        c_games = [games[idx] for idx in c.game_indices]
        c_size = len(c_games)
        if c_size == 0:
            continue
        c_step_invs = 0
        for t in range(TOTAL_STEPS):
            counts = Counter(g.steps[t].field_canonical for g in c_games if t < len(g.steps))
            if counts:
                f_mode_count = counts.most_common(1)[0][1]
                if (f_mode_count / c_size) >= INVARIANT_THRESHOLD:
                    c_step_invs += 1
        invariants_by_route[c.cluster_name] = c_step_invs

    derived_rules = []

    # Step 0 opening orders rule
    step0_acts = Counter(g.steps[0].market_canonical for g in games if len(g.steps) > 0)
    if step0_acts:
        step0_top = step0_acts.most_common(1)[0]
        if (step0_top[1] / n_games) >= 0.90:
            derived_rules.append(InvariantRule(
                rule_description="Fixed Opening Market Orders: Step 0 market orders executed with exact pre-compiled buys",
                occurrence_count=step0_top[1],
                frequency_pct=(step0_top[1] / n_games) * 100.0,
            ))

    h23_sales = sum(
        1 for g in games
        if any(g.steps[t].player_market_act and any(str(o[0]).upper() == "SELL" for o in g.steps[t].player_market_act if isinstance(o, list) and o)
               for t in range(23, len(g.steps), 24))
    )
    if (h23_sales / n_games) >= 0.85:
        derived_rules.append(InvariantRule(
            rule_description="Night Shed Sweep: Sells inventory at hour 23 when shed holds accumulated harvest",
            occurrence_count=h23_sales,
            frequency_pct=(h23_sales / n_games) * 100.0,
        ))

    term_sells = sum(
        1 for g in games
        if any(g.steps[t].player_market_act and any(str(o[0]).upper() == "SELL" for o in g.steps[t].player_market_act if isinstance(o, list) and o)
               for t in range(696, min(720, len(g.steps))))
    )
    if (term_sells / n_games) >= 0.90:
        derived_rules.append(InvariantRule(
            rule_description="Terminal Liquidation: Final liquidation sweeps executed across turns 696–719",
            occurrence_count=term_sells,
            frequency_pct=(term_sells / n_games) * 100.0,
        ))

    max_seed_steps = []
    for g in games:
        last_s = 0
        for t, s in enumerate(g.steps):
            if any(isinstance(o, list) and o and str(o[0]).upper() == "BUY_SEED" for o in s.player_market_act):
                last_s = t
        max_seed_steps.append(last_s)
    if max_seed_steps:
        p95_cutoff = sorted(max_seed_steps)[int(len(max_seed_steps) * 0.95)]
        derived_rules.append(InvariantRule(
            rule_description=f"Seed Purchasing Horizon: Halts all seed purchases after Turn {p95_cutoff} (Day {p95_cutoff//24+1})",
            occurrence_count=int(n_games * 0.95),
            frequency_pct=95.0,
        ))

    return InvariantResult(
        invariant_actions_top30=top30,
        total_invariant_actions_count=total_invariants_count,
        invariants_by_route=invariants_by_route,
        derived_market_rules=derived_rules,
    )


# =============================================================================
# I. ERAS AND OUTCOMES (Section 13)
# =============================================================================

@dataclass
class EraInfo:
    era_id: int
    era_name: str
    date_start: datetime
    date_end: datetime
    match_count: int
    wins: int
    losses: int
    ties: int
    win_rate: float
    avg_margin: float


@dataclass
class EraOutcomeResult:
    eras: List[EraInfo]
    win_rate_by_cluster: Dict[str, Tuple[int, float, float]]
    win_rate_by_shop_pair: Dict[str, Tuple[int, float, float]]
    loss_divergence_stats: Dict[str, Any]


def decode_eras_and_outcomes(
    games: List[DecodedGame],
    script_res: ScriptIdentificationResult,
) -> EraOutcomeResult:
    """
    Detects chronological strategy eras using statistical change-point detection on the match timeline,
    summarizes win rates by route and shop, and computes loss divergence points.
    """
    n_games = len(games)
    if n_games == 0:
        return EraOutcomeResult([], {}, {}, {})

    def get_sort_key(g: DecodedGame):
        if g.timestamp:
            return (g.timestamp.timestamp(), g.episode_id)
        if str(g.episode_id).isdigit():
            return (float(g.episode_id), g.episode_id)
        return (0.0, g.episode_id)

    time_games = sorted(games, key=get_sort_key)

    # Change-point detection across the sequence of games
    min_seg_len = 10 if n_games >= 30 else max(1, n_games // 3)

    if n_games < 2 * min_seg_len:
        split_points = [0, n_games]
    else:
        best_splits = [0, n_games]
        best_score = float("inf")

        # 1-split search
        for i in range(min_seg_len, n_games - min_seg_len + 1):
            m1 = [g.margin for g in time_games[:i]]
            m2 = [g.margin for g in time_games[i:]]
            v1 = sum((x - sum(m1)/len(m1))**2 for x in m1)
            v2 = sum((x - sum(m2)/len(m2))**2 for x in m2)
            score = v1 + v2
            if score < best_score:
                best_score = score
                best_splits = [0, i, n_games]

        # 2-splits search
        for i in range(min_seg_len, n_games - 2 * min_seg_len + 1):
            for j in range(i + min_seg_len, n_games - min_seg_len + 1):
                m1 = [g.margin for g in time_games[:i]]
                m2 = [g.margin for g in time_games[i:j]]
                m3 = [g.margin for g in time_games[j:]]
                v1 = sum((x - sum(m1)/len(m1))**2 for x in m1)
                v2 = sum((x - sum(m2)/len(m2))**2 for x in m2)
                v3 = sum((x - sum(m3)/len(m3))**2 for x in m3)
                score = (v1 + v2 + v3) * 1.05
                if score < best_score:
                    best_score = score
                    best_splits = [0, i, j, n_games]

        split_points = best_splits

    eras: List[EraInfo] = []
    for era_idx in range(len(split_points) - 1):
        start_i = split_points[era_idx]
        end_i = split_points[era_idx + 1]
        era_games = time_games[start_i:end_i]
        if not era_games:
            continue

        e_wins = sum(1 for g in era_games if g.result == "WIN")
        e_losses = sum(1 for g in era_games if g.result == "LOSS")
        e_ties = sum(1 for g in era_games if g.result == "TIE")
        e_cnt = len(era_games)
        e_wr = (e_wins / e_cnt * 100.0) if e_cnt > 0 else 0.0
        e_margin = (sum(g.margin for g in era_games) / e_cnt) if e_cnt > 0 else 0.0

        d_start = era_games[0].timestamp or datetime.now(timezone.utc)
        d_end = era_games[-1].timestamp or datetime.now(timezone.utc)

        eras.append(EraInfo(
            era_id=era_idx + 1,
            era_name=f"Era {era_idx + 1}",
            date_start=d_start,
            date_end=d_end,
            match_count=e_cnt,
            wins=e_wins,
            losses=e_losses,
            ties=e_ties,
            win_rate=e_wr,
            avg_margin=e_margin,
        ))

    cluster_outcomes: Dict[str, Tuple[int, float, float]] = {}
    for c in script_res.clusters:
        c_games = [games[idx] for idx in c.game_indices]
        c_cnt = len(c_games)
        c_wins = sum(1 for g in c_games if g.result == "WIN")
        c_wr = (c_wins / c_cnt * 100.0) if c_cnt > 0 else 0.0
        c_margin = (sum(g.margin for g in c_games) / c_cnt) if c_cnt > 0 else 0.0
        cluster_outcomes[c.cluster_name] = (c_cnt, c_wr, c_margin)

    shop_outcomes: Dict[str, Tuple[int, float, float]] = {}
    pair_grouped: Dict[str, List[DecodedGame]] = defaultdict(list)
    for g in games:
        p_str = f"{g.shop_pair[0]} -> {g.shop_pair[1]}"
        pair_grouped[p_str].append(g)

    for p_str, g_list in pair_grouped.items():
        cnt = len(g_list)
        wins = sum(1 for g in g_list if g.result == "WIN")
        wr = (wins / cnt * 100.0) if cnt > 0 else 0.0
        margin = (sum(g.margin for g in g_list) / cnt) if cnt > 0 else 0.0
        shop_outcomes[p_str] = (cnt, wr, margin)

    loss_divergence_steps = []
    for g in games:
        if g.result == "LOSS":
            div_step = None
            for t, s in enumerate(g.steps):
                cash_lead = s.opp_money - s.player_money
                if cash_lead >= LOSS_DIVERGENCE_MARGIN:
                    div_step = t
                    break
            if div_step is not None:
                loss_divergence_steps.append(div_step)

    loss_stats = {}
    if loss_divergence_steps:
        loss_divergence_steps.sort()
        cnt = len(loss_divergence_steps)
        mean_step = sum(loss_divergence_steps) / cnt
        med_step = loss_divergence_steps[cnt // 2]
        min_step = loss_divergence_steps[0]
        max_step = loss_divergence_steps[-1]
        q1 = loss_divergence_steps[int(cnt * 0.25)]
        q3 = loss_divergence_steps[int(cnt * 0.75)]
        loss_stats = {
            "count": cnt,
            "mean_step": mean_step,
            "mean_day": (mean_step / 24.0) + 1.0,
            "median_step": med_step,
            "median_day": (med_step / 24.0) + 1.0,
            "min_step": min_step,
            "max_step": max_step,
            "q1_step": q1,
            "q3_step": q3,
        }

    return EraOutcomeResult(
        eras=eras,
        win_rate_by_cluster=cluster_outcomes,
        win_rate_by_shop_pair=shop_outcomes,
        loss_divergence_stats=loss_stats,
    )


# =============================================================================
# ARTIFACT EXPORTERS (CSV / JSON)
# =============================================================================

def export_strategy_artifacts(
    out_dir: Path,
    player_prefix: str,
    script_res: ScriptIdentificationResult,
    route_res: RouteSelectionResult,
    overlay_res: ScriptOverlayResult,
    selling_res: SellingMarketResult,
    field_res: FieldLaborResult,
    opp_res: OpponentInteractionResult,
    era_res: EraOutcomeResult,
    inv_res: InvariantResult,
) -> None:
    """Writes detailed tables to CSV and JSON next to the report."""
    out_dir.mkdir(parents=True, exist_ok=True)

    heatmap_file = out_dir / f"{player_prefix}_selling_heatmap.csv"
    with open(heatmap_file, "w", encoding="utf-8") as f:
        f.write("product,day,hour,total_quantity_sold\n")
        for prod, dh_map in selling_res.sell_heatmap.items():
            for (d, h), qty in sorted(dh_map.items()):
                f.write(f"{prod},{d+1},{h},{qty:.1f}\n")

    clusters_file = out_dir / f"{player_prefix}_route_clusters.json"
    clusters_data = [
        {
            "cluster_id": c.cluster_id,
            "cluster_name": c.cluster_name,
            "match_count": c.match_count,
            "match_share_pct": round(c.match_share_pct, 2),
            "public_route_name": c.public_route_name,
            "public_match_pct": round(c.public_match_pct, 2),
            "public_match_category": c.public_match_category,
        }
        for c in script_res.clusters
    ]
    clusters_file.write_text(json.dumps(clusters_data, indent=2), encoding="utf-8")

    invariants_file = out_dir / f"{player_prefix}_action_invariants.csv"
    with open(invariants_file, "w", encoding="utf-8") as f:
        f.write("step,day,hour,unit,command,frequency_pct\n")
        for step, unit, cmd, freq in inv_res.invariant_actions_top30:
            f.write(f"{step},{step//24+1},{step%24},{unit},\"{cmd}\",{freq:.1f}\n")

    overlays_file = out_dir / f"{player_prefix}_conditional_investments.json"
    inv_data = [
        {
            "episode_id": ev.episode_id,
            "step": ev.step,
            "day": ev.day + 1,
            "hour": ev.hour,
            "category": ev.category,
            "action": ev.action_str,
            "cash": round(ev.cash, 2),
            "prices": ev.prices,
            "unlocked_shops": ev.shops,
        }
        for ev in overlay_res.conditional_investments
    ]
    overlays_file.write_text(json.dumps(inv_data, indent=2), encoding="utf-8")


# =============================================================================
# REPORT SECTION RENDERERS
# =============================================================================

def render_extended_section_9(
    script_res: ScriptIdentificationResult,
    route_res: RouteSelectionResult,
    overlay_res: ScriptOverlayResult,
    inv_res: InvariantResult,
    total_matches: int,
) -> List[str]:
    """Generates the newly enhanced Section 9 sub-blocks with all decoded strategy variables."""
    lines = []
    lines.append("  [DEEP STRATEGY DECODING & SCRIPT IDENTIFICATION (strategy_decoder)]")
    lines.append(f"  * Deterministic Opening Trunk:   {script_res.common_trunk_length} steps (100% identical FIELD actions across all matches)")
    branch_str = ", ".join(f"Turn {s}" for s in script_res.branch_steps[:8]) if script_res.branch_steps else "None"
    lines.append(f"  * Key Branch Points:             {branch_str}")
    endgame_str = f"Turn {script_res.endgame_shared_from_step} (Day {script_res.endgame_shared_from_step//24+1})" if script_res.endgame_shared_from_step is not None else "No endgame convergence across all games"
    lines.append(f"  * Endgame Action Convergence:    {endgame_str}")
    lines.append(f"  * Public Route Library Alignment: {script_res.library_status_msg}")
    lines.append("")

    lines.append("  [Route Clustering Analysis (Days 6–26 / Steps 144–647, Tolerance <= 10%)]")
    lines.append("  " + f"{'Cluster':<14} {'Matches':<14} {'Share':<10} {'Public Alignment':<28} {'Match %':<10}")
    lines.append("  " + "-" * 78)
    displayed_clusters = script_res.clusters[:8]
    for c in displayed_clusters:
        lines.append(
            f"  {c.cluster_name:<14} {c.match_count:>4} matches  "
            f"{c.match_share_pct:>6.1f}%   "
            f"{c.public_match_category[:26]:<28} "
            f"{c.public_match_pct:>6.1f}%"
        )
    if len(script_res.clusters) > 8:
        remaining_cnt = sum(c.match_count for c in script_res.clusters[8:])
        remaining_share = (remaining_cnt / total_matches) * 100.0 if total_matches > 0 else 0.0
        lines.append(f"  ... and {len(script_res.clusters) - 8} additional minor route clusters ({remaining_cnt} matches, {remaining_share:.1f}% share - details in JSON artifact)")
    lines.append("")

    lines.append("  [Shop-Driven Route Selection Table (at Step 144 / Day 6 Unlock)]")
    lines.append("  " + f"{'Shop 1 -> Shop 2':<34} {'Assigned Route':<16} {'Matches':<10} {'Win Rate':<10} {'Avg Margin':>11}")
    lines.append("  " + "-" * 85)
    for entry in route_res.table[:8]:
        lines.append(
            f"  {entry.shop1 + ' -> ' + entry.shop2:<34} "
            f"{entry.cluster_name:<16} "
            f"{entry.matches:>4} matches "
            f"{entry.win_rate:>6.1f}%    "
            f"{'+' if entry.avg_margin>=0 else ''}${entry.avg_margin:>9,.0f}"
        )
    if len(route_res.table) > 8:
        lines.append(f"  ... and {len(route_res.table) - 8} additional shop-route pairs (details in JSON artifact)")
    lines.append("")

    lines.append("  [Router Leave-One-Out (LOO) Key Accuracy vs Majority Baseline]")
    lines.append(f"    - Router Consistency Rate:     {route_res.router_consistency_pct:.1f}% of games follow dominant cluster for their shop pair")
    lines.append(f"    - First Route Divergence Step: Step {route_res.route_decision_step} (Day {route_res.route_decision_step//24+1})")
    lines.append("    - Router Key Accuracy Benchmark (LOO-CV):")
    for k_name, acc in route_res.router_key_accuracies.items():
        lines.append(f"        * {k_name:<44}: {acc:>5.1f}% accuracy")
    lines.append(f"    - Optimal Route Classifier Key: {route_res.best_predictive_key}")
    if route_res.unmapped_pairs:
        unmapped_str = ", ".join(f"{s1}->{s2}" for s1, s2 in route_res.unmapped_pairs[:5])
        lines.append(f"    - Unmapped / Default Route Pairs: {unmapped_str}")
    lines.append("")

    lines.append("  [Adaptive Script Overlays & Micro-Adjustments]")
    lines.append(f"    - Field Deviation Rate:        {overlay_res.avg_deviation_rate_field:.2f}% of turn steps differ from base cluster script")
    lines.append(f"    - Market Deviation Rate:       {overlay_res.avg_deviation_rate_market:.2f}% of turn steps differ from base cluster script")
    lines.append(f"    - Reactivity Score:            {overlay_res.reactivity_score:.2f}% (Mean pairwise action diff between same-shop games)")
    lines.append("")
    lines.append("  [Overlay Deviation Categories & Active Time Windows]")
    if overlay_res.deviation_type_counts:
        for cat, count in sorted(overlay_res.deviation_type_counts.items(), key=lambda x: x[1], reverse=True):
            win_str = overlay_res.active_windows_summary.get(cat, "Various turns")
            lines.append(f"    - {cat:<24}: {count:>6} occurrences | Active: {win_str}")
    else:
        lines.append("    (No off-script deviations detected)")
    lines.append("")

    if overlay_res.investment_trigger_ranges:
        lines.append("  [Off-Script Conditional Investment Triggers]")
        for inv_type, trig_str in overlay_res.investment_trigger_ranges.items():
            lines.append(f"    - {inv_type:<18}: {trig_str}")
        lines.append("")

    lines.append("  [Strategic Action & Market Invariants (>= 95% Determinism)]")
    lines.append(f"    - Total Invariant Actions:     {inv_res.total_invariant_actions_count} deterministic unit-step actions across all games")
    if inv_res.derived_market_rules:
        lines.append("    - Derived Invariant Market Rules:")
        for rule in inv_res.derived_market_rules:
            lines.append(f"        * {rule.rule_description} ({rule.frequency_pct:.1f}% determinism)")
    lines.append("")

    return lines


def render_extended_section_6(field_res: FieldLaborResult) -> List[str]:
    """Appends labor schedule and layout details to Section 6."""
    lines = []
    lines.append("  [Labor Scheduling & Plant/Livestock Deployment]")
    hire_str = ", ".join(f"Turn {s}" for s in field_res.hire_steps[:8]) if field_res.hire_steps else "None"
    lines.append(f"    - Key Hiring Turn Milestones:  {hire_str}")
    if field_res.feed_skip_rates:
        feed_str = " | ".join(f"{anim}: {skip:.1f}% skipped" for anim, skip in sorted(field_res.feed_skip_rates.items()))
        lines.append(f"    - Livestock Feed Skip Rates:   {feed_str}")
    if field_res.layout_fingerprints_d6:
        top_d6 = field_res.layout_fingerprints_d6.most_common(1)[0]
        lines.append(f"    - Day 6 Layout Fingerprint:    {top_d6[0][:60]} ({top_d6[1]} matches)")
    if field_res.layout_fingerprints_d20:
        top_d20 = field_res.layout_fingerprints_d20.most_common(1)[0]
        lines.append(f"    - Day 20 Layout Fingerprint:   {top_d20[0][:60]} ({top_d20[1]} matches)")
    lines.append("")
    return lines


def render_section_11(selling_res: SellingMarketResult) -> List[str]:
    """Renders Section 11: Selling Pattern & Market Moves."""
    lines = []
    lines.append("-" * 80)
    lines.append("11. SELLING PATTERN & MARKET TRADING MOVES")
    lines.append("-" * 80)
    lines.append(f"  * Terminal Liquidation Share:    {selling_res.terminal_liquidation_share_d30:.1f}% of total revenue in Day 30 | {selling_res.terminal_liquidation_share_step718:.1f}% at Step 718/719")
    lines.append(f"  * Market Order Prioritization:   {selling_res.sell_precedes_buy_pct:.1f}% turns execute all SELLs before BUYs | {selling_res.sells_sorted_by_value_pct:.1f}% sells sorted by unit value")
    lines.append(f"  * Presale Lead vs Opponent:      {selling_res.presale_rate_pct:.1f}% of shared product-days sold before opponent (Price Advantage: {selling_res.presale_price_advantage:+.1f}/unit)")
    lines.append(f"  * Storage Discipline:            Overnight Shed Max Avg: {selling_res.overnight_shed_max_avg:.1f} items | Lost to Overflow: {selling_res.overflow_units_lost_total:.1f} units")
    lines.append(f"  * Market Queue Saturation:       {selling_res.full_queue_turns_pct:.1f}% of turns utilize all 10 order slots")
    lines.append("")

    lines.append("  [Product Selling Milestones & Cadence]")
    lines.append("  " + f"{'Product':<14} {'First Sale':<16} {'Last Sale':<16} {'Realized / Day Price':<22} {'Cadence Profile':<24}")
    lines.append("  " + "-" * 94)
    all_prods = sorted(set(list(selling_res.first_sale_steps.keys()) + list(selling_res.realised_vs_day_price_pct.keys())))
    for prod in all_prods:
        f_s = selling_res.first_sale_steps.get(prod)
        l_s = selling_res.last_sale_steps.get(prod)
        f_str = f"Turn {f_s} (D{f_s//24+1})" if f_s is not None else "No sales"
        l_str = f"Turn {l_s} (D{l_s//24+1})" if l_s is not None else "No sales"
        p_pct = selling_res.realised_vs_day_price_pct.get(prod, 100.0)
        split_p = selling_res.sell_split_pattern.get(prod, "Balanced")
        lines.append(f"  {prod:<14} {f_str:<16} {l_str:<16} {p_pct:>6.1f}% avg price        {split_p:<24}")
    lines.append("")

    lines.append("  [Top Selling Windows (Day x Hour Heatmap Leaders - Avg Units Sold)]")
    if selling_res.top_heatmap_cells:
        for prod, day, hr, qty in selling_res.top_heatmap_cells[:6]:
            lines.append(f"    - Day {day:>2}, Hour {hr:>2} ({prod:<12}): {qty:>6.1f} units liquidated per match")
    lines.append("    (Complete 30x24 selling heatmap exported to CSV)")
    lines.append("")

    lines.append("  [Opening Market Orders (First 3 Turns)]")
    for step_num, ord_list, count, pct in selling_res.opening_orders:
        lines.append(f"    - Step {step_num}: {ord_list[0][:50]:<50} ({count} matches, {pct:.1f}%)")
    lines.append("")

    lines.append("  [Wheat Arbitrage & Fertilizer Flow]")
    lines.append(f"    - Wheat Round Trips (Buy->Sell):  {selling_res.wheat_round_trips_count} cycles/match | Est. Arbitrage Profit: ${selling_res.wheat_round_trip_profit_est:,.2f}")
    if selling_res.wheat_topups_by_day:
        topup_str = ", ".join(f"Day {d+1}: {cnt}u" for d, cnt in list(selling_res.wheat_topups_by_day.items())[:5])
        lines.append(f"    - Adaptive Wheat Topups:          {topup_str}")
    lines.append(f"    - Fertilizer Net Flow:            Bought: {selling_res.fertilizer_bought:,.1f}u | Sold: {selling_res.fertilizer_sold:,.1f}u | Applied in Field: {selling_res.fertilizer_used:,.1f}u")
    lines.append("")
    return lines


def render_section_12(opp_res: OpponentInteractionResult) -> List[str]:
    """Renders Section 12: Opponent Interaction."""
    lines = []
    lines.append("-" * 80)
    lines.append("12. OPPONENT INTERACTION & MIRROR MATCHUP DYNAMICS")
    lines.append("-" * 80)
    lines.append("  [Performance vs Mirror Opponents (>=90% Shared Farm Layout on >=6 Days)]")
    lines.append(f"  * Mirror Bot Matches:            {opp_res.mirror_matches_count} matches | Win Rate: {opp_res.mirror_win_rate:.1f}% | Avg Margin: {'+' if opp_res.mirror_avg_margin>=0 else ''}${opp_res.mirror_avg_margin:,.0f}")
    lines.append(f"  * Non-Mirror Opponent Matches:   {opp_res.non_mirror_matches_count} matches | Win Rate: {opp_res.non_mirror_win_rate:.1f}% | Avg Margin: {'+' if opp_res.non_mirror_avg_margin>=0 else ''}${opp_res.non_mirror_avg_margin:,.0f}")
    lines.append("")

    if opp_res.daily_mirror_similarities:
        d3_sim = opp_res.daily_mirror_similarities[2] * 100.0 if len(opp_res.daily_mirror_similarities) > 2 else 0.0
        d10_sim = opp_res.daily_mirror_similarities[9] * 100.0 if len(opp_res.daily_mirror_similarities) > 9 else 0.0
        d20_sim = opp_res.daily_mirror_similarities[19] * 100.0 if len(opp_res.daily_mirror_similarities) > 19 else 0.0
        d30_sim = opp_res.daily_mirror_similarities[29] * 100.0 if len(opp_res.daily_mirror_similarities) > 29 else 0.0
        lines.append(f"  * Mirror Similarity Trajectory:  Day 3: {d3_sim:.1f}% -> Day 10: {d10_sim:.1f}% -> Day 20: {d20_sim:.1f}% -> Day 30: {d30_sim:.1f}%")

    if opp_res.opponent_conditioned_deviations:
        lines.append("")
        lines.append("  [Rival-Specific Opening Counters (Steps 0–10)]")
        for opp_name, note in opp_res.opponent_conditioned_deviations.items():
            lines.append(f"    - Rival '{opp_name}': {note}")
    else:
        lines.append("  * Rival-Specific Counters:       None (Executes consistent universal opening regardless of opponent name)")
    lines.append("")
    return lines


def render_section_13(era_res: EraOutcomeResult) -> List[str]:
    """Renders Section 13: Eras & Outcomes."""
    lines = []
    lines.append("-" * 80)
    lines.append("13. ERAS & CHRONOLOGICAL OUTCOMES")
    lines.append("-" * 80)
    lines.append("  [Chronological Performance Eras (Statistical Change-Point Detection)]")
    lines.append("  " + f"{'Era ID':<10} {'Date Range (UTC)':<32} {'Matches':<10} {'Record (W/L/T)':<16} {'Win Rate':<10} {'Avg Margin':>11}")
    lines.append("  " + "-" * 93)
    for era in era_res.eras:
        d_range = f"{era.date_start.strftime('%Y-%m-%d %H:%M')} - {era.date_end.strftime('%m-%d %H:%M')}"
        rec = f"{era.wins}W / {era.losses}L / {era.ties}T"
        lines.append(
            f"  {era.era_name:<10} {d_range:<32} {era.match_count:>4} matches "
            f"{rec:<16} {era.win_rate:>6.1f}%    "
            f"{'+' if era.avg_margin>=0 else ''}${era.avg_margin:>9,.0f}"
        )
    lines.append("")

    lines.append("  [Outcome Breakdown by Route Cluster]")
    lines.append("  " + f"{'Route Cluster':<22} {'Matches':<10} {'Win Rate':<10} {'Avg Margin':>12}")
    lines.append("  " + "-" * 58)
    items = list(era_res.win_rate_by_cluster.items())
    for c_name, (cnt, wr, margin) in items[:6]:
        lines.append(f"  {c_name:<22} {cnt:>4} matches {wr:>6.1f}%    {'+' if margin>=0 else ''}${margin:>10,.0f}")
    if len(items) > 6:
        lines.append(f"  ... and {len(items) - 6} additional minor clusters (details in JSON artifact)")
    lines.append("")

    if era_res.loss_divergence_stats:
        ls = era_res.loss_divergence_stats
        lines.append("  [Loss Divergence Timing (Opponent Cash Lead >= $5,000)]")
        lines.append(f"    - Matches with Major Deficit:  {ls['count']} loss matches")
        lines.append(f"    - Mean Divergence Turn:        Turn {ls['mean_step']:.0f} (Day {ls['mean_day']:.1f})")
        lines.append(f"    - Median Divergence Turn:      Turn {ls['median_step']:.0f} (Day {ls['median_day']:.1f})")
        lines.append(f"    - Interquartile Range (Q1-Q3): Turn {ls['q1_step']} (Day {ls['q1_step']//24+1}) – Turn {ls['q3_step']} (Day {ls['q3_step']//24+1})")
        lines.append(f"    - Extreme Range:               Earliest: Turn {ls['min_step']} | Latest: Turn {ls['max_step']}")
    else:
        lines.append("  [Loss Divergence Timing]: No loss matches where opponent held >= $5,000 cash lead")
    lines.append("")
    return lines
