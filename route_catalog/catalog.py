# SPDX-License-Identifier: Apache-2.0
"""
Builds a catalog of recurring routes across many replays. Two levels of
grouping:

- STRICT entries: (situation, length_windows, action_hash) -> exact
  byte-for-byte repeats. A route "recurs" here only if two players did
  literally the same thing.
- LOOSE families: entries that share (situation, length_windows,
  loose_hash) get grouped into a family, even if their strict hashes
  differ -- catching "clearly the same strategy, minor variation" cases
  that strict hashing alone would treat as unrelated.

Processes replays one at a time and never holds more than one match's
full action data in memory at once, so this scales to a large replay
folder without needing everything loaded simultaneously.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .segment import RouteCandidate, extract_candidates


@dataclass
class Occurrence:
    episode_id: str
    player_index: int
    start_step: int


@dataclass
class CatalogEntry:
    situation: tuple
    length_windows: int
    action_hash: str
    loose_hash: str
    representative_actions: list       # kept only from the FIRST sighting
    occurrences: list = field(default_factory=list)
    reward_margins: list = field(default_factory=list)  # player_reward - opponent_reward, per occurrence

    @property
    def times_seen(self) -> int:
        return len(self.occurrences)


@dataclass
class LooseFamily:
    situation: tuple
    length_windows: int
    loose_hash: str
    member_action_hashes: set = field(default_factory=set)
    total_times_seen: int = 0


@dataclass
class Catalog:
    entries: dict[tuple, CatalogEntry] = field(default_factory=dict)
    families: dict[tuple, LooseFamily] = field(default_factory=dict)
    n_replays_scanned: int = 0
    n_candidates_seen: int = 0
    skipped_replays: list = field(default_factory=list)  # (path, reason)

    def add_candidate(self, c: RouteCandidate) -> None:
        self.n_candidates_seen += 1

        entry_key = (c.situation, c.length_windows, c.action_hash)
        entry = self.entries.get(entry_key)
        if entry is None:
            entry = CatalogEntry(
                situation=c.situation,
                length_windows=c.length_windows,
                action_hash=c.action_hash,
                loose_hash=c.loose_hash,
                representative_actions=c.actions,
            )
            self.entries[entry_key] = entry
        entry.occurrences.append(Occurrence(c.episode_id, c.player_index, c.start_step))
        entry.reward_margins.append(c.player_final_reward - c.opponent_final_reward)

        family_key = (c.situation, c.length_windows, c.loose_hash)
        family = self.families.get(family_key)
        if family is None:
            family = LooseFamily(situation=c.situation, length_windows=c.length_windows, loose_hash=c.loose_hash)
            self.families[family_key] = family
        family.member_action_hashes.add(c.action_hash)
        family.total_times_seen += 1

    def lookup_exact(self, situation: tuple, length_windows: int, action_hash: str) -> CatalogEntry | None:
        return self.entries.get((situation, length_windows, action_hash))

    def lookup_loose(self, situation: tuple, length_windows: int, loose_hash: str) -> LooseFamily | None:
        return self.families.get((situation, length_windows, loose_hash))


def build_catalog(replay_paths: list[str | Path], player_indices: tuple[int, ...] = (0, 1)) -> Catalog:
    catalog = Catalog()

    for path in replay_paths:
        try:
            # extract_candidates loads the replay once per player index it's
            # called with; for a 2-player match this reads the file twice,
            # which is simple and fine at this scale (a few thousand small
            # JSON files) -- worth optimizing to a single shared load if you
            # scale into the tens of thousands of replays.
            for player_index in player_indices:
                for candidate in extract_candidates(path, player_index):
                    catalog.add_candidate(candidate)
            catalog.n_replays_scanned += 1
        except (KeyError, IndexError, ValueError) as e:
            catalog.skipped_replays.append((str(path), f"not a recognizable episode replay: {e}"))
        except Exception as e:
            catalog.skipped_replays.append((str(path), f"unexpected error: {e}"))

    return catalog


def save_catalog(catalog: Catalog, path: str | Path) -> None:
    """Serialize to JSON. Tuple keys (situation) become lists; reconstructed
    back into tuples on load."""
    import json

    def entry_to_dict(e: CatalogEntry) -> dict:
        return {
            "situation": list(e.situation),
            "length_windows": e.length_windows,
            "action_hash": e.action_hash,
            "loose_hash": e.loose_hash,
            "representative_actions": e.representative_actions,
            "occurrences": [
                {"episode_id": o.episode_id, "player_index": o.player_index, "start_step": o.start_step}
                for o in e.occurrences
            ],
            "reward_margins": e.reward_margins,
        }

    def family_to_dict(f: LooseFamily) -> dict:
        return {
            "situation": list(f.situation),
            "length_windows": f.length_windows,
            "loose_hash": f.loose_hash,
            "member_action_hashes": sorted(f.member_action_hashes),
            "total_times_seen": f.total_times_seen,
        }

    payload = {
        "n_replays_scanned": catalog.n_replays_scanned,
        "n_candidates_seen": catalog.n_candidates_seen,
        "skipped_replays": catalog.skipped_replays,
        "entries": [entry_to_dict(e) for e in catalog.entries.values()],
        "families": [family_to_dict(f) for f in catalog.families.values()],
    }
    Path(path).write_text(json.dumps(payload), encoding="utf-8")


def load_catalog(path: str | Path) -> Catalog:
    import json

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    catalog = Catalog(
        n_replays_scanned=payload["n_replays_scanned"],
        n_candidates_seen=payload["n_candidates_seen"],
        skipped_replays=[tuple(x) for x in payload["skipped_replays"]],
    )
    for ed in payload["entries"]:
        situation = tuple(ed["situation"])
        entry = CatalogEntry(
            situation=situation,
            length_windows=ed["length_windows"],
            action_hash=ed["action_hash"],
            loose_hash=ed["loose_hash"],
            representative_actions=ed["representative_actions"],
            occurrences=[Occurrence(o["episode_id"], o["player_index"], o["start_step"]) for o in ed["occurrences"]],
            reward_margins=ed["reward_margins"],
        )
        catalog.entries[(situation, entry.length_windows, entry.action_hash)] = entry
    for fd in payload["families"]:
        situation = tuple(fd["situation"])
        family = LooseFamily(
            situation=situation,
            length_windows=fd["length_windows"],
            loose_hash=fd["loose_hash"],
            member_action_hashes=set(fd["member_action_hashes"]),
            total_times_seen=fd["total_times_seen"],
        )
        catalog.families[(situation, family.length_windows, family.loose_hash)] = family
    return catalog
