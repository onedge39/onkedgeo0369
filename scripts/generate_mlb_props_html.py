#!/usr/bin/env python3
"""
generate_mlb_props_html.py - MLB props dashboard HTML using saved Kalshi scan JSONL.

Reads the latest scan JSONL in data/scans, extracts MLB Kalshi prop markets,
groups by matchup/player/stat/line, and renders data/mlb_props.html.

This is a first-pass MLB props page for the current repo reality: MLB Kalshi
prop markets are present, while MLB comparison prop sources are not yet wired.
"""

from __future__ import annotations

import csv
import json
import os
import re
import tempfile
import time
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CT = ZoneInfo("America/Chicago")

MLB_TEAM_NAME_TO_ABBR = {
    "Arizona Diamondbacks": "ARI",
    "Athletics": "ATH",
    "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL",
    "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC",
    "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN",
    "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL",
    "Detroit Tigers": "DET",
    "Houston Astros": "HOU",
    "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA",
    "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA",
    "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN",
    "New York Mets": "NYM",
    "New York Yankees": "NYY",
    "Oakland Athletics": "OAK",
    "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SD",
    "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA",
    "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB",
    "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR",
    "Washington Nationals": "WSH",
}

MLB_ABBR_TO_TEAM_NAME = dict((abbr, name) for name, abbr in MLB_TEAM_NAME_TO_ABBR.items())
MLB_ABBREVS_SORTED = sorted(MLB_ABBR_TO_TEAM_NAME.keys(), key=len, reverse=True)

MLB_PROP_SERIES = {
    "KXMLBHIT": "H",
    "KXMLBHR": "HR",
    "KXMLBHRR": "H+R+RBI",
    "KXMLBKS": "K",
    "KXMLBTB": "TB",
}

BOOK_ORDER = [
    "kalshi",
]

BOOK_DISPLAY = {
    "kalshi": "Kalshi",
}


