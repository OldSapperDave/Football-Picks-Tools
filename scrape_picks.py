#!/usr/bin/env python3
"""
NFL picks scraper: Yahoo Pick'em lines + FTN DVOA + DraftKings spreads -> one JSON bundle.

Setup (once):
    pip install playwright requests
    playwright install chromium
    python scrape_picks.py login          # opens a browser; sign in to Yahoo and FTN, then close it

Weekly:
    export ODDS_API_KEY=...                # the-odds-api.com (free tier is fine)
    python scrape_picks.py pull            # Yahoo URL defaults to your league page

Outputs:
    week.json      paste into the app's "Import bundle" box
    week_paste.txt the same data as three plain-text blocks, in case you'd rather paste per source
    debug_*.txt    raw page text, for tuning the parser if a site changes layout

Logins live in ./browser_profile (a persistent Chromium profile). Nothing is stored elsewhere.
"""
import argparse, json, os, re, sys, time
from pathlib import Path

PROFILE_DIR = Path("browser_profile")
YAHOO_LOGIN = "https://login.yahoo.com/"
FTN_LOGIN = "https://www.ftnfantasy.com/login"
FTN_DVOA_URL = "https://www.ftnfantasy.com/dvoa/nfl/team-total-dvoa"
YAHOO_URL = "https://football.fantasysports.yahoo.com/pickem/11602/7/"
DK_DAYS_AHEAD = 8  # only games kicking off within this many days

TEAMS = {
    "ARI": ["arizona", "cardinals", "ari"], "ATL": ["atlanta", "falcons", "atl"],
    "BAL": ["baltimore", "ravens", "bal"], "BUF": ["buffalo", "bills", "buf"],
    "CAR": ["carolina", "panthers", "car"], "CHI": ["chicago", "bears", "chi"],
    "CIN": ["cincinnati", "bengals", "cin"], "CLE": ["cleveland", "browns", "cle"],
    "DAL": ["dallas", "cowboys", "dal"], "DEN": ["denver", "broncos", "den"],
    "DET": ["detroit", "lions", "det"], "GB": ["green bay", "packers", "gb", "gnb"],
    "HOU": ["houston", "texans", "hou"], "IND": ["indianapolis", "colts", "ind"],
    "JAX": ["jacksonville", "jaguars", "jax", "jac"], "KC": ["kansas city", "chiefs", "kc", "kan"],
    "LV": ["las vegas", "raiders", "lv", "lvr"], "LAC": ["los angeles chargers", "la chargers", "chargers", "lac"],
    "LAR": ["los angeles rams", "la rams", "rams", "lar"], "MIA": ["miami", "dolphins", "mia"],
    "MIN": ["minnesota", "vikings", "min"], "NE": ["new england", "patriots", "ne", "nwe"],
    "NO": ["new orleans", "saints", "no", "nor"], "NYG": ["new york giants", "ny giants", "giants", "nyg"],
    "NYJ": ["new york jets", "ny jets", "jets", "nyj"], "PHI": ["philadelphia", "eagles", "phi"],
    "PIT": ["pittsburgh", "steelers", "pit"], "SF": ["san francisco", "49ers", "sf", "sfo"],
    "SEA": ["seattle", "seahawks", "sea"], "TB": ["tampa bay", "buccaneers", "bucs", "tb", "tam"],
    "TEN": ["tennessee", "titans", "ten"], "WAS": ["washington", "commanders", "was", "wsh"],
}
ALIASES = sorted(((a, n) for a, ns in TEAMS.items() for n in ns), key=lambda x: -len(x[1]))
TEAM_RE = re.compile(r"(?<![a-z])(" + "|".join(re.escape(n).replace(r"\ ", r"\s+") for _, n in ALIASES) + r")(?![a-z])", re.I)
ALIAS_TO_ABBR = {n: a for a, n in ALIASES}
SPREAD_RE = re.compile(r"(?<![\d.])([+-]\s?\d{1,2}(?:\.5)?)(?![\d.%])|\b(PK|PICK)\b", re.I)
PCT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*%")


def team_hits(text):
    """[(abbr, start, end)] in order of appearance, deduped by position."""
    hits, last_end = [], -1
    for m in TEAM_RE.finditer(text):
        if m.start() < last_end:
            continue
        abbr = ALIAS_TO_ABBR[re.sub(r"\s+", " ", m.group(1).lower())]
        if hits and hits[-1][0] == abbr and m.start() - hits[-1][2] <= 2:
            hits[-1] = (abbr, hits[-1][1], m.end())  # "Dallas" + "Cowboys" -> one hit
        else:
            hits.append((abbr, m.start(), m.end()))
        last_end = m.end()
    return hits


