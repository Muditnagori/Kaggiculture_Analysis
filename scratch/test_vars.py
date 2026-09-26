import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from field_ledger.reconstruct import reconstruct_match

rep = json.loads(Path("Kaggiculture_Analysis/replay_downloader/downloads/kaggriculture/01_DSM/112477863.json").read_text(encoding="utf-8"))
match = reconstruct_match(rep)
p_records = match[0]

# Min cash
min_cash = min(r.cash for r in p_records)
print(f"Min cash dip: ${min_cash:,.2f}")

# Phase spends
p1_spend = sum(sum(r.spend_by_category.values()) for r in p_records[:240])
p1_rev = sum(sum(r.revenue_by_category.values()) for r in p_records[:240])
p2_spend = sum(sum(r.spend_by_category.values()) for r in p_records[240:480])
p2_rev = sum(sum(r.revenue_by_category.values()) for r in p_records[240:480])
p3_spend = sum(sum(r.spend_by_category.values()) for r in p_records[480:])
p3_rev = sum(sum(r.revenue_by_category.values()) for r in p_records[480:])

print(f"P1 Spend: ${p1_spend:,.0f}, P1 Rev: ${p1_rev:,.0f}")
print(f"P2 Spend: ${p2_spend:,.0f}, P2 Rev: ${p2_rev:,.0f}")
print(f"P3 Spend: ${p3_spend:,.0f}, P3 Rev: ${p3_rev:,.0f}")

# Liquidation step
last_productive_buy_step = 0
for t, s in enumerate(rep["steps"]):
    act = s[0].get("action", {})
    # Check if planting or buying seeds/animals
    f = act.get("farmer")
    if f and isinstance(f, list) and f[0] in ("PLANT", "BUILD_PASTURE", "BUILD_COOP"):
        last_productive_buy_step = t
    for h in act.get("hands", []):
        if h and isinstance(h, list) and h[0] in ("PLANT", "BUILD_PASTURE", "BUILD_COOP"):
            last_productive_buy_step = t
    for m in act.get("market", []):
        if m and isinstance(m, list) and m[0] in ("BUY_SEED", "BUY_ANIMAL", "BUY_LAND"):
            last_productive_buy_step = t

print(f"Last planting/investment step: {last_productive_buy_step} (Day {last_productive_buy_step//24 + 1})")

# Movement vs productive actions
moves = 0
productive = 0
for s in rep["steps"]:
    for h in s[0].get("action", {}).get("hands", []):
        if h and isinstance(h, list):
            cmd = h[0]
            if cmd in ("NORTH", "SOUTH", "EAST", "WEST", "MOVE"):
                moves += 1
            elif cmd not in ("PASS",):
                productive += 1

print(f"Hand moves: {moves}, Productive: {productive}, Pathing ratio: {moves/(moves+productive)*100:.1f}%")
