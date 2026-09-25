# SPDX-License-Identifier: Apache-2.0
"""
Cross-references a pooled world-frequency table against a submission's
own route_db: for each world actually seen in the broader field, does
OUR submission have an exact recorded route for it? This is the piece
that turns "here's what the field looks like" into "here's where our
own route library is thin."

Depends on the field_skeleton project (submission_loader.py) being
importable -- see README for the expected directory layout. This is kept
as an optional import so field_worlds still works standalone (just
without the cross-reference column) if field_skeleton isn't available.
"""
from __future__ import annotations

from dataclasses import dataclass

from .worlds import WorldFrequency


@dataclass
class CoverageResult:
    world: tuple[str, ...]
    has_exact_route: bool
    route_id: int | None
    match_type: str | None  # None if lookup itself failed/unavailable


def load_route_db(submission_dir: str):
    """Returns a loaded submission's (ROUTE_DB, normalize_shop_name) pair,
    or raises ImportError with a clear message if field_skeleton isn't on
    the path. normalize_shop_name is the submission's OWN function -- e.g.
    it maps 'ICE_CREAM_SHOP' -> 'ICE_CREAM' and 'BAKERY_SHOP' -> 'BAKERY'
    for this particular route_db's canonical vocabulary. Pulling it live
    from the loaded module (instead of hardcoding a second copy here)
    means this stays correct even if the submission's naming scheme
    changes -- a hardcoded copy silently drifting out of sync would
    produce exactly the kind of false "no coverage" result this function
    exists to avoid."""
    try:
        from field_skeleton.submission_loader import load_submission
    except ImportError as e:
        raise ImportError(
            "field_skeleton isn't importable -- cross-referencing against a "
            "submission's route_db requires the field_skeleton project to be "
            "a sibling directory on sys.path. See field_worlds/README.md."
        ) from e
    module = load_submission(submission_dir)
    normalize = getattr(module, "normalize_shop_name", lambda s: s)
    return module.ROUTE_DB, normalize


def cross_reference(world_table: dict[tuple[str, ...], WorldFrequency], route_db, normalize=lambda s: s) -> dict[tuple[str, ...], CoverageResult]:
    results: dict[tuple[str, ...], CoverageResult] = {}
    has_same_step = hasattr(route_db, "lookup_same_step")

    for world in world_table:
        canonical_world = [normalize(shop) for shop in world]
        if has_same_step:
            route_id, match_type, _matched = route_db.lookup_same_step(canonical_world)
        else:
            route_id = route_db.lookup(canonical_world)
            match_type = "exact" if route_id is not None else "none"

        results[world] = CoverageResult(
            world=world,
            has_exact_route=(match_type == "exact"),
            route_id=route_id,
            match_type=match_type,
        )
    return results
