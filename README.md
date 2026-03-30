# onkedgeo0369

GitHub Pages wrapper for the KALSHI phone-facing dashboards.

Published layout:

- `docs/index.html` — tabbed launcher for all dashboards
- `docs/nba_gl.html`
- `docs/mlb_gl.html`
- `docs/props.html`
- `docs/mlb_props.html`

Local workflow:

```zsh
python3 /Users/kylejones/Desktop/KALSHI_API/scripts/scan_loop.py --once
```

The scan loop regenerates the HTML dashboards and then exports the latest copies
into `docs/` for GitHub Pages.

Manual publish refresh without running the loop:

```zsh
python3 /Users/kylejones/Desktop/KALSHI_API/scripts/publish_site.py
```

GitHub Pages:

1. Push this repo to GitHub.
2. In repo settings, enable GitHub Pages from the `main` branch and `/docs` folder.
3. The site URL will be:

`https://onedge39.github.io/onkedgeo0369/`

Note: this is public if someone has the URL. The obscure repo name only reduces
casual discovery; it does not make the site private.
