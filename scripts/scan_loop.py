#!/usr/bin/env python3
"""
Concurrent scan loop — core data collection for NBA prop scanning.

Runs every 5 minutes (configurable). Each cycle:
  1. ESPN fetch to determine game states
  2. All sources fetch concurrently (OddsAPI, Underdog, Polymarket, Kalshi)
  3. Log all data to append-only JSONL
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# ---------------------------------------------------------------------------
# Source imports
# ---------------------------------------------------------------------------

from test_espn import fetch_scoreboard

from test_underdog import fetch_raw as ud_fetch_raw, parse_props as ud_parse_props

from test_polymarket import fetch_events as poly_fetch_events, parse_props as poly_parse_props

from test_oddsapi import load_api_key as oa_load_api_key, fetch_game_lines as oa_fetch_game_lines

from kalshi_demo import send_demo_request, DEFAULT_CREDENTIALS_PATH

from test_fanduel_bulk import discover_events as fd_discover_events, fetch_event_tabs as fd_fetch_event_tabs

from generate_nba_gl_html import generate as gen_nba_gl
from generate_mlb_gl_html import generate as gen_mlb_gl
from generate_props_html import generate as gen_props
from generate_mlb_props_html import generate as gen_mlb_props
from publish_site import publish as publish_site

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOCAL_TZ = ZoneInfo("America/Chicago")
CYCLE_INTERVAL = 300  # 5 minutes

KALSHI_NBA_PROP_SERIES = [
    "KXNBAPTS", "KXNBAREB", "KXNBAAST", "KXNBA3PT",
    "KXNBASTL", "KXNBABLK", "KXNBATOV",
]
KALSHI_NBA_GL_SERIES = ["KXNBAGAME"]
KALSHI_MLB_GL_SERIES = ["KXMLBGAME", "KXMLBSPREAD", "KXMLBTOTAL"]
KALSHI_MLB_PROP_SERIES = ["KXMLBHIT", "KXMLBHR", "KXMLBHRR", "KXMLBKS", "KXMLBTB"]
KALSHI_ALL_SERIES = (
    KALSHI_NBA_PROP_SERIES
    + KALSHI_NBA_GL_SERIES
    + KALSHI_MLB_GL_SERIES
    + KALSHI_MLB_PROP_SERIES
)

NBA_ABBREV_TO_FDNAME = {
    "ATL": "Atlanta", "BOS": "Boston", "BKN": "Brooklyn", "CHA": "Charlotte",
    "CHI": "Chicago", "CLE": "Cleveland", "DAL": "Dallas", "DEN": "Denver",
    "DET": "Detroit", "GS": "Golden State", "HOU": "Houston", "IND": "Indiana",
    "LAC": "Clippers", "LAL": "Lakers", "MEM": "Memphis", "MIA": "Miami",
    "MIL": "Milwaukee", "MIN": "Minnesota", "NO": "New Orleans", "NY": "New York Knicks",
    "OKC": "Oklahoma", "ORL": "Orlando", "PHI": "76ers", "PHX": "Phoenix",
    "POR": "Portland", "SAC": "Sacramento", "SA": "San Antonio", "TOR": "Toronto",
    "UTA": "Utah", "WSH": "Washington",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_ct() -> datetime:
    return datetime.now(LOCAL_TZ)


def _ts_iso() -> str:
    return _now_ct().isoformat()


def _log_path() -> Path:
    today = _now_ct().strftime("%Y-%m-%d")
    p = ROOT / "data" / "scans" / f"scan_{today}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _write_jsonl(path: Path, record: Dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")


def _today_ticker_prefix() -> str:
    """Kalshi date prefix for today, e.g. '26MAR28'."""
    return datetime.now(LOCAL_TZ).strftime("%y%b%d").upper()


def _fd_log_path() -> Path:
    today = _now_ct().strftime("%Y-%m-%d")
    p = ROOT / "data" / "fanduel" / f"fd_props_{today}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _match_fd_events(active_matchups: List[str], fd_events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter FanDuel events to only those matching ESPN active matchups."""
    match_pairs = []
    for matchup in active_matchups:
        parts = matchup.split(" @ ")
        if len(parts) != 2:
            continue
        away_name = NBA_ABBREV_TO_FDNAME.get(parts[0].strip())
        home_name = NBA_ABBREV_TO_FDNAME.get(parts[1].strip())
        if away_name and home_name:
            match_pairs.append((away_name.lower(), home_name.lower()))

    matched = []
    for ev in fd_events:
        ev_name_lower = ev.get("name", "").lower()
        for away_name, home_name in match_pairs:
            if away_name in ev_name_lower and home_name in ev_name_lower:
                matched.append(ev)
                break
    return matched


