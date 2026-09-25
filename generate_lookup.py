import json
from pathlib import Path

def find_player_index(replay_path, player_name):
    replay = json.loads(Path(replay_path).read_text())
    names = replay.get("info", {}).get("TeamNames", [])
    for i, name in enumerate(names):
        if name == player_name:
            return i
    return None  # name didn't match either seat — check for typos/aliases