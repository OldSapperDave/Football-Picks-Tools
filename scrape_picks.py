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
FTN_DVOA_URL = os.environ.get("FTN_URL", "https://www.ftnfantasy.com/stats/nfl/team-total-dvoa")
YAHOO_URL = "https://football.fantasysports.yahoo.com/pickem/11602/7/"
FTN_PROJ_URL = "https://ftnfantasy.com/stats/nfl/dvoa-game-projections"
FTN_SEASON = os.environ.get("FTN_SEASON", str(time.localtime().tm_year))
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


YAHOO_DIST_RE = re.compile(r"^\s*(@)?\s*(.+?)\s*(?:\(\s*(-?\d+(?:\.\d+)?)\s*\))?\s+(\d+)%")


YAHOO_DIST_PAGE_RE = re.compile(
    r"Favorite\s*\n(@\s*)?([^\n]+?)\s*\nFavorite Pick Percentage\s*\n(\d+)%\s*\n"
    r"Underdog\s*\n(@\s*)?([^\n]+?)\s*\nUnderdog Pick Percentage\s*\n(\d+)%"
    r"[\s\S]*?(\d+(?:\.\d+)?)\s*pts\s+Favorite", re.I)


def parse_yahoo_distribution_page(text):
    """
    The public /pickdistribution page (no login needed). Each game reads:
        Favorite / [@ ]Team / Favorite Pick Percentage / NN% /
        Underdog / [@ ]Team / Underdog Pick Percentage / NN% / ... / X.X pts  Favorite
    '@' marks the home team. Returns (games, pick_pcts) keyed 'AWAY@HOME'.
    """
    games, pcts = {}, {}
    for m in YAHOO_DIST_PAGE_RE.finditer(text):
        fav_home, fav_name, fav_pct, dog_home, dog_name, dog_pct, pts = m.groups()
        fav, dog = team_hits(fav_name), team_hits(dog_name)
        if not (fav and dog):
            continue
        fav, dog = fav[-1][0], dog[-1][0]
        if bool(fav_home) == bool(dog_home):
            continue
        home, away = (fav, dog) if fav_home else (dog, fav)
        spread = float(pts)
        key = f"{away}@{home}"
        games[key] = (-spread if fav == home else spread) + 0.0
        pcts[key] = {fav: int(fav_pct), dog: int(dog_pct)}
    return games, pcts


def parse_yahoo_pickem(text):
    """
    Yahoo Pick'em page. The 'Pick Distribution' block lists each game as two lines:
        @ Seattle (-3.5)    73%
        New England         27%
    '@' marks home, the parenthesised number marks the favorite. Returns
    ({'AWAY@HOME': home_spread}, {'AWAY@HOME': {'AWAY': pct, 'HOME': pct}}).
    Falls back to the generic parser if the block is missing.
    """
    g, p = parse_yahoo_distribution_page(text)
    if g:
        return g, p
    lo = text.lower()
    start = lo.find("pick distribution - week")
    if start < 0:
        start = lo.rfind("pick distribution")
    if start < 0:
        return parse_games(text), {}
    rows = []
    for line in text[start:].splitlines():
        m = YAHOO_DIST_RE.match(line)
        if not m:
            continue
        hits = team_hits(m.group(2))
        if not hits:
            continue
        rows.append({"abbr": hits[-1][0], "home": bool(m.group(1)),
                     "spread": float(m.group(3)) if m.group(3) else None, "pct": int(m.group(4))})
    games, pcts = {}, {}
    for i in range(0, len(rows) - 1, 2):
        a, b = rows[i], rows[i + 1]
        if a["home"] == b["home"]:
            continue  # misaligned pair; skip rather than guess
        home, away = (a, b) if a["home"] else (b, a)
        fav_spread = a["spread"] if a["spread"] is not None else b["spread"]
        fav = a if a["spread"] is not None else b
        if fav_spread is None:
            home_spread = 0.0
        else:
            home_spread = fav_spread if fav is home else -fav_spread  # fav_spread is negative
        key = f"{away['abbr']}@{home['abbr']}"
        games[key] = home_spread + 0.0
        pcts[key] = {away["abbr"]: away["pct"], home["abbr"]: home["pct"]}
    return games, pcts


