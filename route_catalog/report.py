# SPDX-License-Identifier: Apache-2.0
"""Renders route_catalog output into two kinds of self-contained HTML
report: a catalog overview (most-recurring routes across the whole
dataset) and a per-match timeline (a Gantt-style view of exactly which
route was active during which steps)."""
from __future__ import annotations

import json
from pathlib import Path

from .catalog import Catalog
from .timeline import TimelineEvent

COMMON_STYLE = """
:root { color-scheme: dark; }
body {
  background:#171a14; color:#e8e6df; font-family: -apple-system, Segoe UI, Roboto, sans-serif;
  margin:0; padding:24px; max-width:1100px; margin-inline:auto;
}
h1 { font-size: 22px; margin-bottom:4px; }
.subtitle { color:#a9a496; margin-bottom:24px; font-size:14px; }
.caveat {
  background:#20241a; border:1px solid #d1a256; border-radius:8px;
  padding:12px 16px; margin-bottom:24px; font-size:13px; color:#d1a256;
}
.summary-grid {
  display:grid; grid-template-columns: repeat(auto-fit, minmax(180px,1fr));
  gap:12px; margin-bottom:28px;
}
.stat { background:#1e2118; border:1px solid #33362a; border-radius:8px; padding:12px 14px; }
.stat .label { font-size:11px; text-transform:uppercase; letter-spacing:.05em; color:#a9a496; }
.stat .value { font-size:20px; font-weight:700; margin-top:4px; }
.panel {
  background:#1e2118; border:1px solid #33362a; border-radius:12px;
  padding:18px 20px; margin-bottom:24px;
}
.panel h2 { font-size:16px; margin:0 0 4px 0; }
.panel .desc { font-size:13px; color:#a9a496; margin-bottom:14px; }
table { width:100%; border-collapse: collapse; font-size:13px; }
th, td { text-align:left; padding:6px 10px; border-bottom:1px solid #33362a; }
th { color:#a9a496; font-weight:600; font-size:11px; text-transform:uppercase; letter-spacing:.04em; }
tr:hover td { background:#20241a; }
.match-exact { color:#a3d977; }
.match-loose { color:#d1a256; }
.match-none { color:#e07a5f; }
.gantt-row { display:flex; height:36px; margin-bottom:6px; border-radius:4px; overflow:hidden; }
.gantt-seg {
  display:flex; align-items:center; justify-content:center; font-size:11px;
  color:#171a14; font-weight:600; border-right:2px solid #171a14; white-space:nowrap; overflow:hidden;
}
.gantt-axis { display:flex; font-size:10px; color:#a9a496; margin-bottom:4px; }
"""


# --------------------------------------------------------------------------
# Catalog overview report
# --------------------------------------------------------------------------

def build_catalog_report_payload(catalog: Catalog, top_n: int = 30) -> dict:
    recurring = [e for e in catalog.entries.values() if e.times_seen > 1]
    recurring.sort(key=lambda e: -e.times_seen)

    rows = []
    for e in recurring[:top_n]:
        avg_margin = sum(e.reward_margins) / len(e.reward_margins) if e.reward_margins else 0
        rows.append({
            "situation": " -> ".join(e.situation) if e.situation else "(opening, no shops yet)",
            "length_windows": e.length_windows,
            "length_steps": e.length_windows * 72,
            "times_seen": e.times_seen,
            "avg_margin": round(avg_margin),
            "distinct_episodes": len({o.episode_id for o in e.occurrences}),
        })

    length_histogram: dict[int, int] = {}
    for e in recurring:
        length_histogram[e.length_windows] = length_histogram.get(e.length_windows, 0) + 1

    return {
        "n_replays_scanned": catalog.n_replays_scanned,
        "n_skipped": len(catalog.skipped_replays),
        "n_candidates_seen": catalog.n_candidates_seen,
        "n_distinct_entries": len(catalog.entries),
        "n_recurring_entries": len(recurring),
        "n_loose_families": len(catalog.families),
        "length_histogram": dict(sorted(length_histogram.items())),
        "rows": rows,
    }


CATALOG_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Route Catalog</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.4/chart.umd.min.js"></script>
<style>{style}</style>
</head>
<body>
<h1>Route Catalog</h1>
<div class="subtitle">Recurring action-sequence routes discovered directly from player replays, at every length</div>
<div class="caveat" id="caveat"></div>
<div class="summary-grid" id="summary-grid"></div>

<div class="panel">
  <h2>Recurring routes by length (in windows)</h2>
  <div class="desc">How many distinct recurring routes exist at each length. A "window" is 72 steps (one shop-unlock period).</div>
  <canvas id="chart-length"></canvas>
</div>

<div class="panel">
  <h2>Top recurring routes</h2>
  <div class="desc">Ranked by how many times the exact same action sequence was played, from the same starting situation.</div>
  <table id="route-table"></table>
</div>

<script>
const DATA = {data_json};
const COLORS = ["#d1a256","#7fb3d5","#a3d977","#e07a5f","#9b8fd1","#f2c14e"];

document.getElementById("caveat").textContent =
  `Built from ${{DATA.n_replays_scanned}} replay(s), ${{DATA.n_skipped}} skipped. ` +
  `${{DATA.n_candidates_seen}} candidate routes examined across every length; ` +
  `${{DATA.n_recurring_entries}} of ${{DATA.n_distinct_entries}} distinct exact routes recur more than once.`;

