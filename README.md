# Kaggriculture Analysis Pipeline

A high-performance, multi-stage engineering pipeline for downloading, normalizing, analyzing, and extracting gameplay moves from **Kaggriculture** simulation replays on Kaggle.

---

## Repository Architecture

```text
Kaggiculture_Analysis/
|-- replay_downloader/        # Automated Kaggle replay downloader & session manager
|   |-- main.py               # Downloader entrypoint & interactive manager
|   |-- downloader.py         # Multi-worker async replay fetcher
|   |-- client.py             # Kaggle API client & leaderboard fetcher
|   |-- auth.py               # Authentication helper
|   `-- run.bat               # Windows quick-launch runner
|
|-- replay_formatter/         # High-Performance Formatter & Single-Player Moves Exporter (F2)
|   |-- formatter.py          # 9 normalized Parquet tables generator
|   |-- live_rankings.py      # Real-time Kaggle leaderboard sync (zero overlap)
|   |-- export_player_moves.py # Single Parquet per player moves extractor
|   |-- parquet_extractor.py  # Shop sequence + winning-agent moves (formerly Formatter)
|   `-- run.bat               # Interactive menu runner
|
`-- sequence_extractor/       # Shop Sequence Move Extractor & Strategy Analyzer
    |-- extractor.py          # Unlocked shop sequence and priority analyzer
    `-- run.bat               # Sequence analysis runner
```

---

## 1. Downloader (`replay_downloader/`)
Automated multi-worker pipeline that fetches match replays directly from the Kaggle API.
- Configurable top-player percentage, matches per player, and outcome filters in `replay_downloader/config.json`.
- Automatic resume and deduplication via SQLite download history.

## 2. Replay Formatter & Player Moves Exporter (`replay_formatter/`)
Stage-3 High-Performance Replay Formatter that normalizes raw replays into flat, queryable Parquet datasets:
- **Zero-overlap Live Ranking**: Connects to the Kaggle API to sync real-time leaderboard positions.
- **Single-Player Moves Exporter**: Extracts all moves across all matches for top $N$ players in seconds.
- **Interactive Menu**: Run `replay_formatter/run.bat` to launch the formatter, sync live ranks, or export player move sets.

## 3. Sequence Move Extractor (`sequence_extractor/`)
Analyzes shop unlock milestones, opening move patterns, and strategic trajectories of winning agents.

---

## Getting Started

1. Set up Python 3.10+ environment.
2. Install requirements in each subfolder:
   ```bash
   pip install -r replay_downloader/requirements.txt
   pip install -r F2/requirements.txt
   ```
3. Run any module via its respective `run.bat` or directly via Python.