def parse_ftn_projections(text):
    """
    FTN's projections table: 'Week | Home | Away | Home Pts | Away Pts' — home is
    listed FIRST, opposite of the away@home convention used everywhere else here.
    Returns {'AWAY@HOME': home_spread} where a negative spread means home favored.
    """
    header = next((l for l in text.splitlines() if re.search(r"home", l, re.I) and re.search(r"away", l, re.I)), "")
    home_first = header.lower().find("home") < header.lower().find("away") if header else True
    games = {}
    for line in text.splitlines():
        if re.search(r"home\s*pts", line, re.I):
            continue
        hits = team_hits(line)
        if len(hits) < 2:
            continue
        t1, t2 = hits[0], hits[1]
        home, away = (t1[0], t2[0]) if home_first else (t2[0], t1[0])
        nums = [float(m.group(1)) for m in re.finditer(r"(?<![\d.%-])(\d{1,2}(?:\.\d+)?)(?![\d.]*%)", line[t2[2]:])]
        if len(nums) >= 2:
            p1, p2 = nums[-2], nums[-1]
            home_pts, away_pts = (p1, p2) if home_first else (p2, p1)
            games[f"{away}@{home}"] = round((away_pts - home_pts) * 2) / 2
    return games


def parse_dvoa(text):
    """
    FTN's table renders one cell per line: team abbreviation, then TOT DVOA on the
    next line. Match team -> first percentage within a short window, first hit wins.
    Only the table region is scanned (from the TEAM header to 'Total Rows').
    """
    lo = text.lower()
    start = lo.find("tot dvoa")
    end = lo.find("total rows", start if start >= 0 else 0)
    region = text[start if start >= 0 else 0 : end if end > 0 else len(text)]
    flat = re.sub(r"\s+", " ", region)
    out, season = {}, None
    m = re.search(r"\b(20\d\d)\b", flat)
    if m:
        season = m.group(1)
    for abbr, s0, s1 in team_hits(flat):
        pm = PCT_RE.search(flat, s1, min(len(flat), s1 + 15))
        if pm and abbr not in out:
            out[abbr] = float(pm.group(1))
    return out, season


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


page_url_holder = {}


def grab_text(ctx, url, wait_for=None, name=""):
    page = ctx.new_page()
    load_page(page, url)
    page_url_holder["url"] = page.url
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


def select_season(page, season):
    """Try to switch FTN's year picker to `season`. Returns a short label of what worked."""
    # 1. a real <select> with a matching option
    try:
        for sel in page.query_selector_all("select"):
            opts = [o.inner_text().strip() for o in sel.query_selector_all("option")]
            if season in opts:
                sel.select_option(label=season)
                time.sleep(3)
                return "select"
    except Exception:
        pass
    # 2. a custom dropdown: click the element showing the current year, then the target year
    for cur in ("2025", "2024"):
        try:
            btn = page.get_by_text(cur, exact=True).first
            if btn.count() and btn.is_visible():
                btn.click()
                time.sleep(1)
                opt = page.get_by_text(season, exact=True).first
                if opt.count():
                    opt.click()
                    time.sleep(3)
                    return "dropdown"
        except Exception:
            continue
    return "none"


def ftn_logged_in(page):
    try:
        head = page.inner_text("body")[:1500].lower()
    except Exception:
        return False
    return "log in" not in head and "sign up" not in head


