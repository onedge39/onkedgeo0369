# KALSHI_API — Handoff (3/29/26, Session 4)

## Current Status

Session 4 wired FanDuel cooldown scrape, added weighted fair edge model, collapsed book rows in HTML, and added `--no-oddsapi` flag. **OddsAPI credits were burned during this session on per-event prop calls that should have been removed.** Core priority for the next session is fixing the data capture loop so props are actually being saved for backtesting.

---

## What was built in Session 4

1. **FanDuel cooldown scrape (`scan_loop.py`)**
   - Runs after each cycle as a cooldown task
   - Scopes to ESPN active matchups via `NBA_ABBREV_TO_FDNAME` map
   - Saves to `data/fanduel/fd_props_YYYY-MM-DD.jsonl` (append-only, backtest only)
   - `--no-fanduel` flag to skip

2. **Weighted Fair edge model (HTML generators)**
   - `weighted_fair = 0.55 * fair_avg + 0.45 * espn_prob`
   - Fallback to 100% fair_avg if ESPN unavailable, 100% ESPN if no books
   - Edge and Kelly now use weighted_fair instead of raw fair_avg

3. **Collapsible book rows (NBA + MLB GL HTML)**
   - Default view: Wtd Fair → ESPN → Kalshi → Edge → Kelly
   - "▶ Books (N)" toggle expands Fair Avg + all 8 fixed book rows
   - Fixed book list always present (blank row if no data, excluded from calc)

4. **`--no-oddsapi` flag**
   - Skips OddsAPI fetch entirely (use during testing to avoid burning credits)

---

## Flags reference

| Flag | Effect |
|------|--------|
| `--once` | Run one cycle and exit |
| `--no-kalshi` | Skip Kalshi fetch |
| `--no-oddsapi` | Skip OddsAPI fetch |
| `--no-fanduel` | Skip FanDuel cooldown scrape |

**Test command (no credits burned):**
```
python3 ~/Desktop/KALSHI_API/scripts/scan_loop.py --once --no-oddsapi --no-fanduel
```

---

## Known Bugs / Outstanding Gaps

### CRITICAL — Data not being captured correctly

1. **OddsAPI per-event props still in scan loop** — `fetch_oddsapi_source()` calls `oa_fetch_event_props()` per event (~7 credits each). This directly contradicts the architecture decision. Only `fetch_game_lines()` should be called (1 credit/sport). The per-event loop needs to be removed and replaced with the bulk game lines call.

2. **Props JSONL contains only OddsAPI data** — `generate_props_html.py` reads from scan JSONL but only finds OddsAPI props records. Underdog props are being fetched and logged but the props dashboard does not read them. Kalshi prop markets are also not in the props dashboard.

3. **MLB Kalshi series never fetched** — `KALSHI_ALL_SERIES` only contains NBA tickers. `KXMLBGAME`, `KXMLBSPREAD`, `KXMLBTOTAL`, and MLB prop series are never fetched. The `generate_mlb_gl_html.py` has `load_kalshi_gl()` logic that will always return empty.

### Not yet built

4. **Props HTML: Underdog** — props dashboard reads OddsAPI only. Should read Underdog (primary source) from JSONL.
5. **Props HTML: Kalshi** — no Kalshi ask prices, edge, or Kelly in props dashboard.
6. **MLB props HTML** — does not exist.
7. **NBA roster file** — `data/rosters/nba_player_team_map.csv` not in this repo yet. Props dashboard uses GLedge-dev copy.
8. **NBA team logos** — loaded from `~/Desktop/GLedge-dev/data/team_logos.json`, not self-contained.

---

## File Structure (current)

```
KALSHI_API/
├── CLAUDE.md
├── HANDOFF.md                       # This file
├── NEXT_SESSION.md
├── AGENTS.md
├── .gitignore
├── kalshi_demo.py
├── demo_credentials.local.yaml      # gitignored
├── oddsapi_credentials.local.yaml   # gitignored
├── scripts/
│   ├── scan_loop.py                 # Flags: --once --no-kalshi --no-oddsapi --no-fanduel
│   ├── test_espn.py
│   ├── test_oddsapi.py
│   ├── test_underdog.py
│   ├── test_polymarket.py
│   ├── test_fanduel_bulk.py
│   ├── generate_nba_gl_html.py      # Weighted fair, collapsible books
│   ├── generate_mlb_gl_html.py      # Weighted fair, collapsible books (Kalshi empty — not fetched)
│   ├── generate_props_html.py       # OddsAPI only — Underdog/Kalshi not wired
│   ├── scrape_mlb_rosters.py
│   ├── check_demo_auth.py
│   └── run_kalshi_categories.py
└── data/
    ├── nba_gl.html
    ├── mlb_gl.html
    ├── props.html
    ├── mlb_team_logos.json
    ├── oddsapi_test_dump.json
    ├── rosters/
    │   └── mlb_player_team_map.csv
    ├── fanduel/                     # Created on first FanDuel cooldown run
    │   └── fd_props_YYYY-MM-DD.jsonl
    └── scans/
        └── scan_YYYY-MM-DD.jsonl
```
