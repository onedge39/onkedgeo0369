"""
Test FanDuel fetch — uses the exact same working path as KSprop/DataScripts/fanduel.py.

1. Discovery POST to find NBA competition + events
2. One event-page GET per event per tab slug (6 tabs)
3. Dumps raw JSON and prints summary

Usage:
    python3 scripts/test_fanduel_bulk.py
    python3 scripts/test_fanduel_bulk.py --event-limit 1
    python3 scripts/test_fanduel_bulk.py --dump data/raw_fd_nba.json
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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DISCOVERY_URL = "https://scan.il.sportsbook.fanduel.com/api/sports/navigation/facet/v1.0/search"
DISCOVERY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "X-Application": "FhMFpcPWXMeyZxOx",
    "Origin": "https://sportsbook.fanduel.com",
    "Referer": "https://sportsbook.fanduel.com/",
}

EVENT_PAGE_URL = "https://sbapi.il.sportsbook.fanduel.com/api/event-page"
EVENT_PAGE_PARAMS = {
    "betexRegion": "GBR",
    "capiJurisdiction": "intl",
    "currencyCode": "USD",
    "exchangeLocale": "en_US",
    "language": "en",
    "regionCode": "NAMERICA",
    "_ak": "FhMFpcPWXMeyZxOx",
    "includePrices": "true",
    "priceHistory": "1",
}
EVENT_PAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://sportsbook.fanduel.com/",
    "Origin": "https://sportsbook.fanduel.com",
}

SLUGS = [
    "player-points",
    "player-assists",
    "player-rebounds",
    "player-threes",
    "player-combos",
    "player-defense",
]

SLUG_DELAY = 1.5
REQUEST_TIMEOUT = 30


def _http_post(url: str, body: dict, headers: dict) -> Any:
    raw = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=raw, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read())


def _http_get(url: str, params: dict, headers: dict) -> Any:
    request_url = url + "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(request_url, headers=headers)
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read())


def discover_events(pre_tip_only: bool = False) -> List[Dict[str, Any]]:
    # Step 1: find competition IDs
    body = {
        "textQuery": {"query": "nba", "facetsToSearch": ["COMPETITION"]},
        "filter": {
            "eventTypeIds": [],
            "productTypes": ["SPORTSBOOK"],
            "selectBy": "RANK",
            "contentGroup": {"language": "en", "regionCode": "NAMERICA"},
            "maxResults": 0,
            "attachments": [],
        },
        "facets": [{"type": "COMPETITION", "next": {"type": "EVENT_TYPE"}}],
        "currencyCode": "USD",
    }
    data = _http_post(DISCOVERY_URL, body, DISCOVERY_HEADERS)
    competitions = (data.get("attachments") or {}).get("competitions") or {}
    comp_ids = []
    for comp in competitions.values():
        name = re.sub(r"\s+", " ", (comp.get("name") or "")).strip()
        region = (comp.get("region") or "").upper()
        if name == "NBA" and region == "USA":
            cid = comp.get("competitionId")
            if cid:
                comp_ids.append(int(cid))
    comp_ids = sorted(set(comp_ids))
    print(f"  Discovery: {len(comp_ids)} NBA competition(s)")

    # Step 2: fetch events per competition
    all_events = {}
    all_in_play = {}
    for cid in comp_ids:
        body2 = {
            "filter": {
                "competitionIds": [cid],
                "contentGroup": {"language": "en", "regionCode": "NAMERICA"},
                "maxResults": 0,
                "productTypes": ["SPORTSBOOK"],
                "selectBy": "FIRST_TO_START",
            },
            "facets": [
                {"type": "COMPETITION"},
                {"type": "EVENT", "next": {"type": "IN_PLAY"}},
            ],
            "currencyCode": "USD",
        }
        data2 = _http_post(DISCOVERY_URL, body2, DISCOVERY_HEADERS)
        events = (data2.get("attachments") or {}).get("events") or {}
        all_events.update(events)
        for facet in data2.get("facets") or []:
            if (facet.get("type") or "").upper() != "EVENT":
                continue
            for val in facet.get("values") or []:
                eid = (val.get("key") or {}).get("eventId")
                if not eid:
                    continue
                nv = ((val.get("next") or {}).get("values") or [])
                if nv:
                    raw = nv[0].get("value")
                    if isinstance(raw, bool):
                        all_in_play[str(eid)] = raw
                    elif raw is not None:
                        all_in_play[str(eid)] = str(raw).strip().lower() == "true"

    now_utc = datetime.now(timezone.utc)
    results = []
    for eid, ev in all_events.items():
        name = re.sub(r"\s+", " ", (ev.get("name") or "")).strip()
        if " @ " not in name:
            continue
        if pre_tip_only:
            if all_in_play.get(str(eid)) is True:
                continue
            od = ev.get("openDate", "")
            if od:
                text = od.rstrip("Z") + "+00:00" if od.endswith("Z") else od
                try:
                    dt = datetime.fromisoformat(text)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt <= now_utc:
                        continue
                except Exception:
                    pass
        results.append({
            "event_id": str(eid),
            "name": name,
            "open_date": ev.get("openDate"),
            "in_play": all_in_play.get(str(eid), False),
        })
    return results


def fetch_event_tabs(event_id: str) -> Dict[str, Any]:
    """Fetch all 6 tabs for one event. Returns {slug: raw_response}."""
    results = {}
    for idx, slug in enumerate(SLUGS):
        params = dict(EVENT_PAGE_PARAMS)
        params["eventId"] = event_id
        params["tab"] = slug
        try:
            data = _http_get(EVENT_PAGE_URL, params, EVENT_PAGE_HEADERS)
            results[slug] = data
        except Exception as e:
            results[slug] = {"error": str(e)}
        if idx < len(SLUGS) - 1:
            time.sleep(SLUG_DELAY)
    return results


def summarize_tab(slug: str, data: Dict[str, Any]) -> Dict[str, int]:
    if "error" in data:
        return {"error": 1}
    markets = (data.get("attachments") or {}).get("markets") or {}
    total_runners = sum(len(m.get("runners", [])) for m in markets.values())
    return {"markets": len(markets), "runners": total_runners}


def main() -> int:
    parser = argparse.ArgumentParser(description="Test FanDuel fetch (known-working path)")
    parser.add_argument("--event-limit", type=int, default=0, help="Max events to fetch (0 = all)")
    parser.add_argument("--pre-tip-only", action="store_true", help="Only fetch pre-tip events")
    parser.add_argument("--dump", help="Write raw JSON to this file")
    args = parser.parse_args()

    t0 = time.time()
    print("Discovering NBA events...")
    events = discover_events(pre_tip_only=args.pre_tip_only)
    print(f"  Found {len(events)} game event(s)")
    for ev in events:
        print(f"    {ev['event_id']}: {ev['name']}  inPlay={ev['in_play']}  open={ev['open_date']}")

    if args.event_limit:
        events = events[: args.event_limit]
        print(f"  (limited to {args.event_limit})")

    all_results = {}
    for ev in events:
        eid = ev["event_id"]
        print(f"\n  Fetching {ev['name']} ({eid})...")
        tab_data = fetch_event_tabs(eid)
        for slug in SLUGS:
            s = summarize_tab(slug, tab_data.get(slug, {}))
            print(f"    {slug:20s}  {s}")
        all_results[eid] = {"event": ev, "tabs": tab_data}

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")

    if args.dump:
        dump_path = Path(args.dump).expanduser()
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        dump_path.write_text(
            json.dumps(all_results, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote: {dump_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
