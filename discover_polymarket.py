"""Show what the dashboard pulls from Polymarket: the World Cup GAMES (via the
sports series) plus the futures (via the tag).

    python discover_polymarket.py

Reads the public Gamma API — no auth. Uses the OS trust store so it works on
corporate networks that intercept TLS.
"""
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

import requests

GAMMA = "https://gamma-api.polymarket.com"
SPORT = "fifwc"  # Polymarket's code for the FIFA World Cup


def get(path, **params):
    r = requests.get(f"{GAMMA}{path}", params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def series_ids():
    ids = []
    for s in get("/sports"):
        code = (s.get("sport") or "").lower()
        if code == SPORT or SPORT in code:
            sid = s.get("series")
            if sid:
                ids += [x.strip() for x in str(sid).split(",") if x.strip()]
    return ids


def page_all(**params):
    out, offset = [], 0
    while True:
        page = get("/events", closed="false", limit=100, offset=offset, **params)
        out.extend(page)
        if len(page) < 100:
            return out
        offset += 100


def main():
    sids = series_ids()
    print(f"World Cup series id(s) for sport '{SPORT}': {sids or 'NONE FOUND'}\n")

    games = []
    for sid in sids:
        games += page_all(series_id=sid)
    print(f"GAMES via series: {len(games)} events")
    for ev in games[:20]:
        print(f"  {ev.get('title','?')[:48]:<50} /{ev.get('slug','')}")
    if len(games) > 20:
        print(f"  ... and {len(games) - 20} more")

    futures = page_all(tag_slug="world-cup", related_tags="true")
    print(f"\nFUTURES via tag 'world-cup': {len(futures)} events (sample)")
    for ev in futures[:10]:
        print(f"  {ev.get('title','?')[:48]:<50} /{ev.get('slug','')}")


if __name__ == "__main__":
    main()