def load_latest_scan() -> Tuple[List[Dict[str, Any]], str]:
    scans_dir = ROOT / "data" / "scans"
    jsonl_files = sorted(scans_dir.glob("scan_*.jsonl"))
    if not jsonl_files:
        raise FileNotFoundError("No scan JSONL files found in {}".format(scans_dir))
    latest = jsonl_files[-1]
    rows = []
    with open(latest, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows, latest.name


def load_team_logos() -> Dict[str, str]:
    logos_path = ROOT / "data" / "mlb_team_logos.json"
    if logos_path.exists():
        with open(logos_path, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def load_roster() -> Dict[str, Dict[str, str]]:
    roster_path = ROOT / "data" / "rosters" / "mlb_player_team_map.csv"
    result = {}
    if not roster_path.exists():
        return result
    with open(roster_path, encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            player = (row.get("player_name") or "").strip()
            if not player:
                continue
            norm = normalize_name(player)
            result[norm] = {
                "player_name": player,
                "team_name": (row.get("team_name") or "").strip(),
                "team_abbrev": (row.get("team_abbrev") or "").strip(),
            }
    return result


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "")
    text = text.encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in text.upper() if ch.isalpha())


def fmt_money(value: Optional[Any]) -> str:
    if value is None:
        return "—"
    try:
        return "${:.2f}".format(float(value))
    except (TypeError, ValueError):
        return str(value)


def fmt_ts(ts: Optional[str]) -> str:
    if not ts:
        return "Unknown"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(CT).strftime("%Y-%m-%d %I:%M %p CT")
    except Exception:
        return ts


def parse_player_and_line(title: str) -> Tuple[str, Optional[float]]:
    raw = (title or "").strip()
    player = raw
    line = None
    if ":" in raw:
        player, rhs = raw.split(":", 1)
        player = player.strip()
        m = re.search(r"(\d+(?:\.\d+)?)\+", rhs)
        if m:
            try:
                line = float(m.group(1)) - 0.5
            except ValueError:
                line = None
    return player, line


def parse_matchup_from_ticker(ticker: str) -> str:
    parts = (ticker or "").split("-")
    if len(parts) < 3:
        return ""
    code = parts[1]
    if len(code) <= 11:
        return code
    matchup_code = code[11:]
    for away_abbr in MLB_ABBREVS_SORTED:
        if not matchup_code.startswith(away_abbr):
            continue
        home_abbr = matchup_code[len(away_abbr):]
        if home_abbr in MLB_ABBR_TO_TEAM_NAME:
            return "{} @ {}".format(
                MLB_ABBR_TO_TEAM_NAME[away_abbr],
                MLB_ABBR_TO_TEAM_NAME[home_abbr],
            )
    return matchup_code


def parse_mlb_kalshi_props(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    target_row = None
    for row in reversed(rows):
        if row.get("source") != "KALSHI":
            continue
        markets = (row.get("data") or {}).get("markets") or []
        if any((m.get("ticker") or "").startswith("KXMLB") for m in markets):
            target_row = row
            break
    if target_row is None:
        return []

    out = []
    for market in (target_row.get("data") or {}).get("markets") or []:
        ticker = market.get("ticker") or ""
        series = ticker.split("-", 1)[0]
        if series not in MLB_PROP_SERIES:
            continue

        title = market.get("title") or ""
        player, line = parse_player_and_line(title)
        if not player or line is None:
            continue

        matchup = parse_matchup_from_ticker(ticker)
        if not matchup:
            continue

        out.append({
            "matchup": matchup,
            "event_key": ticker.split("-", 2)[1] if "-" in ticker else "",
            "player": player,
            "stat": MLB_PROP_SERIES[series],
            "line": line,
            "ticker": ticker,
            "yes_ask": market.get("yes_ask_dollars"),
            "no_ask": market.get("no_ask_dollars"),
            "status": market.get("status"),
            "title": title,
            "series": series,
        })

    return out


def build_groups(props: List[Dict[str, Any]], roster: Dict[str, Dict[str, str]]) -> Tuple[Dict[Tuple[str, str, str, float], Dict[str, Any]], List[str], List[str]]:
    groups: Dict[Tuple[str, str, str, float], Dict[str, Any]] = {}
    matchup_order: List[str] = []
    seen_matchups = set()
    unmatched_players = set()

    for prop in props:
        matchup = prop["matchup"]
        if matchup not in seen_matchups:
            seen_matchups.add(matchup)
            matchup_order.append(matchup)

        key = (matchup, prop["player"], prop["stat"], prop["line"])
        if key not in groups:
            norm = normalize_name(prop["player"])
            matched = norm in roster
            if not matched:
                unmatched_players.add(prop["player"])
            groups[key] = {
                "matchup": matchup,
                "player": prop["player"],
                "stat": prop["stat"],
                "line": prop["line"],
                "series": prop["series"],
                "title": prop["title"],
                "status": prop["status"],
                "unmatched": not matched,
                "books": {},
            }

        groups[key]["books"]["kalshi"] = {
            "yes_ask": prop["yes_ask"],
            "no_ask": prop["no_ask"],
        }

    return groups, matchup_order, sorted(unmatched_players)


CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:linear-gradient(180deg,#0d1117 0%,#111827 100%);color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:13px;padding:24px}
h1{font-size:20px;color:#e6edf3;margin-bottom:8px}
.updated{font-size:12px;color:#8b949e;margin-bottom:16px;display:block}
.summary{font-size:13px;color:#8b949e;margin-bottom:16px}
.summary strong{color:#e6edf3}
.tab-bar{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:16px;border-bottom:1px solid #21262d;padding-bottom:8px}
.tab-btn{background:transparent;border:1px solid #30363d;color:#8b949e;padding:6px 14px;border-radius:6px 6px 0 0;cursor:pointer;font-size:12px;font-family:inherit;transition:all .15s}
.tab-btn:hover{color:#c9d1d9;border-color:#58a6ff}
.tab-btn.active{background:#161b22;color:#e6edf3;border-color:#58a6ff;border-bottom-color:#161b22}
.tab-panel{display:none}
.tab-panel.active{display:block}
.panel-toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:0 0 12px 0;font-size:12px;color:#8b949e}
.panel-toolbar label{font-weight:700;color:#e6edf3}
.filter-input,.filter-select{background:#0d1117;border:1px solid #30363d;color:#e6edf3;border-radius:6px;padding:5px 8px;font:inherit}
.filter-input{min-width:180px}
.filter-input:focus,.filter-select:focus{outline:none;border-color:#58a6ff;box-shadow:0 0 0 2px rgba(88,166,255,.15)}
.card{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:14px;margin-bottom:12px}
.card[data-unmatched="true"]{border-left:3px solid #da3633}
.card-header{display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap}
.team-stack{display:flex;gap:6px;align-items:center}
.team-logo{width:22px;height:22px;object-fit:contain}
.team-placeholder{width:22px;height:22px;background:#21262d;border-radius:4px}
.card-title{font-size:15px;font-weight:700;color:#e6edf3}
.card-sub{font-size:12px;color:#8b949e;margin-left:4px}
.meta{font-size:11px;color:#6e7681;margin-top:2px}
table.matrix{width:100%;border-collapse:collapse;margin-top:4px}
table.matrix th{padding:5px 8px;font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:#6e7681;text-align:center;border-bottom:2px solid #21262d;white-space:nowrap}
table.matrix td{padding:5px 8px;border-bottom:1px solid #21262d;font-variant-numeric:tabular-nums;text-align:center;font-size:12px}
table.matrix td:first-child{text-align:left;font-weight:600;white-space:nowrap;color:#8b949e}
table.matrix tr:hover td{background:#1c2128}
.cell-empty{color:#484f58}
.unmatched-section{margin-top:32px;padding:14px;background:#161b22;border:1px solid #da3633;border-radius:8px}
.unmatched-section h3{font-size:14px;color:#f85149;margin-bottom:8px}
.unmatched-section ul{padding-left:20px;color:#c9d1d9;font-size:12px;line-height:1.8}
.no-data{color:#6e7681;font-style:italic;padding:20px 0}
"""


JS = """
(function(){
  function switchTab(tabId){
    document.querySelectorAll('.tab-btn').forEach(function(b){
      b.classList.toggle('active', b.dataset.tab === tabId);
    });
    document.querySelectorAll('.tab-panel').forEach(function(p){
      p.classList.toggle('active', p.id === 'tab-' + tabId);
    });
  }
  document.querySelectorAll('.tab-btn').forEach(function(b){
    b.addEventListener('click', function(){ switchTab(b.dataset.tab); });
  });
  document.querySelectorAll('.filter-input[data-filter-kind="player"]').forEach(function(inp){
    inp.addEventListener('input', applyFilters);
  });
  document.querySelectorAll('.filter-select[data-filter-kind="stat"]').forEach(function(sel){
    sel.addEventListener('change', applyFilters);
  });

  function applyFilters(){
    document.querySelectorAll('.tab-panel').forEach(function(panel){
      var playerInp = panel.querySelector('.filter-input[data-filter-kind="player"]');
      var statSel   = panel.querySelector('.filter-select[data-filter-kind="stat"]');
      var playerVal = playerInp ? playerInp.value.toLowerCase().trim() : '';
      var statVal   = statSel ? statSel.value : '';
      panel.querySelectorAll('.card').forEach(function(card){
        var playerMatch = !playerVal || (card.dataset.player||'').toLowerCase().indexOf(playerVal) !== -1;
        var statMatch = !statVal || (card.dataset.stat||'') === statVal;
        card.style.display = (playerMatch && statMatch) ? '' : 'none';
      });
    });
  }
})();
"""


def tab_id(tab_key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", tab_key.lower())


def render_team_logo(team_name: str, logos: Dict[str, str]) -> str:
    logo = logos.get(team_name, "")
    if logo:
        return '<img class="team-logo" src="{}" alt="{}" onerror="this.style.display=\'none\'">'.format(logo, team_name)
    return '<div class="team-placeholder"></div>'


def render_card(group: Dict[str, Any], logos: Dict[str, str], roster: Dict[str, Dict[str, str]]) -> str:
    books = group.get("books") or {}
    kalshi = books.get("kalshi") or {}
    player = group["player"]
    stat = group["stat"]
    line = group["line"]
    matchup = group["matchup"]
    unmatched = group["unmatched"]
    player_norm = normalize_name(player)
    roster_entry = roster.get(player_norm, {})
    team_name = roster_entry.get("team_name", "")
    logo_html = render_team_logo(team_name, logos) if team_name else '<div class="team-placeholder"></div>'

    lines = []
    lines.append('<div class="card"{} data-player="{}" data-stat="{}">'.format(
        ' data-unmatched="true"' if unmatched else "",
        player.lower(),
        stat,
    ))
    lines.append('<div class="card-header">')
    lines.append('<div class="team-stack">{}</div>'.format(logo_html))
    lines.append(
        '<div>'
        '<div class="card-title">{}</div>'
        '<div class="card-sub">{} &mdash; {} {:.1f}</div>'
        '<div class="meta">Kalshi market: {}{}</div>'
        '</div>'.format(
            player,
            matchup,
            stat,
            line,
            group.get("series", ""),
            " | unmatched" if unmatched else "",
        )
    )
    lines.append('</div>')
    lines.append('<table class="matrix">')
    lines.append('<thead><tr><th></th><th>Kalshi Over</th><th>Kalshi Under</th></tr></thead>')
    lines.append('<tbody>')
    lines.append('<tr><td>Ask</td><td>{}</td><td>{}</td></tr>'.format(
        fmt_money(kalshi.get("yes_ask")),
        fmt_money(kalshi.get("no_ask")),
    ))
    lines.append('<tr><td>Status</td><td colspan="2">{}</td></tr>'.format(group.get("status", "—")))
    lines.append('</tbody></table>')
    lines.append('</div>')
    return "\n".join(lines)


def render_toolbar(stats_present: List[str]) -> str:
    stat_options = "\n".join('<option value="{}">{}</option>'.format(s, s) for s in sorted(stats_present))
    return """<div class="panel-toolbar">
  <label>Player</label>
  <input class="filter-input" data-filter-kind="player" type="search" placeholder="Search player...">
  <label>Stat</label>
  <select class="filter-select" data-filter-kind="stat">
    <option value="">All stats</option>
    {}
  </select>
</div>""".format(stat_options)


def render_html(scan_name: str, saved_ts: Optional[str], groups: Dict[Tuple[str, str, str, float], Dict[str, Any]], matchup_order: List[str], unmatched_players: List[str], logos: Dict[str, str], roster: Dict[str, Dict[str, str]]) -> str:
    all_stats = sorted(set(group["stat"] for group in groups.values()))
    sorted_keys = sorted(groups.keys(), key=lambda k: (k[0], k[1].lower(), k[2], k[3]))
    generated_at = datetime.now(tz=CT).strftime("%Y-%m-%d %I:%M %p CT")
    saved_at = fmt_ts(saved_ts)

    tab_buttons = ['<button class="tab-btn active" data-tab="all">All ({})</button>'.format(len(groups))]
    for matchup in matchup_order:
        count = sum(1 for key in groups if key[0] == matchup)
        tab_buttons.append('<button class="tab-btn" data-tab="{}">{} ({})</button>'.format(tab_id(matchup), matchup, count))

    panels = []
    all_cards = [render_card(groups[key], logos, roster) for key in sorted_keys]
    panels.append(
        '<div class="tab-panel active" id="tab-all">'
        '{}'
        '<div class="card-list">{}</div>'
        '</div>'.format(render_toolbar(all_stats), "".join(all_cards) if all_cards else '<p class="no-data">No props data found.</p>')
    )

    for matchup in matchup_order:
        matchup_keys = [key for key in sorted_keys if key[0] == matchup]
        cards = [render_card(groups[key], logos, roster) for key in matchup_keys]
        panels.append(
            '<div class="tab-panel" id="tab-{}">'
            '{}'
            '<div class="card-list">{}</div>'
            '</div>'.format(
                tab_id(matchup),
                render_toolbar(sorted(set(groups[key]["stat"] for key in matchup_keys))),
                "".join(cards) if cards else '<p class="no-data">No props for this matchup.</p>',
            )
        )

    unmatched_html = ""
    if unmatched_players:
        items = "".join("<li>{}</li>".format(player) for player in unmatched_players)
        unmatched_html = """<div class="unmatched-section">
<h3>Unmatched Players ({})</h3>
<p style="font-size:12px;color:#8b949e;margin-bottom:8px">These player names were not found in the MLB roster file.</p>
<ul>{}</ul>
</div>""".format(len(unmatched_players), items)

    template = Template("""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>MLB Props Dashboard</title>
  <style>
$CSS
  </style>
</head>
<body>
  <h1>MLB Props &mdash; Kalshi</h1>
  <span class="updated">Saved: $SAVED_AT &bull; Generated: $GENERATED_AT &bull; Source: $SCAN_NAME</span>
  <div class="summary"><strong>$GROUP_COUNT</strong> player/stat groups &bull; <strong>$MATCHUP_COUNT</strong> matchups</div>
  <div class="tab-bar">
    $TAB_BUTTONS
  </div>
  $PANEL_ALL
  $PANELS
  $UNMATCHED_HTML
  <script>
$JS
  </script>
</body>
</html>""")
    html = template.substitute(
        CSS=CSS,
        SAVED_AT=saved_at,
        GENERATED_AT=generated_at,
        SCAN_NAME=scan_name,
        GROUP_COUNT=len(groups),
        MATCHUP_COUNT=len(matchup_order),
        TAB_BUTTONS="".join(tab_buttons),
        PANEL_ALL=panels[0] if panels else '<div class="tab-panel active" id="tab-all"><p class="no-data">No props data found.</p></div>',
        PANELS="".join(panels[1:]),
        UNMATCHED_HTML=unmatched_html,
        JS=JS,
    )
    return html


def generate(output_path: Optional[Path] = None) -> Path:
    if output_path is None:
        output_path = ROOT / "data" / "mlb_props.html"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    roster = load_roster()

    scan_rows, scan_name = load_latest_scan()
    props = parse_mlb_kalshi_props(scan_rows)
    if not props:
        saved_ts = None
        groups = {}
        matchup_order = []
        unmatched_players = []
    else:
        saved_ts = None
        for row in reversed(scan_rows):
            if row.get("source") == "KALSHI":
                markets = (row.get("data") or {}).get("markets") or []
                if any((m.get("ticker") or "").startswith("KXMLB") for m in markets):
                    saved_ts = row.get("ts")
                    break
        groups, matchup_order, unmatched_players = build_groups(props, roster)

    logos = load_team_logos()
    html = render_html(scan_name, saved_ts, groups, matchup_order, unmatched_players, logos, roster)

    tmp_fd, tmp_path = tempfile.mkstemp(dir=output_path.parent, prefix=".mlb_props_", suffix=".html.tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            fh.write(html)
        os.replace(tmp_path, output_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return output_path


if __name__ == "__main__":
    t0 = time.time()
    path = generate()
    print("Written: {}".format(path))
    print("Elapsed: {:.2f}s".format(time.time() - t0))
