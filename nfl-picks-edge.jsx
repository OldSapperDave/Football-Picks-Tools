import { useState, useMemo } from "react";

// ---------- team dictionary ----------
const TEAMS = {
  ARI: ["arizona", "cardinals", "ari"], ATL: ["atlanta", "falcons", "atl"],
  BAL: ["baltimore", "ravens", "bal"], BUF: ["buffalo", "bills", "buf"],
  CAR: ["carolina", "panthers", "car"], CHI: ["chicago", "bears", "chi"],
  CIN: ["cincinnati", "bengals", "cin"], CLE: ["cleveland", "browns", "cle"],
  DAL: ["dallas", "cowboys", "dal"], DEN: ["denver", "broncos", "den"],
  DET: ["detroit", "lions", "det"], GB: ["green bay", "packers", "gb", "gnb"],
  HOU: ["houston", "texans", "hou"], IND: ["indianapolis", "colts", "ind"],
  JAX: ["jacksonville", "jaguars", "jax", "jac"], KC: ["kansas city", "chiefs", "kc", "kan"],
  LV: ["las vegas", "raiders", "lv", "lvr", "oak"], LAC: ["chargers", "lac", "sd"],
  LAR: ["rams", "lar", "stl"], MIA: ["miami", "dolphins", "mia"],
  MIN: ["minnesota", "vikings", "min"], NE: ["new england", "patriots", "ne", "nwe"],
  NO: ["new orleans", "saints", "no", "nor"], NYG: ["giants", "nyg"],
  NYJ: ["jets", "nyj"], PHI: ["philadelphia", "eagles", "phi"],
  PIT: ["pittsburgh", "steelers", "pit"], SF: ["san francisco", "49ers", "sf", "sfo"],
  SEA: ["seattle", "seahawks", "sea"], TB: ["tampa bay", "buccaneers", "bucs", "tb", "tam"],
  TEN: ["tennessee", "titans", "ten"], WAS: ["washington", "commanders", "was", "wsh"],
};
// longest alias first so "san francisco" beats "sf"
const ALIASES = Object.entries(TEAMS)
  .flatMap(([abbr, names]) => names.map((n) => ({ abbr, n })))
  .sort((a, b) => b.n.length - a.n.length);

function findTeams(text) {
  // returns [{abbr, idx}] in order of appearance
  const lower = text.toLowerCase();
  const hits = [];
  for (const { abbr, n } of ALIASES) {
    const re = new RegExp(`(^|[^a-z])${n.replace(/ /g, "\\s+")}(?=$|[^a-z])`, "g");
    let m;
    while ((m = re.exec(lower))) {
      const idx = m.index + m[1].length;
      if (!hits.some((h) => h.abbr === abbr) && !hits.some((h) => Math.abs(h.idx - idx) < 2))
        hits.push({ abbr, idx, len: n.length });
    }
  }
  return hits.sort((a, b) => a.idx - b.idx);
}

