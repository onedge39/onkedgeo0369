"""
generate_props_html.py — NBA props dashboard using saved multi-source data.

Uses:
- manual OddsAPI props snapshot for book columns
- latest scan JSONL for Underdog and Kalshi
- latest FanDuel cooldown scrape for a separate scraped-FanDuel column

For each exact line, shows:
- raw line pricing / no-vig probabilities by source
- Kalshi edge + Kelly against the available comparison set
- Underdog edge + Kelly against the available comparison set (including Kalshi)
"""

from __future__ import annotations

import csv
import json
import os
import re
import tempfile
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
ODDSAPI_PROPS_SNAPSHOT = ROOT / "data" / "oddsapi_props_latest.json"
FANDUEL_DIR = ROOT / "data" / "fanduel"

CT = ZoneInfo("America/Chicago")

ODDSAPI_BOOK_ORDER = [
    "draftkings",
    "fanduel",
    "betmgm",
    "betonlineag",
    "bovada",
    "pointsbet",
    "betrivers",
    "caesars",
]

BOOK_DISPLAY = {
    "draftkings": "DraftKings",
    "fanduel": "FanDuel (OA)",
    "betmgm": "BetMGM",
    "betonlineag": "BetOnline",
    "bovada": "Bovada",
    "pointsbet": "PointsBet",
    "betrivers": "BetRivers",
    "caesars": "Caesars",
    "fanduel_scrape": "FanDuel Scrape",
    "underdog": "Underdog",
    "kalshi": "Kalshi",
}

COLUMN_ORDER = ODDSAPI_BOOK_ORDER + ["fanduel_scrape", "underdog", "kalshi"]

ODDSAPI_MARKET_TO_STAT = {
    "player_points": "PTS",
    "player_rebounds": "REB",
    "player_assists": "AST",
    "player_threes": "FG3M",
    "player_blocks": "BLK",
    "player_steals": "STL",
    "player_turnovers": "TOV",
}

STAT_DISPLAY = {
    "PTS": "PTS",
    "REB": "REB",
    "AST": "AST",
    "FG3M": "3PT",
    "BLK": "BLK",
    "STL": "STL",
    "TOV": "TOV",
}

SUPPORTED_STATS = set(STAT_DISPLAY.keys())

KALSHI_SERIES_TO_STAT = {
    "KXNBAPTS": "PTS",
    "KXNBAREB": "REB",
    "KXNBAAST": "AST",
    "KXNBA3PT": "FG3M",
    "KXNBASTL": "STL",
    "KXNBABLK": "BLK",
    "KXNBATOV": "TOV",
}

TEAM_NAME_NORM = {
    "Los Angeles Clippers": "LA Clippers",
    "Los Angeles Lakers": "LA Lakers",
}

NBA_FULL_TO_ABBR = {
    "Atlanta Hawks": "ATL",
    "Boston Celtics": "BOS",
    "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI",
    "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL",
    "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW",
    "Houston Rockets": "HOU",
    "Indiana Pacers": "IND",
    "LA Clippers": "LAC",
    "LA Lakers": "LAL",
    "Los Angeles Clippers": "LAC",
    "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA",
    "Washington Wizards": "WAS",
}

NBA_ABBR_TO_FULL = dict((v, k) for k, v in NBA_FULL_TO_ABBR.items())

FD_ALT_LINE_RE = re.compile(r"\b(Over|Under)\s+([\d.]+)\s*$", re.IGNORECASE)
KALSHI_LINE_RE = re.compile(r":\s*.*?(\d+)\+")


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name or "").strip().lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\s+(jr\.?|sr\.?|iii|iv|v)$", "", text, flags=re.IGNORECASE)
    text = text.replace(".", "")
    return re.sub(r"[^a-z0-9]", "", text)


def normalize_stat(raw: str) -> str:
    text = str(raw or "").strip().lower()
    compact = re.sub(r"\s+", "", text)
    mapping = {
        "points": "PTS",
        "point": "PTS",
        "pts": "PTS",
        "rebounds": "REB",
        "rebs": "REB",
        "reb": "REB",
        "assists": "AST",
        "ast": "AST",
        "steals": "STL",
        "stl": "STL",
        "blocks": "BLK",
        "blk": "BLK",
        "turnovers": "TOV",
        "tov": "TOV",
        "threes": "FG3M",
        "3pm": "FG3M",
        "fg3m": "FG3M",
        "3-pointersmade": "FG3M",
        "3-pointers": "FG3M",
        "madethrees": "FG3M",
    }
    return mapping.get(compact, raw.strip().upper())


