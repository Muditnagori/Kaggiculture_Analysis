# K2 Replay Downloader

Automated high-performance downloader for Kaggle simulation match replays, featuring dynamic leaderboard synchronization, match-existence caching, and strict rank enforcement.

---

## 🎯 Key Features

### 1. Automatic Live Rank Synchronization
- Every time the downloader runs, it retrieves the live Kaggle competition leaderboard (8,115+ active teams).
- It compares all existing folders in `downloads/<competition>/` against the live standings:
  - If a player's rank shifted (e.g. `02_3정훈` ➔ `04_3정훈`), the folder is automatically renamed to match their current rank.
  - **No two players can ever share the same rank prefix.**

### 2. Permanent Database Preservation (No Deletions Below Rank 20)
- **Rule**: If a player's rank falls below rank 20 (e.g. dropped to rank #944):
  - Their replay database is **NEVER deleted**.
  - Their folder in `Downloader/downloads/<competition>/` is automatically renamed to match their current live rank (e.g. `944_3정훈`), preserving all historical replays and match data.
  - Corresponding output files in `Formatter/outputs/` are also automatically renamed to match.

### 3. Match-Existence Caching & Fast Streaming
- Scans existing local folders before downloading.
- Existing matches (>500 bytes) are instantly reused without re-downloading.
- Downloads remaining matches using concurrent multi-worker streaming.

---

## 🚀 How to Run

### Interactive Menu:
Double-click [`run.bat`](file:///a:/Kaggle/Replicator/Downloader/run.bat) or run:
```powershell
python Downloader/main.py
```

### Command Line:
```powershell
# Download Top 20 players and all their matches:
python Downloader/main.py --top-20 --matches all --outcome all

# Download Top 5 players (or custom number) and all their matches:
python Downloader/main.py --players 5 --matches all --outcome all

# Download Top 10% players:
python Downloader/main.py --top-percent 10 --matches all

# Download matches for a specific player:
python Downloader/main.py --username "player_name" --matches all
```
