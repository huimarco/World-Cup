"""Fetch + normalize odds from Polymarket and Kalshi into one common schema.

Normalized match dict:
{
  "key":       str,            # stable cross-source key (sorted teams + date)
  "title":     str,            # "France vs Norway"
  "start_time": str | None,    # ISO8601
  "sources": {
     "polymarket": {"outcomes": [{"label", "prob"}], "volume": float, "url": str},
     "kalshi":     {"outcomes": [{"label", "prob"}], "volume": float, "url": str},
  }
}
prob is a 0..1 implied probability.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone

import aiohttp

import config


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

# Single source of truth for team-name normalisation.
# Key  = stripped form of every known spelling (lowercase, ASCII-only, no spaces).
# Value = canonical display name shown on the dashboard.
#
# To add a new mismatch after running find_mismatches.py, just add one line:
#   "rawvariant": "Canonical Name",
_TEAM_NAMES: dict[str, str] = {
    # United States
    "usa":                              "United States",
    "unitedstates":                     "United States",
    "unitedstatesofamerica":            "United States",
    # Türkiye
    "turkiye":                          "Türkiye",
    "turkey":                           "Türkiye",
    # Curaçao
    "curacao":                          "Curaçao",
    # Korea
    "korearepublic":                    "Korea Republic",
    "southkorea":                       "Korea Republic",
    "korea":                            "Korea Republic",
    "republicofkorea":                  "Korea Republic",
    "southkorearepublic":               "Korea Republic",
    # Côte d'Ivoire
    "cotedivoire":                      "Côte d'Ivoire",
    "ivorycoast":                       "Côte d'Ivoire",
    # Bosnia
    "bosniaherzegovina":                "Bosnia-Herzegovina",
    "bosniaandherzegovina":             "Bosnia-Herzegovina",
    # IR Iran
    "iran":                             "IR Iran",
    "iriran":                           "IR Iran",
    "iranislamicrepublic":              "IR Iran",
    # Cabo Verde
    "caboverde":                        "Cabo Verde",
    "capeverde":                        "Cabo Verde",
    # Czechia
    "czechia":                          "Czechia",
    "czechrepublic":                    "Czechia",
    # DR Congo
    "congodr":                          "DR Congo",
    "drcongo":                          "DR Congo",
    "democraticrepublicofthecongo":     "DR Congo",
    "congo":                            "DR Congo",
}


def _strip(name: str) -> str:
    """Shared helper: strip accents, lowercase, remove non-alphanumeric."""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = s.lower()
    s = re.sub(r"\b(national team|nat\.?|fc|the)\b", "", s)
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


def _norm_team(name: str) -> str:
    """Return a stable merge key for a team name.

    Strips accents and punctuation, then looks up the canonical display name
    in _TEAM_NAMES, strips *that* too, and returns it as the key — so every
    spelling of the same country produces the same string for merge/sort.
    """
    if not name:
        return ""
    raw = _strip(name)
    display = _TEAM_NAMES.get(raw, name.strip())
    return _strip(display)


def _norm_outcome(label: str) -> str:
    """Return the canonical display label for an outcome.

    - Strips trailing parentheticals: 'Draw (Mexico vs. South Africa)' → 'Draw'
    - Unifies tie/draw variants → 'Draw'
    - Maps every team-name variant to its canonical display form via _TEAM_NAMES
    """
    s = re.sub(r"\s*\(.*\)\s*$", "", str(label)).strip()
    if s.lower() in ("tie", "draw", "draw/tie"):
        return "Draw"
    return _TEAM_NAMES.get(_strip(s), s)


# separators must be surrounded by whitespace so hyphenated team names
# (e.g. "Guinea-Bissau", "Bosnia-Herzegovina") are never split apart.
_VS_RE = re.compile(r"\s+(?:vs?\.?|@|—|–|-)\s+", re.IGNORECASE)


def _split_matchup(title: str) -> tuple[str, str] | None:
    if not title:
        return None
    parts = _VS_RE.split(title, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    return None


def _match_key(title: str, start_time: str | None) -> str:
    teams = _split_matchup(title)
    if teams:
        a, b = sorted([_norm_team(teams[0]), _norm_team(teams[1])])
        return f"{a}|{b}"
    return _norm_team(title)


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------
# Polymarket (Gamma API, public, no auth)
# --------------------------------------------------------------------------
# World Cup match events are individual events (slug like fifwc-mex-rsa-...),
# all sharing the World Cup sports tag. We discover them by tag, not by one
# fixed slug. We try a few tag-slug spellings so it self-heals if the slug
# changes; the first that returns events wins.
_PM_TAG_CANDIDATES = ["world-cup", "fifa-world-cup", "world-cup-2026", "2026-world-cup"]

# Spread / totals markets live in the same event as the moneyline — skip them.
_SKIP_SMT = {"spreads", "spread", "totals", "total", "spread_line", "total_line"}

# Keywords (in a market's question or sportsMarketType) that mean it is NOT the
# full-time moneyline: halftime, exact score, both-teams-to-score, etc.
_NON_MONEYLINE_KW = (
    "halftime", "half time", "half-time", "1st half", "first half", "2nd half",
    "second half", "exact score", "correct score", "both teams", "to score",
    "btts", "spread", "total", "over", "under", "corner", "booking", "card",
    "margin", "handicap", "clean sheet", "penalt",
)

# A score label like "2-1" or "Any Other Score" signals an exact-score market.
_SCORE_LABEL = re.compile(r"^\s*\d+\s*[-:]\s*\d+\s*$")


def _looks_like_moneyline_match(title, outcomes):
    """True only for a full-time Country-A-vs-Country-B win/draw/win market."""
    if not _split_matchup(title):
        return False
    if not (2 <= len(outcomes) <= 3):
        return False
    for o in outcomes:
        lab = str(o.get("label", "")).strip().lower()
        if _SCORE_LABEL.match(lab) or lab in ("any other score", "yes", "no"):
            return False
    return True


async def _pm_get(session, params):
    async with session.get(f"{config.POLYMARKET_GAMMA}/events", params=params) as r:
        r.raise_for_status()
        data = await r.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


async def _pm_series_ids_for_world_cup(session):
    """The individual GAMES are attached to the World Cup sports *series*, not
    the 'world-cup' tag. /sports maps each sport code (e.g. 'fifwc') to its
    series id, so we resolve it here. Returns a list of series id strings."""
    if config.POLYMARKET_SERIES_IDS:
        return list(config.POLYMARKET_SERIES_IDS)
    try:
        async with session.get(f"{config.POLYMARKET_GAMMA}/sports") as r:
            r.raise_for_status()
            sports = await r.json()
    except Exception:  # noqa: BLE001
        return []
    want = config.POLYMARKET_SPORT_CODE.lower()
    ids = []
    for s in sports if isinstance(sports, list) else []:
        blob = " ".join(str(v) for v in s.values()).lower()
        code = (s.get("sport") or "").lower()
        if code == want or want in code or "world cup" in blob or "fifa" in blob:
            sid = s.get("series")
            if sid:
                ids += [x.strip() for x in str(sid).split(",") if x.strip()]
    return ids


async def _pm_game_events(session) -> dict:
    """Collect World Cup game events. Primary: by sports series. Fallback: the
    global sports 'games' tag, filtered to World Cup events by slug prefix."""
    out: dict[str, dict] = {}
    for sid in await _pm_series_ids_for_world_cup(session):
        for ev in await _pm_events_by_series(session, sid):
            out[str(ev.get("id") or ev.get("slug"))] = ev
    if not out and config.POLYMARKET_GAMES_TAG_ID:
        # tag_id for sports "games"; filter to World Cup by the fifwc- slug.
        offset = 0
        for _ in range(20):
            page = await _pm_get(
                session,
                {"tag_id": config.POLYMARKET_GAMES_TAG_ID, "closed": "false",
                 "limit": "100", "offset": str(offset)},
            )
            for ev in page:
                if str(ev.get("slug", "")).startswith(config.POLYMARKET_SPORT_CODE + "-"):
                    out[str(ev.get("id") or ev.get("slug"))] = ev
            if len(page) < 100:
                break
            offset += 100
    return out


async def _pm_events_by_series(session, series_id):
    out, offset = [], 0
    for _ in range(12):
        page = await _pm_get(
            session,
            {"series_id": series_id, "closed": "false", "limit": "100", "offset": str(offset)},
        )
        out.extend(page)
        if len(page) < 100:
            break
        offset += 100
    return out


async def _pm_events_by_tag(session, tag_slug):
    """Paginate all open events for a tag."""
    out, offset = [], 0
    for _ in range(12):  # up to 1200 events
        page = await _pm_get(
            session,
            {
                "tag_slug": tag_slug,
                "related_tags": "true",
                "closed": "false",
                "limit": "100",
                "offset": str(offset),
            },
        )
        out.extend(page)
        if len(page) < 100:
            break
        offset += 100
    return out


def _is_yes_no(outs):
    return {str(o).strip().lower() for o in outs} == {"yes", "no"}


def _pm_yes_ask(m, outs, prices):
    """Cost to BUY the 'Yes' share of a Polymarket Yes/No market (0..1).

    This is the ask price — what you actually pay to execute — not the mid in
    `outcomePrices`. Gamma quotes `bestAsk`/`bestBid` on the first outcome
    token: when 'Yes' is outcome 0 its ask is `bestAsk`; when 'Yes' is outcome
    1, buying it equals selling outcome 0 at its bid, i.e. `1 - bestBid`.
    Falls back to the mid (`outcomePrices`) when the book side is missing.
    """
    yes_idx = 0 if str(outs[0]).strip().lower() == "yes" else 1
    if yes_idx == 0:
        ask = _f(m.get("bestAsk"))
        if ask is not None and ask:
            return ask
    else:
        bid = _f(m.get("bestBid"))
        if bid is not None and bid:
            return 1.0 - bid
    return _f(prices[yes_idx], 0.0) or 0.0


# group titles that are spread/total lines, not teams (e.g. "MEX -1.5", "Over 2.5")
_LINE_TITLE = re.compile(r"[+-]?\d+\.\d|\bover\b|\bunder\b", re.IGNORECASE)


def _pm_extract(ev):
    """Build (outcomes, volume) for an event's main market.

    Handles two Polymarket structures:
      * a single market with a multi-outcome array (e.g. ["Mexico","Draw","RSA"])
      * a negRisk GROUP of Yes/No sub-markets, one per outcome, where each
        market's `groupItemTitle` is the outcome name and the Yes price is its
        probability (this is how World Cup game moneylines are shaped).
    Spreads, totals, halftime, exact-score and BTTS markets are skipped.
    """
    direct = None          # (outcomes, prices, volume) best multi-outcome market
    group = []             # [(label, prob, volume)] from Yes/No sub-markets
    group_vol = 0.0
    for m in ev.get("markets", []) or []:
        if m.get("closed"):
            continue
        smt = (m.get("sportsMarketType") or "").lower()
        if smt in _SKIP_SMT or m.get("line") is not None:
            continue
        blob = f"{m.get('question', '')} {smt}".lower()
        if any(kw in blob for kw in _NON_MONEYLINE_KW):
            continue
        git = str(m.get("groupItemTitle") or "").strip()
        if git and _LINE_TITLE.search(git):
            continue  # spread/total sub-market
        outs = _parse_json_list(m.get("outcomes"))
        prices = _parse_json_list(m.get("outcomePrices"))
        if not outs or len(outs) != len(prices):
            continue
        vol = _f(m.get("volumeNum") or m.get("volume"), 0.0) or 0.0
        if _is_yes_no(outs):
            if not git:
                continue  # a lone yes/no with no outcome label is unusable
            group.append((_norm_outcome(git), _pm_yes_ask(m, outs, prices), vol))
            group_vol += vol
        else:
            if direct is None or vol > direct[2]:
                direct = (outs, prices, vol)

    if direct:
        # Multi-outcome single market: Gamma exposes only one scalar bestAsk
        # (for outcome 0), so there's no per-outcome ask to use here. Fall back
        # to the mid in outcomePrices. World Cup moneylines use the Yes/No group
        # path above, which does use the ask.
        outs, prices, vol = direct
        outcomes = [{"label": _norm_outcome(str(o)), "prob": _f(p, 0.0) or 0.0} for o, p in zip(outs, prices)]
        return outcomes, vol
    if group:
        group.sort(key=lambda x: -x[1])
        return [{"label": l, "prob": p} for (l, p, _v) in group], group_vol
    return None, 0.0


async def fetch_polymarket(session: aiohttp.ClientSession) -> list[dict]:
    events: dict[str, dict] = {}

    # 1) any explicitly configured event slugs (optional, e.g. world-cup-winner)
    for slug in config.POLYMARKET_EVENT_SLUGS:
        for ev in await _pm_get(session, {"slug": slug, "closed": "false"}):
            events[str(ev.get("id") or ev.get("slug"))] = ev

    # 2) GAMES — via the World Cup sports series (this is the A-v-B match list)
    for k, ev in (await _pm_game_events(session)).items():
        events[k] = ev

    # 3) FUTURES (winner, group winners, etc.) — via tag; first spelling wins
    tags = [config.POLYMARKET_TAG_SLUG] if config.POLYMARKET_TAG_SLUG else []
    tags += [t for t in _PM_TAG_CANDIDATES if t not in tags]
    for tag in tags:
        found = await _pm_events_by_tag(session, tag)
        if found:
            for ev in found:
                events[str(ev.get("id") or ev.get("slug"))] = ev
            break

    matches: list[dict] = []
    for ev in events.values():
        parsed, vol = _pm_extract(ev)
        if not parsed:
            continue
        title = ev.get("title") or ""
        # kickoff: prefer a market's gameStartTime; the event startDate is the
        # listing date, not the match time.
        start = None
        for m in ev.get("markets", []) or []:
            start = m.get("gameStartTime") or m.get("eventStartTime")
            if start:
                break
        start = start or ev.get("startTime") or ev.get("startDate")
        slug = ev.get("slug") or ""
        matches.append(
            {
                "key": _match_key(title, start),
                "title": title,
                "start_time": start,
                "source": "polymarket",
                "kind": "match" if _looks_like_moneyline_match(title, parsed) else "other",
                "outcomes": parsed,
                "volume": vol,
                "url": f"https://polymarket.com/event/{slug}" if slug else None,
            }
        )
    return matches


def _parse_json_list(raw):
    """Gamma returns outcomes/prices as JSON-encoded strings."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# --------------------------------------------------------------------------
