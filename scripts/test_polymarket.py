#!/usr/bin/env python3
"""
Standalone Polymarket NBA props fetcher.
Fetches active NBA events from Gamma API, parses player prop markets,
and prints a summary. No external dependencies beyond stdlib.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GAMMA_BASE = "https://gamma-api.polymarket.com"
NBA_SERIES_ID = "10345"
REQUEST_TIMEOUT = 20
REQUEST_RETRIES = 2

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}

# Regex: "Player Name: Stat O/U Line"
QUESTION_RE = re.compile(r"^(.+?):\s*(.+?)\s*O/U\s*([\d.]+)\s*$")

# Inline stat type mapping
STAT_MAP = {
    "points": "PTS",
    "rebounds": "REB",
    "assists": "AST",
    "threes": "FG3M",
    "steals": "STL",
    "blocks": "BLK",
    "turnovers": "TOV",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _maybe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_json_field(raw: Any) -> list:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def _normalize_stat(raw_stat: str) -> str:
    """Map raw stat string to canonical abbreviation."""
    key = raw_stat.strip().lower()
    return STAT_MAP.get(key, key.upper())


# ---------------------------------------------------------------------------
# HTTP with retries
# ---------------------------------------------------------------------------

def _get_json(url: str, params: Optional[Dict[str, str]] = None) -> Any:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    last_err: Optional[BaseException] = None
    for attempt in range(REQUEST_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except (urllib.error.HTTPError, urllib.error.URLError,
                socket.timeout, TimeoutError) as exc:
            last_err = exc
            if attempt >= REQUEST_RETRIES:
                raise
            backoff = 1.0 + attempt
            print(f"  Retry {attempt + 1}/{REQUEST_RETRIES} after {backoff}s ({exc})")
            time.sleep(backoff)
    if last_err:
        raise last_err
    return []


# ---------------------------------------------------------------------------
# Fetch events
# ---------------------------------------------------------------------------

def fetch_events() -> List[Dict[str, Any]]:
    params = {
        "closed": "false",
        "active": "true",
        "series_id": NBA_SERIES_ID,
        "limit": "100",
    }
    data = _get_json(f"{GAMMA_BASE}/events", params)
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# Parse props
# ---------------------------------------------------------------------------

def parse_props(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    props: List[Dict[str, Any]] = []

    for ev in events:
        title = ev.get("title", "").strip()
        markets = ev.get("markets") or []

        for mkt in markets:
            question = mkt.get("question", "")
            m = QUESTION_RE.match(question)
            if not m:
                continue

            player_name = m.group(1).strip()
            raw_stat = m.group(2).strip()
            line_val = float(m.group(3))
            stat_type = _normalize_stat(raw_stat)

            # Skip game totals that slip through
            if " vs" in player_name.lower():
                continue

            # Extract prices
            prices_raw = _parse_json_field(mkt.get("outcomePrices", ""))
            prices: List[Optional[float]] = []
            for p in prices_raw:
                prices.append(_maybe_float(p))

            best_ask = _maybe_float(mkt.get("bestAsk"))
            best_bid = _maybe_float(mkt.get("bestBid"))
            fallback_yes = prices[0] if len(prices) >= 1 else None
            fallback_no = prices[1] if len(prices) >= 2 else None

            over_prob = best_ask if best_ask is not None else fallback_yes
            if over_prob is None or over_prob < 0 or over_prob > 1:
                continue

            if best_bid is not None:
                under_prob = 1.0 - best_bid
            elif fallback_no is not None:
                under_prob = fallback_no
            else:
                under_prob = 1.0 - over_prob

            if under_prob < 0 or under_prob > 1:
                continue

            props.append({
                "player": player_name,
                "stat_type": stat_type,
                "line": line_val,
                "over_prob": round(over_prob, 4),
                "under_prob": round(under_prob, 4),
                "matchup": title,
                "question": question,
                "source": "POLYMARKET",
            })

    return props


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Polymarket NBA props fetcher")
    parser.add_argument("--dump", metavar="FILE", help="Write raw JSON events to FILE")
    args = parser.parse_args()

    print("Fetching Polymarket NBA events...")
    events = fetch_events()
    print(f"  {len(events)} events returned")

    if args.dump:
        with open(args.dump, "w") as f:
            json.dump(events, f, indent=2)
        print(f"  Raw events written to {args.dump}")

    props = parse_props(events)
    total_markets = sum(len(ev.get("markets") or []) for ev in events)
    print(f"  {total_markets} total markets across events")
    print(f"  {len(props)} player prop markets parsed")

    # Breakdown by stat type
    stat_counts: Dict[str, int] = {}
    for p in props:
        st = p["stat_type"]
        stat_counts[st] = stat_counts.get(st, 0) + 1

    if stat_counts:
        print("\n  Props by stat type:")
        for st, cnt in sorted(stat_counts.items(), key=lambda x: -x[1]):
            print(f"    {st}: {cnt}")

    # Sample props
    if props:
        print(f"\n  Sample props (first 10 of {len(props)}):")
        for p in props[:10]:
            over_str = f"{p['over_prob'] * 100:.1f}%"
            under_str = f"{p['under_prob'] * 100:.1f}%"
            print(f"    {p['player']} {p['stat_type']} O/U {p['line']} "
                  f"-- over={over_str} under={under_str} ({p['matchup']})")
    else:
        print("\n  No player props found. This may mean no NBA games are currently active on Polymarket.")


if __name__ == "__main__":
    main()