def run_fanduel_cooldown(active_matchups: List[str], cycle_num: int) -> float:
    """Run FanDuel prop scrape as cooldown between scan cycles. Returns elapsed seconds."""
    if not active_matchups:
        print("FanDuel cooldown: no active matchups, skipping")
        return 0.0

    t0 = time.time()
    ts_iso = _ts_iso()
    log_path = _fd_log_path()

    print(f"\n--- FanDuel cooldown scrape ---")

    # Discover FanDuel events
    try:
        fd_events = fd_discover_events()
    except Exception as e:
        print(f"FanDuel discovery failed: {e}")
        return time.time() - t0

    # Match to active ESPN matchups
    matched = _match_fd_events(active_matchups, fd_events)
    print(f"FanDuel: {len(fd_events)} events discovered, {len(matched)} match active matchups")

    if not matched:
        print("FanDuel: no matching events, skipping tab fetches")
        return time.time() - t0

    # Fetch tabs for each matched event
    for ev in matched:
        eid = ev["event_id"]
        ev_name = ev["name"]
        print(f"  Fetching {ev_name} ({eid})...")
        try:
            tabs = fd_fetch_event_tabs(eid)
            # Count markets across all tabs
            total_markets = 0
            for slug, tab_data in tabs.items():
                if isinstance(tab_data, dict) and "error" not in tab_data:
                    markets = (tab_data.get("attachments") or {}).get("markets") or {}
                    total_markets += len(markets)
            print(f"    {total_markets} markets across {len(tabs)} tabs")
        except Exception as e:
            tabs = {"error": str(e)}
            print(f"    ERROR: {e}")

        record = {
            "ts": ts_iso,
            "cycle": cycle_num,
            "source": "FANDUEL",
            "event_id": eid,
            "event_name": ev_name,
            "tabs": tabs,
        }
        _write_jsonl(log_path, record)

    elapsed = time.time() - t0
    print(f"FanDuel cooldown: {len(matched)} events scraped in {elapsed:.1f}s → {log_path.name}")
    return elapsed


# ---------------------------------------------------------------------------
# Source fetchers — each returns (data_dict, elapsed_s, error_or_None)
# ---------------------------------------------------------------------------

def fetch_espn_source() -> tuple:
    t0 = time.time()
    try:
        raw_json, games = fetch_scoreboard()
        elapsed = time.time() - t0
        return {"games": games}, elapsed, None, raw_json, games
    except Exception as e:
        return None, time.time() - t0, str(e), None, []


def fetch_oddsapi_source() -> tuple:
    t0 = time.time()
    try:
        api_key = oa_load_api_key()
        sports = []
        for sport_name, sport_key in [
            ("nba", "basketball_nba"),
            ("mlb", "baseball_mlb"),
        ]:
            games = oa_fetch_game_lines(api_key, sport=sport_key)
            sports.append({
                "sport": sport_name,
                "games": games,
            })
        elapsed = time.time() - t0
        return {
            "sports": sports,
        }, elapsed, None
    except Exception as e:
        return None, time.time() - t0, str(e)


def fetch_underdog_source() -> tuple:
    t0 = time.time()
    try:
        raw = ud_fetch_raw()
        props = ud_parse_props(raw, include_live=True)
        games_count = len(raw.get("games", []))
        lines_count = len(raw.get("over_under_lines", []))
        elapsed = time.time() - t0
        return {
            "raw_meta": {"games_count": games_count, "lines_count": lines_count},
            "props": props,
        }, elapsed, None
    except Exception as e:
        return None, time.time() - t0, str(e)


