"""Central configuration. Everything you might need to tune lives here or in env vars."""
import os

# --- Polling cadence -------------------------------------------------------
# How often the background task refreshes the cache from the upstream APIs.
# 20s is far below every rate limit (see README) and feels "live" on screen.
POLL_INTERVAL_SECONDS = int(os.getenv("WC_POLL_INTERVAL", "20"))

# HTTP timeout per upstream request.
HTTP_TIMEOUT_SECONDS = float(os.getenv("WC_HTTP_TIMEOUT", "12"))

# --- Polymarket ------------------------------------------------------------
# Gamma is fully public, no auth. World Cup matches are individual events under
# the World Cup sports tag, so we discover them by tag (default below). You can
# additionally name specific event slugs (e.g. "world-cup-winner") to pin extra
# markets — comma separated. Leave blank to rely purely on the tag.
POLYMARKET_GAMMA = "https://gamma-api.polymarket.com"
POLYMARKET_EVENT_SLUGS = [
    s.strip() for s in os.getenv("WC_PM_SLUGS", "").split(",") if s.strip()
]
# Sports tag slug for the World Cup. The fetcher also tries a few alternate
# spellings automatically, so this rarely needs changing.
POLYMARKET_TAG_SLUG = os.getenv("WC_PM_TAG", "world-cup").strip()

# Individual GAMES are attached to a sports *series*, not the tag. We resolve the
# series id from /sports using this sport code (Polymarket's code for the FIFA
# World Cup). Override the resolved id(s) directly with WC_PM_SERIES if needed.
POLYMARKET_SPORT_CODE = os.getenv("WC_PM_SPORT", "fifwc").strip()
POLYMARKET_SERIES_IDS = [
    s.strip() for s in os.getenv("WC_PM_SERIES", "").split(",") if s.strip()
]

# --- Kalshi ----------------------------------------------------------------
# Public market-data endpoints (no auth needed for reads). These are the
# confirmed 2026 World Cup series tickers; override/extend via WC_KALSHI_SERIES.
#   KXWCGAME       individual match moneyline (win / draw / win)
#   KXMENWORLDCUP  tournament winner (all 48 teams)
#   KXWCGROUPWIN   group winners (groups A-L)
#   KXWC1STTIMEWIN first-time winner
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_SERIES_TICKERS = [
    s.strip()
    for s in os.getenv(
        "WC_KALSHI_SERIES", "KXWCGAME,KXMENWORLDCUP,KXWCGROUPWIN,KXWC1STTIMEWIN"
    ).split(",")
    if s.strip()
]
# If you have credentials and hit auth issues on reads, set these (optional).
KALSHI_API_KEY_ID = os.getenv("WC_KALSHI_KEY_ID", "")

# Toggle sources off entirely if one is misconfigured.
ENABLE_POLYMARKET = os.getenv("WC_ENABLE_PM", "1") != "0"
ENABLE_KALSHI = os.getenv("WC_ENABLE_KALSHI", "1") != "0"
