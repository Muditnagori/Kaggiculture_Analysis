# Shop Sequence Move Extractor (Multi-Source Edition)

A high-performance analysis and extraction engine for Kaggriculture simulation replays. It extracts every unique shop unlock sequence progression along with the corresponding 72 moves (or all remaining moves after the 8th shop unlock) from winning agents, strictly adhering to player priority rules.

---

## 1. Supported Input Sources

The Extractor seamlessly supports **two** input sources:

### Source A: Formatter2 Consolidated Parquet (Default & Recommended)
- **Path**: `..\Formatter2\formatted_data` (or `B:\Replicator\Formatter2\formatted_data`)
- **Coverage**: Full match coverage across all Kaggle simulation replays.
- **Unique Sequences**: **34,714** unique sequences discovered.
- **Input Tables Consumed**:
  - `town.parquet`: Shop unlock events and exact step boundaries.
  - `episodes.parquet`: Match scores, winners, and player ranking metadata.
  - `steps.parquet`: Fast streaming slice of turn-by-turn composite and atomic moves.

### Source B: Formatter Outputs
- **Path**: `..\Formatter\outputs` (or `B:\Replicator\Formatter\outputs`)
- **Coverage**: 42 top ranked player datasets.
- **Input Tables Consumed**:
  - `shop_unlocked_sequence.parquet`
  - `*_moves.parquet`

---

## 2. Interactive Menu (`run.bat`)

Double-clicking `run.bat` opens an interactive console menu with the currently active source clearly displayed in the header:

```text
==============================================================================
                SHOP SEQUENCE MOVE EXTRACTOR (PLAYER PRIORITY)
==============================================================================
 Active Input Source : Formatter2 Consolidated Parquet (..\Formatter2\formatted_data)
 Output Directory    : .

 Key Extraction Logic:
  - (SHOP 1)                    : 72 moves before next shop unlock
  - (SHOP 1, SHOP 2)            : 72 moves before next shop unlock
  - ...
  - After 8th shop unlock       : all moves before match end

 Cascading Priority Rules:
  - Strict hierarchical priority (Rank 1 > Rank 2 > Rank 3 > ...)
  - Highest winning reward resolves ties within same rank
==============================================================================

 Select an option:

  [1] Run Full Sequence Extraction (Generates sequence_moves.parquet)
  [2] Query Specific Sequence (e.g. PIZZA, ICE_CREAM)
  [3] View Sequence Statistics
  [4] View All Sequences Arranged by Most Frequent (Top 200)
  [5] Switch Input Source (Toggle Formatter2 <-> Formatter)
  [6] Exit
```

---

## 3. Move Windows Logic

In Kaggriculture matches (720 total steps):
- **1st Shop Unlock** (e.g. `(PIZZA)` at step 72):
  - Captures **72 moves** (steps 72 to 143) before the 2nd shop unlocks.
- **2nd Shop Unlock** (e.g. `(PIZZA, ICE_CREAM)` at step 144):
  - Captures **72 moves** (steps 144 to 215) before the 3rd shop unlocks.
- **3rd Shop Unlock** (e.g. `(PIZZA, ICE_CREAM, SMOOTHIE)` at step 216):
  - Captures **72 moves** (steps 216 to 287) before the 4th shop unlocks.
- ...
- **8th Shop Unlock** (step 576):
  - Captures **all moves before match end** (steps 576 to 719, 144 moves).

---

## 4. Cascading Player Priority & Overwrite Rules

The extractor enforces a strict **cascading priority hierarchy** across all players:
$$\text{Rank 1} > \text{Rank 2} > \text{Rank 3} > \text{Rank 4} > \dots$$

1. **Higher-Priority Locks In**:
   If a particular sequence is found in a higher-ranked player, it locks in. Lower-ranked players cannot overwrite it.
2. **Cascading Overwrite**:
   If a sequence was observed in a lower-ranked player, but also played by a higher-ranked player in another match, the higher-ranked player overwrites it.
3. **Tie-Breaking**:
   Within the same rank tier, the match with the highest winning reward is chosen.

---

## 5. Output Datasets & Reports

All datasets are written using **atomic writes** (`temp_` + backup swap) to ensure zero corruption if interrupted.

| File Name | Description | Rows (F2) | Size (F2) |
| :--- | :--- | :--- | :--- |
| `sequence_moves.parquet` | Master turn moves (Farmer, Farmhands, Market, Full Action) | 3,374,928 | ~191 MB |
| `sequences_summary.parquet` | Summary catalog of all unique sequences, match metadata, and steps | 34,714 | ~0.7 MB |
| `sequence_frequencies.parquet` | All sequences sorted by frequency descending (Rank, Label, Length, Count, %) | 34,714 | ~0.2 MB |
| `sequence_frequencies.csv` | Human-readable CSV export of sequence frequency ranking | 34,714 | ~1.1 MB |

---

## 6. CLI Usage

```bash
# Frequency Analysis (Top 200, Formatter2 dataset)
python extractor.py --input "..\Formatter2\formatted_data" --frequency --top 200

# Full Sequence Extraction (Formatter2 dataset)
python extractor.py --input "..\Formatter2\formatted_data"

# Full Sequence Extraction (Formatter dataset)
python extractor.py --input "..\Formatter\outputs"

# Query a Specific Sequence
python extractor.py --query "PIZZA, ICE_CREAM"

# View Statistics
python extractor.py --stats
```
