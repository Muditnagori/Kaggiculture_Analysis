# Field Ledger (local, dependency-free)

Per-match economic reconstruction and dashboard for Kaggriculture replays,
built the way the notebook's "Field Ledger" panel described it, adapted to
what's actually recoverable from a public replay JSON.

## Requirements

Python 3.9+, standard library only. No `pip install` needed. The dashboard
is a single HTML file that loads Chart.js from a CDN when you open it in a
browser (needs internet access at *viewing* time, not at generation time).

## Input format

A raw Kaggle episode replay JSON — the same format `kaggle_environments`
exports (a `"steps"` list, each entry containing per-player
`observation`/`action`/`reward`). This is **not** the actions-only Parquet
format from the mining pipeline — that format doesn't carry cash,
inventory, or tile state, which this tool needs.

## Run it

```bash
cd field_ledger
python run_field_ledger.py /path/to/replay.json --player 1 --out dashboard.html
```

- `--player`: which player index (0 or 1) is "us" in the dashboard. Defaults to 0.
- `--out`: output HTML path. Defaults to `dashboard.html`.

Then open the generated file in any browser.

## What each file does

| File | Role |
|---|---|
| `reconstruct.py` | Parses one replay JSON into a per-step ledger: cash, unsold inventory value, growing-crop value, idle hands, empty tiles, revenue/spend by category |
| `analytics.py` | Derives the trend (`D_48`), cumulative revenue/spend series, and a "free cash" estimate on top of the raw ledger |
| `dashboard.py` | Renders everything into one self-contained HTML file with Chart.js |
| `run_field_ledger.py` | CLI entry point tying it together |

## Important caveats — read before trusting the numbers

1. **"Projected value" is not a price forecast.** Anything still growing or
   sitting unsold is valued at *today's* market price, because no
   price-prediction model exists anywhere in the public replay data. If
   prices move a lot before harvest, the real value will differ from what's
   shown.
2. **"Idle hands" is inferred, not a labeled field.** The replay only lists
   hands that were given an explicit order each turn; this tool treats any
   hand with no listed order as idle. That's a reasonable read of the data
   but not a field the game hands you directly.
3. **"Free cash" is an approximation**, using the same wage-reserve logic
   built for the submission's budget governor (headcount × $8/hire × 1.5
   safety margin). It likely does not match whatever the original notebook's
   internal accounting did — no breakdown of that calculation was published.
4. **Animal product value is not counted** in "growing value" — the tile
   schema doesn't separately expose what an occupied pasture is about to
   produce, so this tool conservatively contributes $0 for animals still on
   the tile (their value only shows up once harvested into the shed).
5. Tested against the two replay files this project started from
   (720 steps × 2 players each, full integrity check passing with no
   crashes or invariant violations). Not yet run against a large batch —
   worth doing before trusting it as a general tool across your full
   10,000-match set.

## Extending this

- To batch this across many replays: loop `reconstruct_match()` over a
  directory of replay JSONs and aggregate `analytics.match_summary()`
  across all of them, rather than only rendering one dashboard at a time.
- Field Skeleton (reading your own agent's branch logic + real outcomes)
  and Field Worlds (shop-pair-world frequency across your 10,000-match
  dataset) are natural next modules to add alongside this one, following
  the same "standard library, runs locally" approach.
