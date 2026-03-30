# Next Session Instructions

> Paste this into a new guide chat opened in `/Users/kylejones/Desktop/KALSHI_API`

---

## You are a guide chat.

Read these files first, in order:
1. `/Users/kylejones/Desktop/KALSHI_API/CLAUDE.md`
2. `/Users/kylejones/Desktop/KALSHI_API/HANDOFF.md`
3. `/Users/kylejones/Desktop/KALSHI_API/AGENTS.md`

You hold architectural context and make design decisions. You do NOT implement directly unless it's trivial (< 5 lines). You delegate implementation to agents.

---

## Priority: Fix the data capture loop

The scan loop is burning OddsAPI credits on per-event prop calls and not properly saving prop data for backtesting. Fix this before touching anything else.

### Fix 1: OddsAPI — remove per-event prop calls

In `scripts/scan_loop.py`, `fetch_oddsapi_source()` currently calls `oa_fetch_event_props()` per event. This costs ~7 credits/event and was supposed to be removed. The architecture decision is **game lines only from OddsAPI** (1 credit/sport).

Fix:
- Remove the per-event props loop from `fetch_oddsapi_source()`
- Replace with the bulk game lines call: `fetch_game_lines(api_key, sport)` for both `basketball_nba` and `baseball_mlb`
- The function should save two bulk records: `{"sport": "nba", "games": [...]}` and `{"sport": "mlb", "games": [...]}`
- Check `test_oddsapi.py` — `fetch_game_lines()` already exists and is verified working

### Fix 2: MLB Kalshi series — add to scan loop

`KALSHI_ALL_SERIES` in `scan_loop.py` is missing MLB series. Add:
```python
KALSHI_MLB_GL_SERIES = ["KXMLBGAME", "KXMLBSPREAD", "KXMLBTOTAL"]
KALSHI_MLB_PROP_SERIES = ["KXMLBHIT", "KXMLBHR", "KXMLBHRR", "KXMLBKS", "KXMLBTB"]
KALSHI_ALL_SERIES = KALSHI_NBA_PROP_SERIES + KALSHI_NBA_GL_SERIES + KALSHI_MLB_GL_SERIES + KALSHI_MLB_PROP_SERIES
```

### Fix 3: Props HTML — wire Underdog

`generate_props_html.py` reads props from scan JSONL but only picks up OddsAPI records. It should read `source == "UNDERDOG"` records instead (Underdog is the primary props source). OddsAPI is NOT used for props.

---

## After data capture is fixed

- NBA roster scrape (same pattern as `scrape_mlb_rosters.py`)
- Copy NBA team logos into this repo
- Props HTML: add Kalshi ask prices + edge + Kelly per prop line
- MLB props HTML

---

## What NOT to do

- Do NOT pull OddsAPI player props. Game lines only (1 credit/sport, bulk call).
- Do NOT filter out any props. Save everything.
- Do NOT redesign the JSONL format.
- Do NOT use Python 3.10+ syntax.
- Do NOT build new HTML dashboards until data capture is confirmed working.

---

## Current source status

| Source | Role | Status |
|--------|------|--------|
| ESPN | Game state gating + win probabilities | Working |
| OddsAPI | Game lines only (not props) | Bug: still calling per-event props — fix first |
| Underdog | Primary props source | Fetched but not wired into props HTML |
| Polymarket | Display-only sharp reference | Working |
| Kalshi | Execution venue (NBA only) | Working — MLB series missing from scan loop |
| FanDuel | Cooldown scrape, backtest-only props | Implemented, untested |
