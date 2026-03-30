# KALSHI_API — Project Context

Rebuild of the sports prop / game-line scanning system. Centered on the **authenticated Kalshi API** for execution, with OddsAPI, Underdog, Polymarket, ESPN, and FanDuel as pricing/data sources.

**This is not a prediction model.** It is a market-inefficiency scanner with a path to programmatic order placement on Kalshi.

---

## Deprecated Notice

- The old KSprop / CBBprop / GLedge-dev / KSarb repos are deprecated as architecture.
- Useful old code and historical data will be selectively carried over later.
- Do not treat old repo structure or patterns as the canonical design.

---

## Scope

### In scope
- **NBAprop** — NBA player props
- **NBAgl** — NBA game lines
- **MLBprop** — MLB player props
- **MLBgl** — MLB game lines

### Out of scope
- CBB (college basketball)
- Old loop architecture
- Old Kalshi scrape architecture

---

## Kalshi's Role

1. **Execution venue.** Sharp sources provide pricing inputs. Kalshi is where trades execute.
2. **Does NOT gate what gets scanned or saved.** Every source is fetched and logged for every lane regardless of Kalshi availability. All data is backtesting data.
3. **Provides the executable price.** For tradeable markets, entry price = Kalshi ask side (`yes_ask` for over, `no_ask` for under).
4. **Authenticated API removes the old bottleneck.** The rebuild uses authenticated access — proper rate limits, full paginated pulls, path to order placement. Old public-API pacing constraints do not apply.
5. **Demo vs. production = credential swap.** Same client code, different `base_url` in the credential file.
6. **Order placement is the end goal.** Read-only scanning first, then programmatic execution.

---

## Data Sources

| Source | Pattern | Speed | Auth | Notes |
|--------|---------|-------|------|-------|
| Kalshi | Authenticated REST, paginated per series | ~5s | RSA-PSS signed | Execution target |
| OddsAPI | REST, bulk per sport (game lines only) | <1s per sport | API key | Game lines from 5+ books in one call. 1 credit/call |
| Underdog | Single bulk GET | <3s | None | All NBA props in one request. Primary props source |
| Polymarket | Single bulk GET (Gamma API) | <2s | None | Display-only for sharp reference |
| ESPN | Scoreboard + predictor per game | <5s | None | Game state gating + win probabilities |
| FanDuel | Discovery + per-event tab GETs | ~112s | None | Cooldown scrape between cycles, backtest-only |

### OddsAPI (implemented 3/29/26)
- **Game lines only** — bulk moneyline call returns all books for all games per sport. 1 credit per call.
- **NOT used for props** — per-event prop fetch costs ~7 credits/event, too expensive for continuous polling. Underdog covers props.
- Endpoints used:
  - `GET /v4/sports/basketball_nba/odds?markets=h2h&regions=us` — NBA game lines
  - `GET /v4/sports/baseball_mlb/odds?markets=h2h&regions=us` — MLB game lines
- API key in `oddsapi_credentials.local.yaml` (gitignored)
- Free tier: 500 credits/month. At 1 credit/sport/cycle, lasts ~250 game days.
- Books returned: DraftKings, FanDuel, BetMGM, BetOnline.ag, Bovada (varies by event)

### FanDuel (cooldown scrape — next to implement)
- Runs BETWEEN scan cycles as a cooldown task, scraping player props for active matchups.
- Output goes to a backtest-only file (not main JSONL or HTML dashboards).
- Scoped to matchups ESPN flagged as `pre` or `in` in the preceding cycle.
- ~9s per event × N events. Fills dead time in the 5-min cycle gap.
- Test script: `scripts/test_fanduel_bulk.py` (verified 3/28/26)

### DraftKings (investigated 3/28/26, not viable without proxy)
- Geo-blocked from Texas behind Akamai bot detection.
- Would need a proxy in a legal DK state.

### ESPN (verified 3/28-29/26)
- Scoreboard: game state classification (pre/in/post), team abbreviations
- Predictor endpoint: pregame BPI win probabilities (gameProjection 0-100)
- Probabilities endpoint: live in-game win probabilities (used when game state = "in")
- Both endpoints used in game line HTML generators

### Kalshi Series Tickers

