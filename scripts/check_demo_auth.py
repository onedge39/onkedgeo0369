from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kalshi_demo import DEFAULT_CREDENTIALS_PATH, send_demo_request


def main() -> int:
    credentials_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CREDENTIALS_PATH
    response = send_demo_request(
        "GET",
        "/portfolio/balance",
        credentials_path=credentials_path,
    )

    print(f"status_code={response.status_code}")
    try:
        body = response.json()
    except json.JSONDecodeError:
        print(response.text)
        return 1 if not response.ok else 0

    print(json.dumps(body, indent=2, sort_keys=True))
    return 0 if response.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