def parse_games(text, window=60):
    """
    Pair consecutive team mentions that are close together, and attach the nearest
    spread token. First team is away unless 'vs' sits between them. Returns
    {'AWAY@HOME': home_spread}. Works on line-per-game text and on Pick'em page
    dumps where the spread sits on its own line.
    """
    flat = re.sub(r"\s+", " ", text)
    hits = team_hits(flat)
    spreads = [(m.start(), 0.0 if m.group(2) else float(m.group(1).replace(" ", ""))) for m in SPREAD_RE.finditer(flat)]
    games, i = {}, 0
    while i < len(hits) - 1:
        (a, a0, a1), (b, b0, b1) = hits[i], hits[i + 1]
        if a == b or b0 - a1 > window:
            i += 1
            continue
        between = flat[a1:b0]
        home, away = (a, b) if re.search(r"\bvs\.?\b", between, re.I) else (b, a)
        # nearest spread within the pair's neighborhood
        cands = [(abs(pos - a1) if pos < b0 else pos - b1, pos, val) for pos, val in spreads if a0 - 12 <= pos <= b1 + 12]
        if cands:
            _, pos, val = min(cands)
            near_a = abs(pos - a1) < abs(pos - b0) if pos < b0 else False
            near_a = near_a or pos < a0
            spread_team = a if near_a else b
            games[f"{away}@{home}"] = (val if spread_team == home else -val) + 0.0
        i += 2
    return games


def parse_dvoa(text):
    out = {}
    for line in text.splitlines():
        hits, pct = team_hits(line), PCT_RE.search(line)
        if hits and pct and hits[0][0] not in out:
            out[hits[0][0]] = float(pct.group(1))
    return out


# ---------------- browser ----------------
def cookie_header_to_list(header, domain, host_url):
    out = []
    for part in header.strip().strip('"').split(";"):
        if "=" in part:
            name, val = part.strip().split("=", 1)
            c = {"name": name, "value": val, "secure": True, "sameSite": "None"}
            if name.startswith("__Host-"):
                c["url"] = host_url          # __Host- cookies are host-only, no domain allowed
            else:
                c.update({"domain": domain, "path": "/"})
            out.append(c)
    return out