def american_to_implied(odds: Optional[int]) -> Optional[float]:
    if odds is None:
        return None
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def implied_to_american(prob: Optional[float]) -> Optional[int]:
    if prob is None or prob <= 0 or prob >= 1:
        return None
    if prob >= 0.5:
        return int(round(-(prob / (1.0 - prob)) * 100.0))
    return int(round(((1.0 - prob) / prob) * 100.0))


def devig_pair(over_prob: Optional[float], under_prob: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    if over_prob is None or under_prob is None:
        return over_prob, under_prob
    total = over_prob + under_prob
    if total <= 0:
        return None, None
    return over_prob / total, under_prob / total


def payout_b_from_american(odds: Optional[int]) -> Optional[float]:
    if odds is None:
        return None
    if odds > 0:
        return float(odds) / 100.0
    if odds < 0:
        return 100.0 / abs(float(odds))
    return None


def payout_b_from_price(price_prob: Optional[float]) -> Optional[float]:
    if price_prob is None or price_prob <= 0 or price_prob >= 1:
        return None
    return (1.0 / price_prob) - 1.0


def mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / float(len(values))


def format_pct(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return "{:.1f}%".format(value * 100.0)


def format_edge(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return "{:+.1f}%".format(value * 100.0)


def format_american(odds: Optional[int]) -> str:
    if odds is None:
        return "—"
    if odds > 0:
        return "+{}".format(odds)
    return str(odds)


def format_kalshi_price(prob: Optional[float]) -> str:
    if prob is None:
        return "—"
    return "{}c".format(int(round(prob * 100.0)))


def format_time_label(ts: Optional[str]) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(CT).strftime("%-I:%M%p").lower()
    except Exception:
        return str(ts)


def build_matchup_tab_key(home_team: str, away_team: str) -> str:
    home_n = TEAM_NAME_NORM.get(home_team, home_team)
    away_n = TEAM_NAME_NORM.get(away_team, away_team)
    return "{} @ {}".format(away_n, home_n)


def tab_id(tab_key: str) -> str:
    return tab_key.replace(" ", "").replace("@", "at").replace(".", "").lower()


def load_latest_scan() -> Tuple[List[Dict[str, Any]], str]:
    scans_dir = ROOT / "data" / "scans"
    jsonl_files = sorted(scans_dir.glob("scan_*.jsonl"))
    if not jsonl_files:
        raise FileNotFoundError("No scan JSONL files found in {}".format(scans_dir))
    latest = jsonl_files[-1]
    rows = []
    with open(latest, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows, latest.name


def latest_source_row(scan_rows: List[Dict[str, Any]], source: str) -> Optional[Dict[str, Any]]:
    for row in reversed(scan_rows):
        if row.get("source") == source:
            return row
    return None


def load_oddsapi_snapshot(scan_rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Optional[str], str]:
    if ODDSAPI_PROPS_SNAPSHOT.exists():
        obj = json.loads(ODDSAPI_PROPS_SNAPSHOT.read_text(encoding="utf-8"))
        return obj.get("props", []), obj.get("saved_ts"), obj.get("source_file", ODDSAPI_PROPS_SNAPSHOT.name)

    for row in reversed(scan_rows):
        if row.get("source") != "ODDSAPI":
            continue
        props = (row.get("data") or {}).get("props")
        if isinstance(props, list):
            return props, row.get("ts"), "legacy scan JSONL"
    return [], None, "No saved snapshot"


def load_team_logos() -> Dict[str, str]:
    logos_path = ROOT.parent / "GLedge-dev" / "data" / "team_logos.json"
    try:
        with open(logos_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_roster() -> Dict[str, Dict[str, str]]:
    roster_path = Path("/Users/kylejones/Desktop/KSprop/data/rosters/nba_player_team_map.csv")
    result = {}
    if not roster_path.exists():
        return result
    with open(roster_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            norm = row.get("player_norm", "").strip().upper()
            if norm:
                result[norm] = {
                    "team": row.get("team_abbreviation", "").strip(),
                    "name": row.get("player_name", "").strip(),
                }
    return result


def build_abbrev_tab_lookup(oddsapi_props: List[Dict[str, Any]]) -> Dict[str, str]:
    lookup = {}
    for prop in oddsapi_props:
        home_team = prop.get("home_team") or ""
        away_team = prop.get("away_team") or ""
        home_abbrev = NBA_FULL_TO_ABBR.get(TEAM_NAME_NORM.get(home_team, home_team))
        away_abbrev = NBA_FULL_TO_ABBR.get(TEAM_NAME_NORM.get(away_team, away_team))
        if not home_abbrev or not away_abbrev:
            continue
        lookup["{} @ {}".format(away_abbrev, home_abbrev)] = build_matchup_tab_key(home_team, away_team)
    return lookup


def ensure_group(
    groups: Dict[Tuple[str, str, str, float], Dict[str, Any]],
    matchup_order: List[str],
    seen_tabs: set,
    roster: Dict[str, Dict[str, str]],
    tab_key: str,
    player: str,
    stat_type: str,
    line: float,
) -> Dict[str, Any]:
    gkey = (tab_key, normalize_name(player), stat_type, round(line, 1))
    if tab_key not in seen_tabs:
        matchup_order.append(tab_key)
        seen_tabs.add(tab_key)
    if gkey not in groups:
        player_norm = normalize_name(player)
        roster_hit = player_norm.upper() in roster
        groups[gkey] = {
            "tab_key": tab_key,
            "player": player,
            "stat": stat_type,
            "line": round(line, 1),
            "sources": {},
            "unmatched": not roster_hit,
        }
    return groups[gkey]


def finalize_book_entry(entry: Dict[str, Any]) -> None:
    entry["price_over"] = american_to_implied(entry.get("over_american"))
    entry["price_under"] = american_to_implied(entry.get("under_american"))
    fair_over, fair_under = devig_pair(entry["price_over"], entry["price_under"])
    entry["fair_over"] = fair_over
    entry["fair_under"] = fair_under


def ingest_oddsapi_props(
    groups: Dict[Tuple[str, str, str, float], Dict[str, Any]],
    matchup_order: List[str],
    seen_tabs: set,
    roster: Dict[str, Dict[str, str]],
    oddsapi_props: List[Dict[str, Any]],
) -> None:
    for prop in oddsapi_props:
        stat_type = ODDSAPI_MARKET_TO_STAT.get(prop.get("market", ""))
        if stat_type not in SUPPORTED_STATS:
            continue
        player = prop.get("player") or ""
        line = prop.get("point")
        bookmaker = (prop.get("bookmaker") or "").lower()
        if not player or line is None or bookmaker not in ODDSAPI_BOOK_ORDER:
            continue
        tab_key = build_matchup_tab_key(prop.get("home_team", ""), prop.get("away_team", ""))
        group = ensure_group(groups, matchup_order, seen_tabs, roster, tab_key, player, stat_type, float(line))
        entry = group["sources"].setdefault(bookmaker, {"source_type": "sportsbook"})
        name = (prop.get("name") or "").lower()
        price = prop.get("price")
        if name == "over":
            entry["over_american"] = int(price) if price is not None else None
        elif name == "under":
            entry["under_american"] = int(price) if price is not None else None
        finalize_book_entry(entry)


def convert_abbrev_matchup(matchup: str, abbrev_lookup: Dict[str, str]) -> str:
    return abbrev_lookup.get(matchup, matchup)


def ingest_underdog_props(
    groups: Dict[Tuple[str, str, str, float], Dict[str, Any]],
    matchup_order: List[str],
    seen_tabs: set,
    roster: Dict[str, Dict[str, str]],
    scan_rows: List[Dict[str, Any]],
    abbrev_lookup: Dict[str, str],
) -> Optional[str]:
    row = latest_source_row(scan_rows, "UNDERDOG")
    if not row:
        return None
    for prop in (row.get("data") or {}).get("props", []):
        stat_type = normalize_stat(prop.get("stat_type", ""))
        if stat_type not in SUPPORTED_STATS:
            continue
        line = prop.get("line")
        player = prop.get("player") or ""
        if not player or line is None:
            continue
        tab_key = convert_abbrev_matchup(prop.get("matchup", ""), abbrev_lookup)
        group = ensure_group(groups, matchup_order, seen_tabs, roster, tab_key, player, stat_type, float(line))
        entry = {
            "source_type": "sportsbook",
            "over_american": prop.get("over_american"),
            "under_american": prop.get("under_american"),
            "ts": row.get("ts"),
        }
        finalize_book_entry(entry)
        group["sources"]["underdog"] = entry
    return row.get("ts")


def parse_kalshi_matchup(ticker: str) -> Optional[str]:
    parts = str(ticker or "").split("-")
    if len(parts) < 2 or len(parts[1]) < 13:
        return None
    matchup = parts[1][7:]
    if len(matchup) != 6:
        return None
    return "{} @ {}".format(matchup[:3], matchup[3:])


def parse_kalshi_player(title: str) -> Optional[str]:
    parts = str(title or "").split(":", 1)
    if len(parts) >= 2 and parts[0].strip():
        return parts[0].strip()
    return None


def parse_kalshi_line(title: str) -> Optional[float]:
    match = KALSHI_LINE_RE.search(str(title or ""))
    if not match:
        return None
    return float(match.group(1)) - 0.5


def safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def ingest_kalshi_props(
    groups: Dict[Tuple[str, str, str, float], Dict[str, Any]],
    matchup_order: List[str],
    seen_tabs: set,
    roster: Dict[str, Dict[str, str]],
    scan_rows: List[Dict[str, Any]],
    abbrev_lookup: Dict[str, str],
) -> Optional[str]:
    row = latest_source_row(scan_rows, "KALSHI")
    if not row:
        return None
    for market in (row.get("data") or {}).get("markets", []):
        ticker = str(market.get("ticker") or "")
        series = ticker.split("-", 1)[0]
        stat_type = KALSHI_SERIES_TO_STAT.get(series)
        if stat_type not in SUPPORTED_STATS:
            continue
        player = parse_kalshi_player(market.get("yes_sub_title") or market.get("title") or "")
        line = parse_kalshi_line(market.get("yes_sub_title") or market.get("title") or "")
        matchup = parse_kalshi_matchup(ticker)
        if not player or line is None or not matchup:
            continue
        tab_key = convert_abbrev_matchup(matchup, abbrev_lookup)
        group = ensure_group(groups, matchup_order, seen_tabs, roster, tab_key, player, stat_type, line)
        price_over = safe_float(market.get("yes_ask_dollars"))
        price_under = safe_float(market.get("no_ask_dollars"))
        fair_over, fair_under = devig_pair(price_over, price_under)
        group["sources"]["kalshi"] = {
            "source_type": "kalshi",
            "price_over": price_over,
            "price_under": price_under,
            "fair_over": fair_over,
            "fair_under": fair_under,
            "ts": row.get("ts"),
        }
    return row.get("ts")


def fd_parse_stat(market_name: str) -> str:
    raw = market_name.split(" - ", 1)[1].strip() if " - " in market_name else market_name
    raw = re.sub(r"^Alt\s+", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"^\d+(?:st|nd|rd|th)\s+(?:Qtr|Quarter|Half)\s+", "", raw, flags=re.IGNORECASE)
    return normalize_stat(raw)


def fd_infer_period(market_name: str) -> str:
    lowered = str(market_name or "").lower()
    tokens = [
        "1st qtr", "1st quarter", "2nd qtr", "2nd quarter",
        "3rd qtr", "3rd quarter", "4th qtr", "4th quarter",
        "1st half", "half",
    ]
    for token in tokens:
        if token in lowered:
            return "NON_GAME"
    return "GAME"


def fd_compact_matchup(full_matchup: str) -> str:
    if " @ " not in full_matchup:
        return full_matchup
    away, home = [part.strip() for part in full_matchup.split(" @ ", 1)]
    away_abbrev = NBA_FULL_TO_ABBR.get(TEAM_NAME_NORM.get(away, away), away)
    home_abbrev = NBA_FULL_TO_ABBR.get(TEAM_NAME_NORM.get(home, home), home)
    return "{} @ {}".format(away_abbrev, home_abbrev)


def fd_parse_market_rows(market: Dict[str, Any], matchup: str) -> List[Dict[str, Any]]:
    market_name = market.get("marketName") or ""
    if " - " not in market_name or fd_infer_period(market_name) != "GAME":
        return []

    player = market_name.split(" - ", 1)[0].strip()
    stat_type = fd_parse_stat(market_name)
    if stat_type not in SUPPORTED_STATS:
        return []

    runners = market.get("runners") or []
    rows = []
    if len(runners) == 2:
        for runner in runners:
            runner_name = runner.get("runnerName") or ""
            lowered = runner_name.lower()
            if "over" in lowered:
                side = "over"
            elif "under" in lowered:
                side = "under"
            else:
                continue
            handicap = runner.get("handicap")
            if handicap is None:
                continue
            american = ((runner.get("winRunnerOdds") or {}).get("americanDisplayOdds") or {}).get("americanOdds")
            rows.append({
                "player": player,
                "stat_type": stat_type,
                "side": side,
                "line": float(handicap),
                "american": int(american) if american is not None else None,
                "matchup": matchup,
                "line_type": "MAIN",
            })
    else:
        for runner in runners:
            match = FD_ALT_LINE_RE.search(runner.get("runnerName") or "")
            if not match:
                continue
            american = ((runner.get("winRunnerOdds") or {}).get("americanDisplayOdds") or {}).get("americanOdds")
            rows.append({
                "player": player,
                "stat_type": stat_type,
                "side": match.group(1).lower(),
                "line": float(match.group(2)),
                "american": int(american) if american is not None else None,
                "matchup": matchup,
                "line_type": "ALT",
            })
    return rows


def load_latest_fanduel_records() -> Dict[str, Dict[str, Any]]:
    files = sorted(FANDUEL_DIR.glob("fd_props_*.jsonl"))
    if not files:
        return {}
    latest_by_event = {}
    with open(files[-1], encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            latest_by_event[row.get("event_name")] = row
    return latest_by_event


def ingest_fanduel_scrape(
    groups: Dict[Tuple[str, str, str, float], Dict[str, Any]],
    matchup_order: List[str],
    seen_tabs: set,
    roster: Dict[str, Dict[str, str]],
    abbrev_lookup: Dict[str, str],
) -> Optional[str]:
    records = load_latest_fanduel_records()
    latest_ts = None
    for event_name, record in records.items():
        compact_matchup = fd_compact_matchup(event_name)
        tab_key = convert_abbrev_matchup(compact_matchup, abbrev_lookup)
        parsed_rows = []
        for tab in (record.get("tabs") or {}).values():
            if not isinstance(tab, dict) or "error" in tab:
                continue
            markets = ((tab.get("attachments") or {}).get("markets") or {}).values()
            for market in markets:
                parsed_rows.extend(fd_parse_market_rows(market, tab_key))

        merged = {}
        for row in parsed_rows:
            key = (normalize_name(row["player"]), row["stat_type"], round(row["line"], 1))
            entry = merged.setdefault(key, {
                "player": row["player"],
                "stat_type": row["stat_type"],
                "line": round(row["line"], 1),
                "over_american": None,
                "under_american": None,
                "line_type": row["line_type"],
            })
            if row["line_type"] == "MAIN":
                entry["line_type"] = "MAIN"
            if row["side"] == "over":
                entry["over_american"] = row["american"]
            else:
                entry["under_american"] = row["american"]

        for item in merged.values():
            group = ensure_group(groups, matchup_order, seen_tabs, roster, tab_key, item["player"], item["stat_type"], item["line"])
            entry = {
                "source_type": "sportsbook",
                "over_american": item["over_american"],
                "under_american": item["under_american"],
                "ts": record.get("ts"),
            }
            finalize_book_entry(entry)
            group["sources"]["fanduel_scrape"] = entry

        latest_ts = record.get("ts") or latest_ts
    return latest_ts


def compute_target_summary(group: Dict[str, Any], target_key: str) -> Dict[str, Any]:
    target = group["sources"].get(target_key)
    result = {
        "target_key": target_key,
        "direction": None,
        "edge": None,
        "kelly": None,
        "fair_prob": None,
        "target_price": None,
        "compare_count": 0,
    }
    if not target:
        return result

    compare_entries = []
    for key, entry in group["sources"].items():
        if key == target_key:
            continue
        compare_entries.append(entry)

    over_fairs = [e.get("fair_over") for e in compare_entries if e.get("fair_over") is not None]
    under_fairs = [e.get("fair_under") for e in compare_entries if e.get("fair_under") is not None]

    target_over = target.get("price_over")
    target_under = target.get("price_under")
    fair_over = mean(over_fairs)
    fair_under = mean(under_fairs)
    over_edge = fair_over - target_over if fair_over is not None and target_over is not None else None
    under_edge = fair_under - target_under if fair_under is not None and target_under is not None else None

    if over_edge is not None and over_edge > 0 and (under_edge is None or over_edge >= under_edge):
        result["direction"] = "OVER"
        result["edge"] = over_edge
        result["fair_prob"] = fair_over
        result["target_price"] = target_over
        result["compare_count"] = len(over_fairs)
        if target_key == "kalshi":
            b = payout_b_from_price(target_over)
        else:
            b = payout_b_from_american(target.get("over_american"))
        if b is not None and fair_over is not None and b > 0:
            q = 1.0 - fair_over
            f = (b * fair_over - q) / b
            if f > 0:
                result["kelly"] = f / 3.0
    elif under_edge is not None and under_edge > 0:
        result["direction"] = "UNDER"
        result["edge"] = under_edge
        result["fair_prob"] = fair_under
        result["target_price"] = target_under
        result["compare_count"] = len(under_fairs)
        if target_key == "kalshi":
            b = payout_b_from_price(target_under)
        else:
            b = payout_b_from_american(target.get("under_american"))
        if b is not None and fair_under is not None and b > 0:
            q = 1.0 - fair_under
            f = (b * fair_under - q) / b
            if f > 0:
                result["kelly"] = f / 3.0

    return result


def build_groups(
    scan_rows: List[Dict[str, Any]],
    oddsapi_props: List[Dict[str, Any]],
    roster: Dict[str, Dict[str, str]],
) -> Tuple[Dict[Tuple[str, str, str, float], Dict[str, Any]], List[str], set]:
    groups = {}
    matchup_order = []
    seen_tabs = set()

    ingest_oddsapi_props(groups, matchup_order, seen_tabs, roster, oddsapi_props)
    abbrev_lookup = build_abbrev_tab_lookup(oddsapi_props)
    ingest_underdog_props(groups, matchup_order, seen_tabs, roster, scan_rows, abbrev_lookup)
    ingest_kalshi_props(groups, matchup_order, seen_tabs, roster, scan_rows, abbrev_lookup)
    ingest_fanduel_scrape(groups, matchup_order, seen_tabs, roster, abbrev_lookup)

    unmatched = set()
    for group in groups.values():
        if group.get("unmatched"):
            unmatched.add(group["player"])
        group["kalshi_summary"] = compute_target_summary(group, "kalshi")
        group["underdog_summary"] = compute_target_summary(group, "underdog")
    return groups, matchup_order, unmatched


CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:13px;padding:24px}
h1{font-size:20px;color:#e6edf3;margin-bottom:8px}
.updated{font-size:12px;color:#6e7681;margin-bottom:16px;display:block}
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
.player-group{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:14px;margin-bottom:12px}
.player-group[data-unmatched="true"]{border-left:3px solid #da3633}
.group-header{display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap}
.group-header-logo{width:28px;height:28px;object-fit:contain;flex-shrink:0}
.group-header-logo-placeholder{width:28px;height:28px;background:#21262d;border-radius:4px;flex-shrink:0}
.group-header-text{font-size:15px;font-weight:700;color:#e6edf3}
.group-header-sub{font-size:12px;color:#8b949e;margin-left:4px}
.summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin:0 0 12px 0}
.metric-card{background:#0f1520;border:1px solid #263041;border-radius:8px;padding:10px}
.metric-title{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#8b949e;margin-bottom:6px}
.metric-main{font-size:14px;font-weight:700;color:#e6edf3}
.metric-sub{font-size:12px;color:#8b949e;margin-top:4px}
.metric-good{color:#3fb950}
table.matrix{width:100%;border-collapse:collapse;margin-top:4px}
table.matrix th{padding:5px 8px;font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:#6e7681;text-align:center;border-bottom:2px solid #21262d;white-space:nowrap;vertical-align:bottom}
table.matrix td{padding:5px 8px;border-bottom:1px solid #21262d;font-variant-numeric:tabular-nums;text-align:center;font-size:12px}
table.matrix td:first-child{text-align:left;font-weight:600;white-space:nowrap;color:#8b949e}
table.matrix tr:hover td{background:#1c2128}
.cell-empty{color:#484f58}
.source-ts{display:block;font-size:9px;color:#58a6ff;font-weight:600;margin-top:2px}
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
      panel.querySelectorAll('.player-group').forEach(function(grp){
        var playerMatch = !playerVal || (grp.dataset.player||'').toLowerCase().indexOf(playerVal) !== -1;
        var statMatch = !statVal || (grp.dataset.stat||'') === statVal;
        grp.style.display = (playerMatch && statMatch) ? '' : 'none';
      });
    });
  }
})();
"""


def render_target_card(label: str, summary: Dict[str, Any]) -> str:
    if summary.get("direction") is None:
        return (
            '<div class="metric-card">'
            '<div class="metric-title">{}</div>'
            '<div class="metric-main">No edge</div>'
            '<div class="metric-sub">Need target line plus comparison prices</div>'
            '</div>'.format(label)
        )

    edge_cls = " metric-good" if (summary.get("edge") or 0) > 0 else ""
    return (
        '<div class="metric-card">'
        '<div class="metric-title">{}</div>'
        '<div class="metric-main{}">{} {}</div>'
        '<div class="metric-sub">Fair {} vs target {} · {} comps · Kelly {}</div>'
        '</div>'.format(
            label,
            edge_cls,
            summary.get("direction"),
            format_edge(summary.get("edge")),
            format_pct(summary.get("fair_prob")),
            format_pct(summary.get("target_price")),
            summary.get("compare_count", 0),
            format_pct(summary.get("kelly")),
        )
    )


def render_group(group: Dict[str, Any], logos: Dict[str, str]) -> str:
    player = group["player"]
    stat = STAT_DISPLAY.get(group["stat"], group["stat"])
    point = group["line"]
    tab_key = group["tab_key"]
    unmatched = group["unmatched"]

    home_team = ""
    away_team = ""
    if " @ " in tab_key:
        away_team, home_team = [part.strip() for part in tab_key.split(" @ ", 1)]
    home_logo = logos.get(home_team, "")
    logo_html = (
        '<img class="group-header-logo" src="{}" alt="{}" onerror="this.style.display=\'none\'">'.format(home_logo, home_team)
        if home_logo else
        '<div class="group-header-logo-placeholder"></div>'
    )

    lines = []
    unmatched_attr = ' data-unmatched="true"' if unmatched else ''
    lines.append('<div class="player-group"{} data-player="{}" data-stat="{}">'.format(
        unmatched_attr, player.lower(), stat
    ))
    lines.append('<div class="group-header">')
    lines.append(logo_html)
    lines.append(
        '<span class="group-header-text">{}</span><span class="group-header-sub">&mdash; {} {:g}</span>'.format(
            player, stat, point
        )
    )
    if unmatched:
        lines.append('<span style="color:#da3633;font-size:11px;font-weight:600">unmatched</span>')
    lines.append('</div>')

    lines.append('<div class="summary-grid">')
    lines.append(render_target_card("Kalshi Edge vs Others", group["kalshi_summary"]))
    lines.append(render_target_card("Underdog Edge vs Others", group["underdog_summary"]))
    lines.append('</div>')

    lines.append('<table class="matrix">')
    lines.append('<thead><tr><th></th>')
    for key in COLUMN_ORDER:
        entry = group["sources"].get(key)
        title = BOOK_DISPLAY[key]
        if key == "fanduel_scrape" and entry and entry.get("ts"):
            title += '<span class="source-ts">{}</span>'.format(format_time_label(entry.get("ts")))
        lines.append('<th colspan="2">{}</th>'.format(title))
    lines.append('</tr><tr><th></th>')
    for _ in COLUMN_ORDER:
        lines.append('<th>Price</th><th>No-Vig%</th>')
    lines.append('</tr></thead><tbody>')

    row_specs = [
        ("Over Price", "over_price"),
        ("Over No-Vig%", "over_fair"),
        ("Under Price", "under_price"),
        ("Under No-Vig%", "under_fair"),
    ]
    for label, field in row_specs:
        lines.append('<tr><td>{}</td>'.format(label))
        for key in COLUMN_ORDER:
            entry = group["sources"].get(key)
            if not entry:
                lines.append('<td class="cell-empty">—</td><td class="cell-empty">—</td>')
                continue
            if field == "over_price":
                if key == "kalshi":
                    price_cell = format_kalshi_price(entry.get("price_over"))
                else:
                    price_cell = format_american(entry.get("over_american"))
                fair_cell = format_pct(entry.get("fair_over"))
            elif field == "over_fair":
                price_cell = "—"
                fair_cell = format_pct(entry.get("fair_over"))
            elif field == "under_price":
                if key == "kalshi":
                    price_cell = format_kalshi_price(entry.get("price_under"))
                else:
                    price_cell = format_american(entry.get("under_american"))
                fair_cell = format_pct(entry.get("fair_under"))
            else:
                price_cell = "—"
                fair_cell = format_pct(entry.get("fair_under"))
            lines.append('<td>{}</td><td>{}</td>'.format(price_cell, fair_cell))
        lines.append('</tr>')

    lines.append('</tbody></table>')
    lines.append('</div>')
    return "\n".join(lines)


def render_toolbar(stats_present: List[str]) -> str:
    stat_options = "\n".join(
        '<option value="{0}">{0}</option>'.format(STAT_DISPLAY.get(s, s))
        for s in sorted(stats_present)
    )
    return """<div class="panel-toolbar">
  <label>Player</label>
  <input class="filter-input" data-filter-kind="player" type="search" placeholder="Search player…">
  <label>Stat</label>
  <select class="filter-select" data-filter-kind="stat">
    <option value="">All stats</option>
    {}
  </select>
</div>""".format(stat_options)


def render_html(
    source_label: str,
    saved_ts: Optional[str],
    groups: Dict[Tuple[str, str, str, float], Dict[str, Any]],
    matchup_order: List[str],
    unmatched_players: set,
    logos: Dict[str, str],
) -> str:
    ts_display = format_time_label(saved_ts)
    generated_at = datetime.now(tz=CT).strftime("%-I:%M%p").lower()
    total_groups = len(groups)
    total_matchups = len(matchup_order)

    sorted_groups = sorted(
        groups.values(),
        key=lambda g: (g["player"].lower(), g["stat"], g["line"]),
    )
    all_stats = sorted(set(g["stat"] for g in groups.values()))

    tab_btns = ['<button class="tab-btn active" data-tab="all">All ({})</button>'.format(total_groups)]
    for tab_key in matchup_order:
        count = sum(1 for g in sorted_groups if g["tab_key"] == tab_key)
        tab_btns.append('<button class="tab-btn" data-tab="{}">{} ({})</button>'.format(tab_id(tab_key), tab_key, count))

    panels = []
    all_html = "".join(render_group(g, logos) for g in sorted_groups)
    panels.append(
        '<div class="tab-panel active" id="tab-all">{}<div class="group-list">{}</div></div>'.format(
            render_toolbar(all_stats),
            all_html if all_html else '<p class="no-data">No props data found.</p>',
        )
    )

    for tab_key in matchup_order:
        tab_groups = [g for g in sorted_groups if g["tab_key"] == tab_key]
        tab_stats = sorted(set(g["stat"] for g in tab_groups))
        tab_html = "".join(render_group(g, logos) for g in tab_groups)
        panels.append(
            '<div class="tab-panel" id="tab-{}">{}<div class="group-list">{}</div></div>'.format(
                tab_id(tab_key),
                render_toolbar(tab_stats),
                tab_html if tab_html else '<p class="no-data">No props for this matchup.</p>',
            )
        )

    unmatched_html = ""
    if unmatched_players:
        items = "".join("<li>{}</li>".format(p) for p in sorted(unmatched_players))
        unmatched_html = (
            '<div class="unmatched-section"><h3>Unmatched Players ({})</h3>'
            '<p style="font-size:12px;color:#8b949e;margin-bottom:8px">These player names were not found in the roster file.</p>'
            '<ul>{}</ul></div>'.format(len(unmatched_players), items)
        )

    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>NBA Props Dashboard</title>
  <style>
{}
  </style>
</head>
<body>
  <h1>NBA Props &mdash; Multi-Source</h1>
  <span class="updated">OddsAPI props saved: {} CT &bull; Generated: {} CT &bull; Source file: {}</span>
  <div class="summary"><strong>{}</strong> player/stat groups &bull; <strong>{}</strong> matchups &bull; Kalshi and Underdog both scored against exact-line comparison sets</div>
  <div class="tab-bar">{}</div>
  {}
  {}
  <script>
{}
  </script>
</body>
</html>""".format(
        CSS,
        ts_display,
        generated_at,
        source_label,
        total_groups,
        total_matchups,
        "".join(tab_btns),
        "".join(panels),
        unmatched_html,
        JS,
    )


def generate(output_path: Optional[Path] = None) -> Path:
    if output_path is None:
        output_path = ROOT / "data" / "props.html"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    scan_rows, _scan_filename = load_latest_scan()
    oddsapi_props, saved_ts, source_label = load_oddsapi_snapshot(scan_rows)
    logos = load_team_logos()
    roster = load_roster()
    groups, matchup_order, unmatched_players = build_groups(scan_rows, oddsapi_props, roster)
    html = render_html(source_label, saved_ts, groups, matchup_order, unmatched_players, logos)

    tmp_fd, tmp_path = tempfile.mkstemp(dir=output_path.parent, suffix=".html.tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(html)
        os.replace(tmp_path, output_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return output_path


if __name__ == "__main__":
    t0 = time.perf_counter()
    path = generate()
    elapsed = time.perf_counter() - t0
    print("Written: {}  ({:.2f}s)".format(path, elapsed))