const cards = [
  ["Replays scanned", DATA.n_replays_scanned],
  ["Candidates examined", DATA.n_candidates_seen.toLocaleString()],
  ["Distinct exact routes", DATA.n_distinct_entries.toLocaleString()],
  ["Recurring routes", DATA.n_recurring_entries],
  ["Loose families", DATA.n_loose_families.toLocaleString()],
];
document.getElementById("summary-grid").innerHTML = cards.map(([label, value]) =>
  `<div class="stat"><div class="label">${{label}}</div><div class="value">${{value}}</div></div>`
).join("");

const lengths = Object.keys(DATA.length_histogram).map(Number).sort((a,b)=>a-b);
new Chart(document.getElementById("chart-length"), {{
  type: "bar",
  data: {{
    labels: lengths.map(l => `${{l}}w (${{l*72}} steps)`),
    datasets: [{{ label: "recurring routes", data: lengths.map(l => DATA.length_histogram[l]), backgroundColor: COLORS[0] }}]
  }}
}});

const tbl = document.getElementById("route-table");
let rows = "<tr><th>Situation</th><th>Length</th><th>Times seen</th><th>Distinct matches</th><th>Avg margin</th></tr>";
DATA.rows.forEach(r => {{
  rows += `<tr><td>${{r.situation}}</td><td>${{r.length_windows}}w (${{r.length_steps}} steps)</td>` +
          `<td>${{r.times_seen}}</td><td>${{r.distinct_episodes}}</td><td>${{r.avg_margin >= 0 ? '+' : ''}}${{r.avg_margin.toLocaleString()}}</td></tr>`;
}});
tbl.innerHTML = rows;
</script>
</body>
</html>
"""


def write_catalog_report(payload: dict, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.write_text(CATALOG_HTML_TEMPLATE.format(style=COMMON_STYLE, data_json=json.dumps(payload)), encoding="utf-8")
    return out_path


# --------------------------------------------------------------------------
# Per-match timeline report
# --------------------------------------------------------------------------

def build_timeline_report_payload(events: list[TimelineEvent], episode_id: str, player_index: int) -> dict:
    return {
        "episode_id": episode_id,
        "player_index": player_index,
        "events": [
            {
                "start_step": e.start_step,
                "end_step": e.end_step,
                "length_windows": e.length_windows,
                "situation": " -> ".join(e.situation) if e.situation else "(opening)",
                "match_type": e.match_type,
                "times_seen_elsewhere": e.times_seen_elsewhere,
            }
            for e in events
        ],
    }


TIMELINE_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Route Timeline</title>
<style>{style}</style>
</head>
<body>
<h1>Route Timeline</h1>
<div class="subtitle">{episode_id} &mdash; player {player_index}</div>
<div class="caveat">
  Green = a route that also recurs elsewhere in the catalog. Orange = a loose/approximate match.
  Red = no known recurring route was found for this stretch (unique to this match, at least in the
  data this catalog was built from).
</div>

<div class="panel">
  <h2>Timeline (720 steps)</h2>
  <div class="gantt-axis" id="gantt-axis"></div>
  <div class="gantt-row" id="gantt-row"></div>
</div>

<div class="panel">
  <h2>Segments</h2>
  <table id="segment-table"></table>
</div>

<script>
const DATA = {data_json};
const TOTAL_STEPS = 720;

const axis = document.getElementById("gantt-axis");
let axisHtml = "";
for (let s = 0; s <= TOTAL_STEPS; s += 72) {{
  axisHtml += `<div style="width:${{72/TOTAL_STEPS*100}}%">day ${{Math.floor(s/24)+1}}</div>`;
}}
axis.innerHTML = axisHtml;

const row = document.getElementById("gantt-row");
row.innerHTML = DATA.events.map(e => {{
  const width = (e.end_step - e.start_step) / TOTAL_STEPS * 100;
  const color = e.match_type === "exact" ? "#a3d977" : (e.match_type === "loose" ? "#d1a256" : "#e07a5f");
  const label = e.match_type === "none" ? "?" : `${{e.length_windows}}w`;
  return `<div class="gantt-seg" style="width:${{width}}%;background:${{color}}" title="steps ${{e.start_step}}-${{e.end_step}}, ${{e.match_type}}">${{label}}</div>`;
}}).join("");

const tbl = document.getElementById("segment-table");
let rows = "<tr><th>Steps</th><th>Length</th><th>Match</th><th>Seen elsewhere</th><th>Situation</th></tr>";
DATA.events.forEach(e => {{
  const cls = e.match_type === "exact" ? "match-exact" : (e.match_type === "loose" ? "match-loose" : "match-none");
  rows += `<tr><td>${{e.start_step}}-${{e.end_step}}</td><td>${{e.length_windows}}w</td>` +
          `<td class="${{cls}}">${{e.match_type}}</td><td>${{e.times_seen_elsewhere}}</td><td>${{e.situation}}</td></tr>`;
}});
tbl.innerHTML = rows;
</script>
</body>
</html>
"""


def write_timeline_report(payload: dict, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.write_text(
        TIMELINE_HTML_TEMPLATE.format(
            style=COMMON_STYLE, episode_id=payload["episode_id"], player_index=payload["player_index"],
            data_json=json.dumps(payload),
        ),
        encoding="utf-8",
    )
    return out_path