def open_browser(pw, headless):
    """
    Two auth modes:
      - local: persistent profile in ./browser_profile (created by `login`)
      - CI:    YAHOO_COOKIES / FTN_COOKIES env vars holding the raw Cookie header
               from a logged-in browser session (no profile needed)
    """
    yc, fc = os.environ.get("YAHOO_COOKIES"), os.environ.get("FTN_COOKIES")
    if yc or fc:
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context(viewport={"width": 1300, "height": 900},
                                  user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36")
        if yc:
            cl = cookie_header_to_list(yc, ".yahoo.com", "https://football.fantasysports.yahoo.com/")
            ctx.add_cookies(cl); print(f"yahoo: {len(cl)} cookies loaded", file=sys.stderr)
        if fc:
            cl = cookie_header_to_list(fc, ".ftnfantasy.com", "https://www.ftnfantasy.com/")
            ctx.add_cookies(cl); print(f"ftn: {len(cl)} cookies loaded", file=sys.stderr)
        return ctx
    return pw.chromium.launch_persistent_context(str(PROFILE_DIR), headless=headless, viewport={"width": 1300, "height": 900})


def cmd_login():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = open_browser(pw, headless=False)
        ctx.new_page().goto(YAHOO_LOGIN)
        ctx.new_page().goto(FTN_LOGIN)
        print("Sign in to Yahoo and FTN in the browser, then close the window to save the session.")
        try:
            while ctx.pages:
                time.sleep(1)
        except Exception:
            pass
        ctx.close()


def load_page(page, url, settle=4):
    """Ad-heavy sites never reach 'networkidle'; wait for the DOM, then a fixed settle."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        print(f"warning: slow load for {url}: {type(e).__name__}", file=sys.stderr)
    time.sleep(settle)


def grab_text(ctx, url, wait_for=None, name=""):
    page = ctx.new_page()
    load_page(page, url)
    if wait_for:
        try:
            page.wait_for_selector(wait_for, timeout=15000)
        except Exception:
            pass
    text = page.inner_text("body")
    print(f"{name}: title={page.title()!r} url={page.url} chars={len(text)} teams_seen={len(team_hits(text))}", file=sys.stderr)
    Path(f"debug_{name}.txt").write_text(text)
    page.close()
    return text


def fetch_ftn_rows(ctx):
    """Prefer table rows so team and DVOA stay on one line; fall back to page text."""
    page = ctx.new_page()
    load_page(page, FTN_DVOA_URL)
    try:
        page.wait_for_selector("table tr", timeout=20000)
    except Exception:
        pass
    rows = page.eval_on_selector_all("table tr", "rs => rs.map(r => [...r.querySelectorAll('td,th')].map(c => c.innerText.trim()).join(' | '))")
    text = "\n".join(rows) if rows else page.inner_text("body")
    print(f"ftn: title={page.title()!r} url={page.url} table_rows={len(rows)} teams_seen={len(team_hits(text))}", file=sys.stderr)
    if not rows:
        text = page.inner_text("body")
    Path("debug_ftn.txt").write_text(text)
    page.close()
    return text


def fetch_dk(api_key):
    import requests
    r = requests.get(
        "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds/",
        params={"apiKey": api_key, "regions": "us", "markets": "spreads", "bookmakers": "draftkings", "oddsFormat": "american"},
        timeout=30,
    )
    r.raise_for_status()
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    horizon = now + dt.timedelta(days=DK_DAYS_AHEAD)
    games = {}
    for ev in r.json():
        when = dt.datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if not (now - dt.timedelta(hours=6) <= when <= horizon):
            continue
        h, a = team_hits(ev["home_team"]), team_hits(ev["away_team"])
        mkts = (ev.get("bookmakers") or [{}])[0].get("markets", [])
        spreads = next((m for m in mkts if m["key"] == "spreads"), None)
        if not (h and a and spreads):
            continue
        home_out = next((o for o in spreads["outcomes"] if o["name"] == ev["home_team"]), None)
        if home_out:
            games[f"{a[0][0]}@{h[0][0]}"] = home_out["point"]
    print(f"draftkings: {len(games)} games", file=sys.stderr)
    return games


def to_lines(games):
    return "\n".join(f"{k.split('@')[0]} @ {k.split('@')[1]} {('PK' if v == 0 else f'{v:+g}')}" for k, v in games.items())


def cmd_pull(args):
    from playwright.sync_api import sync_playwright
    bundle = {"yahoo": {}, "dk": {}, "dvoa": {}, "pulled_at": time.strftime("%Y-%m-%d %H:%M")}
    with sync_playwright() as pw:
        ctx = open_browser(pw, headless=not args.headed)
        base = args.yahoo_url.rstrip("/")
        try:
            for url in (base, base + "/picks", base + "/pickdistribution"):
                text = grab_text(ctx, url, name="yahoo")
                if "not a member of this group" in text.lower():
                    print("yahoo: 'not a member' error — the cookies aren't being accepted; re-copy YAHOO_COOKIES", file=sys.stderr)
                    break
                bundle["yahoo"] = parse_games(text)
                print(f"yahoo: {len(bundle['yahoo'])} games from {url}", file=sys.stderr)
                if bundle["yahoo"]:
                    break
        except Exception as e:
            print(f"yahoo failed: {type(e).__name__}: {e}", file=sys.stderr)
        if not args.skip_ftn:
            try:
                bundle["dvoa"] = parse_dvoa(fetch_ftn_rows(ctx))
                print(f"ftn dvoa: {len(bundle['dvoa'])} teams", file=sys.stderr)
            except Exception as e:
                print(f"ftn failed: {type(e).__name__}: {e}", file=sys.stderr)
        ctx.close()
    key = args.odds_key or os.environ.get("ODDS_API_KEY")
    if key:
        try:
            bundle["dk"] = fetch_dk(key)
        except Exception as e:
            print(f"draftkings failed: {type(e).__name__}: {e}", file=sys.stderr)
    else:
        print("no ODDS_API_KEY set; skipping DraftKings", file=sys.stderr)

    Path("week.json").write_text(json.dumps(bundle, indent=2))
    Path("week_paste.txt").write_text(
        "=== YAHOO ===\n" + to_lines(bundle["yahoo"]) + "\n\n=== DRAFTKINGS ===\n" + to_lines(bundle["dk"])
        + "\n\n=== DVOA ===\n" + "\n".join(f"{t} {v}%" for t, v in bundle["dvoa"].items()) + "\n"
    )
    print(json.dumps(bundle, indent=2))
    for name, n in (("yahoo", len(bundle["yahoo"])), ("dvoa", len(bundle["dvoa"]))):
        if n == 0:
            print(f"note: {name} came back empty — check debug_{name if name != 'dvoa' else 'ftn'}.txt and adjust the parser", file=sys.stderr)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="open a browser to sign in to Yahoo and FTN")
    p = sub.add_parser("pull", help="scrape this week's data")
    p.add_argument("--yahoo-url", default=YAHOO_URL, help="your Pick'em league page URL")
    p.add_argument("--odds-key", help="the-odds-api.com key (or set ODDS_API_KEY)")
    p.add_argument("--skip-ftn", action="store_true")
    p.add_argument("--headed", action="store_true", help="show the browser while scraping")
    t = sub.add_parser("test", help="run the parsers on a saved debug file")
    t.add_argument("file")
    t.add_argument("--kind", choices=["games", "dvoa"], default="games")
    args = ap.parse_args()
    if args.cmd == "login":
        cmd_login()
    elif args.cmd == "pull":
        cmd_pull(args)
    else:
        txt = Path(args.file).read_text()
        print(json.dumps(parse_games(txt) if args.kind == "games" else parse_dvoa(txt), indent=2))