// ---------- parsers ----------
// Yahoo / DK paste: one game per line, e.g.
//   "Bills @ Jets (-3.5)"   "BUF -3.5 at NYJ"   "KC vs LAC -2.5"
// The spread applies to the team it sits closest to. Output: home spread.
function parseLines(text) {
  const games = {};
  for (const raw of text.split(/\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const teams = findTeams(line);
    if (teams.length < 2) continue;
    const numMatch = [...line.matchAll(/([+-]\s?\d+(?:\.\d+)?)|(\bPK\b|\bPICK\b)/gi)][0];
    if (!numMatch) continue;
    let spread = numMatch[2] ? 0 : parseFloat(numMatch[1].replace(/\s/, ""));
    const nIdx = numMatch.index;
    const [a, b] = teams;
    const isVs = /\bvs\.?\b/i.test(line);
    const away = isVs ? b.abbr : a.abbr; // "A vs B" => A is home
    const home = isVs ? a.abbr : b.abbr;
    const nearFirst = Math.abs(nIdx - a.idx) < Math.abs(nIdx - b.idx);
    const spreadTeam = nearFirst ? a.abbr : b.abbr;
    const homeSpread = spreadTeam === home ? spread : -spread;
    games[`${away}@${home}`] = { away, home, spread: homeSpread };
  }
  return games;
}

// FTN DVOA table paste: any line containing a team and a percentage.
function parseDvoa(text) {
  const out = {};
  for (const raw of text.split(/\n/)) {
    const teams = findTeams(raw);
    const pct = raw.match(/(-?\d+(?:\.\d+)?)\s*%/);
    if (teams.length && pct) out[teams[0].abbr] = parseFloat(pct[1]);
  }
  return out;
}

// ---------- The Odds API (DraftKings) ----------
async function fetchDraftKings(key) {
  const url = `https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds/?apiKey=${key}&regions=us&markets=spreads&bookmakers=draftkings&oddsFormat=american`;
  const r = await fetch(url);
  if (!r.ok) throw new Error(`Odds API ${r.status}: ${(await r.text()).slice(0, 120)}`);
  const data = await r.json();
  const games = {};
  for (const ev of data) {
    const home = findTeams(ev.home_team)[0]?.abbr;
    const away = findTeams(ev.away_team)[0]?.abbr;
    const mkt = ev.bookmakers?.[0]?.markets?.find((m) => m.key === "spreads");
    const homeOut = mkt?.outcomes?.find((o) => o.name === ev.home_team);
    if (home && away && homeOut) games[`${away}@${home}`] = { away, home, spread: homeOut.point, kickoff: ev.commence_time };
  }
  return games;
}

// ---------- sample data ----------
const SAMPLE_YAHOO = `Bills @ Jets (-3)
Chiefs @ Chargers +2.5
Lions -6.5 @ Bears
Eagles vs Cowboys +3.5
Ravens @ Bengals -1
49ers @ Rams +4
Packers -7 @ Panthers
Steelers @ Browns PK`;
const SAMPLE_DK = `Bills @ Jets -1.5
Chiefs @ Chargers +1
Lions -9.5 @ Bears
Eagles vs Cowboys +6.5
Ravens @ Bengals -3
49ers @ Rams +1.5
Packers -7 @ Panthers
Steelers @ Browns -2.5`;
const SAMPLE_DVOA = `1 DET 31.4%
2 PHI 28.9%
3 BAL 27.0%
4 KC 22.5%
5 BUF 21.8%
6 SF 18.2%
7 GB 15.6%
8 LAC 12.0%
9 CIN 8.1%
10 PIT 4.4%
11 LAR 2.2%
12 DAL -3.5%
13 NYJ -6.0%
14 CLE -8.2%
15 CHI -12.9%
16 CAR -21.4%`;

// ---------- helpers ----------
const fmt = (n) => (n == null ? "—" : n === 0 ? "PK" : n > 0 ? `+${n}` : `${n}`);
const KEYS = [3, 7];
const crossesKey = (a, b) =>
  a != null && b != null && KEYS.some((k) => (Math.abs(a) - k) * (Math.abs(b) - k) < 0 || Math.abs(a) === k !== (Math.abs(b) === k));

export default function App() {
  const [yahooText, setYahooText] = useState("");
  const [dkText, setDkText] = useState("");
  const [dvoaText, setDvoaText] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [dkFetched, setDkFetched] = useState(null);
  const [fetchState, setFetchState] = useState({ status: "idle", msg: "" });
  const [k, setK] = useState(0.28); // points per DVOA point
  const [hfa, setHfa] = useState(1.5);
  const [minEdge, setMinEdge] = useState(1);

  const yahoo = useMemo(() => parseLines(yahooText), [yahooText]);
  const dk = useMemo(() => dkFetched ?? parseLines(dkText), [dkFetched, dkText]);
  const dvoa = useMemo(() => parseDvoa(dvoaText), [dvoaText]);

  const rows = useMemo(() => {
    const keys = new Set([...Object.keys(yahoo), ...Object.keys(dk)]);
    return [...keys]
      .map((key) => {
        const g = yahoo[key] || dk[key];
        const y = yahoo[key]?.spread ?? null;
        const d = dk[key]?.spread ?? null;
        const hd = dvoa[g.home], ad = dvoa[g.away];
        const m = hd != null && ad != null ? -((hd - ad) * k + hfa) : null;
        const mkt = y != null && d != null ? y - d : null; // + => home gets more pts on Yahoo
        const mdl = y != null && m != null ? y - m : null;
        const side = (v) => (v == null ? null : v > 0 ? g.home : v < 0 ? g.away : null);
        const mSide = side(mkt), dSide = side(mdl);
        const agree = mSide && dSide && mSide === dSide;
        const score = Math.abs(mkt ?? 0) + 0.5 * Math.abs(mdl ?? 0);
        return { key, ...g, y, d, m, mkt, mdl, mSide, dSide, agree, score, keyCross: crossesKey(y, d) };
      })
      .sort((a, b) => b.score - a.score);
  }, [yahoo, dk, dvoa, k, hfa]);

  const [bundleText, setBundleText] = useState("");
  const [bundleMsg, setBundleMsg] = useState("");
  const gamesToText = (g) => Object.entries(g).map(([k, v]) => `${k.split("@")[0]} @ ${k.split("@")[1]} ${fmt(v)}`).join("\n");
  const [bundleUrl, setBundleUrl] = useState("");
  const applyBundle = (b) => {
    if (b.yahoo) setYahooText(gamesToText(b.yahoo));
    if (b.dk) { setDkFetched(null); setDkText(gamesToText(b.dk)); }
    if (b.dvoa) setDvoaText(Object.entries(b.dvoa).map(([t, v]) => `${t} ${v}%`).join("\n"));
    setBundleMsg(`Imported ${Object.keys(b.yahoo || {}).length} Yahoo, ${Object.keys(b.dk || {}).length} DK, ${Object.keys(b.dvoa || {}).length} DVOA${b.pulled_at ? ` (pulled ${b.pulled_at})` : ""}`);
  };
  const loadUrl = async () => {
    try {
      const r = await fetch(bundleUrl.trim());
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      applyBundle(await r.json());
    } catch (e) { setBundleMsg(`Couldn't load that URL (${e.message}). Use the raw.githubusercontent.com link.`); }
  };
  const importBundle = () => {
    try {
      const b = JSON.parse(bundleText);
      applyBundle(b);
    } catch (e) { setBundleMsg("That isn't valid JSON from scrape_picks.py"); }
  };
  const loadSample = () => {
    setYahooText(SAMPLE_YAHOO); setDkText(SAMPLE_DK); setDvoaText(SAMPLE_DVOA); setDkFetched(null);
  };
  const pullDk = async () => {
    setFetchState({ status: "loading", msg: "" });
    try {
      const g = await fetchDraftKings(apiKey.trim());
      setDkFetched(g);
      setFetchState({ status: "ok", msg: `${Object.keys(g).length} games pulled from DraftKings` });
    } catch (e) {
      setFetchState({ status: "err", msg: e.message });
    }
  };

  const C = { ink: "#1C2530", mute: "#6B7684", line: "#D5DAE0", bg: "#F3F5F7", y: "#7A4CC8", dk: "#0E7C5B", model: "#B8641A", flag: "#C8281E" };
  const box = { background: "#fff", border: `1px solid ${C.line}`, borderRadius: 6 };
  const ta = { ...box, width: "100%", minHeight: 150, padding: 10, fontSize: 13, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", resize: "vertical", color: C.ink };
  const lbl = { fontSize: 13, fontWeight: 600, color: C.ink, marginBottom: 4 };
  const hint = { fontSize: 12, color: C.mute, marginBottom: 6, lineHeight: 1.4 };

  return (
    <div style={{ background: C.bg, minHeight: "100vh", color: C.ink, fontFamily: "'Avenir Next', 'Segoe UI', system-ui, sans-serif", padding: "24px 20px 60px" }}>
      <div style={{ maxWidth: 1180, margin: "0 auto" }}>
        <header style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: 12, marginBottom: 18 }}>
          <h1 style={{ fontSize: 26, fontWeight: 700, margin: 0, letterSpacing: -0.3 }}>Where the Yahoo line is wrong</h1>
          <button onClick={loadSample} style={{ ...box, padding: "6px 12px", fontSize: 13, cursor: "pointer" }}>Load sample week</button>
        </header>

        <div style={{ display: "flex", gap: 8, alignItems: "flex-start", marginBottom: 16, flexWrap: "wrap" }}>
          <textarea value={bundleText} onChange={(e) => setBundleText(e.target.value)} placeholder="Paste week.json from scrape_picks.py" style={{ ...ta, minHeight: 40, height: 40, flex: 1, minWidth: 260 }} />
          <input value={bundleUrl} onChange={(e) => setBundleUrl(e.target.value)} placeholder="…or raw GitHub URL of week.json" style={{ ...box, height: 40, padding: "6px 10px", fontSize: 13, flex: 1, minWidth: 240 }} />
          <button onClick={loadUrl} disabled={!bundleUrl} style={{ ...box, padding: "9px 14px", fontSize: 13, cursor: "pointer", opacity: bundleUrl ? 1 : 0.5 }}>Load from URL</button>
          <button onClick={importBundle} disabled={!bundleText} style={{ ...box, padding: "9px 14px", fontSize: 13, cursor: "pointer", opacity: bundleText ? 1 : 0.5 }}>Import bundle</button>
          {bundleMsg && <div style={{ ...hint, alignSelf: "center", margin: 0 }}>{bundleMsg}</div>}
        </div>

        {/* inputs */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 16, marginBottom: 20 }}>
          <div>
            <div style={{ ...lbl, color: C.y }}>Yahoo league lines</div>
            <div style={hint}>Copy the week's games off your Pick'em page. One game per line; the spread attaches to the nearest team. "@" or "at" = second team is home; "vs" = first team is home.</div>
            <textarea style={ta} value={yahooText} onChange={(e) => setYahooText(e.target.value)} placeholder={"Bills @ Jets (-3)\nEagles vs Cowboys +3.5"} />
            <div style={hint}>{Object.keys(yahoo).length} games parsed</div>
          </div>
          <div>
            <div style={{ ...lbl, color: C.dk }}>DraftKings lines</div>
            <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
              <input value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="the-odds-api.com key" style={{ ...box, flex: 1, padding: "6px 8px", fontSize: 13 }} />
              <button onClick={pullDk} disabled={!apiKey || fetchState.status === "loading"} style={{ ...box, padding: "6px 10px", fontSize: 13, cursor: "pointer", background: C.dk, color: "#fff", border: "none", opacity: apiKey ? 1 : 0.5 }}>
                {fetchState.status === "loading" ? "Pulling…" : "Pull"}
              </button>
            </div>
            {fetchState.msg && <div style={{ ...hint, color: fetchState.status === "err" ? C.flag : C.dk }}>{fetchState.msg}</div>}
            <textarea style={{ ...ta, minHeight: 110 }} value={dkFetched ? Object.values(dkFetched).map((g) => `${g.away} @ ${g.home} ${fmt(g.spread)}`).join("\n") : dkText} onChange={(e) => { setDkFetched(null); setDkText(e.target.value); }} placeholder={"…or paste lines in the same format"} />
            <div style={hint}>{Object.keys(dk).length} games {dkFetched ? "pulled" : "parsed"}</div>
          </div>
          <div>
            <div style={{ ...lbl, color: C.model }}>FTN total DVOA</div>
            <div style={hint}>Paste the team DVOA table (team + total DVOA %). Implied home spread = −((home − away) × pts/DVOA + HFA).</div>
            <textarea style={ta} value={dvoaText} onChange={(e) => setDvoaText(e.target.value)} placeholder={"1 DET 31.4%\n2 PHI 28.9%"} />
            <div style={{ display: "flex", gap: 14, alignItems: "center", ...hint }}>
              <span>{Object.keys(dvoa).length} teams</span>
              <label>pts per DVOA pt <input type="number" step="0.02" value={k} onChange={(e) => setK(+e.target.value)} style={{ ...box, width: 58, padding: "2px 4px", marginLeft: 4 }} /></label>
              <label>HFA <input type="number" step="0.5" value={hfa} onChange={(e) => setHfa(+e.target.value)} style={{ ...box, width: 48, padding: "2px 4px", marginLeft: 4 }} /></label>
            </div>
          </div>
        </div>

        {/* board */}
        {rows.length === 0 ? (
          <div style={{ ...box, padding: 28, textAlign: "center", color: C.mute, fontSize: 14 }}>
            Paste Yahoo lines and DraftKings lines (or pull them) to see where they disagree. Try the sample week to see the layout.
          </div>
        ) : (
          <div style={{ ...box, overflowX: "auto" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 14px", borderBottom: `1px solid ${C.line}`, fontSize: 13, color: C.mute }}>
              <span>Sorted by edge. Home spread shown; negative = home favored.</span>
              <label>Flag at ≥ <input type="number" step="0.5" value={minEdge} onChange={(e) => setMinEdge(+e.target.value)} style={{ ...box, width: 52, padding: "2px 4px" }} /> pts</label>
            </div>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
              <thead>
                <tr style={{ color: C.mute, fontSize: 12, textAlign: "right" }}>
                  <th style={{ textAlign: "left", padding: "8px 14px", fontWeight: 500 }}>Game</th>
                  <th style={{ padding: 8, fontWeight: 600, color: C.y }}>Yahoo</th>
                  <th style={{ padding: 8, fontWeight: 600, color: C.dk }}>DK</th>
                  <th style={{ padding: 8, fontWeight: 600, color: C.model }}>DVOA</th>
                  <th style={{ padding: 8, fontWeight: 500 }}>vs market</th>
                  <th style={{ padding: 8, fontWeight: 500 }}>vs model</th>
                  <th style={{ textAlign: "left", padding: "8px 14px", fontWeight: 500 }}>Read</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const flagged = Math.abs(r.mkt ?? 0) >= minEdge;
                  const EdgeCell = ({ v, side }) => (
                    <td style={{ textAlign: "right", padding: 8, fontVariantNumeric: "tabular-nums", color: v == null ? C.mute : Math.abs(v) >= minEdge ? C.ink : C.mute, fontWeight: v != null && Math.abs(v) >= minEdge ? 600 : 400 }}>
                      {v == null ? "—" : `${Math.abs(v).toFixed(1)} ${side ?? ""}`}
                    </td>
                  );
                  let read = "Lines agree.";
                  if (flagged) {
                    read = `Yahoo gives ${r.mSide} ${Math.abs(r.mkt).toFixed(1)} more than DK`;
                    if (r.keyCross) read += ", across a key number";
                    read += r.agree ? ". DVOA agrees." : r.dSide ? `. DVOA leans ${r.dSide}.` : ".";
                  } else if (r.dSide && Math.abs(r.mdl) >= 3) {
                    read = `Market fair; DVOA leans ${r.dSide} by ${Math.abs(r.mdl).toFixed(1)}.`;
                  }
                  return (
                    <tr key={r.key} style={{ borderTop: `1px solid ${C.line}`, background: flagged && r.agree ? "#FFF4E8" : flagged ? "#FBFBF6" : "#fff" }}>
                      <td style={{ padding: "9px 14px", fontWeight: 600, whiteSpace: "nowrap" }}>
                        {r.away} <span style={{ color: C.mute, fontWeight: 400 }}>@</span> {r.home}
                        {flagged && <span style={{ marginLeft: 8, fontSize: 11, fontWeight: 700, color: "#fff", background: r.agree ? C.flag : C.mute, borderRadius: 3, padding: "1px 6px" }}>{r.mSide}</span>}
                      </td>
                      <td style={{ textAlign: "right", padding: 8, fontVariantNumeric: "tabular-nums" }}>{fmt(r.y)}</td>
                      <td style={{ textAlign: "right", padding: 8, fontVariantNumeric: "tabular-nums" }}>{fmt(r.d)}</td>
                      <td style={{ textAlign: "right", padding: 8, fontVariantNumeric: "tabular-nums", color: C.mute }}>{r.m == null ? "—" : fmt(+r.m.toFixed(1))}</td>
                      <EdgeCell v={r.mkt} side={r.mSide} />
                      <EdgeCell v={r.mdl} side={r.dSide} />
                      <td style={{ padding: "9px 14px", color: C.mute, fontSize: 13 }}>{read}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
