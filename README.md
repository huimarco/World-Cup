# World Cup 2026 — Live Odds Dashboard

Dark, interactive dashboard showing live 2026 World Cup odds from **Polymarket**
and **Kalshi**. Python (FastAPI) backend + a self-contained HTML frontend.

## Files (keep them all in ONE folder)

| File | Purpose |
|------|---------|
| `app.py` | FastAPI server: background poller, cache, `/api/odds`, serves the dashboard |
| `sources.py` | Fetches + normalizes Polymarket & Kalshi; merges the same match across both |
| `config.py` | All settings (tickers, intervals) and env-var overrides |
| `index.html` | The dashboard UI (must sit next to `app.py`) |
| `requirements.txt` | Python dependencies |
| `discover_kalshi.py` | Optional: list Kalshi World Cup series tickers |
| `discover_polymarket.py` | Optional: verify the Polymarket World Cup tag |

## Run it

From inside the folder that contains these files:

```bash
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
```

Then open **http://localhost:8000**

> Note the command is `app:app` (all files are in one folder), not `backend.app:app`.

Everything works with **no configuration** — the Polymarket World Cup tag and
the four Kalshi series tickers are built in. Within a few seconds you should see
match cards populate; Polymarket shows in purple, Kalshi in teal, and matches
that exist on both merge into one card.

## How it stays under rate limits

A single background task refreshes an in-memory cache every 20s. Every browser
tab reads *your* `/api/odds`, so upstream request volume is constant no matter
how many viewers — far below both platforms' public limits. Both use
authentication-free, public market-data endpoints.

## Built-in market coverage

**Polymarket:** discovered by the `world-cup` sports tag (auto-tries alternate
spellings); keeps each match's moneyline market (win/draw/win) plus the group
and tournament-winner futures.

**Kalshi series tickers** (pre-filled in `config.py`):

| Ticker | Markets |
|--------|---------|
| `KXWCGAME` | Individual match moneyline (win/draw/win) |
| `KXMENWORLDCUP` | Tournament winner (48 teams) |
| `KXWCGROUPWIN` | Group winners (A–L) |
| `KXWC1STTIMEWIN` | First-time winner |

## Features

- Filter by source (All / Polymarket / Kalshi / Both), search by team, sort by
  kickoff, volume, or biggest cross-market disagreement.
- Probabilities flash green/red as they move between refreshes.
- Live/stale indicator and per-source error banner.

## Troubleshooting

- **`No module named 'app'`** → run `uvicorn` from the folder that contains
  these files.
- **`CERTIFICATE_VERIFY_FAILED`** (corporate/Windows) → `pip install truststore
  certifi` (already in requirements); `app.py` uses the OS trust store.
- **Python 3.14 install errors on aiohttp/pydantic** → use Python 3.12 and
  recreate the virtual environment there.
- **Kalshi empty** → run `python discover_kalshi.py` to confirm series tickers,
  then `WC_KALSHI_SERIES=...` (PowerShell: `$env:WC_KALSHI_SERIES="..."`).

## Environment overrides (optional)

| Var | Default | Meaning |
|-----|---------|---------|
| `WC_POLL_INTERVAL` | `20` | Seconds between refreshes |
| `WC_PM_TAG` | `world-cup` | Polymarket tag slug |
| `WC_PM_SLUGS` | — | Extra Polymarket event slugs (comma-sep) |
| `WC_KALSHI_SERIES` | the 4 above | Kalshi series tickers (comma-sep) |
| `WC_ENABLE_PM` / `WC_ENABLE_KALSHI` | `1` | Set `0` to disable a source |

## To-Do

- [x] Use actual **ask price** (cost to execute a position, not mid/last)
- [ ] Apply **platform fee haircut** when comparing prices across Polymarket and Kalshi

*Not affiliated with FIFA, Kalshi, or Polymarket. Informational only — not
trading advice.*
