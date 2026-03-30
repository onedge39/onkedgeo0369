"""
generate_nba_gl_html.py — NBA game lines HTML dashboard using saved OddsAPI data.

Reads saved bulk moneylines from the latest scan JSONL, de-vigs per book,
computes Fair Avg, fetches ESPN win probabilities, loads Kalshi game-line
markets from the latest scan JSONL, and renders a dark-theme HTML dashboard
matching the GLedge combined.html visual style.

Usage:
    python scripts/generate_nba_gl_html.py
"""

import json
import os
import sys
import time
from typing import Optional
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from test_espn import fetch_scoreboard

CT = ZoneInfo("America/Chicago")

# ---------------------------------------------------------------------------
# Team name normalization: OddsAPI names → logo file keys
# ---------------------------------------------------------------------------

TEAM_NAME_NORM = {
    "Los Angeles Clippers": "LA Clippers",
    "Los Angeles Lakers": "LA Lakers",
}

# ---------------------------------------------------------------------------
# Sportsbook display rank order
# ---------------------------------------------------------------------------

BOOK_RANK = {
    "draftkings": 0,
    "fanduel": 1,
    "betmgm": 2,
    "betonlineag": 3,
    "bovada": 4,
    "pointsbet": 5,
    "betrivers": 6,
    "caesars": 7,
    "williamhill_us": 8,
}

# Fixed book list: always show these in this order (collapsible section)
FIXED_BOOKS = [
    ("draftkings", "DraftKings"),
    ("fanduel", "FanDuel"),
    ("betmgm", "BetMGM"),
    ("betonlineag", "BetOnline.ag"),
    ("bovada", "Bovada"),
    ("pointsbet", "PointsBet"),
    ("betrivers", "BetRivers"),
    ("caesars", "Caesars"),
]

# ---------------------------------------------------------------------------
# Kalshi abbreviation mapping: OddsAPI full team name → Kalshi ticker abbrev
# ---------------------------------------------------------------------------

