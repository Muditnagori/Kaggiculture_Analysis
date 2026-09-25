# SPDX-License-Identifier: Apache-2.0
"""
Renders reconstructed match data into a single, self-contained HTML file.
Uses Chart.js from a CDN (loaded by the browser when you open the file --
no local install needed) so the only Python dependency for this whole
project is the standard library.
"""
from __future__ import annotations

import json
from pathlib import Path

from .analytics import (
    advantage_series,
    cumulative_by_category,
    free_cash_series,
    match_summary,
    totals_by_category,
    trend_d48,
)
from .reconstruct import StepRecord


def _records_to_payload(records: list[StepRecord]) -> dict:
    return {
        "step": [r.step for r in records],
        "day": [r.day for r in records],
        "cash": [r.cash for r in records],
        "projected_value": [r.projected_value for r in records],
        "idle_hands": [r.idle_hands for r in records],
        "total_hands": [r.total_hands for r in records],
        "empty_tiles": [r.empty_tiles for r in records],
        "total_unlocked_tiles": [r.total_unlocked_tiles for r in records],
        "prices": [r.prices for r in records],
    }


def build_dashboard_payload(
    records_us: list[StepRecord],
    records_opp: list[StepRecord],
    name_us: str,
    name_opp: str,
) -> dict:
    g = advantage_series(records_us, records_opp)
    trend = trend_d48(g)

    all_crops = sorted({item for r in records_us for item in r.prices.keys()})

    return {
        "name_us": name_us,
        "name_opp": name_opp,
        "us": _records_to_payload(records_us),
        "opp": _records_to_payload(records_opp),
        "advantage": g,
        "trend_d48": trend,
        "crops": all_crops,
        "revenue_cum_us": cumulative_by_category(records_us, "revenue_by_category"),
        "spend_cum_us": cumulative_by_category(records_us, "spend_by_category"),
        "revenue_cum_opp": cumulative_by_category(records_opp, "revenue_by_category"),
        "spend_cum_opp": cumulative_by_category(records_opp, "spend_by_category"),
        "revenue_totals_us": totals_by_category(records_us, "revenue_by_category"),
        "spend_totals_us": totals_by_category(records_us, "spend_by_category"),
        "revenue_totals_opp": totals_by_category(records_opp, "revenue_by_category"),
        "spend_totals_opp": totals_by_category(records_opp, "spend_by_category"),
        "free_cash_us": free_cash_series(records_us),
        "free_cash_opp": free_cash_series(records_opp),
        "summary": match_summary(records_us, records_opp),
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Field Ledger &mdash; {name_us} vs {name_opp}</title>
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
  select {{
    background:#171a14; color:#e8e6df; border:1px solid #33362a; border-radius:6px;
    padding:6px 10px; margin-bottom:12px;
  }}
  canvas {{ max-height:340px; }}
  .two-col {{ display:grid; grid-template-columns: 1fr 1fr; gap:20px; }}
</style>
</head>
<body>

<h1>Field Ledger</h1>
<div class="subtitle">{name_us} (us) vs {name_opp} (opponent)</div>

<div class="caveat">
  <b>Read this first:</b> "projected value" uses TODAY's market price for
  anything still growing or unsold, not a true price forecast &mdash; no
  price-prediction model exists in the public replay data. "Free cash"
  reserves an estimated wage cost for re-hiring tomorrow's headcount; it is
  an approximation, not the game's own accounting.
</div>

<div class="summary-grid" id="summary-grid"></div>

<div class="panel">
  <h2>Projected value over time</h2>
  <div class="desc">Cash + unsold harvested inventory + still-growing crops, valued at today's prices.</div>
  <canvas id="chart-projected"></canvas>
</div>

<div class="panel">
  <h2>Trend &mdash; are we pulling ahead or falling behind?</h2>
  <div class="desc">D_48(t): mean advantage over the latest 48 turns minus the previous 48. Positive and climbing = gaining ground.</div>
  <canvas id="chart-trend"></canvas>
</div>

<div class="panel">
  <h2>Market price</h2>
  <select id="crop-select"></select>
  <canvas id="chart-price"></canvas>
</div>

<div class="panel">
  <h2>Where the money came from</h2>
  <div class="desc">Cumulative SELL revenue by item, over time.</div>
  <div class="two-col">
    <canvas id="chart-revenue-us"></canvas>
    <canvas id="chart-revenue-opp"></canvas>
  </div>
</div>

<div class="panel">
  <h2>Where the money went</h2>
  <div class="desc">Cumulative spend by category, over time.</div>
  <div class="two-col">
    <canvas id="chart-spend-us"></canvas>
    <canvas id="chart-spend-opp"></canvas>
  </div>
</div>

<div class="panel">
  <h2>Cash vs. actually free to spend</h2>
  <div class="desc">Cash on hand vs. cash minus an estimated wage reserve for tomorrow's headcount.</div>
  <canvas id="chart-freecash"></canvas>
</div>

<div class="panel">
  <h2>Idle hands &amp; empty tiles</h2>
  <div class="desc">Hands with no order this turn, and unlocked tiles with nothing productive on them.</div>
  <canvas id="chart-idle"></canvas>
</div>

<script>
const DATA = {data_json};

const COLORS = ["#d1a256","#7fb3d5","#a3d977","#e07a5f","#9b8fd1","#f2c14e","#5fb0a8","#d17aa3"];

function fmtMoney(v) {{
  return "$" + Math.round(v).toLocaleString();
}}

// --- summary cards ---
const s = DATA.summary;
const summaryEl = document.getElementById("summary-grid");
const cards = [
  ["Final projected (us)", fmtMoney(s.final_projected_us)],
  ["Final projected (opp)", fmtMoney(s.final_projected_opp)],
  ["Final margin", (s.final_margin >= 0 ? "+" : "") + fmtMoney(s.final_margin)],
  ["Total revenue (us)", fmtMoney(s.total_revenue_us)],
  ["Total spend (us)", fmtMoney(s.total_spend_us)],
];
summaryEl.innerHTML = cards.map(([label, value]) =>
  `<div class="stat"><div class="label">${{label}}</div><div class="value">${{value}}</div></div>`
).join("");

// --- projected value chart ---
new Chart(document.getElementById("chart-projected"), {{
  type: "line",
  data: {{
    labels: DATA.us.step,
    datasets: [
      {{ label: DATA.name_us, data: DATA.us.projected_value, borderColor: COLORS[0], pointRadius: 0, borderWidth: 2 }},
      {{ label: DATA.name_opp, data: DATA.opp.projected_value, borderColor: COLORS[1], pointRadius: 0, borderWidth: 2 }},
    ]
  }},
  options: {{ scales: {{ x: {{ title: {{ display:true, text:"step" }} }} }} }}
}});

// --- trend chart ---
new Chart(document.getElementById("chart-trend"), {{
  type: "line",
  data: {{
    labels: DATA.us.step,
    datasets: [
      {{ label: "D_48(t)", data: DATA.trend_d48, borderColor: COLORS[2], pointRadius: 0, borderWidth: 2 }},
    ]
  }},
  options: {{ scales: {{ x: {{ title: {{ display:true, text:"step" }} }} }} }}
}});

// --- market price chart (selectable crop) ---
const cropSelect = document.getElementById("crop-select");
DATA.crops.forEach(c => {{
  const opt = document.createElement("option");
  opt.value = c; opt.textContent = c;
  cropSelect.appendChild(opt);
}});
let priceChart;
function renderPriceChart(crop) {{
  const series = DATA.us.prices.map(p => p[crop] ?? null);
  if (priceChart) priceChart.destroy();
  priceChart = new Chart(document.getElementById("chart-price"), {{
    type: "line",
    data: {{ labels: DATA.us.step, datasets: [{{ label: crop, data: series, borderColor: COLORS[3], pointRadius: 0, borderWidth: 2 }}] }},
    options: {{ scales: {{ x: {{ title: {{ display:true, text:"step" }} }} }} }}
  }});
}}
cropSelect.addEventListener("change", e => renderPriceChart(e.target.value));
if (DATA.crops.length) {{ cropSelect.value = DATA.crops[0]; renderPriceChart(DATA.crops[0]); }}

// --- revenue / spend stacked area, both players ---
function stackedAreaConfig(labels, seriesObj) {{
  const keys = Object.keys(seriesObj);
  return {{
    type: "line",
    data: {{
      labels: labels,
      datasets: keys.map((k, i) => ({{
        label: k, data: seriesObj[k], borderColor: COLORS[i % COLORS.length],
        backgroundColor: COLORS[i % COLORS.length] + "55", fill: true, pointRadius: 0, borderWidth: 1,
      }}))
    }},
    options: {{ scales: {{ y: {{ stacked: true }}, x: {{ title: {{ display:true, text:"step" }} }} }} }}
  }};
}}
new Chart(document.getElementById("chart-revenue-us"), stackedAreaConfig(DATA.us.step, DATA.revenue_cum_us));
new Chart(document.getElementById("chart-revenue-opp"), stackedAreaConfig(DATA.opp.step, DATA.revenue_cum_opp));
new Chart(document.getElementById("chart-spend-us"), stackedAreaConfig(DATA.us.step, DATA.spend_cum_us));
new Chart(document.getElementById("chart-spend-opp"), stackedAreaConfig(DATA.opp.step, DATA.spend_cum_opp));

// --- free cash vs cash ---
new Chart(document.getElementById("chart-freecash"), {{
  type: "line",
  data: {{
    labels: DATA.us.step,
    datasets: [
      {{ label: "Cash on hand", data: DATA.us.cash, borderColor: COLORS[0], pointRadius: 0, borderWidth: 2 }},
      {{ label: "Actually free (est.)", data: DATA.free_cash_us, borderColor: COLORS[4], pointRadius: 0, borderWidth: 2 }},
    ]
  }},
  options: {{ scales: {{ x: {{ title: {{ display:true, text:"step" }} }} }} }}
}});

// --- idle hands & empty tiles ---
new Chart(document.getElementById("chart-idle"), {{
  type: "line",
  data: {{
    labels: DATA.us.step,
    datasets: [
      {{ label: "Idle hands", data: DATA.us.idle_hands, borderColor: COLORS[5], pointRadius: 0, borderWidth: 2 }},
      {{ label: "Empty tiles", data: DATA.us.empty_tiles, borderColor: COLORS[6], pointRadius: 0, borderWidth: 2 }},
    ]
  }},
  options: {{ scales: {{ x: {{ title: {{ display:true, text:"step" }} }} }} }}
}});
</script>
</body>
</html>
"""


def render_html(payload: dict) -> str:
    return HTML_TEMPLATE.format(
        name_us=payload["name_us"],
        name_opp=payload["name_opp"],
        data_json=json.dumps(payload),
    )


def write_dashboard(payload: dict, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.write_text(render_html(payload), encoding="utf-8")
    return out_path
