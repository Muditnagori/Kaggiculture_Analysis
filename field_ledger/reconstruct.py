# SPDX-License-Identifier: Apache-2.0
"""
Field Ledger: turn-by-turn economic reconstruction of a Kaggriculture match,
built from a raw Kaggle episode replay JSON (the format kaggle_environments
exports, e.g. the "steps" list with per-player observation/action/reward).

This is intentionally standard-library only, so it runs anywhere with
Python 3.9+ and no pip installs.

WHAT THIS DOES NOT DO: it does not predict future prices. Every "projected"
harvest value uses the market price AT THE STEP BEING RECONSTRUCTED as a
stand-in for the true expected price at harvest time, because no price
forecasting model is available in the public replay data. Treat projected
value as "what this inventory is worth if sold at today's prices", not a
guaranteed future number.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Data access helpers -- replay JSON is plain dicts, no Struct/attr access
# needed here (unlike the live agent, which must handle both).
# ---------------------------------------------------------------------------

def load_replay(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def team_names(replay: dict) -> list[str]:
    return replay.get("info", {}).get("TeamNames", ["player_0", "player_1"])


def num_steps(replay: dict) -> int:
    return len(replay["steps"])


# ---------------------------------------------------------------------------
# Per-step record
# ---------------------------------------------------------------------------

@dataclass
class StepRecord:
    step: int
    day: int
    cash: float
    shed_value: float          # harvested-but-unsold inventory, valued at current prices
    growing_value: float       # value of crops/animal-products still in the field, at current prices
    projected_value: float     # cash + shed_value + growing_value  (this step's V_p(t))
    idle_hands: int
    total_hands: int
    empty_tiles: int
    total_unlocked_tiles: int
    revenue_by_category: dict[str, float]   # this step's SELL income, by item
    spend_by_category: dict[str, float]     # this step's spend, by category (seed/animal/land/hire/product)
    prices: dict[str, float]                # market prices this step, for optional per-crop charts


CATEGORY_FOR_OP = {
    "BUY_SEED": "seed",
    "BUY_ANIMAL": "animal",
    "BUY_LAND": "land",
    "HIRE": "hire",
    "BUY_PRODUCT": "buy_product",
}

# Same empirically-derived fallback prices used in the submission's budget
# governor -- kept in sync here so spend estimates for non-priced order
# types (seeds, animals, land, hires) are consistent across both tools.
FALLBACK_COSTS = {
    ("BUY_SEED", "WHEAT"): 10,
    ("BUY_SEED", "MELON"): 80,
    ("BUY_SEED", "STRAWBERRY"): 100,
    ("BUY_SEED", "CARROT"): 40,
    ("BUY_SEED", "TOMATO"): 40,
    ("BUY_ANIMAL", "COW"): 400,
    ("BUY_ANIMAL", "SHEEP"): 250,
    ("BUY_ANIMAL", "GOOSE"): 250,
    ("BUY_LAND", None): 1000,
    ("HIRE", None): 8,
}
UNKNOWN_ITEM_COST_ESTIMATE = 500


def _order_cost(order: list, prices: dict) -> float:
    op = order[0]
    if op == "BUY_PRODUCT":
        item, qty = order[1], order[2]
        return prices.get(item, UNKNOWN_ITEM_COST_ESTIMATE) * qty
    if op in ("BUY_SEED", "BUY_ANIMAL"):
        item, qty = order[1], order[2]
        return FALLBACK_COSTS.get((op, item), UNKNOWN_ITEM_COST_ESTIMATE) * qty
    if op == "HIRE":
        return FALLBACK_COSTS[("HIRE", None)]
    if op == "BUY_LAND":
        return FALLBACK_COSTS[("BUY_LAND", None)]
    return 0.0


def _order_credit(order: list, prices: dict) -> float:
    if order[0] == "SELL":
        item, qty = order[1], order[2]
        return prices.get(item, 0) * qty
    return 0.0


def _tile_is_empty(tile: Any) -> bool:
    """A tile counts as 'empty' if it's unlocked capacity with nothing
    productive placed on it (bare dirt, an unplanted PLANT tile, or an
    unstocked PASTURE)."""
    if tile is None:
        return True
    if tile == "LOCKED":
        return False  # not usable at all, so not "wasted" capacity
    kind = tile.get("kind")
    if kind == "PLANT":
        return not tile.get("crop")
    if kind == "PASTURE":
        return not tile.get("animal")
    return False


def _tile_growing_value(tile: Any, prices: dict) -> float:
    """Estimated sale value of whatever is currently growing on this tile,
    at TODAY's market price (see module docstring caveat)."""
    if tile is None or tile == "LOCKED":
        return 0.0
    kind = tile.get("kind")
    if kind == "PLANT" and tile.get("crop"):
        item = tile["crop"]
        qty = tile.get("yield_units", 1) or 1
        return prices.get(item, 0) * qty
    if kind == "PASTURE" and tile.get("animal"):
        # Animal product isn't separately named on the tile in this schema;
        # conservatively contribute 0 rather than guess a product/price.
        return 0.0
    return 0.0


def reconstruct_player(replay: dict, player_index: int) -> list[StepRecord]:
    """Build the full per-step ledger for one player across the match."""
    steps = replay["steps"]
    records: list[StepRecord] = []

    # Shift by 1: actions at steps[k] are taken for game step t = k - 1
    for k in range(1, len(steps)):
        t = k - 1
        entry = steps[k][player_index]
        obs = entry["observation"]
        action = entry["action"]

        farm = obs["farms"][player_index]
        prices = ((obs.get("market") or {}).get("prices")) or {}

        cash = float(farm.get("money", 0) or 0)

        private = obs.get("private") or {}
        shed = private.get("shed") or {}
        shed_value = sum(prices.get(item, 0) * qty for item, qty in shed.items())

        tiles = farm.get("tiles") or []
        growing_value = 0.0
        empty_tiles = 0
        total_unlocked_tiles = 0
        for row in tiles:
            for tile in row:
                if tile == "LOCKED":
                    continue
                total_unlocked_tiles += 1
                if _tile_is_empty(tile):
                    empty_tiles += 1
                growing_value += _tile_growing_value(tile, prices)

        total_hands = len(farm.get("hands") or [])
        active_hands = len(action.get("hands") or [])
        idle_hands = max(0, total_hands - active_hands)

        market_orders = action.get("market") or []
        revenue_by_category: dict[str, float] = {}
        spend_by_category: dict[str, float] = {}
        for order in market_orders:
            if not order:
                continue
            op = order[0]
            if op == "SELL":
                item = order[1]
                revenue_by_category[item] = revenue_by_category.get(item, 0.0) + _order_credit(order, prices)
            else:
                cat = CATEGORY_FOR_OP.get(op, "other")
                spend_by_category[cat] = spend_by_category.get(cat, 0.0) + _order_cost(order, prices)

        records.append(
            StepRecord(
                step=t,
                day=t // 24,
                cash=cash,
                shed_value=shed_value,
                growing_value=growing_value,
                projected_value=cash + shed_value + growing_value,
                idle_hands=idle_hands,
                total_hands=total_hands,
                empty_tiles=empty_tiles,
                total_unlocked_tiles=total_unlocked_tiles,
                revenue_by_category=revenue_by_category,
                spend_by_category=spend_by_category,
                prices=prices,
            )
        )

    return records


def reconstruct_match(replay: dict) -> dict[int, list[StepRecord]]:
    """Reconstruct both players' ledgers for a match."""
    n_players = len(replay["steps"][0])
    return {p: reconstruct_player(replay, p) for p in range(n_players)}
