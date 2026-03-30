import urllib.request
import json
import csv
import os
from datetime import date

TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams"
ROSTER_URL = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams/{team_id}/roster"
OUTPUT_PATH = "/Users/kylejones/Desktop/KALSHI_API/data/rosters/mlb_player_team_map.csv"
SOURCE_DATE = "2026-03-29"


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_teams():
    data = fetch_json(TEAMS_URL)
    teams = []
    for sport in data.get("sports", []):
        for league in sport.get("leagues", []):
            for team in league.get("teams", []):
                t = team.get("team", {})
                teams.append({
                    "id": t["id"],
                    "name": t.get("displayName", t.get("name", "")),
                    "abbrev": t.get("abbreviation", ""),
                })
    return teams


def get_roster(team_id):
    url = ROSTER_URL.format(team_id=team_id)
    data = fetch_json(url)
    players = []
    for group in data.get("athletes", []):
        # roster response nests athletes in position groups
        items = group.get("items", group) if isinstance(group, dict) else [group]
        for athlete in items:
            espn_id = athlete.get("id", "")
            display_name = athlete.get("displayName", "")
            if display_name:
                players.append({
                    "espn_id": espn_id,
                    "player_name": display_name,
                })
    return players


def main():
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    teams = get_teams()
    rows = []

    for team in teams:
        team_id = team["id"]
        team_name = team["name"]
        team_abbrev = team["abbrev"]
        print(f"Fetching roster: {team_name} ({team_id})...")
        try:
            players = get_roster(team_id)
            for p in players:
                rows.append({
                    "player_name": p["player_name"],
                    "team_name": team_name,
                    "team_abbrev": team_abbrev,
                    "espn_id": p["espn_id"],
                    "source_date": SOURCE_DATE,
                })
        except Exception as e:
            print(f"  WARNING: failed to fetch roster for {team_name}: {e}")

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["player_name", "team_name", "team_abbrev", "espn_id", "source_date"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nTotal players saved: {len(rows)}")
    print(f"Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
