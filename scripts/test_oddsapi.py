"""
test_oddsapi.py — Standalone test for OddsAPI NBA player props.

Fetches NBA events, then fetches player props per event.
Also exposes a bulk game-line helper used by the scan loop.

Usage:
    python scripts/test_oddsapi.py
    python scripts/test_oddsapi.py --dump
"""

import argparse
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]

EVENTS_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/events"
ODDS_URL_TEMPLATE = (
    "https://api.the-odds-api.com/v4/sports/basketball_nba/events/{event_id}/odds"
)
PROP_MARKETS = (
    "player_points,player_rebounds,player_assists,"
    "player_threes,player_blocks,player_steals,player_turnovers"
)
CREDITS_PER_EVENT = 7  # 7 markets × 1 region


def load_api_key() -> str:
    """Load API key from oddsapi_credentials.local.yaml"""
    cred_path = ROOT / "oddsapi_credentials.local.yaml"
    if not cred_path.exists():
        sys.exit(f"ERROR: credentials file not found at {cred_path}")
    with open(cred_path, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("api_key"):
                _, value = line.split(": ", 1)
                return value.strip().strip("'\"")
    sys.exit("ERROR: 'api_key' not found in credentials file")


def _print_credits(resp: requests.Response, label: str) -> None:
    remaining = resp.headers.get("x-requests-remaining", "unknown")
    used = resp.headers.get("x-requests-used", "unknown")
    print(f"  [credits] {label}: {remaining} remaining (used: {used})")


def fetch_events(api_key: str) -> list:
    """Fetch NBA event list from OddsAPI. Returns list of event dicts."""
    resp = requests.get(EVENTS_URL, params={"apiKey": api_key})
    _print_credits(resp, "event list")
    if resp.status_code != 200:
        print(f"ERROR fetching events: HTTP {resp.status_code}")
        print(resp.text)
        return []
    return resp.json()


def fetch_event_props(api_key: str, event_id: str) -> dict:
    """Fetch player props for a single event. Returns the full response dict."""
    url = ODDS_URL_TEMPLATE.format(event_id=event_id)
    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": PROP_MARKETS,
        "oddsFormat": "american",
    }
    resp = requests.get(url, params=params)
    _print_credits(resp, f"props for {event_id}")
    if resp.status_code != 200:
        print(f"ERROR fetching props for {event_id}: HTTP {resp.status_code}")
        print(resp.text)
        return {}
    return resp.json()


def fetch_game_lines(api_key: str, sport: str = "basketball_nba") -> list:
    """Fetch sport moneylines from all US books in one bulk call.
    Costs 1 credit (1 market × 1 region). Each event has bookmakers[].markets[h2h].outcomes."""
    game_lines_url = "https://api.the-odds-api.com/v4/sports/{}/odds".format(sport)
    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": "h2h",
        "oddsFormat": "american",
    }
    resp = requests.get(game_lines_url, params=params)
    _print_credits(resp, "{} game lines bulk".format(sport))
    if resp.status_code != 200:
        print("ERROR fetching {} game lines: HTTP {}".format(sport, resp.status_code))
        print(resp.text)
        return []
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="Test OddsAPI NBA player props")
    parser.add_argument("--dump", action="store_true", help="Dump raw JSON to data/oddsapi_test_dump.json")
    args = parser.parse_args()

    t_total_start = time.time()

    api_key = load_api_key()
    print(f"API key loaded ({api_key[:6]}...{api_key[-4:]})\n")

    # --- Step 1: Fetch events ---
    print("--- Step 1: Fetching NBA events ---")
    t0 = time.time()
    events = fetch_events(api_key)
    elapsed = time.time() - t0
    print(f"  Fetched {len(events)} events in {elapsed:.2f}s\n")

    if not events:
        print("No events found. Exiting.")
        return

    # --- Credit warning before per-event fetches ---
    estimated_credits = len(events) * CREDITS_PER_EVENT
    # Try to get remaining credits from a lightweight call — we already have it from Step 1
    print(
        f"Fetching props for {len(events)} events "
        f"(~{estimated_credits} credits). "
        f"Check remaining credits above."
    )
    print()

    # --- Step 2: Fetch props per event ---
    print("--- Step 2: Fetching player props per event ---")
    all_results = []
    total_outcomes = 0

    for i, event in enumerate(events, 1):
        home = event.get("home_team", "?")
        away = event.get("away_team", "?")
        eid = event.get("id", "?")
        print(f"[{i}/{len(events)}] {away} @ {home}  (id: {eid})")

        t0 = time.time()
        data = fetch_event_props(api_key, eid)
        elapsed = time.time() - t0
        print(f"  Fetch time: {elapsed:.2f}s")

        if not data:
            print("  No data returned.\n")
            all_results.append({"event": event, "props": {}})
            continue

        bookmakers = data.get("bookmakers", [])
        num_bookmakers = len(bookmakers)
        outcome_count = 0
        for bk in bookmakers:
            for market in bk.get("markets", []):
                outcome_count += len(market.get("outcomes", []))

        total_outcomes += outcome_count
        print(f"  Bookmakers with props: {num_bookmakers}")
        print(f"  Total outcomes: {outcome_count}")
        print()

        all_results.append({"event": event, "props": data})

    # --- Summary ---
    t_total = time.time() - t_total_start
    print("=" * 60)
    print(f"SUMMARY")
    print(f"  Events: {len(events)}")
    print(f"  Total outcomes across all events: {total_outcomes}")
    print(f"  Total elapsed time: {t_total:.2f}s")
    print("=" * 60)

    # --- Optional dump ---
    if args.dump:
        dump_dir = ROOT / "data"
        dump_dir.mkdir(parents=True, exist_ok=True)
        dump_path = dump_dir / "oddsapi_test_dump.json"
        with open(dump_path, "w") as f:
            json.dump(all_results, f, indent=2)
        print(f"\nRaw JSON dumped to {dump_path}")


if __name__ == "__main__":
    main()