| Lane | Series |
|------|--------|
| `nba_prop` | `KXNBAPTS`, `KXNBAREB`, `KXNBAAST`, `KXNBA3PT`, `KXNBASTL`, `KXNBABLK`, `KXNBATOV` |
| `nba_gl` | `KXNBAGAME` |
| `mlb_gl` | `KXMLBGAME`, `KXMLBSPREAD`, `KXMLBTOTAL` |
| `mlb_prop` | `KXMLBHIT`, `KXMLBHR`, `KXMLBHRR`, `KXMLBKS`, `KXMLBTB` |

---

## Pricing Model

### OddsAPI / Underdog (sportsbook lines)
De-vigged: `devig(over, under) = (over / (over + under), under / (over + under))`
Applied to American odds after converting to raw implied probability.

### Kalshi (game lines)
De-vigged from ask prices: `ks_home = yes_ask / (yes_ask + no_ask)`, `ks_away = no_ask / (yes_ask + no_ask)`
Raw ask prices sum to >1 (vig). De-vig before comparing to sharp sources.

### Kalshi (props)
Executable ask-side: `OVER = yes_ask`, `UNDER = no_ask`

### Polymarket
Executable order-book: `OVER = best_ask`, `UNDER = 1 - best_bid`

### Edge model (game lines)
```python
away_edge = fair_avg_away - kalshi_devigged_away
home_edge = fair_avg_home - kalshi_devigged_home
```
Fair Avg = mean of per-book de-vigged probabilities across all available sportsbooks.

### Kelly criterion
1/3 fractional Kelly, $1000 bankroll.
```python
kelly_frac = (edge / (1.0 - raw_ask)) * (1/3)
kelly_dollars = kelly_frac * bankroll
```
Only shown when edge > 0.

---

## Data Contract

### Important: save everything
- Do NOT filter out 1Q, 1H, or any period-specific props at the data layer.
- All props from all sources get logged for backtesting regardless of period, Kalshi availability, or matching status.
- Filtering for display/actionable alerts is a separate output-layer concern.

### Matching key (props)
```python
key = (lane, normalize(player), stat_type, round(line, 1))
```

---

## Sharp Reference Hierarchy

### Game lines
1. **OddsAPI Fair Avg** (de-vigged consensus across DraftKings, FanDuel, BetMGM, etc.)
2. **ESPN** (BPI predictor pregame, live probabilities in-game)

### Player props
1. **Underdog** (primary — free, bulk, fast)
2. **FanDuel** (cooldown scrape — backtest reference)

Polymarket = display-only. Data still fetched, saved, shown.

---

## HTML Dashboards

All three regenerate automatically at the end of each scan cycle.

| File | Content | Generator |
|------|---------|-----------|
| `data/nba_gl.html` | NBA game lines — sportsbook rows (ranked), ESPN, Fair Avg, Kalshi devigged, Edge, Kelly | `scripts/generate_nba_gl_html.py` |
| `data/mlb_gl.html` | MLB game lines — same structure as NBA | `scripts/generate_mlb_gl_html.py` |
| `data/props.html` | NBA props — grouped by player/stat/line, tabbed by matchup | `scripts/generate_props_html.py` |

### Sportsbook display order (fixed)
DraftKings → FanDuel → BetMGM → BetOnline.ag → Bovada → PointsBet → BetRivers → Caesars

### Game line card structure (per game)
- Per-book rows: ML | No-Vig%
- ESPN row: Win probability (predictor pregame, probabilities live)
- Fair Avg row: Mean of per-book de-vigged probs
- Kalshi row: De-vigged ask-side implied probs
- Edge row: Fair Avg − Kalshi (green if +, red if −)
- Kelly row: Dollar amount at 1/3 Kelly on $1000 bankroll (only shown on + edge side)

### Team logos
- NBA: loaded from `~/Desktop/GLedge-dev/data/team_logos.json` (ESPN CDN URLs, 30 teams)
- MLB: `data/mlb_team_logos.json` (scraped from ESPN 3/29/26, 30 teams)
- Name normalization: OddsAPI "Los Angeles Clippers/Lakers" → logo key "LA Clippers/Lakers"

---

## Shell Aliases (`~/.zshrc`)

| Alias | Command |
|-------|---------|
| `kscan` | `python3 ~/Desktop/KALSHI_API/scripts/scan_loop.py --once` — one cycle + HTML |
| `ksauto` | `caffeinate -i python3 ~/Desktop/KALSHI_API/scripts/scan_loop.py` — continuous 5-min loop |

