#!/usr/bin/env python3
"""
Standalone ESPN NBA scoreboard fetch script.
Fetches game states, scores, and clock info from the public ESPN API.
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
REQUEST_TIMEOUT = 15
LOCAL_TZ = ZoneInfo("America/Chicago")

# ESPN uses non-standard abbreviations for some teams
ESPN_ABBREV_MAP = {
    "GS": "GSW",
    "NY": "NYK",
    "NO": "NOP",
    "SA": "SAS",
    "WSH": "WAS",
    "PHO": "PHX",
    "UTAH": "UTA",
    "BKLYN": "BKN",
}


def _normalize(abbrev):
    upper = abbrev.strip().upper()
    return ESPN_ABBREV_MAP.get(upper, upper)


def _fetch_json(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _scoreboard_url(date_str=None):
    if date_str:
        return ESPN_SCOREBOARD + "?" + urllib.parse.urlencode({"dates": date_str})
    return ESPN_SCOREBOARD


def fetch_scoreboard(date_str=None):
    """Fetch NBA scoreboard. Returns list of game dicts."""
    if not date_str:
        date_str = datetime.now(LOCAL_TZ).strftime("%Y%m%d")

    data = _fetch_json(_scoreboard_url(date_str))
    games = []

    for event in data.get("events", []):
        comp = (event.get("competitions") or [{}])[0]
        competitors = comp.get("competitors", [])

        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue

        status = comp.get("status", {})
        status_type = status.get("type", {})
        state = status_type.get("state", "pre")

        period = status.get("period", 0)
        clock = status.get("displayClock", "")

        home_abbrev = _normalize(home.get("team", {}).get("abbreviation", ""))
        away_abbrev = _normalize(away.get("team", {}).get("abbreviation", ""))

        try:
            home_score = int(home.get("score", "0"))
        except (ValueError, TypeError):
            home_score = 0
        try:
            away_score = int(away.get("score", "0"))
        except (ValueError, TypeError):
            away_score = 0

        games.append({
            "game_id": event.get("id", ""),
            "matchup": "{} @ {}".format(away_abbrev, home_abbrev),
            "state": state,
            "period": period,
            "clock": clock,
            "away_abbrev": away_abbrev,
            "home_abbrev": home_abbrev,
            "away_score": away_score,
            "home_score": home_score,
        })

    return data, games


def _period_label(period):
    if period <= 4:
        return "Q{}".format(period)
    if period == 5:
        return "OT"
    return "OT{}".format(period - 4)


def main():
    parser = argparse.ArgumentParser(description="ESPN NBA scoreboard fetch")
    parser.add_argument("--date", type=str, default=None,
                        help="Date in YYYYMMDD format (default: today CT)")
    parser.add_argument("--dump", type=str, default=None,
                        help="Write raw JSON response to FILE")
    args = parser.parse_args()

    date_label = args.date or datetime.now(LOCAL_TZ).strftime("%Y%m%d")
    print("Fetching ESPN NBA scoreboard for {} ...".format(date_label))

    try:
        raw, games = fetch_scoreboard(args.date)
    except Exception as e:
        print("ERROR: {}".format(e), file=sys.stderr)
        sys.exit(1)

    if args.dump:
        with open(args.dump, "w") as f:
            json.dump(raw, f, indent=2)
        print("Raw JSON written to {}".format(args.dump))

    # Classify
    upcoming = [g for g in games if g["state"] == "pre"]
    live = [g for g in games if g["state"] == "in"]
    ended = [g for g in games if g["state"] == "post"]

    print("\nTotal games: {}".format(len(games)))
    print("  Upcoming: {}  |  Live: {}  |  Ended: {}".format(
        len(upcoming), len(live), len(ended)))
    print()

    for g in games:
        state = g["state"]
        matchup = g["matchup"]

        if state == "pre":
            print("  [UPCOMING]  {}".format(matchup))
        elif state == "in":
            pl = _period_label(g["period"])
            print("  [LIVE]      {}  —  {} {} | {} {}  ({} {})".format(
                matchup,
                g["away_abbrev"], g["away_score"],
                g["home_abbrev"], g["home_score"],
                pl, g["clock"]))
        else:
            print("  [FINAL]     {}  —  {} {} | {} {}".format(
                matchup,
                g["away_abbrev"], g["away_score"],
                g["home_abbrev"], g["home_score"]))

    print("\nDone.")


if __name__ == "__main__":
    main()
