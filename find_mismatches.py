"""
Find outcome-label mismatches between Polymarket and Kalshi.

For every match that appears on BOTH platforms, this script prints the
raw outcome labels from each source side by side so you can spot any
that aren't consolidating.

    python find_mismatches.py

Uses the OS trust store so it works on corporate/Windows networks.
No API keys needed.
"""
try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

import json, re, unicodedata
import requests

# ── single normalisation table — keep in sync with sources.py ────────────────
# Key  = stripped variant (lowercase ASCII no-spaces)
# Value = canonical display name
# To add a mismatch: one line here AND in sources.py _TEAM_NAMES.
_TEAM_NAMES = {
    "usa":                              "United States",
    "unitedstates":                     "United States",
    "unitedstatesofamerica":            "United States",
    "turkiye":                          "Türkiye",
    "turkey":                           "Türkiye",
    "curacao":                          "Curaçao",
    "korearepublic":                    "Korea Republic",
    "southkorea":                       "Korea Republic",
    "korea":                            "Korea Republic",
    "republicofkorea":                  "Korea Republic",
    "southkorearepublic":               "Korea Republic",
    "cotedivoire":                      "Côte d'Ivoire",
    "ivorycoast":                       "Côte d'Ivoire",
    "bosniaherzegovina":                "Bosnia-Herzegovina",
    "bosniaandherzegovina":             "Bosnia-Herzegovina",
    "iran":                             "IR Iran",
    "iriran":                           "IR Iran",
    "iranislamicrepublic":              "IR Iran",
    "caboverde":                        "Cabo Verde",
    "capeverde":                        "Cabo Verde",
    "czechia":                          "Czechia",
    "czechrepublic":                    "Czechia",
    "congodr":                          "DR Congo",
    "drcongo":                          "DR Congo",
    "democraticrepublicofthecongo":     "DR Congo",
    "congo":                            "DR Congo",
}

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA       = "https://gamma-api.polymarket.com"
KALSHI_GAME_SERIES = "KXWCGAME"


# ── helpers ──────────────────────────────────────────────────────────────────
def _strip(name):
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"\b(national team|nat\.?|fc|the)\b", "", s)
    s = re.sub(r"[^a-z0-9]", "", s)
    return s

def norm_team(name):
    raw = _strip(name)
    display = _TEAM_NAMES.get(raw, name.strip())
    return _strip(display)

def norm_outcome(label):
    s = re.sub(r"\s*\(.*\)\s*$", "", str(label)).strip()
    if s.lower() in ("tie", "draw", "draw/tie"):
        return "Draw"
    return _TEAM_NAMES.get(_strip(s), s)

def match_key(title):
    m = re.split(r"\s+(?:vs?\.?|@|—|–|-)\s+", title, maxsplit=1, flags=re.I)
    if len(m) == 2:
        a, b = sorted([norm_team(m[0]), norm_team(m[1])])
        return f"{a}|{b}"
    return norm_team(title)

def parse_json_list(raw):
    if isinstance(raw, list): return raw
    try: v = json.loads(raw); return v if isinstance(v, list) else []
    except: return []

def get(url, **params):
    r = requests.get(url, params=params or None, timeout=20)
    r.raise_for_status()
    return r.json()


# ── fetch Polymarket game outcomes ───────────────────────────────────────────
def fetch_pm_outcomes():
    """Returns {match_key: set_of_raw_labels}"""
    series_ids = []
    for s in get(f"{GAMMA}/sports"):
        if "fifwc" in (s.get("sport") or "").lower():
            sid = s.get("series")
            if sid:
                series_ids += [x.strip() for x in str(sid).split(",") if x.strip()]

    out = {}
    for sid in series_ids:
        offset = 0
        while True:
            events = get(f"{GAMMA}/events", series_id=sid, closed="false", limit=100, offset=offset)
            for ev in events:
                title = ev.get("title") or ""
                labels = set()
                for m in ev.get("markets") or []:
                    if m.get("closed"): continue
                    git = (m.get("groupItemTitle") or "").strip()
                    if git:
                        labels.add(git)
                    else:
                        for o in parse_json_list(m.get("outcomes")):
                            labels.add(str(o))
                if labels:
                    out[match_key(title)] = {"title": title, "labels": labels}
            if len(events) < 100:
                break
            offset += 100
    return out


# ── fetch Kalshi game outcomes ────────────────────────────────────────────────
def fetch_ks_outcomes():
    """Returns {match_key: set_of_raw_labels}"""
    out = {}
    cursor = ""
    while True:
        params = {"series_ticker": KALSHI_GAME_SERIES,
                  "with_nested_markets": "true", "status": "open", "limit": "200"}
        if cursor:
            params["cursor"] = cursor
        data = get(f"{KALSHI_BASE}/events", **params)
        for ev in data.get("events") or []:
            title = ev.get("title") or ""
            labels = set()
            for mk in ev.get("markets") or []:
                sub = mk.get("yes_sub_title") or mk.get("title") or ""
                if sub:
                    labels.add(sub)
            if labels:
                out[match_key(title)] = {"title": title, "labels": labels}
        cursor = data.get("cursor") or ""
        if not cursor:
            break
    return out


# ── compare ───────────────────────────────────────────────────────────────────
def main():
    print("Fetching Polymarket games…")
    pm = fetch_pm_outcomes()
    print(f"  {len(pm)} game events")

    print("Fetching Kalshi games…")
    ks = fetch_ks_outcomes()
    print(f"  {len(ks)} game events")

    common = set(pm) & set(ks)
    print(f"\n{len(common)} matches found on BOTH platforms\n")
    print("─" * 70)

    mismatches = []
    for key in sorted(common):
        pm_raw   = pm[key]["labels"]
        ks_raw   = ks[key]["labels"]
        pm_norm  = {norm_outcome(l) for l in pm_raw}
        ks_norm  = {norm_outcome(l) for l in ks_raw}
        pm_only  = pm_norm - ks_norm - {"Draw"}
        ks_only  = ks_norm - pm_norm - {"Draw"}
        if pm_only or ks_only:
            mismatches.append((pm[key]["title"], pm_raw, ks_raw, pm_only, ks_only))

    if not mismatches:
        print("✓  No unresolved label mismatches — all outcome names consolidate correctly.")
        return

    print(f"{'Match':<35} {'PM raw':<25} {'KS raw':<25} {'PM unmatched':<20} {'KS unmatched'}")
    print("─" * 70)
    for title, pm_raw, ks_raw, pm_only, ks_only in mismatches:
        pm_str = ", ".join(sorted(pm_raw - {"Draw"}))
        ks_str = ", ".join(sorted(ks_raw - {"Draw"}))
        print(f"{title:<35} {pm_str:<25} {ks_str:<25}  PM:{sorted(pm_only)}  KS:{sorted(ks_only)}")

    print(f"\n{len(mismatches)} match(es) with unresolved label mismatches.")
    print("\nTo fix: add one line to _TEAM_NAMES in both sources.py and find_mismatches.py.")
    print('Example: "newvariant": "Canonical Name",')


if __name__ == "__main__":
    main()