def fetch_polymarket_source() -> tuple:
    t0 = time.time()
    try:
        events = poly_fetch_events()
        props = poly_parse_props(events)
        elapsed = time.time() - t0
        return {
            "events_count": len(events),
            "props": props,
        }, elapsed, None
    except Exception as e:
        return None, time.time() - t0, str(e)


def fetch_kalshi_source() -> tuple:
    t0 = time.time()
    try:
        today_prefix = _today_ticker_prefix()
        all_markets = []
        series_counts: Dict[str, int] = {}

        for ticker in KALSHI_ALL_SERIES:
            cursor = None
            ticker_markets = []
            while True:
                params = {
                    "series_ticker": ticker,
                    "status": "open",
                    "limit": "1000",
                }
                if cursor:
                    params["cursor"] = cursor
                resp = send_demo_request("GET", "/markets", params=params)
                resp.raise_for_status()
                data = resp.json()
                markets = data.get("markets", [])
                # Filter to today
                for m in markets:
                    m_ticker = m.get("ticker", "")
                    if today_prefix in m_ticker:
                        ticker_markets.append(m)
                # Pagination
                new_cursor = data.get("cursor")
                if not new_cursor or new_cursor == cursor or not markets:
                    break
                cursor = new_cursor

            series_counts[ticker] = len(ticker_markets)
            all_markets.extend(ticker_markets)

        elapsed = time.time() - t0
        return {
            "markets": all_markets,
            "series_counts": series_counts,
        }, elapsed, None
    except Exception as e:
        return None, time.time() - t0, str(e)


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------

