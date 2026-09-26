#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for strategy_decoder.py using synthetic fake games.
Verifies trunk length, clustering, router consistency, and invariant detection.
"""
from __future__ import annotations

from datetime import datetime, timezone
import unittest

from strategy_decoder import (
    DecodedGame,
    DecodedStep,
    TOTAL_STEPS,
    TOTAL_DAYS,
    canonical_action_field,
    canonical_action_market,
    compute_day_fingerprint,
    decode_script_identification,
    decode_route_selection,
    decode_script_overlays,
    decode_invariants,
    decode_eras_and_outcomes,
    load_reference_routes,
)


def make_synthetic_game(
    ep_id: str,
    shop1: str,
    shop2: str,
    route_variant: int,
    result: str = "WIN",
    margin: float = 10000.0,
    timestamp: datetime = None,
) -> DecodedGame:
    """Creates a deterministic synthetic DecodedGame with specified route behavior."""
    if timestamp is None:
        timestamp = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)

    steps = []
    daily_field = [[] for _ in range(TOTAL_DAYS)]

    for t in range(TOTAL_STEPS):
        day = t // 24
        hour = t % 24

        # Trunk: Steps 0..71 are 100% identical in all games
        if t < 72:
            farmer_act = ["PLANT", "MELON"] if t == 1 else ["PASS"]
            hands_act = [["WEST"]] if t > 2 else []
            market_act = [["BUY_SEED", "MELON", 2]] if t == 1 else []
        elif t < 144:
            # Pre-branch: slight variance depending on shop1
            farmer_act = ["WATER"] if shop1 == "BAKERY" else ["NORTH"]
            hands_act = [["HARVEST"]]
            market_act = [["SELL", "MELON", 1]] if hour == 23 else []
        elif t < 648:
            # Route variant branch (steps 144..647)
            if route_variant == 1:
                farmer_act = ["CARE"]
                hands_act = [["FEED", "COW"]]
                market_act = [["SELL", "MILK", 5]] if hour == 23 else []
            elif route_variant == 2:
                farmer_act = ["HARVEST"]
                hands_act = [["FEED", "SHEEP"]]
                market_act = [["SELL", "WOOL", 5]] if hour == 23 else []
            else:
                farmer_act = ["DIG"]
                hands_act = [["WEST"]]
                market_act = []
        else:
            # Endgame convergence: steps 648..719 are identical in all games!
            farmer_act = ["PASS"]
            hands_act = []
            market_act = [["SELL", "WHEAT", 10]] if t == 718 else []

        f_canon = canonical_action_field(farmer_act, hands_act)
        m_canon = canonical_action_market(market_act)
        daily_field[day].append(f_canon)

        steps.append(DecodedStep(
            step=t,
            day=day,
            hour=hour,
            player_farmer_act=farmer_act,
            player_hands_act=hands_act,
            player_market_act=market_act,
            opp_farmer_act=["PASS"],
            opp_hands_act=[],
            opp_market_act=[],
            player_money=1000.0,
            opp_money=500.0,
            player_shed={"MELON": 2, "MILK": 5},
            market_prices={"MELON": 250, "MILK": 160, "WOOL": 200, "WHEAT": 25},
            unlocked_shops=[shop1, shop2, "PIZZA"],
            player_tiles=[[{"kind": "PLANT", "crop": "MELON"}]],
            opp_tiles=[[{"kind": "PLANT", "crop": "MELON"}]],
            player_quads=[1, 2],
            opp_quads=[1],
            field_canonical=f_canon,
            market_canonical=m_canon,
        ))

    fingerprints = [compute_day_fingerprint(daily_field[d]) for d in range(TOTAL_DAYS)]

    return DecodedGame(
        episode_id=ep_id,
        file_name=f"{ep_id}.json",
        player_name="TestBot",
        opponent_name="OpponentBot",
        player_index=0,
        opponent_index=1,
        player_reward=100000.0,
        opponent_reward=90000.0,
        result=result,
        margin=margin,
        timestamp=timestamp,
        steps=steps,
        field_fingerprints=fingerprints,
        unlocked_shops=[shop1, shop2, "PIZZA"],
        shop_pair=(shop1, shop2),
        shop_triplet=(shop1, shop2, "PIZZA"),
    )


class TestStrategyDecoder(unittest.TestCase):

    def setUp(self):
        # 4 synthetic games:
        # Game 1: Bakery -> Smoothie (Variant 1)
        # Game 2: Bakery -> Smoothie (Variant 1)
        # Game 3: Yarn -> PetCafe (Variant 2)
        # Game 4: Yarn -> PetCafe (Variant 2)
        self.g1 = make_synthetic_game("g1", "BAKERY", "SMOOTHIE", route_variant=1, timestamp=datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc))
        self.g2 = make_synthetic_game("g2", "BAKERY", "SMOOTHIE", route_variant=1, timestamp=datetime(2026, 9, 23, 11, 0, tzinfo=timezone.utc))
        self.g3 = make_synthetic_game("g3", "YARN_STORE", "PET_CAFE", route_variant=2, timestamp=datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc))
        self.g4 = make_synthetic_game("g4", "YARN_STORE", "PET_CAFE", route_variant=2, timestamp=datetime(2026, 9, 23, 13, 0, tzinfo=timezone.utc))
        self.games = [self.g1, self.g2, self.g3, self.g4]

    def test_trunk_length(self):
        res = decode_script_identification(self.games, reference_routes={})
        # Common trunk length must be 72 turns
        self.assertEqual(res.common_trunk_length, 72)

    def test_endgame_convergence(self):
        res = decode_script_identification(self.games, reference_routes={})
        # All games converge at step 648..719
        self.assertIsNotNone(res.endgame_shared_from_step)
        self.assertEqual(res.endgame_shared_from_step, 648)

    def test_clustering(self):
        res = decode_script_identification(self.games, reference_routes={})
        # Should produce exactly 2 clusters of size 2 each
        self.assertEqual(len(res.clusters), 2)
        self.assertEqual(res.clusters[0].match_count, 2)
        self.assertEqual(res.clusters[1].match_count, 2)

    def test_router_consistency(self):
        script_res = decode_script_identification(self.games, reference_routes={})
        route_res = decode_route_selection(self.games, script_res)
        # 100% router consistency because identical shop pairs always chose the same cluster
        self.assertEqual(route_res.router_consistency_pct, 100.0)
        self.assertEqual(route_res.router_key_accuracies["First 1 Shop (Shop 1)"], 100.0)
        self.assertEqual(route_res.router_key_accuracies["First 2 Ordered (Shop 1 -> Shop 2)"], 100.0)

    def test_invariant_detection(self):
        script_res = decode_script_identification(self.games, reference_routes={})
        inv_res = decode_invariants(self.games, script_res)
        # Invariants at step 0, 1 (trunk) and step 648..719 (endgame) must be detected
        self.assertGreater(inv_res.total_invariant_actions_count, 50)
        self.assertTrue(any(item[0] == 1 for item in inv_res.invariant_actions_top30))

    def test_overlays_and_diffs(self):
        script_res = decode_script_identification(self.games, reference_routes={})
        overlay_res = decode_script_overlays(self.games, script_res)
        # Since games match their cluster perfectly, field deviation should be 0.0%
        self.assertEqual(overlay_res.avg_deviation_rate_field, 0.0)


if __name__ == "__main__":
    unittest.main()