NBA_TO_KALSHI_ABBREV = {
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

# ---------------------------------------------------------------------------
# Team logos
# ---------------------------------------------------------------------------

def load_team_logos() -> dict:
    """Load NBA team logos from GLedge-dev data directory."""
    logos_path = ROOT.parent / "GLedge-dev" / "data" / "team_logos.json"
    try:
        with open(logos_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Kalshi game-line data
# ---------------------------------------------------------------------------

def load_saved_oddsapi_games(sport_name: str) -> list:
    """Load saved bulk OddsAPI games for a given sport from the latest scan JSONL."""
    try:
        scans_dir = ROOT / "data" / "scans"
        jsonl_files = sorted(scans_dir.glob("scan_*.jsonl"))
        if not jsonl_files:
            return []
        latest = jsonl_files[-1]
        latest_oddsapi = None
        with open(latest, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("source") == "ODDSAPI":
                    latest_oddsapi = row
        if latest_oddsapi is None:
            return []
        for sport_data in (latest_oddsapi.get("data") or {}).get("sports", []):
            if sport_data.get("sport") == sport_name:
                return sport_data.get("games", []) or []
        return []
    except Exception:
        return []

def load_kalshi_gl(series_prefix: str) -> dict:
    """Load Kalshi game-line markets from the latest scan JSONL.

    Finds the most recent data/scans/scan_YYYY-MM-DD.jsonl, reads the last
    KALSHI record, filters markets where ticker contains series_prefix, and
    returns a dict keyed by event_ticker.  Each value is the market dict for
    the HOME-team side of that event (ticker suffix == home abbrev), which
    gives:
        yes_ask_dollars  — home-team win probability (cost to bet home)
        no_ask_dollars   — away-team win probability (cost to bet away)

    Returns {} on any error so callers degrade gracefully.
    """
    try:
        scans_dir = ROOT / "data" / "scans"
        scan_files = sorted(scans_dir.glob("scan_*.jsonl"))
        if not scan_files:
            return {}
        latest = scan_files[-1]

        # Read the last KALSHI record in the file
        kalshi_rec = None
        with open(latest, encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if rec.get("source") == "KALSHI":
                    kalshi_rec = rec

        if kalshi_rec is None:
            return {}

        markets = kalshi_rec.get("data", {}).get("markets", [])

        # Filter to this series
        series_markets = [
            m for m in markets if series_prefix in m.get("ticker", "")
        ]

        # Build result: one entry per event_ticker, using the home-team market.
        # The event_ticker encodes AWAY+HOME (e.g. KXNBAGAME-26MAR29NYKOKC →
        # away=NYK, home=OKC).  The home-team market has ticker suffix matching
        # the home abbrev (e.g. -OKC).  On that market yes_ask = home win prob,
        # no_ask = away win prob.
        #
        # To identify the home-team market without knowing abbreviations ahead of
        # time, we group by event_ticker and pick the market whose yes_ask is
        # higher (home team is usually favored, but not always).  A more reliable
        # approach: the event_ticker suffix is AWAYABBREV+HOMEABBREV; we find the
        # home abbrev as the last characters of the suffix that match the ticker
        # team suffix.  We do this by comparing the ticker team suffix against both
        # halves of the matchup string.
        #
        # Simplest robust approach: store ALL markets keyed by (event_ticker, team_suffix)
        # and let the match function pick the right one.

        result = {}
        for m in series_markets:
            event_ticker = m.get("event_ticker", "")
            if not event_ticker:
                continue
            # ticker = event_ticker + "-" + TEAM_ABBREV
            ticker = m.get("ticker", "")
            team_suffix = ticker[len(event_ticker) + 1:] if ticker.startswith(event_ticker + "-") else ""
            if event_ticker not in result:
                result[event_ticker] = {}
            result[event_ticker][team_suffix.upper()] = m

        return result

    except Exception:
        return {}


def match_kalshi_market(away: str, home: str, kalshi_gl: dict) -> Optional[dict]:
    """Find the Kalshi market for a given away/home team pair.

    Returns the HOME-team market dict (yes_ask = home win prob,
    no_ask = away win prob), or None if not found.
    """
    if not kalshi_gl:
        return None

    away_abbrev = NBA_TO_KALSHI_ABBREV.get(away, "")
    home_abbrev = NBA_TO_KALSHI_ABBREV.get(home, "")
    if not away_abbrev or not home_abbrev:
        return None

    matchup_str = (away_abbrev + home_abbrev).upper()

    for event_ticker, markets_by_team in kalshi_gl.items():
        # event_ticker looks like KXNBAGAME-26MAR29NYKOKC
        # The suffix after the date is AWAY+HOME abbrevs
        et_upper = event_ticker.upper()
        if matchup_str in et_upper:
            # Return the home-team market
            home_market = markets_by_team.get(home_abbrev.upper())
            if home_market:
                return home_market
            # Fallback: return any market for this event
            for m in markets_by_team.values():
                return m

    return None


# ---------------------------------------------------------------------------
# ESPN win probabilities
# ---------------------------------------------------------------------------

ESPN_PREDICTOR_URL = (
    "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"
    "/events/{gid}/competitions/{gid}/predictor"
)
ESPN_PROBS_URL = (
    "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"
    "/events/{gid}/competitions/{gid}/probabilities"
)
REQUEST_TIMEOUT = 10


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _get_game_projection(gid: str, state: str) -> Optional[tuple]:
    """Returns (away_wp, home_wp) as fractions (0-1). Uses predictor for pregame, probabilities for live."""
    if state == "in":
        # Live: use probabilities endpoint
        try:
            data = _fetch_json(ESPN_PROBS_URL.format(gid=gid))
            items = data.get("items", [])
            if items:
                last = items[-1]
                return float(last.get("awayWinPercentage", 0)), float(last.get("homeWinPercentage", 0))
        except Exception:
            pass
    # Pregame (or live fallback): use predictor endpoint
    data = _fetch_json(ESPN_PREDICTOR_URL.format(gid=gid))
    # gameProjection (0-100) is on whichever team has statistics
    away_team = data.get("awayTeam", {})
    home_team = data.get("homeTeam", {})
    for team, is_away in [(away_team, True), (home_team, False)]:
        for stat in team.get("statistics", []):
            if stat.get("name") == "gameProjection":
                wp = float(stat["value"]) / 100.0
                if is_away:
                    return wp, 1.0 - wp
                else:
                    return 1.0 - wp, wp
    return None


def fetch_espn_win_probs() -> dict:
    """Returns dict of {(away_abbrev, home_abbrev): (away_wp, home_wp)} for today's NBA games."""
    try:
        _raw, games = fetch_scoreboard()
    except Exception:
        return {}

    result = {}
    for g in games:
        gid = g.get("game_id", "")
        if not gid:
            continue
        away_abbrev = g.get("away_abbrev", "")
        home_abbrev = g.get("home_abbrev", "")
        state = g.get("state", "pre")
        try:
            wp = _get_game_projection(gid, state)
            if wp:
                result[(away_abbrev, home_abbrev)] = wp
        except Exception:
            continue

    return result


# ---------------------------------------------------------------------------
# Odds math
# ---------------------------------------------------------------------------

def american_to_implied(odds: int) -> float:
    if odds > 0:
        return 100 / (odds + 100)
    else:
        return abs(odds) / (abs(odds) + 100)


def devig(away_odds: int, home_odds: int):
    """Return (away_fair, home_fair) de-vigged probabilities."""
    r_away = american_to_implied(away_odds)
    r_home = american_to_implied(home_odds)
    total = r_away + r_home
    return r_away / total, r_home / total


def fmt_ml(odds: int) -> str:
    """Format American odds with sign."""
    if odds > 0:
        return f"+{odds}"
    return str(odds)


def fmt_pct(p: float) -> str:
    """Format probability as percentage string."""
    return f"{p * 100:.1f}%"


def fmt_edge(e: float) -> str:
    """Return a <td> cell with signed, color-coded edge value."""
    sign = "+" if e >= 0 else ""
    if e > 0.005:
        color = "#3fb950"
    elif e < -0.005:
        color = "#da3633"
    else:
        color = "#8b949e"
    return f'<td class="num" style="font-weight:700;color:{color}">{sign}{e * 100:.1f}%</td>'


def ct_time_str(utc_str: str) -> str:
    """Convert ISO UTC string to CT display string like '7:30pm CT'."""
    try:
        dt_utc = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        dt_ct = dt_utc.astimezone(CT)
        hour = dt_ct.strftime("%I").lstrip("0") or "12"
        minute = dt_ct.strftime("%M")
        ampm = dt_ct.strftime("%p").lower()
        if minute == "00":
            return f"{hour}{ampm} CT"
        return f"{hour}:{minute}{ampm} CT"
    except Exception:
        return utc_str


def is_live(commence_time_str: str) -> bool:
    """Return True if game has already started (commence_time <= now UTC).

    Games that have started will appear in the 'Live' section automatically.
    This is correct for pre/live detection — no logic change needed.
    """
    try:
        dt_utc = datetime.fromisoformat(commence_time_str.replace("Z", "+00:00"))
        return dt_utc <= datetime.now(timezone.utc)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Team name matching: OddsAPI full names → ESPN abbreviations
# ---------------------------------------------------------------------------

# Mapping of OddsAPI full team name → ESPN abbreviation for win prob lookup
_ODDSAPI_TO_ESPN_ABBREV = {
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
    "Los Angeles Clippers": "LAC",
    "LA Lakers": "LAL",
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


def _team_to_abbrev(name: str) -> str:
    return _ODDSAPI_TO_ESPN_ABBREV.get(name, "")


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

CSS = """\
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
    body{background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px;padding:24px}
    header{display:flex;align-items:baseline;gap:16px;margin-bottom:20px;border-bottom:1px solid #21262d;padding-bottom:16px}
    header h1{font-size:18px;font-weight:600;color:#e6edf3;letter-spacing:.02em}
    header .updated{font-size:12px;color:#6e7681}
    .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(560px,1fr));gap:16px}
    .card{background:#161b22;border:1px solid #21262d;border-radius:8px;overflow:hidden}
    .card-header{display:flex;align-items:center;gap:10px;padding:10px 16px;background:#1c2128;border-bottom:1px solid #21262d;flex-wrap:wrap}
    .tip-time{font-size:12px;font-weight:500;color:#6e7681;background:#21262d;padding:2px 8px;border-radius:12px;white-space:nowrap}
    .matchup-title{font-size:14px;font-weight:600;color:#e6edf3;display:flex;align-items:center;gap:6px;flex:1}
    .data-ts{font-size:11px;color:#484f58;white-space:nowrap;margin-left:auto}
    .badge{font-size:11px;font-weight:600;padding:2px 8px;border-radius:10px;white-space:nowrap}
    .badge-pregame{background:#1c2128;color:#58a6ff;border:1px solid #58a6ff44}
    .badge-live{background:#1c2128;color:#3fb950;border:1px solid #3fb95044}
    .live-dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#3fb950;margin-right:4px;vertical-align:middle}
    table{width:100%;border-collapse:collapse}
    thead tr{background:#1c2128}
    th{padding:7px 12px;font-size:11px;font-weight:500;text-transform:uppercase;letter-spacing:.06em;color:#6e7681;text-align:right;border-bottom:1px solid #21262d}
    .th-metric{text-align:left;padding:7px 10px;width:100px;font-size:10px}
    .th-team{text-align:center;font-size:12px;font-weight:600;padding:8px 14px;white-space:nowrap;min-width:100px}
    .th-sub{text-align:center;font-size:10px;font-weight:500;color:#6e7681;padding:4px 8px;border-bottom:1px solid #21262d;text-transform:uppercase;letter-spacing:.05em}
    td{padding:9px 12px;border-bottom:1px solid #21262d}
    tbody tr:last-child td{border-bottom:none}
    tbody tr:hover td{background:#1c2128}
    td.metric{text-align:left;font-size:10px;color:#8b949e;font-weight:600;text-transform:uppercase;letter-spacing:.05em;padding:7px 10px;white-space:nowrap;width:100px}
    td.num{text-align:center;font-variant-numeric:tabular-nums;color:#c9d1d9}
    td.fair{text-align:center;font-variant-numeric:tabular-nums;font-weight:700;color:#e6edf3}
    .team-logo{width:22px;height:22px;object-fit:contain;vertical-align:middle;margin-right:4px}
    .empty-section{color:#6e7681;font-size:13px;padding:32px 0;text-align:center}
    .section-label{font-size:12px;font-weight:600;color:#6e7681;text-transform:uppercase;letter-spacing:.08em;margin-bottom:12px;padding-bottom:8px;border-bottom:1px solid #21262d}
    .books-toggle{cursor:pointer;user-select:none;padding:7px 10px;font-size:10px;font-weight:600;color:#58a6ff;text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid #21262d;background:#161b22}
    .books-toggle:hover{background:#1c2128}
    .books-section{display:none}
    .books-section.open{display:table-row-group}
    .books-section td{background:#0d1117}
    footer{margin-top:32px;font-size:11px;color:#6e7681;text-align:center}
    @media(max-width:700px){
      body{padding:10px 8px}
      .grid{grid-template-columns:1fr}
      th{padding:5px 6px;font-size:10px}
      td{padding:7px 6px;font-size:12px}
      .card-header{padding:8px 10px;gap:6px}
      .matchup-title{font-size:12px}
      .data-ts{display:none}
    }"""


def _logo_img(team_name: str, logos: dict) -> str:
    """Return an <img> tag for the team logo, or empty string if not found."""
    key = TEAM_NAME_NORM.get(team_name, team_name)
    url = logos.get(key, "")
    if not url:
        return ""
    return f'<img src="{url}" class="team-logo" alt="">'


_card_id_counter = 0


def render_card(
    event: dict,
    now_ts: str,
    logos: dict,
    espn_probs: dict,
    kalshi_market: Optional[dict],
) -> str:
    """Render a single game card as HTML string."""
    global _card_id_counter
    _card_id_counter += 1
    card_id = f"nba_books_{_card_id_counter}"

    home = event.get("home_team", "?")
    away = event.get("away_team", "?")
    commence = event.get("commence_time", "")
    bookmakers = event.get("bookmakers", [])

    tip_str = ct_time_str(commence)
    live = is_live(commence)

    if live:
        badge = '<span class="badge badge-live"><span class="live-dot"></span>Live</span>'
    else:
        badge = '<span class="badge badge-pregame">Pre-Game</span>'

    matchup = f"{away} @ {home}"

    # -----------------------------------------------------------------------
    # Build per-book data: extract h2h odds, compute implied + de-vigged
    # -----------------------------------------------------------------------
    book_data = {}  # type: dict  # book_key -> (title, away_ml, home_ml, away_fair, home_fair)
    fair_away_list = []
    fair_home_list = []

    for bk in bookmakers:
        book_key = bk.get("key", "")
        title = bk.get("title", book_key or "?")
        h2h = None
        for market in bk.get("markets", []):
            if market.get("key") == "h2h":
                h2h = market
                break
        if h2h is None:
            continue

        outcomes = h2h.get("outcomes", [])
        away_odds = None
        home_odds = None
        for o in outcomes:
            name = o.get("name", "")
            price = o.get("price")
            if name == away:
                away_odds = price
            elif name == home:
                home_odds = price

        if away_odds is None or home_odds is None:
            continue

        a_fair, h_fair = devig(away_odds, home_odds)
        fair_away_list.append(a_fair)
        fair_home_list.append(h_fair)

        book_data[book_key] = (title, away_odds, home_odds, a_fair, h_fair)

    # Fair Avg: mean of per-book de-vigged probabilities
    if fair_away_list:
        avg_away = sum(fair_away_list) / len(fair_away_list)
        avg_home = sum(fair_home_list) / len(fair_home_list)
    else:
        avg_away = None
        avg_home = None

    # -----------------------------------------------------------------------
    # ESPN win probability lookup
    # -----------------------------------------------------------------------
    away_abbrev = _team_to_abbrev(away)
    home_abbrev = _team_to_abbrev(home)
    espn_wp = espn_probs.get((away_abbrev, home_abbrev))

    # -----------------------------------------------------------------------
    # Weighted Fair: 55% Fair Avg + 45% ESPN
    # -----------------------------------------------------------------------
    if avg_away is not None and espn_wp is not None:
        wf_away = 0.55 * avg_away + 0.45 * espn_wp[0]
        wf_home = 0.55 * avg_home + 0.45 * espn_wp[1]
    elif avg_away is not None:
        wf_away = avg_away
        wf_home = avg_home
    elif espn_wp is not None:
        wf_away = espn_wp[0]
        wf_home = espn_wp[1]
    else:
        wf_away = None
        wf_home = None

    # -----------------------------------------------------------------------
    # Build table HTML
    # -----------------------------------------------------------------------
    away_logo = _logo_img(away, logos)
    home_logo = _logo_img(home, logos)

    # thead: two rows -- team names (colspan=2), then sub-column labels
    thead = (
        "<thead>"
        "<tr>"
        '<th class="th-metric"></th>'
        f'<th class="th-team" colspan="2">{away_logo}{away}</th>'
        f'<th class="th-team" colspan="2">{home_logo}{home}</th>'
        "</tr>"
        "<tr>"
        '<th class="th-sub"></th>'
        '<th class="th-sub">ML</th>'
        '<th class="th-sub">No-Vig%</th>'
        '<th class="th-sub">ML</th>'
        '<th class="th-sub">No-Vig%</th>'
        "</tr>"
        "</thead>"
    )

    # === VISIBLE SECTION (main tbody) ===
    visible_rows = ""

    # Weighted Fair row
    if wf_away is not None:
        visible_rows += (
            "<tr>"
            '<td class="metric">Wtd Fair</td>'
            '<td class="num">\u2014</td>'
            f'<td class="fair">{fmt_pct(wf_away)}</td>'
            '<td class="num">\u2014</td>'
            f'<td class="fair">{fmt_pct(wf_home)}</td>'
            "</tr>"
        )
    else:
        visible_rows += (
            "<tr>"
            '<td class="metric">Wtd Fair</td>'
            '<td class="num" colspan="4" style="text-align:center;color:#6e7681">No data available</td>'
            "</tr>"
        )

    # ESPN row
    if espn_wp is not None:
        away_wp, home_wp = espn_wp
        visible_rows += (
            "<tr>"
            '<td class="metric">ESPN</td>'
            '<td class="num">\u2014</td>'
            f'<td class="num">{fmt_pct(away_wp)}</td>'
            '<td class="num">\u2014</td>'
            f'<td class="num">{fmt_pct(home_wp)}</td>'
            "</tr>"
        )

    # Kalshi + Edge + Kelly rows
    kalshi_rows = ""
    if kalshi_market is not None and wf_away is not None:
        try:
            ks_yes_ask = float(kalshi_market["yes_ask_dollars"])
            ks_no_ask = float(kalshi_market["no_ask_dollars"])
            ks_total = ks_yes_ask + ks_no_ask
            ks_home = ks_yes_ask / ks_total
            ks_away = ks_no_ask / ks_total

            away_edge = wf_away - ks_away
            home_edge = wf_home - ks_home

            kelly_away_frac = 0.0
            kelly_home_frac = 0.0
            if away_edge > 0:
                kelly_away_frac = (away_edge / (1.0 - ks_no_ask)) * (1 / 3)
            if home_edge > 0:
                kelly_home_frac = (home_edge / (1.0 - ks_yes_ask)) * (1 / 3)

            bankroll = 1000
            kelly_away_dollars = kelly_away_frac * bankroll
            kelly_home_dollars = kelly_home_frac * bankroll

            dash = "\u2014"
            kelly_away_str = "${:.0f}".format(kelly_away_dollars) if kelly_away_dollars > 0 else dash
            kelly_home_str = "${:.0f}".format(kelly_home_dollars) if kelly_home_dollars > 0 else dash

            kalshi_rows = (
                # Kalshi row
                "<tr>"
                '<td class="metric">Kalshi</td>'
                f'<td class="num">{dash}</td>'
                f'<td class="num">{fmt_pct(ks_away)}</td>'
                f'<td class="num">{dash}</td>'
                f'<td class="num">{fmt_pct(ks_home)}</td>'
                "</tr>"
                # Edge row
                "<tr>"
                '<td class="metric">Edge</td>'
                f'<td class="num">{dash}</td>'
                + fmt_edge(away_edge)
                + f'<td class="num">{dash}</td>'
                + fmt_edge(home_edge)
                + "</tr>"
                # Kelly row
                "<tr>"
                '<td class="metric">Kelly</td>'
                f'<td class="num">{dash}</td>'
                f'<td class="num" style="font-weight:700;color:#3fb950">'
                f'{kelly_away_str}</td>'
                f'<td class="num">{dash}</td>'
                f'<td class="num" style="font-weight:700;color:#3fb950">'
                f'{kelly_home_str}</td>'
                "</tr>"
            )
        except (KeyError, ValueError, TypeError):
            kalshi_rows = ""

    visible_rows += kalshi_rows

    main_tbody = f"<tbody>{visible_rows}</tbody>"

    # === COLLAPSIBLE SECTION (books toggle + book rows) ===
    books_with_data = sum(1 for bk, _ in FIXED_BOOKS if bk in book_data)

    toggle_row = (
        f'<tbody><tr><td colspan="5" class="books-toggle" '
        f'onclick="var s=document.getElementById(\'{card_id}\');'
        f's.classList.toggle(\'open\');'
        f'this.textContent=s.classList.contains(\'open\')'
        f'?\'\u25BC Books ({books_with_data})\':\'\u25B6 Books ({books_with_data})\';">'
        f'\u25B6 Books ({books_with_data})</td></tr></tbody>'
    )

    # Build collapsible book rows
    collapsible_rows = ""

    # Fair Avg row (inside collapsible)
    if avg_away is not None:
        collapsible_rows += (
            "<tr>"
            '<td class="metric">Fair Avg</td>'
            '<td class="num">\u2014</td>'
            f'<td class="fair">{fmt_pct(avg_away)}</td>'
            '<td class="num">\u2014</td>'
            f'<td class="fair">{fmt_pct(avg_home)}</td>'
            "</tr>"
        )
    else:
        collapsible_rows += (
            "<tr>"
            '<td class="metric">Fair Avg</td>'
            '<td class="num" colspan="4" style="text-align:center;color:#6e7681">No odds available</td>'
            "</tr>"
        )

    # Fixed book rows
    for book_key, book_title in FIXED_BOOKS:
        if book_key in book_data:
            _title, a_ml, h_ml, a_fair, h_fair = book_data[book_key]
            collapsible_rows += (
                "<tr>"
                f'<td class="metric">{book_title}</td>'
                f'<td class="num">{fmt_ml(a_ml)}</td>'
                f'<td class="num">{fmt_pct(a_fair)}</td>'
                f'<td class="num">{fmt_ml(h_ml)}</td>'
                f'<td class="num">{fmt_pct(h_fair)}</td>'
                "</tr>"
            )
        else:
            collapsible_rows += (
                "<tr>"
                f'<td class="metric">{book_title}</td>'
                '<td class="num"></td>'
                '<td class="num"></td>'
                '<td class="num"></td>'
                '<td class="num"></td>'
                "</tr>"
            )

    books_tbody = f'<tbody id="{card_id}" class="books-section">{collapsible_rows}</tbody>'

    return (
        '<div class="card">'
        '<div class="card-header">'
        f'<span class="tip-time">{tip_str}</span>'
        f'<span class="matchup-title">{matchup}</span>'
        f'{badge}'
        f'<span class="data-ts">{now_ts}</span>'
        "</div>"
        f"<table>{thead}{main_tbody}{toggle_row}{books_tbody}</table>"
        "</div>"
    )


def render_html(events: list, logos: dict, espn_probs: dict) -> str:
    """Render full HTML page for all events."""
    now_utc = datetime.now(timezone.utc)
    now_ct = now_utc.astimezone(CT)
    hour = now_ct.strftime("%I").lstrip("0") or "12"
    minute = now_ct.strftime("%M")
    ampm = now_ct.strftime("%p").lower()
    updated_str = f"Updated {hour}:{minute}{ampm} CT &nbsp;&bull;&nbsp; auto-refresh 5 min"

    # Timestamp label shown in each card header
    ts_label = now_ct.strftime("%-I:%M:%S%p").lower() + " CT"

    # Load Kalshi GL data once for all cards
    kalshi_gl = load_kalshi_gl("KXNBAGAME")

    # Sort: pre-game (by commence_time asc), then live (by commence_time asc)
    def sort_key(ev):
        ct_str = ev.get("commence_time", "")
        live = is_live(ct_str)
        return (1 if live else 0, ct_str)

    sorted_events = sorted(events, key=sort_key)

    pregame = [e for e in sorted_events if not is_live(e.get("commence_time", ""))]
    live = [e for e in sorted_events if is_live(e.get("commence_time", ""))]

    def _card(e):
        km = match_kalshi_market(e.get("away_team", ""), e.get("home_team", ""), kalshi_gl)
        return render_card(e, ts_label, logos, espn_probs, km)

    sections_html = ""

    if pregame:
        cards_html = "\n".join(_card(e) for e in pregame)
        sections_html += (
            '<div style="margin-bottom:32px">'
            '<div class="section-label">Pre-Game</div>'
            f'<div class="grid">{cards_html}</div>'
            "</div>"
        )

    if live:
        cards_html = "\n".join(_card(e) for e in live)
        sections_html += (
            '<div style="margin-bottom:32px">'
            '<div class="section-label"><span class="live-dot"></span>Live</div>'
            f'<div class="grid">{cards_html}</div>'
            "</div>"
        )

    if not pregame and not live:
        sections_html = '<div class="empty-section">No NBA games found.</div>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <meta http-equiv="refresh" content="300">
  <title>NBA Game Lines &middot; OddsAPI</title>
  <style>
{CSS}
  </style>
</head>
<body>
  <header>
    <h1>NBA Game Lines &middot; OddsAPI</h1>
    <span class="updated">{updated_str}</span>
  </header>
  {sections_html}
  <footer>No-vig implied probabilities &nbsp;&bull;&nbsp; Weighted Fair = 55% Book Avg + 45% ESPN &nbsp;&bull;&nbsp; Fair Avg = mean of per-book de-vig</footer>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate(output_path=None) -> Path:
    """Render NBA game lines from saved scan data and write HTML."""
    if output_path is None:
        output_path = ROOT / "data" / "nba_gl.html"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    events = load_saved_oddsapi_games("nba")

    logos = load_team_logos()
    espn_probs = fetch_espn_win_probs()

    html = render_html(events, logos, espn_probs)

    # Atomic write: temp file → os.replace
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=output_path.parent, prefix=".nba_gl_", suffix=".html.tmp"
    )
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


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    t0 = time.time()
    path = generate()
    elapsed = time.time() - t0
    print(f"Written: {path}")
    print(f"Elapsed: {elapsed:.2f}s")
