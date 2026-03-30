from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo


ROOT = Path("/Users/kylejones/Desktop/KALSHI_API")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kalshi_demo import DEFAULT_CREDENTIALS_PATH, load_demo_credentials, send_demo_request


LOCAL_TZ = ZoneInfo("America/Chicago")

CATEGORY_SERIES = OrderedDict(
    [
        (
            "nba_prop",
            [
                "KXNBAPTS",
                "KXNBAREB",
                "KXNBAAST",
                "KXNBA3PT",
                "KXNBASTL",
                "KXNBABLK",
                "KXNBATOV",
            ],
        ),
        ("nba_gl", ["KXNBAGAME"]),
        ("mlb_gl", ["KXMLBGAME", "KXMLBSPREAD", "KXMLBTOTAL"]),
        ("mlb_prop", ["KXMLBHIT", "KXMLBHR", "KXMLBHRR", "KXMLBKS", "KXMLBTB"]),
    ]
)


def _today_ticker_str() -> str:
    return datetime.now(LOCAL_TZ).strftime("%y%b%d").upper()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Authenticated Kalshi category runner for nba_prop, nba_gl, mlb_gl, mlb_prop.",
    )
    parser.add_argument(
        "--category",
        choices=list(CATEGORY_SERIES.keys()) + ["all"],
        default="all",
        help="Category to fetch. Use 'all' to run every category.",
    )
    parser.add_argument(
        "--credentials",
        default=str(DEFAULT_CREDENTIALS_PATH),
        help="Path to Kalshi credentials YAML.",
    )
    parser.add_argument(
        "--date-prefix",
        default=_today_ticker_str(),
        help="Filter returned markets by ticker containing this YYMONDD prefix. Use '' to disable.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Per-page market request limit.",
    )
    parser.add_argument(
        "--status",
        default="open",
        help="Market status filter passed to /markets.",
    )
    parser.add_argument(
        "--output",
        help="Optional JSON file to write full results.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the authenticated request plan without calling the API.",
    )
    return parser.parse_args()


def _iter_categories(category_arg: str) -> Iterable[str]:
    if category_arg == "all":
        return CATEGORY_SERIES.keys()
    return [category_arg]


def _filter_markets(markets: List[Dict[str, Any]], date_prefix: str) -> List[Dict[str, Any]]:
    if not date_prefix:
        return markets
    prefix = str(date_prefix).upper().strip()
    if not prefix:
        return markets
    kept = []
    for market in markets:
        ticker = str(market.get("ticker") or "").upper()
        event_ticker = str(market.get("event_ticker") or "").upper()
        if prefix in ticker or prefix in event_ticker:
            kept.append(market)
    return kept


def _paged_markets_for_series(
    series_ticker: str,
    *,
    credentials_path: Path,
    status: str,
    limit: int,
    date_prefix: str,
) -> Dict[str, Any]:
    cursor: Optional[str] = None
    seen = set()
    all_markets: List[Dict[str, Any]] = []
    pages: List[Dict[str, Any]] = []

    while True:
        params: Dict[str, str] = {
            "series_ticker": series_ticker,
            "status": status,
            "limit": str(limit),
        }
        if cursor:
            params["cursor"] = cursor

        response = send_demo_request(
            "GET",
            "/markets",
            params=params,
            credentials_path=credentials_path,
        )
        response.raise_for_status()
        payload = response.json()

        page_markets = payload.get("markets") or []
        if isinstance(page_markets, list):
            for market in page_markets:
                if isinstance(market, dict):
                    all_markets.append(market)

        pages.append(
            {
                "request_params": dict(params),
                "market_count": len(page_markets) if isinstance(page_markets, list) else 0,
                "cursor": payload.get("cursor"),
            }
        )

        cursor = payload.get("cursor")
        if not cursor or cursor in seen:
            break
        seen.add(cursor)

    filtered_markets = _filter_markets(all_markets, date_prefix)
    return {
        "series_ticker": series_ticker,
        "pages": pages,
        "markets_all": all_markets,
        "markets_filtered": filtered_markets,
        "summary": {
            "page_count": len(pages),
            "market_count_all": len(all_markets),
            "market_count_filtered": len(filtered_markets),
        },
    }


def _category_request_plan(category: str, *, status: str, limit: int, date_prefix: str) -> Dict[str, Any]:
    return {
        "category": category,
        "endpoint": "/markets",
        "series_tickers": CATEGORY_SERIES[category],
        "params_template": {
            "status": status,
            "limit": limit,
            "series_ticker": "<one series ticker per request>",
            "cursor": "<set on paginated follow-up pages>",
        },
        "date_prefix_filter": date_prefix,
    }


def _run_category(
    category: str,
    *,
    credentials_path: Path,
    status: str,
    limit: int,
    date_prefix: str,
    dry_run: bool,
) -> Dict[str, Any]:
    series_tickers = CATEGORY_SERIES[category]

    if dry_run:
        return {
            "category": category,
            "mode": "dry_run",
            "plan": _category_request_plan(category, status=status, limit=limit, date_prefix=date_prefix),
        }

    series_results = []
    for series_ticker in series_tickers:
        series_results.append(
            _paged_markets_for_series(
                series_ticker,
                credentials_path=credentials_path,
                status=status,
                limit=limit,
                date_prefix=date_prefix,
            )
        )

    return {
        "category": category,
        "mode": "live_request",
        "series": series_results,
        "summary": {
            "series_count": len(series_results),
            "market_count_all": sum(item["summary"]["market_count_all"] for item in series_results),
            "market_count_filtered": sum(item["summary"]["market_count_filtered"] for item in series_results),
        },
    }


def _print_summary(results: Dict[str, Any], credentials_path: Path) -> None:
    credentials = load_demo_credentials(credentials_path)
    print("Kalshi base_url:", credentials.base_url)
    print("Credentials file:", credentials_path)
    print()
    for category, payload in results["categories"].items():
        print("[{}]".format(category))
        if payload.get("mode") == "dry_run":
            plan = payload["plan"]
            print("  mode: dry_run")
            print("  endpoint:", plan["endpoint"])
            print("  series:", ", ".join(plan["series_tickers"]))
            print("  date_prefix_filter:", plan["date_prefix_filter"] or "<disabled>")
        else:
            summary = payload["summary"]
            print("  series_count:", summary["series_count"])
            print("  market_count_all:", summary["market_count_all"])
            print("  market_count_filtered:", summary["market_count_filtered"])
            for series_result in payload["series"]:
                s = series_result["summary"]
                print(
                    "   - {}: all={} filtered={} pages={}".format(
                        series_result["series_ticker"],
                        s["market_count_all"],
                        s["market_count_filtered"],
                        s["page_count"],
                    )
                )
        print()


def main() -> int:
    args = _parse_args()
    credentials_path = Path(args.credentials).expanduser().resolve()

    results: Dict[str, Any] = {
        "generated_at": datetime.now(LOCAL_TZ).isoformat(),
        "categories": OrderedDict(),
        "options": {
            "category": args.category,
            "date_prefix": args.date_prefix,
            "limit": args.limit,
            "status": args.status,
            "dry_run": args.dry_run,
        },
    }

    for category in _iter_categories(args.category):
        results["categories"][category] = _run_category(
            category,
            credentials_path=credentials_path,
            status=args.status,
            limit=args.limit,
            date_prefix=args.date_prefix,
            dry_run=args.dry_run,
        )

    _print_summary(results, credentials_path)

    if args.output:
        output_path = Path(args.output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("Wrote:", output_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