def ftn_login(ctx):
    """Sign in with FTN_EMAIL / FTN_PASSWORD if the cookie route didn't. Returns True on success."""
    email, pw = os.environ.get("FTN_EMAIL"), os.environ.get("FTN_PASSWORD")
    if not (email and pw):
        print("ftn: no FTN_EMAIL/FTN_PASSWORD set; relying on cookies only", file=sys.stderr)
        return False
    page = ctx.new_page()
    load_page(page, FTN_LOGIN, settle=3)
    if ftn_logged_in(page):
        page.close()
        return True
    try:
        # try the obvious field types first, then common name/placeholder patterns
        for sel in ("input[type=email]", "input[name*=mail i]", "input[placeholder*=mail i]", "input[type=text]"):
            if page.locator(sel).count():
                page.locator(sel).first.fill(email)
                break
        page.locator("input[type=password]").first.fill(pw)
        for sel in ("button[type=submit]", "button:has-text('Log In')", "button:has-text('Sign In')", "button:has-text('Login')"):
            if page.locator(sel).count():
                page.locator(sel).first.click()
                break
        else:
            page.keyboard.press("Enter")
        time.sleep(6)
        ok = ftn_logged_in(page)
        print(f"ftn login: {'ok' if ok else 'FAILED'} (url now {page.url})", file=sys.stderr)
        if not ok:
            Path("debug_ftn_login.txt").write_text(page.inner_text("body"))
        page.close()
        return ok
    except Exception as e:
        print(f"ftn login: error {type(e).__name__}: {e}", file=sys.stderr)
        try:
            Path("debug_ftn_login.txt").write_text(page.inner_text("body"))
        except Exception:
            pass
        page.close()
        return False


def fetch_ftn_rows(ctx):
    page = ctx.new_page()
    load_page(page, FTN_DVOA_URL)
    try:
        page.wait_for_selector("text=TOT DVOA", timeout=20000)
    except Exception:
        pass
    text = page.inner_text("body")
    _, season = parse_dvoa(text)
    how = "already"
    if season != FTN_SEASON:
        how = select_season(page, FTN_SEASON)
        text = page.inner_text("body")
    print(f"ftn: title={page.title()!r} url={page.url} season_switch={how} teams_seen={len(team_hits(text))}", file=sys.stderr)
    Path("debug_ftn.txt").write_text(text)
    page.close()
    return text


def fetch_ftn_projections(ctx):
    """FTN's 'DVOA Game Projections' page: their projected spread per game."""
    page = ctx.new_page()
    load_page(page, FTN_PROJ_URL, settle=6)
    if not ftn_logged_in(page):
        print("ftn projections: page shows the paywall — not logged in", file=sys.stderr)
    rows = page.eval_on_selector_all("table tr", "rs => rs.map(r => [...r.querySelectorAll('td,th')].map(c => c.innerText.trim()).join(' | '))")
    text = "\n".join(rows) if rows else page.inner_text("body")
    Path("debug_ftn_proj.txt").write_text(text)
    games = parse_ftn_projections(text)
    print(f"ftn projections: url={page.url} teams_seen={len(team_hits(text))} games={len(games)}", file=sys.stderr)
    page.close()
    return games


def fetch_dk(api_key):
    """DraftKings line, plus a consensus (median of all books) and Pinnacle when available."""
    import requests, statistics
    r = requests.get(
        "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds/",
        params={"apiKey": api_key, "regions": "us,eu", "markets": "spreads,totals", "oddsFormat": "american"},
        timeout=30,
    )
    r.raise_for_status()
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    horizon = now + dt.timedelta(days=DK_DAYS_AHEAD)
    dk, consensus, pinnacle, nbooks, kickoff, totals = {}, {}, {}, {}, {}, {}
    for ev in r.json():
        when = dt.datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if not (now - dt.timedelta(hours=6) <= when <= horizon):
            continue
        h, a = team_hits(ev["home_team"]), team_hits(ev["away_team"])
        if not (h and a):
            continue
        key = f"{a[0][0]}@{h[0][0]}"
        kickoff[key] = ev["commence_time"]
        lines, tots = {}, []
        for bk in ev.get("bookmakers", []):
            spreads = next((m for m in bk.get("markets", []) if m["key"] == "spreads"), None)
            home_out = spreads and next((o for o in spreads["outcomes"] if o["name"] == ev["home_team"]), None)
            if home_out and home_out.get("point") is not None:
                lines[bk["key"]] = float(home_out["point"])
            tot = next((m for m in bk.get("markets", []) if m["key"] == "totals"), None)
            over = tot and next((o for o in tot["outcomes"] if o["name"] == "Over"), None)
            if over and over.get("point") is not None:
                tots.append(float(over["point"]))
        if tots:
            totals[key] = round(statistics.median(tots) * 2) / 2
        if not lines:
            continue
        if "draftkings" in lines:
            dk[key] = lines["draftkings"]
        if "pinnacle" in lines:
            pinnacle[key] = lines["pinnacle"]
        consensus[key] = round(statistics.median(lines.values()) * 2) / 2  # to the half point
        nbooks[key] = len(lines)
    print(f"draftkings: {len(dk)} games; consensus from {min(nbooks.values(), default=0)}-{max(nbooks.values(), default=0)} books; pinnacle: {len(pinnacle)} games", file=sys.stderr)
    return dk, consensus, pinnacle, kickoff, totals


