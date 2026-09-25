# SPDX-License-Identifier: Apache-2.0
"""
Reads the REAL structure of a submission's route_db/ directly -- no
replay data involved. This is "ground truth from source", the same
principle the reference notebook's Field Skeleton used: don't guess the
branch table statistically when you can read it.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .submission_loader import load_submission


@dataclass
class StaticRouteDBStats:
    n_routes: int
    route_len: int
    n_shop_types: int
    routes_per_sequence_length: dict[int, int]  # k -> how many distinct routes have a k-shop key
    metadata: dict


def analyze_route_db(submission_dir: str) -> StaticRouteDBStats:
    module = load_submission(submission_dir)
    route_db = module.ROUTE_DB
    meta = dict(route_db.meta)

    routes_per_k: dict[int, int] = defaultdict(int)

    if getattr(route_db, "_use_radix", False):
        # route_compressor.BASE_OFFSETS marks where each sequence-length
        # band starts in the sorted radix-key space; count real DB entries
        # (not the full theoretical keyspace) that fall in each band.
        import route_compressor as rc  # same module the submission itself imported

        for key in route_db._radix_keys:
            for k in range(8, 0, -1):
                if key >= rc.BASE_OFFSETS[k]:
                    routes_per_k[k] += 1
                    break
    else:
        # Non-radix index: sequence length isn't directly recoverable from
        # the key without re-decoding each one; report total only.
        routes_per_k[-1] = len(getattr(route_db, "_index_keys", []))

    return StaticRouteDBStats(
        n_routes=meta.get("n_routes", 0),
        route_len=meta.get("route_len", 0),
        n_shop_types=meta.get("n_shop_types", 0),
        routes_per_sequence_length=dict(sorted(routes_per_k.items())),
        metadata=meta,
    )