# Kalshi (public market data)
# --------------------------------------------------------------------------
async def fetch_kalshi(session: aiohttp.ClientSession) -> list[dict]:
    if not config.KALSHI_SERIES_TICKERS:
        return []  # nothing configured; see discover_kalshi.py

    matches: list[dict] = []
    for series in config.KALSHI_SERIES_TICKERS:
        cursor = ""
        for _ in range(25):  # pagination safety bound
            params = {
                "series_ticker": series,
                "with_nested_markets": "true",
                "status": "open",
                "limit": "200",
            }
            if cursor:
                params["cursor"] = cursor
            async with session.get(f"{config.KALSHI_BASE}/events", params=params) as r:
                r.raise_for_status()
                data = await r.json()

            for ev in data.get("events", []) or []:
                title = ev.get("title") or ev.get("sub_title") or ev.get("event_ticker", "")
                ev_ticker = ev.get("event_ticker", "")
                outcomes, vol, start = [], 0.0, None
                for mk in ev.get("markets", []) or []:
                    if mk.get("status") in ("closed", "settled", "finalized"):
                        continue
                    prob = _kalshi_prob(mk)
                    label = mk.get("yes_sub_title") or mk.get("title") or mk.get("ticker")
                    if label is None:
                        continue
                    outcomes.append({"label": _norm_outcome(str(label)), "prob": prob})
                    vol += _f(mk.get("volume_fp"), 0.0) or 0.0
                    start = start or mk.get("close_time") or mk.get("open_time")
                if not outcomes:
                    continue
                matches.append(
                    {
                        "key": _match_key(title, start),
                        "title": title,
                        "start_time": start,
                        "source": "kalshi",
                        "kind": "match" if _looks_like_moneyline_match(title, outcomes) else "other",
                        "outcomes": outcomes,
                        "volume": vol,
                        "url": f"https://kalshi.com/markets/{series.lower()}/{ev_ticker.lower()}",
                    }
                )
            cursor = data.get("cursor") or ""
            if not cursor:
                break
    return matches


