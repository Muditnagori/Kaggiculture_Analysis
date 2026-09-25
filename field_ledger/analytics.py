# SPDX-License-Identifier: Apache-2.0
"""
Derived series built on top of reconstruct.StepRecord lists: the
projected-advantage trend, cumulative revenue/spend, and a "free cash"
estimate. Formulas follow the notebook's math model where the data
supports them; deviations are called out in comments.
"""
from __future__ import annotations

from .reconstruct import StepRecord, FALLBACK_COSTS


def advantage_series(us: list[StepRecord], opp: list[StepRecord]) -> list[float]:
    """g(t) = V_us(t) - V_opp(t)"""
    return [u.projected_value - o.projected_value for u, o in zip(us, opp)]


def trend_d48(g: list[float], window: int = 48) -> list[float | None]:
    """
    D_48(t) = mean(g over the latest `window` turns) - mean(g over the
    previous `window` turns). None where there isn't enough history yet
    (first 2*window-1 steps).
    """
    out: list[float | None] = []
    for t in range(len(g)):
        if t < 2 * window - 1:
            out.append(None)
            continue
        recent = g[t - window + 1 : t + 1]
        prior = g[t - 2 * window + 1 : t - window + 1]
        out.append(sum(recent) / window - sum(prior) / window)
    return out


def cumulative_by_category(records: list[StepRecord], field: str) -> dict[str, list[float]]:
    """
    field is 'revenue_by_category' or 'spend_by_category'. Returns, per
    category seen anywhere in the match, a running cumulative total at
    every step (0 for steps before that category first appears).
    """
    categories: set[str] = set()
    for r in records:
        categories.update(getattr(r, field).keys())

    running = {c: 0.0 for c in categories}
    out: dict[str, list[float]] = {c: [] for c in categories}
    for r in records:
        step_data = getattr(r, field)
        for c in categories:
            running[c] += step_data.get(c, 0.0)
            out[c].append(running[c])
    return out


def totals_by_category(records: list[StepRecord], field: str) -> dict[str, float]:
    totals: dict[str, float] = {}
    for r in records:
        for c, v in getattr(r, field).items():
            totals[c] = totals.get(c, 0.0) + v
    return totals


HIRE_UNIT_COST = FALLBACK_COSTS[("HIRE", None)]
WAGE_RESERVE_SAFETY_MULTIPLIER = 1.5


def free_cash_series(records: list[StepRecord]) -> list[float]:
    """
    Approximation of 'cash actually free to spend without wrecking
    anything', reusing the same wage-reserve idea from the submission's
    budget governor: cash minus what tomorrow's re-hiring would cost at
    today's headcount. This is NOT the same accounting the original
    notebook used (which likely also nets out committed land/seed
    purchases already in motion) -- treat it as a conservative estimate,
    not an exact figure.
    """
    out = []
    for r in records:
        reserve = r.total_hands * HIRE_UNIT_COST * WAGE_RESERVE_SAFETY_MULTIPLIER
        out.append(max(0.0, r.cash - reserve))
    return out


def match_summary(us: list[StepRecord], opp: list[StepRecord]) -> dict:
    g = advantage_series(us, opp)
    return {
        "final_projected_us": us[-1].projected_value,
        "final_projected_opp": opp[-1].projected_value,
        "final_margin": g[-1],
        "final_cash_us": us[-1].cash,
        "final_cash_opp": opp[-1].cash,
        "total_revenue_us": sum(totals_by_category(us, "revenue_by_category").values()),
        "total_spend_us": sum(totals_by_category(us, "spend_by_category").values()),
        "total_revenue_opp": sum(totals_by_category(opp, "revenue_by_category").values()),
        "total_spend_opp": sum(totals_by_category(opp, "spend_by_category").values()),
    }
