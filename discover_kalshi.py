"""Find the Kalshi series ticker(s) for the 2026 World Cup.

Kalshi groups markets under series tickers (e.g. a winner series + per-match
series). I couldn't verify the exact tickers offline, so run this once to list
soccer / World Cup series, then set them in WC_KALSHI_SERIES.

    python discover_kalshi.py
    python discover_kalshi.py "world cup"   # filter term (default)

Reads only — no auth required.
"""
import sys
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass
import requests

BASE = "https://api.elections.kalshi.com/trade-api/v2"


def main():
    term = (sys.argv[1] if len(sys.argv) > 1 else "world cup").lower()
    cursor = ""
    hits = []
    for _ in range(50):
        params = {"limit": 200}
        if cursor:
            params["cursor"] = cursor
        r = requests.get(f"{BASE}/series", params=params, timeout=15)
        if r.status_code != 200:
            # Some deployments expose /series/list; try events as a fallback.
            break
        data = r.json()
        for s in data.get("series", []):
            blob = f"{s.get('ticker','')} {s.get('title','')} {s.get('category','')}".lower()
            if term in blob:
                hits.append(s)
        cursor = data.get("cursor") or ""
        if not cursor:
            break

    if not hits:
        print(f"No series matched '{term}'. Try a broader term, e.g. 'soccer'.")
        print("You can also browse https://kalshi.com to find the market, then")
        print("copy the series ticker from the URL.")
        return

    print(f"Series matching '{term}':\n")
    for s in hits:
        print(f"  {s.get('ticker'):<24} {s.get('title','')}")
    print("\nSet them like:")
    print(f"  export WC_KALSHI_SERIES={','.join(s.get('ticker') for s in hits)}")


if __name__ == "__main__":
    main()
