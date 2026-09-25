# SPDX-License-Identifier: Apache-2.0
"""Renders Field Worlds' pooled world-frequency table (and optional
route_db coverage cross-reference) into one self-contained HTML report."""
from __future__ import annotations

import json
from pathlib import Path

from .cross_reference import CoverageResult
from .parquet_scan import ScanResult
from .worlds import WorldFrequency, distinct_worlds_possible


def build_report_payload(
    scan: ScanResult,
    world_table: dict[tuple, WorldFrequency],
    k: int,
    n_shop_types: int,
    coverage: dict[tuple, CoverageResult] | None,
) -> dict:
    rows = []
    for world, wf in sorted(world_table.items(), key=lambda kv: -kv[1].times_seen):
        row = {
            "world": " -> ".join(world),
            "times_seen": wf.times_seen,
            "tie_rate": round(wf.tie_rate * 100, 1),
            "avg_abs_margin": round(wf.avg_abs_margin),
            "avg_combined_score": round(wf.avg_combined_score),
        }
        if coverage is not None:
            cr = coverage.get(world)
            row["our_coverage"] = "yes" if (cr and cr.has_exact_route) else "no"
            row["our_route_id"] = cr.route_id if (cr and cr.has_exact_route) else None
        rows.append(row)

    return {
        "k": k,
        "used_files": scan.used_files,
        "skipped_files": [{"path": p, "reason": r} for p, r in scan.skipped_files],
        "n_episodes": len(scan.records),
        "distinct_worlds_observed": len(world_table),
        "distinct_worlds_possible": distinct_worlds_possible(n_shop_types, k),
        "has_coverage": coverage is not None,
        "coverage_count": sum(1 for c in (coverage or {}).values() if c.has_exact_route),
        "rows": rows,
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Field Worlds</title>
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
  .cov-yes {{ color:#a3d977; }}
  .cov-no {{ color:#e07a5f; }}
  .skip-list {{ font-size:12px; color:#a9a496; }}
  canvas {{ max-height:340px; }}
</style>
</head>
<body>

<h1>Field Worlds</h1>
<div class="subtitle">Pooled shop-unlock RNG frequency, k={k}, across many different players' matches</div>

<div class="caveat" id="caveat"></div>

<div class="summary-grid" id="summary-grid"></div>

<div class="panel">
  <h2>World frequency, top 20</h2>
  <div class="desc">How often each ordered shop-sequence draw actually occurred across the pooled sample.</div>
  <canvas id="chart-freq"></canvas>
</div>

<div class="panel">
  <h2>Worlds &amp; coverage table</h2>
  <div class="desc">Every world observed, its real record, and (if a submission was supplied) whether our own route_db has an exact route for it.</div>
  <table id="world-table"></table>
</div>

<div class="panel" id="skipped-panel" style="display:none">
  <h2>Files skipped</h2>
  <div class="desc">These files didn't have the columns Field Worlds needs (the enriched shop-sequence schema).</div>
  <div class="skip-list" id="skip-list"></div>
</div>

<script>
const DATA = {data_json};
const COLORS = ["#d1a256","#7fb3d5","#a3d977","#e07a5f","#9b8fd1","#f2c14e"];

document.getElementById("caveat").textContent =
  `Built from ${{DATA.used_files.length}} file(s), ${{DATA.n_episodes}} episodes. ` +
  `${{DATA.distinct_worlds_observed}} of ${{DATA.distinct_worlds_possible}} theoretically possible k=${{DATA.k}} worlds were actually observed.`;

const cards = [
  ["Episodes pooled", DATA.n_episodes],
  ["Distinct worlds observed", `${{DATA.distinct_worlds_observed}} / ${{DATA.distinct_worlds_possible}}`],
  ["Files used", DATA.used_files.length],
  ["Files skipped", DATA.skipped_files.length],
];
if (DATA.has_coverage) {{
  cards.push(["Our route_db covers", `${{DATA.coverage_count}} / ${{DATA.distinct_worlds_observed}}`]);
}}
document.getElementById("summary-grid").innerHTML = cards.map(([label, value]) =>
  `<div class="stat"><div class="label">${{label}}</div><div class="value">${{value}}</div></div>`
).join("");

// --- frequency bar chart, top 20 ---
const top20 = DATA.rows.slice(0, 20);
new Chart(document.getElementById("chart-freq"), {{
  type: "bar",
  data: {{
    labels: top20.map(r => r.world),
    datasets: [{{ label: "times seen", data: top20.map(r => r.times_seen), backgroundColor: COLORS[0] }}]
  }},
  options: {{ indexAxis: "y", scales: {{ y: {{ ticks: {{ font: {{ size: 10 }} }} }} }} }}
}});

// --- full table ---
const tbl = document.getElementById("world-table");
let header = "<tr><th>World</th><th>Times seen</th><th>Tie rate</th><th>Avg |margin|</th><th>Avg combined score</th>";
if (DATA.has_coverage) header += "<th>Our route_db</th>";
header += "</tr>";
let rows = header;
DATA.rows.forEach(r => {{
  rows += `<tr><td>${{r.world}}</td><td>${{r.times_seen}}</td><td>${{r.tie_rate}}%</td>` +
          `<td>$${{r.avg_abs_margin.toLocaleString()}}</td><td>$${{r.avg_combined_score.toLocaleString()}}</td>`;
  if (DATA.has_coverage) {{
    const cls = r.our_coverage === "yes" ? "cov-yes" : "cov-no";
    const label = r.our_coverage === "yes" ? `yes (route ${{r.our_route_id}})` : "no";
    rows += `<td class="${{cls}}">${{label}}</td>`;
  }}
  rows += "</tr>";
}});
tbl.innerHTML = rows;

// --- skipped files ---
if (DATA.skipped_files.length) {{
  document.getElementById("skipped-panel").style.display = "block";
  document.getElementById("skip-list").innerHTML = DATA.skipped_files.map(
    f => `<div>${{f.path}}: ${{f.reason}}</div>`
  ).join("");
}}
</script>
</body>
</html>
"""


def render_html(payload: dict) -> str:
    return HTML_TEMPLATE.format(k=payload["k"], data_json=json.dumps(payload))


def write_report(payload: dict, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.write_text(render_html(payload), encoding="utf-8")
    return out_path
