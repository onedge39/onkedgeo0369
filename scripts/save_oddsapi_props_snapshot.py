#!/usr/bin/env python3
"""
Fetch OddsAPI player props on demand and save a flat snapshot for props HTML.

Usage:
    python3 scripts/save_oddsapi_props_snapshot.py
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from test_oddsapi import fetch_event_props, fetch_events, load_api_key

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "data" / "oddsapi_props_latest.json"
LOCAL_TZ = ZoneInfo("America/Chicago")


def build_snapshot() -> dict:
    api_key = load_api_key()
    events = fetch_events(api_key)

    props = []
    for ev in events:
        eid = ev.get("id", "")
        data = fetch_event_props(api_key, eid)
        if not data:
            continue
        for bk in data.get("bookmakers", []):
            for market in bk.get("markets", []):
                for outcome in market.get("outcomes", []):
                    props.append({
                        "event_id": eid,
                        "home_team": ev.get("home_team"),
                        "away_team": ev.get("away_team"),
                        "bookmaker": bk.get("key"),
                        "market": market.get("key"),
                        "player": outcome.get("description"),
                        "name": outcome.get("name"),
                        "price": outcome.get("price"),
                        "point": outcome.get("point"),
                    })

    return {
        "saved_ts": datetime.now(LOCAL_TZ).isoformat(),
        "source_file": OUT_PATH.name,
        "source_scan_file": None,
        "source_cycle": None,
        "props_count": len(props),
        "props": props,
    }


def main() -> int:
    t0 = time.time()
    snapshot = build_snapshot()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    elapsed = time.time() - t0
    print("Written: {}".format(OUT_PATH))
    print("Saved ts: {}".format(snapshot["saved_ts"]))
    print("Props: {}".format(snapshot["props_count"]))
    print("Elapsed: {:.2f}s".format(elapsed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
