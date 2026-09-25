# Route Catalog (local, dependency-free)

Discovers recurring action-sequence "routes" directly from player replays,
at EVERY possible length (not just a fixed 72-step window — a route can be
as short as one shop-unlock window or span the whole 720-step match), and
pinpoints exactly when each discovered route was used in any given match.

## Requirements

Python 3.9+, standard library only.

## How it works

1. **`segment.py`** — for a given player in a given match, generates every
   `(start, length)` candidate anchored at shop-unlock boundaries (the only
   points where "situation" meaningfully changes): every possible starting
   window × every possible length in windows from there to the end of the
   match. A 720-step match produces 55 candidates per player (1+2+...+10).
2. **`fingerprint.py`** — two ways of hashing a candidate's action sequence:
   a **strict** hash (byte-exact, catches literal repeats) and a **loose**
   hash (keeps which market operations/hand actions happened, drops exact
   tile coordinates and minor quantity differences, catches "same strategy,
   small variation").
3. **`catalog.py`** — folds candidates from many replays into a catalog:
   exact-hash entries (`(situation, length, action_hash)` → occurrences),
   plus loose-hash families for near-duplicate clustering. Processes
   replays one at a time so memory doesn't grow with dataset size.
4. **`timeline.py`** — given a built catalog and one specific match, walks
   through it and finds, at every point, the LONGEST catalog-matched route
   available, preferring exact over loose matches and reporting explicit
   gaps ("no known route here") rather than hiding them.
5. **`report.py`** — renders both a catalog-wide overview and a per-match
   Gantt-style timeline into self-contained HTML.

## Run it

```bash
cd route_catalog

# 1. build a catalog from a folder of raw replay JSONs
python run_build_catalog.py --replays-dir /path/to/replays \
  --catalog-out catalog.json --report-out catalog_report.html

# 2. pinpoint route usage in one specific match against that catalog
python run_timeline.py --catalog catalog.json \
  --replay /path/to/replays/some_match.json --player 1 --out timeline.html
```

Both commands write self-contained HTML — open them in any browser.

## A real bug this project caught and fixed during development

The first working version of `timeline.py` always picked the ENTIRE
720-step match as one single "route", with zero exceptions, no matter
what replay was fed in. The cause: the catalog naturally includes an entry
for every candidate ever folded into it — including the very replay being
evaluated. So "does this exact sequence exist in the catalog" was always
trivially true (it exists because this replay put it there), which meant
the longest-match-first logic always won by matching a replay against
itself.

**The fix**: a route is only accepted as a genuine match if it recurs
*beyond* the specific occurrence being evaluated right now — computed by
explicitly excluding the current `(episode_id, player_index, start_step)`
from the entry's occurrence list before counting. `tests/test_route_catalog.py`
asserts this directly: `times_seen_elsewhere > 0` for every "exact" or
"loose" match in every test replay, specifically to prevent this bug from
coming back silently.

**Lesson for extending this**: any time a discovery tool is evaluated
against data that could be part of its own training/reference set,
explicitly guard against the tool trivially "finding" the input it was
just given back to itself.

## Run the tests

```bash
python tests/test_route_catalog.py --replays-dir /path/to/replays
```

Checks: candidate counts match the combinatorial expectation exactly,
catalog totals match, save/load round-trips losslessly, every timeline is
gap-free and overlap-free and covers the full match, and — the specific
regression test for the bug above — no timeline ever reports a match with
zero occurrences elsewhere.

## Important caveats

1. **Tested on 2 replay files (3 agent-perspectives).** Every finding in
   the demo report reflects that tiny sample — e.g. the one genuinely
   recurring route found (the fixed day-1 opening) recurs because all 3
   perspectives happen to be the same underlying agent. Real cross-player
   recurring routes will only show up once you point this at your actual
   downloaded dataset.
2. **Loose-family matching is an approximation.** It drops exact tile
   coordinates and buckets quantities coarsely — reasonable for "is this
   clearly the same idea", not for "is this exactly reproducible."
3. **Performance**: candidate generation is O(n²) in the number of windows
   per match (55 candidates for a 10-window match), which is trivial per
   match but adds up — reading each replay file twice (once per player)
   is the current simple approach; worth optimizing to a single shared
   load per file if you scale into the tens of thousands of replays.
4. This needs raw Kaggle episode replay JSON (same format as
   `field_ledger`/`field_skeleton`/`field_worlds`'s `--format json` path) —
   not the actions-only Parquet mining format.

## Extending this

- Point `--replays-dir` at your full downloaded replay collection —
  nothing about this tool is specific to the 2 files it was built against.
- Cross-reference discovered routes against an existing submission's
  `route_db` the same way `field_worlds/cross_reference.py` does, to see
  which discovered routes your own agent already has vs. is missing.
- The catalog's `representative_actions` field stores a real, replayable
  action sequence for every entry — this is the actual bridge to building
  a new `route_db` from scratch: converting the catalog's strongest,
  most-recurring entries per `(situation, length)` into the same
  radix-indexed format `route_compressor.RouteDB` reads is the next step
  toward a self-built agent, not just an analysis tool.