---

## Project Structure

```
KALSHI_API/
├── CLAUDE.md                        # Project context (this file)
├── HANDOFF.md                       # Session handoff state
├── NEXT_SESSION.md                  # Instructions for next chat
├── AGENTS.md                        # Agent/Codex instructions
├── .gitignore                       # Ignores credentials + __pycache__
├── kalshi_demo.py                   # Authenticated Kalshi client (RSA-PSS signing)
├── demo_credentials.local.yaml      # Demo account credentials (gitignored)
├── oddsapi_credentials.local.yaml   # OddsAPI key (gitignored)
├── scripts/
│   ├── scan_loop.py                 # Concurrent scan loop + auto HTML generation
│   ├── test_espn.py                 # ESPN scoreboard fetch
│   ├── test_oddsapi.py              # OddsAPI fetch (events, props, game lines)
│   ├── test_underdog.py             # Underdog v2 bulk fetch
│   ├── test_polymarket.py           # Polymarket Gamma fetch
│   ├── test_fanduel_bulk.py         # FanDuel event-page fetch (reference)
│   ├── generate_nba_gl_html.py      # NBA game lines HTML dashboard
│   ├── generate_mlb_gl_html.py      # MLB game lines HTML dashboard
│   ├── generate_props_html.py       # NBA props HTML dashboard
│   ├── scrape_mlb_rosters.py        # One-time MLB roster scrape from ESPN
│   ├── check_demo_auth.py           # Quick Kalshi auth validation
│   └── run_kalshi_categories.py     # Ad-hoc Kalshi category explorer
└── data/
    ├── nba_gl.html                  # NBA game lines dashboard (auto-generated)
    ├── mlb_gl.html                  # MLB game lines dashboard (auto-generated)
    ├── props.html                   # NBA props dashboard (auto-generated)
    ├── mlb_team_logos.json          # MLB team logo URLs (ESPN CDN)
    ├── oddsapi_test_dump.json       # Test output from first OddsAPI run
    ├── rosters/
    │   └── mlb_player_team_map.csv  # 780 MLB players (scraped 3/29/26)
    └── scans/
        └── scan_YYYY-MM-DD.jsonl    # Append-only scan data per day
```

---

## What To Carry Over From Old Code (Reference Only)

### Data (into `data/reference/`)
- Historical live prop JSONL: `KSprop/data/live_props/live_props_*.jsonl`
- Edge/alert CSVs: `KSprop/data/edges/`
- NBA roster map: `KSprop/data/rosters/nba_player_team_map.csv`
- Player name overrides

### Code (as reference, rewrite don't copy)
- `KSprop/DataScripts/analyzer.py` — edge/direction/Kelly logic
- `KSprop/DataScripts/player_normalize.py` — name normalization

### Do NOT carry over
- Old loop architecture
- Old Kalshi scrape path (replaced by authenticated client)
- CBBprop
- Old .md session history
- GLedge-dev / KSarb / ONbaEDGE

---

## Non-Negotiable Rules

1. **Save all data for backtesting.** Never skip saving a source row because it doesn't match Kalshi.
2. **No absolute paths inside project code.** Use `ROOT = Path(__file__).resolve().parents[1]` or similar.
3. **Keep docs current.**
4. **Never search for URLs.** User provides URLs directly.
5. **Standalone project.** No runtime imports from old repos.
6. **Python 3.9 compatible.** No `X | Y` union types, no `match` statements. Use `Optional[X]`, `List[X]`.

---

## Stack

- Python 3.9+
- `urllib` for Underdog/Polymarket/ESPN HTTP (proven working)
- `requests` for Kalshi authenticated client and OddsAPI
- `curl_cffi` installed (for future DK proxy work if needed)
- flat JSON/CSV/HTML outputs

---

## Known Issues

1. **OddsAPI free tier burn rate** — per-event prop calls cost ~7 credits each. Props are NOT pulled from OddsAPI in the scan loop. Only game lines (1 credit/sport/cycle).
2. **NBA roster file not yet in this repo** — `data/rosters/nba_player_team_map.csv` needs to be scraped or copied from KSprop. Currently props dashboard uses KSprop's copy.
3. **Props dashboard reads team logos from GLedge-dev** — not self-contained. Should copy NBA logos into this repo.
4. **FanDuel cooldown scrape not yet implemented** — next session priority.