def _kalshi_prob(mk: dict) -> float:
    """Implied probability from a Kalshi binary market: yes ask price in
    dollars (0..1) — cost to buy, falling back to last traded price."""
    ya = _f(mk.get("yes_ask_dollars"))
    if ya is not None and ya:
        return ya
    return _f(mk.get("last_price_dollars"), 0.0) or 0.0


# --------------------------------------------------------------------------
# merge into unified records keyed by match
# --------------------------------------------------------------------------
def merge(records: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for rec in records:
        slot = merged.setdefault(
            rec["key"],
            {
                "key": rec["key"],
                "title": rec["title"],
                "start_time": rec["start_time"],
                "kind": "other",
                "sources": {},
            },
        )
        # Prefer a title that looks like a matchup.
        if _split_matchup(rec["title"]) and not _split_matchup(slot["title"]):
            slot["title"] = rec["title"]
        if rec.get("kind") == "match":
            slot["kind"] = "match"
        slot["start_time"] = slot["start_time"] or rec["start_time"]
        slot["sources"][rec["source"]] = {
            "outcomes": rec["outcomes"],
            "volume": rec.get("volume"),
            "url": rec.get("url"),
        }

    out = list(merged.values())
    out.sort(key=lambda m: (m["start_time"] or "9999", m["title"]))
    return out


async def gather_all(session: aiohttp.ClientSession) -> dict:
    records: list[dict] = []
    errors: dict[str, str] = {}

    if config.ENABLE_POLYMARKET:
        try:
            records += await fetch_polymarket(session)
        except Exception as e:  # noqa: BLE001 - surface to client, keep serving
            errors["polymarket"] = str(e)
    if config.ENABLE_KALSHI:
        try:
            records += await fetch_kalshi(session)
        except Exception as e:  # noqa: BLE001
            errors["kalshi"] = str(e)

    return {
        "matches": merge(records),
        "errors": errors,
        "updated": datetime.now(timezone.utc).isoformat(),
    }