def to_lines(games):
    return "\n".join(f"{k.split('@')[0]} @ {k.split('@')[1]} {('PK' if v == 0 else f'{v:+g}')}" for k, v in games.items())


def cmd_pull(args):
    from playwright.sync_api import sync_playwright
    bundle = {"yahoo": {}, "yahoo_pick_pct": {}, "dk": {}, "consensus": {}, "pinnacle": {}, "kickoff": {}, "totals": {}, "dvoa": {}, "ftn_proj": {}, "pulled_at": time.strftime("%Y-%m-%d %H:%M")}
    with sync_playwright() as pw:
        ctx = open_browser(pw, headless=not args.headed)
        base = args.yahoo_url.rstrip("/")
        try:
            # 1) logged-in league page (group's own pick %), 2) public distribution page (Yahoo-wide %)
            for url, label in ((base, "league page (cookie)"), (base + "/pickdistribution", "public distribution page")):
                text = grab_text(ctx, url, name="yahoo")
                if "login.yahoo.com" in page_url_holder.get("url", "") or "not a member of this group" in text.lower():
                    print(f"yahoo: {label} needs a valid cookie — skipping", file=sys.stderr)
                    continue
                games, pcts = parse_yahoo_pickem(text)
                if games:
                    bundle["yahoo"], bundle["yahoo_pick_pct"] = games, pcts
                    bundle["yahoo_source"] = label
                    print(f"yahoo: {len(games)} games from {label}", file=sys.stderr)
                    break
                print(f"yahoo: 0 games from {label}", file=sys.stderr)
        except Exception as e:
            print(f"yahoo failed: {type(e).__name__}: {e}", file=sys.stderr)
        if not args.skip_ftn:
            try:
                ftn_login(ctx)
                bundle["dvoa"], bundle["dvoa_season"] = parse_dvoa(fetch_ftn_rows(ctx))
                print(f"ftn dvoa: {len(bundle['dvoa'])} teams (season shown: {bundle['dvoa_season']})", file=sys.stderr)
                bundle["ftn_proj"] = fetch_ftn_projections(ctx)
            except Exception as e:
                print(f"ftn failed: {type(e).__name__}: {e}", file=sys.stderr)
        ctx.close()
    key = args.odds_key or os.environ.get("ODDS_API_KEY")
    if key:
        try:
            bundle["dk"], bundle["consensus"], bundle["pinnacle"], bundle["kickoff"], bundle["totals"] = fetch_dk(key)
        except Exception as e:
            print(f"draftkings failed: {type(e).__name__}: {e}", file=sys.stderr)
    else:
        print("no ODDS_API_KEY set; skipping DraftKings", file=sys.stderr)

    if bundle["yahoo"]:
        # keep market lines to this week's Yahoo slate (drops next Thursday's game etc.)
        for src in ("dk", "consensus", "pinnacle", "kickoff", "totals"):
            bundle[src] = {k: v for k, v in bundle[src].items() if k in bundle["yahoo"]}
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
    t.add_argument("--kind", choices=["games", "dvoa", "yahoo"], default="games")
    args = ap.parse_args()
    if args.cmd == "login":
        cmd_login()
    elif args.cmd == "pull":
        cmd_pull(args)
    else:
        txt = Path(args.file).read_text()
        print(json.dumps({"games": parse_games, "dvoa": lambda t: parse_dvoa(t)[0], "yahoo": lambda t: parse_yahoo_pickem(t)[0]}[args.kind](txt), indent=2))