def run_cycle(cycle_num: int, skip_kalshi: bool, skip_oddsapi: bool = False) -> Tuple[float, List[str]]:
    cycle_start = time.time()
    ts = _now_ct().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n=== Cycle {cycle_num} @ {ts} CT ===")

    log_path = _log_path()
    ts_iso = _ts_iso()

    # --- Phase 1: ESPN (blocking) ---
    espn_data, espn_elapsed, espn_err, espn_raw, games = fetch_espn_source()

    if espn_err:
        print(f"ESPN: ERROR — {espn_err} [{espn_elapsed:.1f}s]")
        games = []
    else:
        upcoming = [g for g in games if g["state"] == "pre"]
        live = [g for g in games if g["state"] == "in"]
        ended = [g for g in games if g["state"] == "post"]
        print(f"ESPN: {len(games)} games ({len(upcoming)} upcoming, {len(live)} live, {len(ended)} ended) [{espn_elapsed:.1f}s]")

    active_games = [g for g in games if g["state"] in ("pre", "in")]
    active_matchups = [g["matchup"] for g in active_games]
    all_ended = len(active_games) == 0

    if active_matchups:
        print(f"Active games: {', '.join(active_matchups)}")
    elif games:
        print("No upcoming/live games — logging ESPN data, skipping other sources this cycle")

    # Build game_states dict
    game_states = {g["matchup"]: g["state"] for g in games}

    # Log ESPN
    espn_record = {
        "ts": ts_iso,
        "cycle": cycle_num,
        "source": "ESPN",
        "elapsed_s": round(espn_elapsed, 2),
        "game_states": game_states,
        "active_games": active_matchups,
        "data": espn_data,
        "error": espn_err,
    }
    _write_jsonl(log_path, espn_record)

    if all_ended and games:
        # Still log ESPN, but skip other fetches
        elapsed = time.time() - cycle_start
        print(f"Logged 1 source record to {log_path}")
        print(f"Cycle {cycle_num} complete in {elapsed:.1f}s")
        return elapsed, []

    # --- Phase 2: Concurrent fetches ---
    results: Dict[str, tuple] = {}

    def _oa_wrapper():
        return fetch_oddsapi_source()

    def _ud_wrapper():
        return fetch_underdog_source()

    def _poly_wrapper():
        return fetch_polymarket_source()

    def _kalshi_wrapper():
        return fetch_kalshi_source()

    tasks = {
        "UNDERDOG": _ud_wrapper,
        "POLYMARKET": _poly_wrapper,
    }
    if not skip_oddsapi:
        tasks["ODDSAPI"] = _oa_wrapper
    if not skip_kalshi:
        tasks["KALSHI"] = _kalshi_wrapper

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as e:
                results[name] = (None, 0, str(e))

    # --- Phase 3: Log and print ---
    source_count = 1  # ESPN already logged

    for source_name in ["ODDSAPI", "UNDERDOG", "POLYMARKET", "KALSHI"]:
        if source_name not in results:
            continue

        res = results[source_name]
        data, elapsed_s, error = res[0], res[1], res[2]

        # Print summary
        if error:
            print(f"{source_name}: ERROR — {error} [{elapsed_s:.1f}s]")
        elif source_name == "ODDSAPI":
            sport_summaries = []
            for sport_data in data.get("sports", []):
                sport_name = sport_data.get("sport", "?")
                games_count = len(sport_data.get("games", []))
                sport_summaries.append(f"{sport_name} {games_count} games")
            joined = ", ".join(sport_summaries) if sport_summaries else "no sports"
            print(f"ODDSAPI: {joined} [{elapsed_s:.1f}s]")
        elif source_name == "UNDERDOG":
            pc = len(data.get("props", []))
            print(f"UNDERDOG: {pc} props [{elapsed_s:.1f}s]")
        elif source_name == "POLYMARKET":
            pc = len(data.get("props", []))
            print(f"POLYMARKET: {pc} props [{elapsed_s:.1f}s]")
        elif source_name == "KALSHI":
            mc = len(data.get("markets", []))
            sc = len(data.get("series_counts", {}))
            print(f"KALSHI: {mc} markets across {sc} series [{elapsed_s:.1f}s]")

        record = {
            "ts": ts_iso,
            "cycle": cycle_num,
            "source": source_name,
            "elapsed_s": round(elapsed_s, 2),
            "game_states": game_states,
            "active_games": active_matchups,
            "data": data,
            "error": error,
        }
        _write_jsonl(log_path, record)
        source_count += 1

    total_elapsed = time.time() - cycle_start
    print(f"Logged {source_count} source records to {log_path}")
    print(f"Cycle {cycle_num} complete in {total_elapsed:.1f}s")

    # --- Regenerate HTML dashboards ---
    for label, gen_fn in [
        ("NBA GL", gen_nba_gl),
        ("MLB GL", gen_mlb_gl),
        ("NBA Props", gen_props),
        ("MLB Props", gen_mlb_props),
    ]:
        try:
            gen_fn()
            print(f"HTML: {label} updated")
        except Exception as e:
            print(f"HTML: {label} ERROR — {e}")

    try:
        publish_site()
        print("HTML: docs site updated")
    except Exception as e:
        print(f"HTML: docs site ERROR — {e}")

    return total_elapsed, active_matchups


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="NBA prop scan loop")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--interval", type=int, default=CYCLE_INTERVAL,
                        help=f"Cycle interval in seconds (default {CYCLE_INTERVAL})")
    parser.add_argument("--no-kalshi", action="store_true",
                        help="Skip Kalshi fetch (useful without credentials)")
    parser.add_argument("--no-fanduel", action="store_true",
                        help="Skip FanDuel cooldown scrape between cycles")
    parser.add_argument("--no-oddsapi", action="store_true",
                        help="Skip OddsAPI fetch (avoids burning credits during testing)")
    args = parser.parse_args()

    interval = args.interval
    cycle_num = 0

    print(f"Scan loop starting — interval={interval}s, kalshi={'off' if args.no_kalshi else 'on'}, oddsapi={'off' if args.no_oddsapi else 'on'}, fanduel={'off' if args.no_fanduel else 'on'}")

    while True:
        cycle_num += 1
        elapsed, active_matchups = run_cycle(cycle_num, skip_kalshi=args.no_kalshi, skip_oddsapi=args.no_oddsapi)

        # FanDuel cooldown scrape
        if not args.no_fanduel:
            fd_elapsed = run_fanduel_cooldown(active_matchups, cycle_num)
            elapsed += fd_elapsed

        if args.once:
            break

        sleep_time = max(0, interval - elapsed)
        if sleep_time > 0:
            print(f"Sleeping {sleep_time:.0f}s until next cycle")
            time.sleep(sleep_time)


if __name__ == "__main__":
    main()
