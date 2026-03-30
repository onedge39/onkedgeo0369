#!/usr/bin/env python3
"""
Publish docs/ and push the latest GitHub Pages site.

This is intentionally separate from the scan loop so local scans do not create a
new git commit every cycle unless explicitly requested.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from publish_site import publish

ROOT = Path(__file__).resolve().parents[1]
CT = ZoneInfo("America/Chicago")


def run_git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def main() -> None:
    publish()
    run_git(["add", "docs", "README.md"])

    status = run_git(["status", "--short"])
    if not status:
        print("No docs changes to deploy.")
        return

    stamp = datetime.now(CT).strftime("%Y-%m-%d %H:%M CT")
    message = f"Update Pages site {stamp}"
    run_git(["commit", "-m", message])
    run_git(["push", "origin", "main"])
    print(message)


if __name__ == "__main__":
    main()
