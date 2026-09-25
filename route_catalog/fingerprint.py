# SPDX-License-Identifier: Apache-2.0
"""
Two ways of hashing a single step's action:

- `canonical_action_string`: exact, order-sensitive, byte-for-byte --
  used for STRICT route matching (two candidates are "the same route"
  only if every action matches exactly).
- `significant_action_string`: a coarser signature that keeps WHAT was
  done (which market operations, which item, roughly how much; which
  operation type each hand/farmer performed) but drops exact tile
  coordinates and minor quantity/ordering noise -- used for LOOSE
  clustering, to catch routes that are "the same idea" with small
  variations, the way many players converging on a similar opening
  wouldn't be byte-identical but are clearly the same strategy.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_action_string(action: dict) -> str:
    return json.dumps(action, sort_keys=True, separators=(",", ":"))


def _market_signature(market_orders: list) -> tuple:
    sig = []
    for order in market_orders or []:
        if not order:
            continue
        op = order[0]
        item = order[1] if len(order) > 1 else None
        qty = order[2] if len(order) > 2 else None
        # bucket quantity coarsely so e.g. buying 4 vs 5 seeds still counts
        # as "the same kind of move" for loose matching
        qty_bucket = None if qty is None else (0 if qty == 0 else (qty - 1) // 3 + 1)
        sig.append((op, item, qty_bucket))
    return tuple(sorted(sig))


def significant_action_string(action: dict) -> str:
    farmer_op = None
    farmer = action.get("farmer")
    if farmer:
        farmer_op = farmer[0]

    hand_ops = tuple(sorted(h[0] for h in (action.get("hands") or []) if h))
    market_sig = _market_signature(action.get("market") or [])

    return json.dumps(
        {"farmer_op": farmer_op, "hand_ops": hand_ops, "market": market_sig},
        sort_keys=True, separators=(",", ":"),
    )


def hash_strings(strings: list[str]) -> str:
    """Hash an ordered sequence of per-step strings into one route hash."""
    h = hashlib.sha256()
    for s in strings:
        h.update(s.encode("utf-8"))
        h.update(b"\x1f")  # unit separator, so ["ab","c"] != ["a","bc"]
    return h.hexdigest()
