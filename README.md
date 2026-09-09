# NFL picks scraper

Feeds `nfl-picks-edge.jsx` with this week's Yahoo Pick'em lines, FTN total DVOA, and DraftKings spreads.
Runs either on your own machine or on a schedule in GitHub Actions.

## Option A — GitHub Actions (no local install)

1. Create a repo and upload everything in this folder, keeping the `.github/workflows/pull.yml` path.
2. Grab your logged-in cookies once (any browser, no extension needed):
   - Log in to Yahoo. Open DevTools (F12) → **Network** tab → reload the league page →
     click the first request (`7/` or similar) → **Headers** → **Request Headers** → copy the whole `Cookie:` value.
   - Do the same on ftnfantasy.com after logging in there.
3. In the repo: **Settings → Secrets and variables → Actions → New repository secret**. Add:
   - `YAHOO_COOKIES` — the Yahoo cookie string
   - `FTN_COOKIES` — the FTN cookie string
   - `ODDS_API_KEY` — free key from the-odds-api.com
4. **Actions** tab → "Pull NFL picks data" → **Run workflow**. It then runs on its own Tue/Thu/Sat mornings.

Each run commits `week.json` to the repo. Open it on GitHub, hit **Raw**, and either paste the JSON into
the app's "Import bundle" box or paste the raw URL into the app's "Load from URL" field.

If the Yahoo or DVOA count is 0, download the `debug-text` artifact from the run — it has the raw page text
for tuning the parser. Cookies eventually expire; if a run starts reporting a login error, repeat step 2–3.

## Option B — locally
```
pip install -r requirements.txt
playwright install chromium
python scrape_picks.py login            # sign in to Yahoo + FTN in the window, then close it
export ODDS_API_KEY=your_key
python scrape_picks.py pull
```

## Parsing rules
Consecutive team mentions within ~60 characters form a game; the first team is away unless "vs" sits between
them; the spread attaches to whichever team it sits next to. Test on a saved dump with
`python scrape_picks.py test debug_yahoo.txt` (or `--kind dvoa` for FTN).
