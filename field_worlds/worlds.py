# SPDX-License-Identifier: Apache-2.0
"""
Aggregates EpisodeWorld records into a frequency table per "world" --
the first k shops of the match's shop-unlock sequence -- the same idea as
the reference notebook's Field Worlds page: a pure RNG draw, independent
of who's playing, so pooling across many different players' matches gives
real statistical power on how often each world actually occurs.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .parquet_scan import EpisodeWorld

TIE_RESULT_VALUES = {"TIE", "DRAW"}  # match_result spellings seen across datasets; extend if new ones appear


@dataclass
class WorldFrequency:
    world: tuple[str, ...]
    times_seen: int = 0
    ties: int = 0
    margins: list = field(default_factory=list)          # |player_reward - opponent_reward| per match
    combined_scores: list = field(default_factory=list)  # player_reward + opponent_reward per match
    episode_ids: list = field(default_factory=list)

    @property
    def tie_rate(self) -> float:
        return self.ties / self.times_seen if self.times_seen else 0.0

    @property
    def avg_abs_margin(self) -> float:
        return sum(self.margins) / len(self.margins) if self.margins else 0.0

    @property
    def avg_combined_score(self) -> float:
        return sum(self.combined_scores) / len(self.combined_scores) if self.combined_scores else 0.0


def world_frequency_table(records: list[EpisodeWorld], k: int) -> dict[tuple[str, ...], WorldFrequency]:
    """Build the frequency table for worlds of prefix length k (e.g. k=2
    means 'the first two shops to unlock', matching the reference
    notebook's own definition)."""
    table: dict[tuple[str, ...], WorldFrequency] = {}

    for rec in records:
        if len(rec.shop_sequence) < k:
            continue  # match ended or was recorded before this many shops unlocked
        world = rec.shop_sequence[:k]
        if world not in table:
            table[world] = WorldFrequency(world=world)
        wf = table[world]
        wf.times_seen += 1
        wf.episode_ids.append(rec.episode_id)
        if rec.match_result.upper() in TIE_RESULT_VALUES:
            wf.ties += 1
        wf.margins.append(abs(rec.player_final_reward - rec.opponent_final_reward))
        wf.combined_scores.append(rec.player_final_reward + rec.opponent_final_reward)

    return table


def distinct_worlds_possible(n_shop_types: int, k: int) -> int:
    """Theoretical count of distinct ORDERED k-length shop sequences,
    matching route_compressor's own radix keyspace definition (order
    matters, repeats allowed -- e.g. PET->PET is a valid, distinct world
    from PET->YARN)."""
    return n_shop_types ** k
