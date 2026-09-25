# Field Skeleton (local, dependency-free)

Reads a submission's REAL route-branching logic directly from its own
`route_db`/`route_compressor.py` (ground truth, not a statistical guess),
then cross-references it against real replay outcomes -- the same
principle the reference notebook's Field Skeleton used for its own agent.

## Requirements

Python 3.9+, standard library only, plus the submission itself (its
`main.py`/`route_compressor.py`/`route_db/`, unmodified). The report is a
single HTML file loading Chart.js from a CDN when opened in a browser.

## How it works

1. **Static analysis** (`static_analysis.py`) reads `route_db/metadata.json`
   and the actual radix-indexed route keys to report exactly how many
   distinct shop-sequence lengths (k = 1..8) have a recorded route, versus
   the theoretical maximum (8^k) -- no replay data needed for this part.
2. **Instrumentation** (`instrument.py`) monkeypatches the submission's
   `ROUTE_DB.lookup_same_step` and `select_route` at runtime, without
   touching the submission's source files, so every real route-selection
   decision gets logged (day, live shop sequence, which route_id was
   chosen, and whether it was an exact match or a same-step fallback).
3. **Replay walking** (`route_log.py`) feeds a real Kaggle episode replay
   JSON through the instrumented submission turn by turn -- the same way
   the live competition harness would call it -- and records the match's
   outcome.
4. **Aggregation** (`aggregate.py`) combines multiple matches' logs into a
   routing table: which worlds trigger which route, how often, and with
   what real win/loss record.
5. **Report** (`report.py`) renders both the static coverage picture and
   the dynamic routing table into one HTML file.

## Run it

```bash
cd field_skeleton
python run_field_skeleton.py \
  --submission /path/to/submission_dir \
  --replay replay1.json:1 \
  --replay replay2.json:0 \
  --out report.html
```

Each `--replay` is `path:player_index` -- which seat in that replay file
was played by the submission you're pointing `--submission` at. Repeat
`--replay` for every match you want folded into the routing table.

`--submission` must be a directory containing `main.py`,
`route_compressor.py`, and `route_db/` -- the same layout as the
competition `.tar.gz` archive.

## Run the tests

```bash
python tests/test_field_skeleton.py \
  --submission /path/to/submission_dir \
  --replay replay1.json:1
```

## Important caveats — read before trusting the numbers

1. **The sample size right now is tiny.** This project ships with a demo
   report built from 3 match-perspectives (2 replay files). The reference
   notebook's own version used 98. Every win/loss number in the routing
   table is illustrative of the *mechanism*, not statistically reliable —
   scale up `--replay` arguments with real matches before trusting any
   specific route's record.
2. **Only replays where the SAME submission played are valid inputs.**
   Feeding in another player's match would replay a hypothetical "what
   would this route_db have chosen here", which is a different (and
   separately useful) question from "how did this route_db actually
   perform" — don't mix the two into one win/loss record.
3. **`match_type` matters as much as the win/loss numbers.** A route
   logged as `same_step_prefix_4` means only the first 4 shops in the
   sequence matched a recorded route — the rest of that route's own
   assumptions (which shops unlock next) may not match what's actually
   happening live. That's a real risk signal independent of outcome.
4. This requires the raw Kaggle episode replay JSON format (same as
   `field_ledger`'s requirement) — not the actions-only Parquet mining
   format, since route selection needs the live shop-unlock sequence at
   each step, which those Parquet files may or may not carry depending on
   which extraction pipeline produced them (see the field_ledger project's
   notes on the two inconsistent Parquet schemas found earlier).

## Extending this

- Scale the `--replay` list up with every full-state replay you have
  where this submission (or an ancestor sharing the same route_db) played,
  to get a routing table with real statistical weight.
- Field Worlds (shop-pair-world frequency across the full 10,000-match
  Parquet dataset, regardless of who played) is a natural next module —
  unlike this one, it doesn't need full replay state, since it only needs
  the shop-sequence columns already present in the enriched Parquet schema.
