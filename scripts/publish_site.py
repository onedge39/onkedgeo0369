#!/usr/bin/env python3
"""
Publish the generated dashboards into docs/ for GitHub Pages.

This keeps the heavy HTML outputs in data/ for local generation while exporting
only the phone-facing site artifacts into docs/.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Tuple
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
CT = ZoneInfo("America/Chicago")

PAGES: List[Tuple[str, str, str, str]] = [
    ("nba-gl", "NBA GL", "nba_gl.html", "NBA game lines"),
    ("mlb-gl", "MLB GL", "mlb_gl.html", "MLB game lines"),
    ("nba-props", "NBA Props", "props.html", "NBA props"),
    ("mlb-props", "MLB Props", "mlb_props.html", "MLB props"),
]


def _fmt_ct(ts: datetime) -> str:
    return ts.astimezone(CT).strftime("%b %d, %Y %I:%M %p CT").replace(" 0", " ")


def _build_index_html(generated_at: datetime) -> str:
    default_slug = PAGES[0][0]
    pages_json = ",\n".join(
        f'      "{slug}": {{"label": "{label}", "file": "{filename}", "summary": "{summary}"}}'
        for slug, label, filename, summary in PAGES
    )
    links_html = "\n".join(
        f'        <button class="tab" type="button" data-page="{slug}">{label}</button>'
        for slug, label, _, _ in PAGES
    )
    menu_html = "\n".join(
        f'          <a href="{filename}" target="_blank" rel="noopener">{label}</a>'
        for _, label, filename, _ in PAGES
    )
    generated_label = _fmt_ct(generated_at)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>onkedgeo0369</title>
  <style>
    :root {{
      --bg: #0e1621;
      --panel: #172231;
      --panel-alt: #1f2d40;
      --line: #2e4159;
      --text: #eef4fb;
      --muted: #a8b6c7;
      --accent: #4ecdc4;
      --accent-2: #ffd166;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #0a121c 0%, #0f1824 100%);
      color: var(--text);
    }}
    a {{ color: var(--accent); }}
    .shell {{
      min-height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr;
    }}
    .header {{
      position: sticky;
      top: 0;
      z-index: 10;
      padding: 14px 16px 12px;
      background: rgba(10, 18, 28, 0.94);
      backdrop-filter: blur(10px);
      border-bottom: 1px solid var(--line);
    }}
    .topline {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 10px;
    }}
    h1 {{
      margin: 0;
      font-size: 18px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .meta {{
      font-size: 12px;
      color: var(--muted);
    }}
    .controls {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 8px;
    }}
    .tab {{
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      border-radius: 999px;
      padding: 9px 12px;
      font-size: 13px;
      cursor: pointer;
    }}
    .tab.active {{
      background: var(--accent);
      border-color: var(--accent);
      color: #072129;
      font-weight: 700;
    }}
    .summary {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px;
      font-size: 12px;
      color: var(--muted);
    }}
    .summary strong {{
      color: var(--accent-2);
      font-weight: 700;
    }}
    .menu {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .viewer {{
      min-height: 0;
      padding: 0;
    }}
    iframe {{
      width: 100%;
      height: calc(100vh - 128px);
      border: 0;
      background: white;
    }}
    @media (max-width: 720px) {{
      iframe {{
        height: calc(100vh - 168px);
      }}
    }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="header">
      <div class="topline">
        <h1>onkedgeo0369</h1>
        <div class="meta">Published from <code>docs/</code> • refreshed {generated_label}</div>
      </div>
      <div class="controls">
{links_html}
      </div>
      <div class="summary">
        <strong id="page-label">NBA GL</strong>
        <span id="page-summary">NBA game lines</span>
        <span class="menu">
{menu_html}
        </span>
      </div>
    </div>
    <div class="viewer">
      <iframe id="frame" title="Dashboard viewer" loading="eager"></iframe>
    </div>
  </div>
  <script>
    const pages = {{
{pages_json}
    }};
    const tabs = Array.from(document.querySelectorAll(".tab"));
    const frame = document.getElementById("frame");
    const label = document.getElementById("page-label");
    const summary = document.getElementById("page-summary");

    function currentSlug() {{
      const fromHash = window.location.hash.replace(/^#/, "");
      if (pages[fromHash]) return fromHash;
      return "{default_slug}";
    }}

    function setPage(slug, updateHash = true) {{
      const page = pages[slug] || pages["{default_slug}"];
      frame.src = page.file;
      label.textContent = page.label;
      summary.textContent = page.summary;
      tabs.forEach((tab) => {{
        tab.classList.toggle("active", tab.dataset.page === slug);
      }});
      if (updateHash) {{
        window.location.hash = slug;
      }}
    }}

    tabs.forEach((tab) => {{
      tab.addEventListener("click", () => setPage(tab.dataset.page));
    }});

    window.addEventListener("hashchange", () => setPage(currentSlug(), false));
    setPage(currentSlug(), false);
  </script>
</body>
</html>
"""


def publish() -> List[Path]:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []

    for _, _, filename, _ in PAGES:
        src = DATA_DIR / filename
        if not src.exists():
            raise FileNotFoundError(f"missing source HTML: {src}")
        dst = DOCS_DIR / filename
        shutil.copy2(src, dst)
        written.append(dst)

    index_path = DOCS_DIR / "index.html"
    index_path.write_text(_build_index_html(datetime.now(CT)), encoding="utf-8")
    written.append(index_path)

    nojekyll_path = DOCS_DIR / ".nojekyll"
    nojekyll_path.write_text("", encoding="utf-8")
    written.append(nojekyll_path)

    return written


def main() -> None:
    written = publish()
    print(f"Published {len(written)} site files to {DOCS_DIR}")


if __name__ == "__main__":
    main()
