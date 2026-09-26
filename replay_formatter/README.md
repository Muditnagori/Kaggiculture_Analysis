# Kaggriculture Replay Formatter (F2) - High-Performance & Flat Structure Edition

A fast, resilient, multi-core Python data-engineering pipeline that converts raw **Kaggriculture** simulation replay `.json` files into **9 clean, normalized, rank-aware Parquet tables** stored together in a single folder.

---

## 1. Key Performance & Reliability Features

1. **Multi-Core Parallel Execution**: Utilizes your CPU's multi-core power via `ProcessPoolExecutor` (processing up to 10-12 replays simultaneously).
2. **Fast Rust-Based Deserializer (`orjson`)**: Uses `orjson` (with standard `json` fallback) for rapid JSON loading.
3. **Immediate Progress Saving (Never Lose Work)**: Commits parsed replays directly to disk in batches. If interrupted at any time, all previously committed batches are safely stored on disk.
4. **Instant Resume (Zero Resetting)**: When restarted, the formatter detects already-formatted matches in milliseconds, skips them instantly, and continues remaining files.
5. **Unified Single Folder Output**: All 9 Parquet tables are saved directly inside `formatted_data/` without nested subdirectories, making analysis and multi-table queries straightforward.
6. **Streaming Row-Group Consolidation**: Merges batch checkpoints using PyArrow row-group streams consuming minimal RAM (< 50 MB), avoiding memory spikes.

---

## 2. Output Datasets (All in `formatted_data/`)

All datasets reside directly in `formatted_data/`:

```text
B:/Kaggle/F2/formatted_data/
|-- rankings.parquet       (Master rankings table: rank, player_name, match_count)
|-- source_files.parquet   (Audit log of all scanned JSON replays and parse status)
|-- episodes.parquet       (1 row per replay: metadata, rules, scores, players, rank)
|-- steps.parquet          (1 row per step + player: timeline, action, reward)
|-- players.parquet        (1 row per step + player: money, coords, hands, rank)
|-- farm_state.parquet     (1 row per step + player + tile: 100 tiles per step)
|-- market.parquet         (1 row per step + product: prices and stock)
|-- inventory.parquet      (1 row per step + player + location + item)
`-- town.parquet           (1 row per step + shop: chronological shop unlocks)
```

---

## 3. Quick Start

Double-click `run.bat` or run from terminal:

```bat
run.bat
```

Or run Python directly:

```bash
python formatter.py --input-folder "B:/Kaggle/replay_downloader/downloads" --output-folder "formatted_data"
```

---

## 4. Verification & Integrity

All tables share `episode_id` for relational joins. You can query any table instantly with PyArrow, DuckDB, or Pandas:

```python
import pyarrow.parquet as pq

# Load episodes and rankings
episodes = pq.read_table("formatted_data/episodes.parquet").to_pandas()
rankings = pq.read_table("formatted_data/rankings.parquet").to_pandas()
```

---

## 5. Exporting Single-Player Moves Parquet

Extract 1 consolidated Parquet file per player containing all moves across all matches directly from existing `formatted_data/`:

### Interactive Mode:
Run `run.bat` and select Option `[2]`:
```bat
run.bat
```
Enter the number of top players (e.g. `3`).

### CLI Command:
```bash
python export_player_moves.py --top 3
# Or via formatter:
python formatter.py --top-players 3
```

### Output Files (`player_moves/`):
```text
B:/Kaggle/F2/player_moves/
|-- 01_Majkel1337_moves.parquet
|-- 01_SpaTaro_moves.parquet
`-- 02_Artem_The_Farmer_moves.parquet
```

### Schema:
- `episode_id` (int64)
- `step`, `day`, `hour` (int32)
- `current_shop` (string: active unlocked shop for current 72h phase, or `"PRE_SHOP"` for steps 0–71)
- `step_in_shop_phase` (int32: step offset 0..71 within current shop phase)
- `unlocked_shops_count` (int32: number of shops unlocked so far, 0..8)
- `unlocked_shops` (string: JSON array of open shops, e.g. `'["YARN_STORE", "BAKERY"]'`)
- `match_shop_sequence` (string: full match sequence, e.g. `"YARN_STORE -> BAKERY -> SMOOTHIE_SHOP..."`)
- `player_rank` (int32), `player_name` (string), `player_index` (int32)
- `action` (string: JSON move details)
- `reward` (double: reward at step)
- `opponent_name` (string)
- `player_final_reward`, `opponent_final_reward` (double)
- `match_result` (string: WIN, LOSS, TIE)
- `source_file` (string)


---

## 6. Shop Sequence & Winning-Agent Moves Extractor (`parquet_extractor.py`, formerly `Formatter/`)

The legacy `Formatter` module has been merged into this folder. It is a multi-process converter that parses raw replay JSONs into two flat Parquet outputs, and is still an input source for the `Extractor` (option `[2] Formatter Outputs`).

### Inputs
- **Default Input**: `../replay_downloader/downloads/kaggriculture/` (or `../replay_downloader/downloads/`, or `inputs/`)
- Automatically discovers player groups (e.g. `01_Mengfei Li/`, `02_自己找差距/`, `05_SpaTaro/`).

### Outputs (flat, in `outputs/`)
1. **`shop_unlocked_sequence.parquet`**: chronological shop unlock sequence for **all matches across all players**.
   - Columns: `episode_id`, `source_file`, `seed`, `team_0`, `team_1`, `reward_0`, `reward_1`, `winner`, `total_steps`, `num_shops_unlocked`, `sequence_string`, `shop_sequence`, `unlock_steps`, `unlock_days`, `unlock_hours`.
2. **`<rank>_<name>_moves.parquet`** (e.g. `01_Mengfei Li_moves.parquet`): all steps, moves, inventories, shed goods, farm tiles, and market interactions of winning agents per player.
   - **Dynamic Rank Synchronization**: when a player's rank changes (e.g. `02_3정훈` ➔ `04_3정훈`), the file is renamed to the current rank (also done by the Downloader).
   - **Incremental Merge**: new rows are appended to existing tables, deduplicated by `episode_id`.
   - **Data Preservation**: never deletes existing Parquet tables.

### How to Run
Interactive: `run.bat` → options **[5]** Extract (shop sequence + moves), **[6]** Single match winning agent, **[7]** Custom input/output paths.

Command line:
```bat
:: via run.bat (passes remaining args to parquet_extractor.py)
run.bat legacy ..\replay_downloader\downloads\kaggriculture -o outputs

:: or directly from within replay_formatter
python parquet_extractor.py ..\replay_downloader\downloads\kaggriculture -o outputs
python parquet_extractor.py --match <replay.json or episode_id> -o outputs
python parquet_extractor.py --clean-downloads   :: delete processed raw JSONs after success
```
