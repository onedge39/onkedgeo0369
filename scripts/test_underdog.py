#!/usr/bin/env python3
"""
Standalone Underdog Fantasy NBA props fetcher.

Single GET to v2 endpoint, parses appearance chain, prints summary.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

API_URL = "https://api.underdogfantasy.com/v2/over_under_lines"
API_PARAMS = {"product": "fantasy", "sport_id": "NBA"}
TIMEOUT = 30
RETRIES = 2

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

STAT_MAP = {
    "points": "PTS",
    "rebounds": "REB",
    "assists": "AST",
    "threes": "FG3M",
    "3-pointers made": "FG3M",
    "made threes": "FG3M",
    "3s attempted": "FG3A",
    "steals": "STL",
    "blocks": "BLK",
    "turnovers": "TOV",
    "fg attempted": "FGA",
    "pts + rebs + asts": "PRA",
    "pts+reb+ast": "PRA",
    "points+rebounds+assists": "PRA",
    "points + rebounds + assists": "PRA",
    "pts+ast": "PA",
    "points+assists": "PA",
    "points + assists": "PA",
    "pts+reb": "PR",
    "points+rebounds": "PR",
    "points + rebounds": "PR",
    "reb+ast": "RA",
    "rebounds+assists": "RA",
    "rebounds + assists": "RA",
    "blocks + steals": "BLK+STL",
    "double doubles": "DD",
    "double-double": "DD",
    "triple doubles": "TD",
    "triple-double": "TD",
    "fantasy points": "FPTS",
    "1q points": "1Q_PTS",
    "1q rebounds": "1Q_REB",
    "1q assists": "1Q_AST",
    "1h points": "1H_PTS",
    "1h rebounds": "1H_REB",
    "1h assists": "1H_AST",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _index_by_id(items: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if isinstance(items, list):
        for o in items:
            if isinstance(o, dict) and o.get("id") is not None:
                out[str(o["id"])] = o
    return out


def _safe_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_int(val: Any) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(round(float(val)))
    except (TypeError, ValueError):
        return None


def _normalize_stat(raw: str) -> str:
    key = raw.strip().lower()
    return STAT_MAP.get(key, key.upper())


def _normalize_matchup(abbr: Optional[str]) -> str:
    raw = (abbr or "").strip()
    if "@" in raw and " @ " not in raw:
        raw = raw.replace("@", " @ ")
    return raw


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_raw() -> Dict[str, Any]:
    url = API_URL + "?" + urllib.parse.urlencode(API_PARAMS)
    last_err: Optional[BaseException] = None
    for attempt in range(RETRIES + 1):
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504} or attempt >= RETRIES:
                raise
            last_err = exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            if attempt >= RETRIES:
                raise
            last_err = exc
        backoff = 1.0 + attempt
        print(f"  Retry {attempt + 1}/{RETRIES} after {backoff:.0f}s ({last_err})")
        time.sleep(backoff)
    raise last_err or RuntimeError("Fetch failed")


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def parse_props(data: Dict[str, Any], include_live: bool = False) -> List[Dict[str, Any]]:
    players_idx = _index_by_id(data.get("players") or [])
    appearances_idx = _index_by_id(data.get("appearances") or [])
    games_idx = _index_by_id(data.get("games") or [])
    lines = data.get("over_under_lines") or []

    now_utc = datetime.now(timezone.utc)

    # Identify started games
    started_games: set = set()
    if not include_live:
        for gid, g in games_idx.items():
            sched = g.get("scheduled_at")
            if sched:
                text = str(sched).strip()
                if text.endswith("Z"):
                    text = text[:-1] + "+00:00"
                try:
                    dt = datetime.fromisoformat(text)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt <= now_utc:
                        started_games.add(gid)
                except Exception:
                    pass

    # Merge over/under sides by key
    sides: Dict[tuple, Dict[str, Any]] = {}

    for item in lines:
        if not isinstance(item, dict):
            continue

        over_under = item.get("over_under") or {}
        appearance_stat = over_under.get("appearance_stat") or {}
        stat_title = (
            appearance_stat.get("display_stat")
            or over_under.get("title")
            or ""
        )
        line_val = _safe_float(item.get("stat_value"))

        # Follow appearance chain
        appearance_id = appearance_stat.get("appearance_id")
        appearance = appearances_idx.get(str(appearance_id)) if appearance_id else None
        if not isinstance(appearance, dict):
            continue

        player_id = appearance.get("player_id")
        match_id = appearance.get("match_id")

        player = players_idx.get(str(player_id)) if player_id else None
        if not isinstance(player, dict):
            continue

        sport_id = str(player.get("sport_id") or "").upper()
        if sport_id != "NBA":
            continue

        fn = player.get("first_name") or ""
        ln = player.get("last_name") or ""
        player_name = f"{fn} {ln}".strip()
        if not player_name or line_val is None:
            continue

        game = games_idx.get(str(match_id)) if match_id else None
        if not isinstance(game, dict):
            continue

        game_id = str(match_id)
        if game_id in started_games:
            continue

        matchup = _normalize_matchup(game.get("abbreviated_title"))
        stat_type = _normalize_stat(stat_title)
        is_live = bool(item.get("live_event"))

        # Parse options (higher/lower)
        for opt in (item.get("options") or []):
            if not isinstance(opt, dict):
                continue
            choice = (opt.get("choice") or "").lower()
            if choice == "higher":
                side = "over"
            elif choice == "lower":
                side = "under"
            else:
                continue

            american = _safe_int(opt.get("american_price"))

            key = (game_id, player_name.lower(), stat_type, line_val)
            if key not in sides:
                sides[key] = {
                    "player": player_name,
                    "stat_type": stat_type,
                    "line": line_val,
                    "matchup": matchup,
                    "over_american": None,
                    "under_american": None,
                    "live": is_live,
                }

            if side == "over":
                sides[key]["over_american"] = american
            else:
                sides[key]["under_american"] = american

    return list(sides.values())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Underdog NBA props")
    parser.add_argument("--dump", metavar="FILE", help="Write raw JSON response to FILE")
    parser.add_argument("--all", action="store_true", help="Include live/started games (default: pre-tip only)")
    args = parser.parse_args()

    print("Fetching Underdog NBA props (v2)...")
    t0 = time.time()
    data = fetch_raw()
    elapsed = time.time() - t0
    print(f"  Fetch completed in {elapsed:.1f}s")

    if args.dump:
        with open(args.dump, "w") as f:
            json.dump(data, f, indent=2)
        print(f"  Raw JSON dumped to {args.dump}")

    props = parse_props(data, include_live=args.all)
    print(f"\n  Total NBA props: {len(props)}")

    if not props:
        print("  No props found.")
        return

    # Breakdown by stat type
    stat_counts: Dict[str, int] = {}
    for p in props:
        st = p["stat_type"]
        stat_counts[st] = stat_counts.get(st, 0) + 1

    print("\n  Breakdown by stat type:")
    for st, cnt in sorted(stat_counts.items(), key=lambda x: -x[1]):
        print(f"    {st}: {cnt}")

    # Live vs pre-tip
    live_count = sum(1 for p in props if p["live"])
    print(f"\n  Pre-tip: {len(props) - live_count}  |  Live: {live_count}")

    # Sample props
    print("\n  Sample props (first 10):")
    for p in props[:10]:
        over_str = f"{p['over_american']:+d}" if p["over_american"] is not None else "—"
        under_str = f"{p['under_american']:+d}" if p["under_american"] is not None else "—"
        live_tag = " [LIVE]" if p["live"] else ""
        print(f"    {p['player']} {p['stat_type']} O/U {p['line']}  "
              f"over={over_str} under={under_str}  ({p['matchup']}){live_tag}")


if __name__ == "__main__":
    main()
