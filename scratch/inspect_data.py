import json
from pathlib import Path

rep = json.loads(Path("Kaggiculture_Analysis/Downloader/downloads/kaggriculture/01_DSM/112477863.json").read_text(encoding="utf-8"))
for t in [0, 72, 144, 216, 500, 719]:
    obs = rep["steps"][t][0]["observation"]
    farm = obs["farms"][0]
    print(f"Step {t}: money={farm.get('money')}, quadrants={farm.get('unlocked_quadrants')}, hands={len(farm.get('hands', []))}")

# Check tile details
tiles = rep["steps"][500][0]["observation"]["farms"][0]["tiles"]
print(f"Total tile rows: {len(tiles)}, cols: {len(tiles[0])}")
types = set()
for r in tiles:
    for c in r:
        if isinstance(c, dict):
            types.add(c.get("kind") or c.get("type"))
        else:
            types.add(str(c))
print("Tile types at step 500:", types)

# Check all action types
farmer_acts = set()
hand_acts = set()
market_acts = set()
for s in rep["steps"]:
    act = s[0].get("action", {})
    f = act.get("farmer")
    if f and isinstance(f, list):
        farmer_acts.add(f[0])
    for h in act.get("hands", []):
        if h and isinstance(h, list):
            hand_acts.add(h[0])
    for m in act.get("market", []):
        if m and isinstance(m, list):
            market_acts.add(m[0])

print("Farmer action verbs:", sorted(farmer_acts))
print("Hand action verbs:", sorted(hand_acts))
print("Market action verbs:", sorted(market_acts))
