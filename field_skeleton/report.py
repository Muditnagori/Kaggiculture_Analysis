# SPDX-License-Identifier: Apache-2.0
"""Renders Field Skeleton's static (route_db structure) and dynamic
(real replay outcomes) analysis into one self-contained HTML report."""
from __future__ import annotations

import json
from pathlib import Path

from .aggregate import WorldStats
from .static_analysis import StaticRouteDBStats


def _world_row(w: WorldStats) -> dict:
    record = f"{w.wins}W {w.losses}L {w.ties_or_unknown}?"
    return {
        "day": w.day,
        "shops": " -> ".join(w.lookup_shops),
        "route_id": w.route_id,
        "match_type": w.match_type,
        "times_seen": w.times_seen,
        "record": record,
    }


def build_report_payload(static_stats: StaticRouteDBStats, dynamic_stats: dict) -> dict:
    return {
        "static": {
            "n_routes": static_stats.n_routes,
            "route_len": static_stats.route_len,
            "n_shop_types": static_stats.n_shop_types,
            "routes_per_k": static_stats.routes_per_sequence_length,
            "theoretical_per_k": {
                k: (static_stats.n_shop_types ** k) for k in static_stats.routes_per_sequence_length if k > 0
            },
        },
        "dynamic": {
            "n_matches": dynamic_stats["n_matches"],
            "overall_wins": dynamic_stats["overall_wins"],
            "overall_losses": dynamic_stats["overall_losses"],
            "overall_unknown": dynamic_stats["overall_unknown"],
            "match_type_counts": dynamic_stats["match_type_counts"],
            "world_table": [_world_row(w) for w in dynamic_stats["world_table"]],
        },
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Field Skeleton</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.4/chart.umd.min.js"></script>
<style>
  :root {{ color-scheme: dark; }}
  body {{
    background:#171a14; color:#e8e6df; font-family: -apple-system, Segoe UI, Roboto, sans-serif;
    margin:0; padding:24px; max-width:1100px; margin-inline:auto;
  }}
  h1 {{ font-size: 22px; margin-bottom:4px; }}
  .subtitle {{ color:#a9a496; margin-bottom:24px; font-size:14px; }}
  .caveat {{
    background:#20241a; border:1px solid #d1a256; border-radius:8px;
    padding:12px 16px; margin-bottom:24px; font-size:13px; color:#d1a256;
  }}
  .summary-grid {{
    display:grid; grid-template-columns: repeat(auto-fit, minmax(180px,1fr));
    gap:12px; margin-bottom:28px;
  }}
  .stat {{ background:#1e2118; border:1px solid #33362a; border-radius:8px; padding:12px 14px; }}
  .stat .label {{ font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:#a9a496; }}
  .stat .value {{ font-size:20px; font-weight:700; margin-top:4px; }}
  .panel {{
    background:#1e2118; border:1px solid #33362a; border-radius:12px;
    padding:18px 20px; margin-bottom:24px;
  }}
  .panel h2 {{ font-size:16px; margin:0 0 4px 0; }}
  .panel .desc {{ font-size:13px; color:#a9a496; margin-bottom:14px; }}
  table {{ width:100%; border-collapse: collapse; font-size:13px; }}
  th, td {{ text-align:left; padding:6px 10px; border-bottom:1px solid #33362a; }}
  th {{ color:#a9a496; font-weight:600; font-size:11px; text-transform:uppercase; letter-spacing:.04em; }}
  tr:hover td {{ background:#20241a; }}
  .match-exact {{ color:#a3d977; }}
  .match-fallback {{ color:#d1a256; }}
  .match-none {{ color:#e07a5f; }}
  canvas {{ max-height:300px; }}
</style>
</head>
<body>

<h1>Field Skeleton</h1>
<div class="subtitle">Ground truth read from source (route_db structure) + real replay outcomes</div>

<div class="caveat" id="sample-caveat"></div>

<div class="summary-grid" id="summary-grid"></div>

<div class="panel">
  <h2>Route database coverage, by shop-sequence length</h2>
  <div class="desc">How many distinct shop sequences of each length have an EXACT recorded route, vs. the theoretical full space (8^k). Coverage collapses fast as sequences get longer -- this is exactly why same-step fallback matching exists.</div>
  <canvas id="chart-coverage"></canvas>
</div>

<div class="panel">
  <h2>Match-type breakdown across replayed matches</h2>
  <div class="desc">exact = the live shop sequence had a recorded route. same_step_prefix_N = only the first N shops matched a recorded route; the rest of that route's shop assumptions differ from what actually happened live.</div>
  <canvas id="chart-matchtype"></canvas>
</div>

<div class="panel">
  <h2>Worlds &amp; the routing table</h2>
  <div class="desc">Every real route-selection decision observed across replayed matches, ordered by how often it happened.</div>
  <table id="world-table"></table>
</div>

<script>
const DATA = {data_json};
const COLORS = ["#d1a256","#7fb3d5","#a3d977","#e07a5f","#9b8fd1","#f2c14e"];

document.getElementById("sample-caveat").textContent =
  `Sample size: ${{DATA.dynamic.n_matches}} replayed match-perspectives. ` +
  `Treat the routing-table win/loss numbers as illustrative, not statistically ` +
  `reliable, until this is run across many more replays.`;

const s = DATA.dynamic;
const cards = [
  ["Matches replayed", s.n_matches],
  ["Wins", s.overall_wins],
  ["Losses", s.overall_losses],
  ["Ties / unknown", s.overall_unknown],
  ["Total routes in DB", DATA.static.n_routes.toLocaleString()],
];
document.getElementById("summary-grid").innerHTML = cards.map(([label, value]) =>
  `<div class="stat"><div class="label">${{label}}</div><div class="value">${{value}}</div></div>`
).join("");

// --- coverage chart: actual vs theoretical, log scale ---
const ks = Object.keys(DATA.static.routes_per_k).map(Number).sort((a,b)=>a-b);
new Chart(document.getElementById("chart-coverage"), {{
  type: "bar",
  data: {{
    labels: ks.map(k => `k=${{k}}`),
    datasets: [
      {{ label: "Recorded routes", data: ks.map(k => DATA.static.routes_per_k[k]), backgroundColor: COLORS[0] }},
      {{ label: "Theoretical max (8^k)", data: ks.map(k => DATA.static.theoretical_per_k[k] ?? null), backgroundColor: COLORS[1] }},
    ]
  }},
  options: {{ scales: {{ y: {{ type: "logarithmic", title: {{ display:true, text:"count (log scale)" }} }} }} }}
}});

// --- match type breakdown ---
const mtEntries = Object.entries(DATA.dynamic.match_type_counts);
new Chart(document.getElementById("chart-matchtype"), {{
  type: "bar",
  data: {{
    labels: mtEntries.map(([k]) => k),
    datasets: [{{ label: "occurrences", data: mtEntries.map(([,v]) => v), backgroundColor: COLORS[2] }}]
  }},
  options: {{ indexAxis: "y" }}
}});

// --- world table ---
const tbl = document.getElementById("world-table");
let rows = "<tr><th>Day</th><th>Shops</th><th>Route ID</th><th>Match type</th><th>Times seen</th><th>Record</th></tr>";
DATA.dynamic.world_table.forEach(w => {{
  const cls = w.match_type === "exact" ? "match-exact" : (w.match_type === "none" ? "match-none" : "match-fallback");
  rows += `<tr><td>${{w.day}}</td><td>${{w.shops}}</td><td>${{w.route_id}}</td>` +
          `<td class="${{cls}}">${{w.match_type}}</td><td>${{w.times_seen}}</td><td>${{w.record}}</td></tr>`;
}});
tbl.innerHTML = rows;
</script>
</body>
</html>
"""


def render_html(payload: dict) -> str:
    return HTML_TEMPLATE.format(data_json=json.dumps(payload))


def write_report(payload: dict, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.write_text(render_html(payload), encoding="utf-8")
    return out_path
