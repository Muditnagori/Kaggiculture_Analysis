# SPDX-License-Identifier: Apache-2.0
"""
Monkeypatches a loaded submission module so every route-selection decision
it actually makes gets logged -- without editing the submission's source.

Hooks ROUTE_DB.lookup_same_step (or .lookup as a fallback for older
submissions), since that's where match_type (exact / same-step-fallback /
none) is actually computed; select_route() only stores the result into
module globals and doesn't expose match_type itself.
"""
from __future__ import annotations

import types
from dataclasses import dataclass, field


@dataclass
class RouteEvent:
    step: int
    day: int
    lookup_shops: tuple
    route_id: int | None
    match_type: str
    matched_shops: tuple | None = None


def instrument(module: types.ModuleType) -> list[RouteEvent]:
    """
    Wraps module.ROUTE_DB's lookup method and module.select_route so every
    call to select_route that actually changes the active route appends a
    RouteEvent to the returned list. Call this once per freshly loaded
    submission module (see submission_loader.load_submission).
    """
    events: list[RouteEvent] = []
    last_lookup: dict = {}

    route_db = module.ROUTE_DB
    if hasattr(route_db, "lookup_same_step"):
        original_lookup = route_db.lookup_same_step

        def wrapped_lookup(shops):
            route_id, match_type, matched_shops = original_lookup(shops)
            last_lookup["shops"] = shops
            last_lookup["route_id"] = route_id
            last_lookup["match_type"] = match_type
            last_lookup["matched_shops"] = matched_shops
            return route_id, match_type, matched_shops

        route_db.lookup_same_step = wrapped_lookup
    else:
        original_lookup = route_db.lookup

        def wrapped_lookup(shops):
            route_id = original_lookup(shops)
            last_lookup["shops"] = shops
            last_lookup["route_id"] = route_id
            last_lookup["match_type"] = "exact" if route_id is not None else "none"
            last_lookup["matched_shops"] = shops
            return route_id

        route_db.lookup = wrapped_lookup

    original_select_route = module.select_route

    def wrapped_select_route(shops, step):
        day_before = module._current_day
        original_select_route(shops, step)
        if module._current_day != day_before and last_lookup:
            events.append(
                RouteEvent(
                    step=step,
                    day=module._current_day,
                    lookup_shops=tuple(last_lookup.get("shops") or ()),
                    route_id=last_lookup.get("route_id"),
                    match_type=last_lookup.get("match_type", "unknown"),
                    matched_shops=tuple(last_lookup.get("matched_shops") or ())
                    if last_lookup.get("matched_shops") is not None
                    else None,
                )
            )

    module.select_route = wrapped_select_route
    return events
