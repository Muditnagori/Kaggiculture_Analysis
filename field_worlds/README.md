# Field Worlds (local)

Pools the shop-unlock RNG draw across many different players' matches to
characterize how often each possible "world" actually occurs -- a pure
game mechanic, independent of who's playing -- and optionally
cross-references it against a submission's own route_db coverage.

## Requirements

Python 3.9+, `pandas` and `pyarrow` (`pip install pandas pyarrow`). Unlike
`field_ledger`/`field_skeleton`, this one needs pandas since it's
aggregating across potentially thousands of Parquet files at scale.

Cross-referencing against a submission (`--submission`) additionally
requires the `field_skeleton` project to be a **sibling directory** —
expected layout:
```
your_project/
├── field_worlds/
└── field_skeleton/
```

## Input format

Either of two formats, selected with `--format`:

- **`--format json`** (recommended if you don't have the Parquet mining
  pipeline set up yet): a directory of raw Kaggle episode replay JSON
  files — the exact same format `field_ledger` and `field_skeleton` use.
  This works because `obs['town']['unlocked_shops']` grows in unlock
  order and, by the final step, is content-identical to the Parquet
  pipeline's `match_shop_sequence` column (verified directly against real
  data). Nothing needs pre-extracting first.
- **`--format parquet`** (default): a directory of **enriched**
  moves-Parquet files — the schema that carries `match_shop_sequence`,
  `unlocked_shops`, `current_shop`, etc. Not every extraction produced
  this: this project already found one player's file missing these
  columns entirely (see `field_ledger`'s README for that discovery).
  Files without the required columns are **skipped and reported**, not
  silently dropped — check the report's "Files skipped" panel.

`match_shop_sequence` (Parquet) / `unlocked_shops` (JSON, at the final
step) is a pre-determined RNG draw fixed before the match starts and
constant across every row of that episode — so this tool only needs one
row/file per episode, not the full per-step log.

## Run it

```bash
cd field_worlds

# from raw replay JSON directly (no Parquet extraction needed):
python run_field_worlds.py --data-dir /path/to/replay_jsons --format json --k 2 --out report.html

# from the enriched Parquet mining output:
python run_field_worlds.py --data-dir /path/to/parquet_files --k 2 --out report.html

# with route_db cross-reference (either format):
python run_field_worlds.py --data-dir /path/to/replay_jsons --format json --k 2 \
  --submission /path/to/submission_dir --out report.html
```

- `--k`: world prefix length (default 2 — "the first two shops to unlock",
  matching the reference notebook's own definition). Try other values
  (1, 3, 4...) to see how the space of possibilities grows.
- `--submission`: optional. Adds a column showing whether your own
  submission's route_db has an exact recorded route for each world.

## A real bug this project already caught

Cross-referencing initially reported only 25/64 worlds covered, which
contradicted the submission's own static analysis (100% coverage at k=2).
The cause: `route_db`'s canonical vocabulary uses a specific, inconsistent
mix of short/long shop names (`ICE_CREAM`, not `ICE_CREAM_SHOP`; `BAKERY`,
not `BAKERY_SHOP`; but `YARN_STORE` in full) — defined by the submission's
own `normalize_shop_name()` function. The Parquet data uses full names
throughout, so a naive lookup silently produced false misses. The fix:
`cross_reference()` now pulls `normalize_shop_name` live from the loaded
submission module rather than hardcoding a second copy of that mapping —
after the fix, coverage correctly reads 64/64. **Lesson for extending
this**: any time you compare shop names across two different sources
(replay data vs. a submission's internal vocabulary), normalize through
the submission's own function, never assume raw string equality.

## Run the tests

```bash
python tests/test_field_worlds.py --data-dir /path/to/parquet_files --submission /path/to/submission_dir
```

## Important caveats

1. **A match can appear in more than one player's per-player file** (once
   per participant). `scan_directory()` deduplicates by `episode_id`
   across files so it isn't double-counted — verified by the test suite's
   duplicate-ID check.
2. **This tells you frequency, not quality.** A world being common doesn't
   mean your route for it is good — pair this with `field_skeleton`'s
   real win/loss record for worlds your own submission has actually played.
3. Tested against one real enriched Parquet file (659 episodes, 64/64
   theoretical k=2 worlds observed — matching the reference notebook's own
   "64 of 64" finding). Scale `--data-dir` up to your full dataset for
   real statistical weight; nothing about this tool is specific to the
   one file it was built against.

## Extending this

- Point `--data-dir` at your full ~10,000-match collection once every
  file has been re-extracted with the enriched schema (see
  `field_ledger`'s README for why the two schemas need reconciling first).
- Try `--k` values beyond 2 to see how fast the space of distinct worlds
  grows, and cross-reference each against your route_db to find exactly
  which longer sequences your route library is thin on — this is the same
  question `field_skeleton`'s static analysis answers structurally
  (coverage collapsing from 100% at k≤3 down to <1% by k=6), now checked
  against what actually occurs in real play rather than the theoretical
  space.
