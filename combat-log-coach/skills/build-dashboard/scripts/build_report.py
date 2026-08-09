#!/usr/bin/env python3
"""Samlet Combat Log Coach-rapport: analyse, rutekort, coaching og progression
i ét dokument med gennemgående navigation.

Fortælling: hvad skete der -> hvad kostede det -> hvor skete det ->
hvad gør du -> virkede det. Hvert fund i diagnosen linker frem til sin
coaching, og hver coaching-anvisning citerer sin måling og linker tilbage.
"""
import collections
import statistics
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Scriptet ligger i skills/build-dashboard/scripts/ → repo-roden er 4 op.
ROOT = Path(__file__).resolve().parents[4]
PLUGIN = Path(__file__).resolve().parents[2]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".clc-out"
CACHE = Path(sys.argv[2]) if len(sys.argv) > 2 else CACHE
sys.path.insert(0, str(PLUGIN / "analyze-log/scripts"))
import parse as P  # noqa: E402

CSS = ((PLUGIN.parent / "templates/dashboard.css").read_text(encoding="utf-8")
       + (PLUGIN.parent / "templates/coach.css").read_text(encoding="utf-8"))
ME = None   # udledes af summary'ens player-GUID i load()
TELEPORT_YD = 150.0
C = [f"var(--clc-c{i})" for i in range(1, 8)]

KEYS = {
    116: ("1", "Frostbolt", "builder"), 199786: ("1", "Glacial Spike", "spender"),
    30455: ("2", "Ice Lance", "spender"), 44614: ("3", "Flurry", "builder"),
    84714: ("4", "Frozen Orb", "cd"), 120: ("5", "Cone of Cold", "cd"),
    190356: ("6", "Blizzard", "cd"), 205021: ("R", "Ray of Frost", "builder"),
    11426: ("T", "Ice Barrier", "def"), 414658: ("Q", "Ice Cold", "def"),
    342247: ("E", "Alter Time", "def"), 212653: ("Mus frem", "Shimmer", "def"),
    2139: ("Shift-1", "Counterspell", "cd"), 55342: ("?", "Mirror Image", "def"),
    157980: ("?", "Supernova", "cd"), 80353: ("?", "Time Warp", "cd"),
    110959: ("?", "Greater Invisibility", "def"),
    110960: ("?", "Greater Invisibility", "def"),
}
LANES = [("builder", "Builders"), ("spender", "Spenders"),
         ("cd", "Cooldowns"), ("def", "Defensiver")]

NAV = [("overview", "Overblik"), ("group", "Gruppen"), ("diagnosis", "Diagnose"),
       ("route", "Rutekort"), ("waterfall", "Pris"),
       ("coaching", "Coaching"), ("progression", "Progression"),
       ("caveats", "Forbehold")]


def esc(s):
    return html.escape(str(s), quote=True)


def badge(kind):
    lbl = {"measured": "målt", "modeled": "modelleret", "external": "eksternt"}[kind]
    return f'<span class="clc-badge clc-badge--{kind}">{lbl}</span>'


def derive_chrono(summaries):
    """Kronologisk rækkefølge af M+ runs, udledt af summaries.

    Rigtige tidsstempler fra runets t0 — ikke "nu" — så baseline og
    progression er kronologisk ærlige.
    """
    out = []
    for stem, d in summaries.items():
        for r in d["runs"]:
            if r["type"] != "mplus":
                continue
            lens_path = OUT / f"lens-{stem}-run{r['id']}.json"
            if not lens_path.exists():
                continue
            iso = datetime.fromtimestamp(r["t0"], timezone.utc).isoformat(
                timespec="seconds")
            out.append({"iso": iso, "path": str(lens_path), "zone": r["zone"]})
    out.sort(key=lambda e: e["iso"])
    return out


def load():
    global ME
    summaries = {}
    for p in OUT.glob("summary-*.json"):
        d = json.load(open(p, encoding="utf-8"))
        summaries[d["source"]["file"].replace(".txt", "")] = d
    chrono = derive_chrono(summaries)
    if not chrono:
        sys.exit(f"ingen lens-*-runN.json i {OUT} — kør lenses.py pr. run først")
    ME = next(iter(summaries.values()))["player"]["guid"]
    runs = []
    for e in chrono:
        lens = json.load(open(e["path"], encoding="utf-8"))
        lrun = lens["runs"][0]
        stem, rid = lens["log"], lrun["run"]["id"]
        cf = json.load(open(str(e["path"]).replace("lens-", "cf-"),
                            encoding="utf-8"))["runs"][0]
        srun = next(r for r in summaries[stem]["runs"] if r["id"] == rid)
        runs.append({"iso": e["iso"], "lens": lrun, "cf": cf,
                     "summary": srun, "stem": stem, "rid": rid})
    profile = json.load(open(OUT / "profile.json", encoding="utf-8"))
    baseline = json.load(open(OUT / "baseline.json", encoding="utf-8"))
    return runs, profile, baseline


def map_payload(runs):
    """Slim cast/fejl-data pr. pull til det interaktive kort."""
    out = []
    for r in runs:
        rows = list(P.iter_run_events(CACHE, r["stem"], r["rid"]))
        casts = [c for c in rows if c[1] == "SPELL_CAST_SUCCESS" and c[2] == ME]
        taken = [t for t in rows if t[1] in P.DAMAGE_EVENTS and t[4] == ME and t[8]]
        errs = r["lens"]["context"]["events"]
        loss = {}
        for comp in r["cf"]["components"]:
            for e in comp.get("events", []):
                loss[round(e["t"], 3)] = loss.get(round(e["t"], 3), 0) + e["gain_dmg_mid"]
        pulls = []
        for p in r["summary"].get("pulls", []):
            t0, t1 = p["t0"], p["t1"]
            pc = [c for c in casts if t0 <= c[0] <= t1 and c[10] is not None]
            if len(pc) < 4:
                continue
            pe = [e for e in errs if t0 <= e["t"] <= t1]
            blind_t = {round(e["t"], 2) for e in pe if e["type"] == "blind_spender"}
            pulls.append({
                "id": p["id"], "dur": round(p["duration_s"], 1),
                "dmg": p["damage_by_player"].get(ME, 0),
                "loss": round(sum(v for t, v in loss.items() if t0 <= t <= t1)),
                "casts": [[round(c[0] - t0, 2), round(c[10], 1), round(c[11], 1),
                           c[6] or 0, 1 if round(c[0], 2) in blind_t else 0] for c in pc],
                "errs": [[round(e["t"] - t0, 2), round(e["x"], 1), round(e["y"], 1),
                          e["type"], ",".join(e.get("contexts", []))]
                         for e in pe if e.get("x") is not None],
                "taken": [[round(t[0] - t0, 2), t[8]] for t in taken if t0 <= t[0] <= t1],
            })
        out.append({"zone": r["lens"]["run"]["zone"], "key": r["lens"]["run"]["key_level"],
                    "iso": r["iso"][:16].replace("T", " "), "pulls": pulls})
    return out


def group_payload(runs):
    """Gruppedata pr. run: totaler, roller og bidrag pr. pull pr. medlem.

    Roller UDLEDES af data (healer = mest healing, tank = mest skade taget) —
    combat loggen indeholder ingen rolle-erklæring. Det er en slutning.
    Navne pseudonymiseres som default; klienten kan slå dem til.
    """
    out = []
    for r in runs:
        summ = json.load(open(OUT / f"summary-{r['stem']}.json", encoding="utf-8"))
        pets = summ["pets"]
        names = {g["guid"]: g["name"] for g in summ["group"]}
        run = r["summary"]
        dealt, taken, healed, kicks, deaths = (collections.Counter() for _ in range(5))
        over = collections.Counter()
        # skade taget pr. 5 s-vindue pr. spiller pr. pull → spidshed
        buckets = collections.defaultdict(collections.Counter)
        pull_of = [(i, p["t0"], p["t1"]) for i, p in enumerate(run.get("pulls", []))]
        for p in run.get("pulls", []):
            for g, v in p["damage_by_player"].items():
                dealt[pets.get(g, g)] += v
        for row in P.iter_run_events(CACHE, r["stem"], r["rid"]):
            ev, sg, dg, amt, ex = row[1], row[2], row[4], row[8], row[12]
            if ev in P.DAMAGE_EVENTS and dg.startswith("Player-") and amt:
                taken[dg] += amt
                for i, t0, t1 in pull_of:
                    if t0 <= row[0] <= t1:
                        buckets[dg][(i, int((row[0] - t0) // 5))] += amt
                        break
            elif ev in ("SPELL_HEAL", "SPELL_PERIODIC_HEAL") and amt:
                healed[pets.get(sg, sg)] += amt
                if ex and ex.get("overheal"):
                    over[pets.get(sg, sg)] += ex["overheal"]
            elif ev == "SPELL_INTERRUPT":
                kicks[pets.get(sg, sg)] += 1
            elif ev == "UNIT_DIED" and dg.startswith("Player-"):
                deaths[dg] += 1

        def spikiness(g):
            """damage_taken_smoothing pr. spiller — kræver ingen spec-config."""
            per = collections.defaultdict(list)
            for (pi2, _), v in buckets[g].items():
                per[pi2].append(v)
            vals = [max(v) / statistics.median(v)
                    for v in per.values() if len(v) >= 3 and statistics.median(v)]
            return round(statistics.median(vals), 2) if vals else None
        guids = [g for g in dealt if g.startswith("Player-")]
        guids.sort(key=lambda g: -dealt[g])
        healer = max(guids, key=lambda g: healed[g], default=None)
        tank = max((g for g in guids if g != healer),
                   key=lambda g: taken[g], default=None)
        letters = iter("BCDE")
        members = []
        for g in guids:
            is_me = g == ME
            role = ("healer" if g == healer else "tank" if g == tank else "dps")
            members.append({
                "guid": g, "me": is_me,
                "name": names.get(g, g).split("-")[0],
                "alias": "Dig" if is_me else f"Spiller {next(letters)}",
                "role": role,
                "dealt": dealt[g], "taken": taken[g], "healed": healed[g],
                "kicks": kicks[g], "deaths": deaths[g],
                # rolle-metrikker der kan beregnes UDEN spec-config
                "overheal": (round(over[g] / (healed[g] + over[g]), 3)
                             if healed[g] + over[g] else None),
                "spikiness": spikiness(g),
                "perPull": [p["damage_by_player"].get(g, 0)
                            + sum(v for pg, v in p["damage_by_player"].items()
                                  if pets.get(pg) == g)
                            for p in run.get("pulls", [])],
            })
        out.append({
            "zone": r["lens"]["run"]["zone"], "key": r["lens"]["run"]["key_level"],
            "members": members,
            "pulls": [{"id": p["id"], "dur": round(p["duration_s"], 1),
                       "total": p["damage_total"]} for p in run.get("pulls", [])],
        })
    return out


def spam_stat(payload):
    """Andel blinde tryk der følger direkte efter et andet Ice Lance."""
    blind = after = longest = 0
    for r in payload:
        for p in r["pulls"]:
            streak = 0
            for i, c in enumerate(p["casts"]):
                if c[3] != 30455:
                    longest = max(longest, streak)
                    streak = 0
                    continue
                streak += 1
                if not c[4]:
                    continue
                blind += 1
                if i and p["casts"][i - 1][3] == 30455:
                    after += 1
            longest = max(longest, streak)
    return blind, after, longest


def bars(rows, series, w=700, bar_h=20, gap=7, fmt=lambda v: f"{v:.0%}", vmax=None):
    n = len(series)
    gh = n * bar_h + gap * 2
    h = len(rows) * gh + 26
    left, span = 185, w - 185 - 85
    vmax = vmax or max((max(v for v in vals if v is not None)
                        for _, vals in rows if any(x is not None for x in vals)),
                       default=1) or 1
    o = [f'<svg viewBox="0 0 {w} {h}" role="img">']
    for gi, (label, vals) in enumerate(rows):
        y0 = gi * gh + gap
        o.append(f'<text x="0" y="{y0 + gh / 2}" class="clc-axis-label" '
                 f'dominant-baseline="middle">{esc(label)}</text>')
        for si, v in enumerate(vals):
            if v is None:
                continue
            y = y0 + si * bar_h + 2
            bw = max(1, span * v / vmax)
            o.append(f'<rect x="{left}" y="{y}" width="{bw:.1f}" height="{bar_h - 4}" '
                     f'fill="{series[si][1]}" rx="2"/>'
                     f'<text x="{left + bw + 6}" y="{y + (bar_h - 4) / 2}" '
                     f'class="clc-axis-label" dominant-baseline="middle">'
                     f'{esc(fmt(v))}</text>')
    o.append("</svg>")
    leg = '<div class="clc-legend">' + "".join(
        f'<span><i style="background:{c}"></i>{esc(nm)}</span>' for nm, c in series) + "</div>"
    return f'<div class="clc-chart">{"".join(o)}</div>{leg}'


def waterfall_svg(comps, actual, w=700):
    h = len(comps) * 42 + 40
    left, span = 185, w - 185 - 120
    vmax = max(c["gain_dmg"][2] for c in comps) or 1
    o = [f'<svg viewBox="0 0 {w} {h}" role="img">']
    for i, c in enumerate(comps):
        lo, mid, hi = c["gain_dmg"]
        y = i * 42 + 10
        xl, xh, xm = (left + span * lo / vmax, left + span * hi / vmax,
                      left + span * mid / vmax)
        o.append(f'<text x="0" y="{y + 11}" class="clc-axis-label" '
                 f'dominant-baseline="middle">{esc(c["id"])} ({c["count"]})</text>'
                 f'<line x1="{xl:.1f}" x2="{xh:.1f}" y1="{y + 11}" y2="{y + 11}" '
                 f'stroke="var(--clc-potential)" stroke-width="2" class="clc-modeled-stroke"/>'
                 f'<rect x="{left}" y="{y + 3}" width="{max(1, xm - left):.1f}" height="16" '
                 f'fill="var(--clc-potential)" opacity=".55" rx="2"/>'
                 f'<text x="{w - 112}" y="{y + 11}" class="clc-axis-label" '
                 f'dominant-baseline="middle">{mid / 1e6:.1f} mio '
                 f'(+{mid / actual if actual else 0:.0%})</text>')
    o.append("</svg>")
    return (f'<div class="clc-chart">{"".join(o)}</div>'
            '<div class="clc-legend"><span><i style="background:var(--clc-potential)"></i>'
            'modelleret gevinst (midt)</span><span>whisker = interval lo–hi</span></div>')


def build(runs, profile, baseline, payload, group):
    last = runs[-1]
    blind, after, longest = spam_stat(payload)
    kb = profile["player"]["keybinds"]
    parts = []

    # --- overblik -----------------------------------------------------------
    cards = []
    for r in runs:
        run, bs = r["lens"]["run"], r["lens"]["rotation"]["blind_spenders"]["value"]
        cards.append(
            f'<div class="clc-card"><h3>{esc(run["zone"])} +{run["key_level"]}</h3>'
            f'<p class="clc-dim">{esc(r["iso"][:16].replace("T", " "))}</p>'
            f'<p class="clc-stat">{r["cf"]["actual"]["value"]["dps"]:,.0f}<span> DPS</span></p>'
            f'<dl class="clc-kv"><dt>Blind ST</dt><dd>{bs["blind_spender_rate_st"]:.0%}</dd>'
            f'<dt>Blind AoE</dt><dd>{bs["blind_spender_rate_aoe"]:.0%}</dd>'
            f'<dt>Døde</dt><dd>{len(r["lens"]["survival"]["death_recaps"]["value"])}</dd>'
            f'</dl></div>')
    ribbon = "".join('<span class="kc kc--bad">2</span>' for _ in range(longest))
    parts.append(f'''<section data-clc-section="overview" id="overview">
<p class="eyebrow">Combat Log Coach · {esc(profile["player"]["name"])} · Frost Mage</p>
<h1>Tretten tryk i træk</h1>
<div class="ribbon ribbon--hero">{ribbon}</div>
<p class="clc-lede">Det er din længste ubrudte stime på <span class="kc kc--sm">2</span>
(Ice Lance). {after / blind:.0%} af dine {blind} blinde tryk følger et andet
Ice Lance — så det er ikke en fejlvurdering af hvornår tasten er klar.
Det er en vane i fingrene.</p>
<p>Fire timede M+ runs, 6. august 2026. Alle blev gennemført. Det her handler
ikke om at klare nøglen, men om hvad der bliver liggende på gulvet undervejs.</p>
<div class="clc-cards">{"".join(cards)}</div>
<p class="clc-hint">Gruppemedlemmer er pseudonymiseret. Rå logs forlader ikke
maskinen — kun aggregater.</p></section>''')

    # --- gruppen (øverste lag: hele holdet før dig selv) --------------------
    parts.append('''<section data-clc-section="group" id="group">
<h2>Gruppen</h2>
<p>Hele holdet først — din præstation giver kun mening i forhold til de fire
andre. Vælg et run, klik et medlem for at folde det ud.</p>
<div class="clc-toolbar" id="g-runs"></div>
<p class="clc-hint">Navne er pseudonymiseret som default.
<button class="clc-toggle" id="g-names" aria-pressed="false">Vis rigtige navne</button></p>
<div class="clc-roletabs" id="g-roles" role="tablist"></div>
<p class="clc-hint" id="g-rolenote" style="margin-bottom:.6rem"></p>
<div class="clc-cards" id="g-members"></div>
<div class="clc-grid" style="margin-top:1rem">
<div class="clc-panel"><h3>Skade pr. pull</h3>
<p class="clc-hint" style="margin-top:0">Hver søjle er en pull; farverne er
medlemmernes andel. Bredden er pullens varighed.</p>
<div class="clc-chart" id="g-pulls"></div><div class="clc-legend" id="g-legend"></div></div>
<div class="clc-panel" id="g-detail"></div></div>
<p class="clc-hint">Roller er <b>udledt af data</b>, ikke erklæret i loggen:
healeren er den med mest healing, tanken den med mest skade taget. Kicks
tælles som gennemførte interrupts — muligheden for at kicke er styret af hvad
mobsene caster, så et lavt tal er ikke nødvendigvis en fejl.</p></section>''')

    # --- diagnose (bindeled til coaching) -----------------------------------
    rows = [(f'{r["lens"]["run"]["zone"]} +{r["lens"]["run"]["key_level"]}',
             [r["lens"]["rotation"]["blind_spenders"]["value"]["blind_spender_rate_st"],
              r["lens"]["rotation"]["blind_spenders"]["value"]["blind_spender_rate_aoe"]])
            for r in runs]
    fof = [r["lens"]["rotation"]["proc_stats"]["value"]["per_aura"]["44544"] for r in runs]
    fof_lo = min(f["munch"] / f["gained"] for f in fof)
    fof_hi = max(f["munch"] / f["gained"] for f in fof)
    mv = [r["lens"]["movement"]["lost_moving_share"]["value"] for r in runs]
    dt = last["lens"]["survival"]["defensive_timing"]["value"]
    parts.append(f'''<section data-clc-section="diagnosis" id="diagnosis">
<h2>Diagnose</h2>
<p>Tre fund, rangeret efter hvad de koster. Hvert af dem har en anvisning
under <a href="#coaching">Coaching</a>.</p>

<div class="clc-finding">
<h3>1 · Du spammer spender-tasten {badge("measured")}</h3>
{bars(rows, [("single target", C[0]), ("AoE (3+ mål)", C[2])])}
<p>Blinde tryk på <b>2 (Ice Lance)</b> — uden 6+ Freezing-stakke og uden
Fingers of Frost. Cirka dobbelt så hyppigt i AoE som i single target.</p>
<p class="clc-key">Men fordelingen afslører noget vigtigere:
<b>{after / blind:.0%} af alle {blind} blinde tryk kommer direkte efter et
andet Ice Lance.</b> Din længste ubrudte stime er {longest} tryk i træk. Det er
ikke en fejlvurdering af tærsklen — et enkelt for tidligt tryk gentager sig
ikke tolv gange. Det er en tastevane.</p>
<p><a class="clc-jump" href="#phases">Se mønsteret pull for pull →</a>
<a class="clc-jump" href="#coach-spender">Hvad du gør ved det →</a></p>
</div>

<div class="clc-finding">
<h3>2 · Procs falder på gulvet {badge("measured")}</h3>
<p>Fingers of Frost stakker kun til 2. Mellem
<b>{fof_lo:.0%} og {fof_hi:.0%}</b> af dine procs lander oveni en aktiv proc
og går tabt. Til sammenligning forbruger du Brain Freeze og Glacial Spike!
nærmest perfekt — det er ikke manglende opmærksomhed generelt, det er denne
ene proc.</p>
<p><a class="clc-jump" href="#coach-proc">Hvad du gør ved det →</a></p>
</div>

<div class="clc-finding">
<h3>3 · Defensiverne kommer efter smækket {badge("measured")}</h3>
<p>Median <b>{dt["median_s_from_hit_to_defensive"]} s</b> fra du tager et stort
hit til du trykker defensiv — stil: <b>{esc(dt["style"])}</b>. Din nødknap
(Q, Ice Cold) bruges reelt på cooldown, så det er ikke dovenskab. Mellemlaget
står ubrugt: Alter Time på E ligger på 29 % af det mulige.</p>
<p>Dertil ligger {min(mv):.0%}–{max(mv):.0%} af din tabte casttid i bevægelse.</p>
<p><a class="clc-jump" href="#coach-def">Hvad du gør ved det →</a></p>
</div>
</section>''')

    # --- rutekort (interaktivt) ---------------------------------------------
    parts.append('''<section data-clc-section="route" id="route">
<h2>Hvor det sker</h2>
<p>Din cast-sti pr. pull med fejlene plottet dér hvor du stod. Vælg et run,
klik en pull-strip: bredden er varighed, farven er tab pr. sekund, så arealet
svarer til pullens pris.</p>
<div class="clc-toolbar" id="runs"></div>
<div class="clc-strips" id="strips"></div>
<div class="clc-grid">
<div class="clc-panel"><h3>Rute</h3><div class="clc-chart" id="route-svg"></div>
<p class="clc-hint" id="routehint"></p>
<div class="clc-legend">
<span><i style="background:var(--clc-error)"></i>blind spender</span>
<span><i style="background:var(--clc-movement)"></i>cast-hul</span>
<span><i style="background:var(--clc-c5)"></i>selvafbrud</span></div>
<h3 style="margin-top:1rem">Replay</h3><div class="clc-chart" id="replay"></div>
<p class="clc-hint">Lanes efter dine keybinds. Røde markeringer er blinde
tryk på 2.</p></div>
<div class="clc-panel" id="panel"></div></div></section>

<section data-clc-section="phases" id="phases">
<h3>Det skete / spil det sådan</h3>
<p class="clc-dim">De to casts før hvert blindt tryk i den valgte pull,
grupperet. Viser vanen frem for de enkelte fejl.</p>
<div class="clc-panel" id="phases-body"></div></section>

<section data-clc-section="compare" id="compare-sec">
<h3 id="comparetitle">Samme dungeon, to kørsler</h3>
<p class="clc-dim">Begge ruter i samme skala, så klynger kan genkendes på
tværs. Røde prikker er blinde tryk.</p>
<div class="clc-compare" id="compare"></div></section>''')

    # --- pris ---------------------------------------------------------------
    cf = last["cf"]
    parts.append(f'''<section data-clc-section="waterfall" id="waterfall">
<h2>Hvad det koster {badge("modeled")}</h2>
<p>"Samme run, spillet rigtigt" — et overslag med interval, ikke en
simulering. Komponenterne beregnes uafhængigt og kan overlappe.</p>
{waterfall_svg(cf["components"], cf["actual"]["value"]["damage"])}
<p class="clc-hint">{esc(cf["reconciliation"]["note"])}</p>
<p class="clc-hint">Tallet for blinde spenders er endda konservativt: på
Spellslinger konjurerer hver 2 shattrede Freezing-stakke en Frost Splinter,
så et blindt tryk koster både Shatter-skaden og de Splinters. Modellen måler
kun det første.</p></section>''')

    # --- coaching -----------------------------------------------------------
    kbrows = "".join(
        f'<tr><td>{esc(role)}</td><td><span class="kc kc--sm">{esc(b["key"])}</span></td>'
        f'<td>{esc(b["note"].split(" —")[0])}</td></tr>'
        for role, b in kb.items())
    parts.append(f'''<section data-clc-section="coaching" id="coaching">
<h2>Hvad du gør</h2>
<p>Alt herunder står i dine egne taster. <b>Øv én ting ad gangen</b> — planen
har aldrig mere end to fokuspunkter, og det første er optaget.</p>

<div class="clc-coach" id="coach-spender">
<h3>Næste dungeon: kun denne ene regel</h3>
<p class="clc-rule">Efter hvert tryk på <span class="kc">2</span> skal der komme
noget andet før <span class="kc">2</span> igen.</p>
<p>Den er testbar uden at kunne se stakke, og den rammer {after / blind:.0%} af
fejlen. Den længere version, hvis du vil have den præcise tærskel:
<span class="kc kc--sm">2</span> har to tilladelser — Fingers of Frost er oppe, eller den
mob du hard-caster på har 6+ stakke. Ingen tilladelse → <span class="kc kc--sm">1</span>.</p>
<p class="clc-hint">Grundlag: <a href="#diagnosis">diagnose 1</a> ·
tærsklen er Spellslinger-reglen fra Method og Icy&nbsp;Veins&nbsp;{badge("external")}</p>
</div>

<div class="clc-coach" id="coach-proc">
<h3>Byg denne i aften</h3>
<p>Fingers of Frost er en <b>spiller</b>-buff, og det gør den byggbar —
i modsætning til Freezing. I din egen EllesmereUI-kode:</p>
<ol>
<li>Læg Fingers of Frost på en CDM tracking bar</li>
<li>Slå <span class="clc-seq">stackThresholdEnabled</span> til med
<span class="clc-seq">stackThreshold = 2</span> — baren skifter farve i det
øjeblik den næste proc ville gå tabt</li>
<li><span class="clc-seq">EllesmereUICdmBarGlows</span> kan oveni lyse
<b>tast 2</b> op når buffen er aktiv</li>
</ol>
<p class="clc-hint">Tærsklerne er secret-safe: Blizzards Cooldown Manager
beregner stak-antallet, og addon'et driver en StatusBar hvis range koder
grænsen — den læser aldrig tallet. Forbehold: jeg kan se at systemet findes,
ikke om Blizzard har lagt Fingers of Frost i Frost Mages CDM-kategorisæt.
Det ser du i spell pickeren.</p>
</div>

<div class="clc-coach" id="coach-def">
<h3>Når spender-reglen sidder</h3>
<p><span class="kc kc--sm">T</span> <b>(Ice Barrier) har ingen cooldown</b> — mindste afstand mellem to af
dine casts er 0,7 s. Der er intet at spare. Tryk den før pullen og hold den
oppe i stedet for efter smækket.</p>
<p><span class="kc kc--sm">E</span> <b>(Alter Time) på 29 %</b> er dit ubrugte mellemlag. Q bruges allerede
på cooldown, så det er dér gevinsten ligger.</p>
<p><span class="kc kc--sm">5</span> bruges 5 gange på 82 minutter (Cone of Cold). Mirror Image
ligger på 37 % og har ingen fast tast — det er byttet der giver mest.</p>
</div>

<div class="clc-coach">
<h3>Det kan ikke bygges — og hvorfor</h3>
<p>En cue på Freezing-stakke findes ikke i 12.0.7. Secret Values blokerer at
addons læser fjende-debuffs, og EllesmereUIs aura-motor er gated bag
<span class="clc-seq">IS_121</span> — den gør bogstaveligt talt ingenting på
din klient. I 12.1 får du en <b>visning</b> af stak-antallet på nameplates,
men stadig ingen tærskel: CDM-tærsklerne virker kun på det Blizzard sporer,
og det er dine egne cooldowns og buffs.</p>
<p class="clc-hint">Derfor er reglen ovenfor formuleret så den ikke kræver at
du kan se tallet.</p>
</div>

<h3>Dine binds</h3>
<table class="clc-table"><thead><tr><th>Rolle</th><th>Tast</th><th>Spell</th>
</tr></thead><tbody>{kbrows}</tbody></table>
<p class="clc-hint">Roller bevares på tværs af specs: skifter du klasse,
oversættes anvisningerne via rollen, ikke spell-navnet.</p>

<h3>To ting jeg ikke coacher imod</h3>
<p><b>Blizzard</b> <span class="kc kc--sm">6</span> <b>på 58 %</b> hører kun til i AoE med Freezing Rain, og ikke
hver pull er AoE. <b>Time Warp på 25 %</b> er en gruppe-cooldown der ofte er
fordelt. Begge kan være vilkår frem for fejl.</p>
</section>''')

    # --- progression --------------------------------------------------------
    goals = baseline.get("goals", [])
    cur = baseline["entries"][-1]["metrics"]
    grows = ""
    for g in goals:
        v = cur.get(g["metric"])
        ok = v is not None and ((v < g["target"]) if g["op"] == "<" else (v <= g["target"]))
        val = f"{v:.1%}" if isinstance(v, float) and v <= 1 else f"{v}"
        tgt = f"{g['target']:.0%}" if g["target"] <= 1 else f"{g['target']:.0f}"
        grows += (f'<tr><td class="clc-seq">{esc(g["metric"])}</td>'
                  f'<td class="clc-seq">{esc(g["op"])} {tgt}</td>'
                  f'<td class="clc-seq">{val}</td>'
                  f'<td>{"✓ nået" if ok else "✗ ikke nået"}</td>'
                  f'<td class="clc-dim">{esc(g.get("note", ""))}</td></tr>')
    ws = [e for e in baseline["entries"]
          if any(r.get("zone") == "Windrunner Spire" for r in e["runs"])]
    delta = ""
    if len(ws) >= 2:
        a, b = ws[0]["metrics"], ws[-1]["metrics"]
        for k in sorted(set(a) & set(b)):
            va, vb = a[k], b[k]
            if va is None or vb is None:
                continue
            better = vb < va if k not in ("casts_per_min", "proc_utilization",
                                          "cd_discipline", "sustain_min_phase_share") else vb > va
            mark = "✓" if vb != va and better else ("·" if vb == va else "✗")
            f = (lambda x: f"{x:.1%}") if isinstance(va, float) and va <= 1 else (lambda x: f"{x:g}")
            delta += (f'<tr><td class="clc-seq">{esc(k)}</td><td class="clc-seq">{f(va)}</td>'
                      f'<td class="clc-seq">{f(vb)}</td><td>{mark}</td></tr>')
    parts.append(f'''<section data-clc-section="progression" id="progression">
<h2>Virkede det?</h2>
<p>Målene er gemt i baselinen og evalueres automatisk næste gang du logger.
Kør en dungeon, så viser denne sektion delta'et.</p>
<table class="clc-table"><thead><tr><th>Metrik</th><th>Mål</th><th>Nu</th>
<th>Status</th><th>Note</th></tr></thead><tbody>{grows}</tbody></table>
<h3>Windrunner Spire, morgen mod aften</h3>
<p class="clc-dim">Samme dungeon prioriteres i sammenligningen. Bemærk at
nøglen steg fra +10 til +11 — det er ikke en ren sammenligning.</p>
<table class="clc-table"><thead><tr><th>Metrik</th><th>+10</th><th>+11</th>
<th></th></tr></thead><tbody>{delta}</tbody></table></section>''')

    parts.append((OUT / "caveats.html").read_text(encoding="utf-8")
                 .replace('data-clc-section="caveats"',
                          'data-clc-section="caveats" id="caveats"'))

    nav = "".join(f'<a href="#{i}">{esc(t)}</a>' for i, t in NAV)
    data = json.dumps({"runs": payload, "group": group,
                       "keys": {str(k): list(v) for k, v in KEYS.items()},
                       "lanes": LANES, "teleport": TELEPORT_YD},
                      separators=(",", ":"))
    return (f'<!doctype html><html lang="da"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Combat Log Coach — {esc(profile["player"]["name"])}</title>'
            f'<style>{CSS}</style></head><body>'
            f'<nav class="clc-nav" aria-label="Sektioner">{nav}</nav>'
            f'<main>{"".join(parts)}</main>'
            f'<script>window.__CLC__={data};</script><script>{JS}</script>'
            f'</body></html>')


JS = r"""
const D = window.__CLC__;
let ri = D.runs.length - 1, pi = 0;
const $ = s => document.querySelector(s);
function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML}
function fmt(n){return n.toLocaleString('da-DK')}

function runButtons(){
  $('#runs').innerHTML = D.runs.map((r,i)=>
    `<button data-r="${i}" aria-pressed="${i===ri}">${esc(r.zone)} +${r.key}</button>`).join('');
}
function strips(){
  const r = D.runs[ri];
  const mx = Math.max(...r.pulls.map(p=>p.loss/Math.max(p.dur,1)), 1);
  $('#strips').innerHTML = r.pulls.map((p,i)=>{
    const sh = (p.loss/Math.max(p.dur,1))/mx;
    return `<div class="clc-strip" role="button" tabindex="0" data-p="${i}"
      aria-pressed="${i===pi}" style="width:${Math.max(14,p.dur*1.5)}px;
      background:color-mix(in srgb, var(--clc-error) ${Math.round(sh*72)}%, var(--clc-surface-2))"
      title="Pull ${p.id} · ${p.dur}s · ${fmt(p.dmg)} skade · ${p.errs.length} fejl · ≈${fmt(p.loss)} tabt">${p.id}</div>`;
  }).join('');
}
function route(){
  const p = D.runs[ri].pulls[pi];
  const xs=p.casts.map(c=>c[1]), ys=p.casts.map(c=>c[2]);
  const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
  const side=Math.max(x1-x0,y1-y0,15)*1.18, cx=(x0+x1)/2, cy=(y0+y1)/2, u=side/100;
  let o=`<svg viewBox="${cx-side/2} ${-(cy+side/2)} ${side} ${side}" role="img"
    aria-label="Rutekort for pull ${p.id}"><g transform="scale(1,-1)">`;
  let cuts=0;
  for(let i=1;i<p.casts.length;i++){
    const a=p.casts[i-1],b=p.casts[i];
    if(Math.hypot(b[1]-a[1],b[2]-a[2])>D.teleport){cuts++;
      o+=`<text x="${b[1]}" y="${-b[2]}" transform="scale(1,-1)"
        font-size="${(u*3).toFixed(2)}" fill="var(--clc-text-dim)">✂</text>`;continue;}
    o+=`<line x1="${a[1]}" y1="${a[2]}" x2="${b[1]}" y2="${b[2]}"
      stroke="var(--clc-actual)" stroke-opacity="${(0.18+0.82*i/p.casts.length).toFixed(2)}"
      stroke-width="${(u*0.45).toFixed(2)}" stroke-linecap="round"/>`;
  }
  for(const c of p.casts) o+=`<circle cx="${c[1]}" cy="${c[2]}" r="${(u*0.5).toFixed(2)}"
    fill="var(--clc-actual)" fill-opacity=".35"/>`;
  for(const e of p.errs){
    const col=e[3]==='blind_spender'?'var(--clc-error)':e[3]==='selfcancel'?'var(--clc-c5)':'var(--clc-movement)';
    o+=`<circle class="clc-error-dot" cx="${e[1]}" cy="${e[2]}" r="${(u*1.35).toFixed(2)}"
      fill="${col}" fill-opacity=".72" stroke="var(--clc-bg)"
      stroke-width="${(u*0.25).toFixed(2)}"><title>${e[0]}s · ${e[3]}${e[4]?' · '+e[4]:''}</title></circle>`;
  }
  const s=p.casts[0], f=p.casts[p.casts.length-1];
  o+=`<circle cx="${s[1]}" cy="${s[2]}" r="${(u*1.9).toFixed(2)}" fill="none"
    stroke="var(--clc-text)" stroke-width="${(u*0.4).toFixed(2)}"/>`;
  o+=`<circle cx="${f[1]}" cy="${f[2]}" r="${(u*1.6).toFixed(2)}" fill="var(--clc-text)"/>`;
  const bar=Math.max(5,Math.round(side/4/5)*5), bx=cx-side/2+u*6, by=cy-side/2+u*7;
  o+=`<line x1="${bx}" y1="${by}" x2="${bx+bar}" y2="${by}" stroke="var(--clc-text-dim)"
    stroke-width="${(u*0.35).toFixed(2)}"/><text x="${bx}" y="${-(by+u*3.4)}"
    transform="scale(1,-1)" font-size="${(u*3).toFixed(2)}"
    fill="var(--clc-text-dim)">${bar} yd</text>`;
  o+=`</g></svg>`;
  $('#route-svg').innerHTML=o;
  $('#routehint').textContent=`Ring = start, fyldt = slut. ${cuts} spring over ${D.teleport} yd klippet.`;
}
function replay(){
  const p=D.runs[ri].pulls[pi], W=560, lh=17, top=4, dur=Math.max(p.dur,1);
  let o=`<svg viewBox="0 0 ${W} ${lh*(D.lanes.length+2)+14}" role="img"
    aria-label="Replay for pull ${p.id}">`;
  D.lanes.forEach(([id,label],li)=>{
    const y=top+li*lh;
    o+=`<text x="0" y="${y+9}" class="clc-axis-label">${label}</text>`;
    for(const c of p.casts){
      const k=D.keys[c[3]]; if(!k||k[2]!==id) continue;
      const x=92+(c[0]/dur)*(W-100);
      o+=`<rect x="${x.toFixed(1)}" y="${y+1}" width="2.4" height="${lh-5}"
        fill="${c[4]?'var(--clc-error)':'var(--clc-actual)'}"
        fill-opacity="${c[4]?0.95:0.6}"><title>${c[0]}s · ${k[0]} (${k[1]})${c[4]?' · BLIND':''}</title></rect>`;
    }
  });
  const yD=top+D.lanes.length*lh, mt=Math.max(...p.taken.map(t=>t[1]),1);
  o+=`<text x="0" y="${yD+9}" class="clc-axis-label">Skade taget</text>`;
  for(const t of p.taken){
    const x=92+(t[0]/dur)*(W-100), hh=Math.max(1,(t[1]/mt)*(lh-4));
    o+=`<rect x="${x.toFixed(1)}" y="${yD+1}" width="1.6" height="${hh.toFixed(1)}"
      fill="var(--clc-death)" fill-opacity=".7"><title>${t[0]}s · ${fmt(t[1])}</title></rect>`;
  }
  $('#replay').innerHTML=o+`</svg>`;
}
function panel(){
  const p=D.runs[ri].pulls[pi], byType={};
  for(const e of p.errs) byType[e[3]]=(byType[e[3]]||0)+1;
  const blind=p.casts.filter(c=>c[4]).length;
  const sp=p.casts.filter(c=>D.keys[c[3]]&&D.keys[c[3]][2]==='spender').length;
  $('#panel').innerHTML=`<h3>Pull ${p.id}</h3><dl class="clc-kv">
    <dt>Varighed</dt><dd>${p.dur} s</dd><dt>Egen skade</dt><dd>${fmt(p.dmg)}</dd>
    <dt>Casts</dt><dd>${p.casts.length}</dd>
    <dt>Blinde spenders</dt><dd>${blind} af ${sp}${sp?` (${Math.round(blind/sp*100)} %)`:''}</dd>
    ${Object.entries(byType).map(([k,v])=>`<dt>${k}</dt><dd>${v}</dd>`).join('')}
    <dt>Modelleret tab ≈</dt><dd>${fmt(p.loss)}</dd></dl>
    <p class="clc-hint"><a class="clc-jump" href="#coach-spender">Hvad du gør →</a></p>`;
}
function phases(){
  const p=D.runs[ri].pulls[pi], seqs={};
  for(let i=0;i<p.casts.length;i++){
    if(!p.casts[i][4]) continue;
    const win=p.casts.slice(Math.max(0,i-2),i+1);
    const keys=win.map(c=>(D.keys[c[3]]||['?'])[0]), k=keys.join(' → ');
    if(!seqs[k]) seqs[k]={n:0,keys};
    seqs[k].n++;
  }
  const rows=Object.values(seqs).sort((a,b)=>b.n-a.n).slice(0,6);
  if(!rows.length){$('#phases-body').innerHTML='<p class="clc-dim">Ingen blinde tryk i denne pull.</p>';return;}
  const tot=rows.reduce((s,r)=>s+r.n,0);
  $('#phases-body').innerHTML=`<table class="clc-table"><thead><tr>
    <th>Det skete</th><th>Spil det sådan</th><th>Antal</th></tr></thead><tbody>
    ${rows.map(r=>`<tr><td class="clc-seq">${r.keys.map((k,i)=>
      i===r.keys.length-1?`<span class="kc kc--sm kc--bad">${esc(k)}</span>`
        :`<span class="kc kc--sm">${esc(k)}</span>`).join('<span class="arrow"> › </span>')}</td>
      <td>${r.keys.slice(0,-1).concat(['1']).map(k=>
        `<span class="kc kc--sm">${esc(k)}</span>`).join('<span class="arrow"> › </span>')}</td>
      <td>${r.n}</td></tr>`).join('')}</tbody></table>
    <p class="clc-hint">${tot} blinde tryk i denne pull. Venstre kolonne er målt;
    højre er <a href="#coach-spender">tilladelsesreglen</a> anvendt mekanisk.</p>`;
}
function compare(){
  const g={}; D.runs.forEach((r,i)=>{(g[r.zone]=g[r.zone]||[]).push(i)});
  const pair=Object.entries(g).find(([,v])=>v.length>1);
  if(!pair){$('#compare-sec').style.display='none';return;}
  const [zone,idx]=pair;
  const ext=idx.map(i=>{const cs=D.runs[i].pulls.flatMap(p=>p.casts);
    const xs=cs.map(c=>c[1]),ys=cs.map(c=>c[2]);
    return {x0:Math.min(...xs),x1:Math.max(...xs),y0:Math.min(...ys),y1:Math.max(...ys)}});
  const side=Math.max(...ext.map(e=>Math.max(e.x1-e.x0,e.y1-e.y0)))*1.1;
  $('#comparetitle').textContent=`${zone} — to kørsler i samme skala`;
  $('#compare').innerHTML=idx.map((i,k)=>{
    const e=ext[k], r=D.runs[i], cx=(e.x0+e.x1)/2, cy=(e.y0+e.y1)/2, u=side/100;
    let s=`<svg viewBox="${cx-side/2} ${-(cy+side/2)} ${side} ${side}" role="img"
      aria-label="Rute for ${r.zone} +${r.key}"><g transform="scale(1,-1)">`;
    for(const p of r.pulls){
      for(let j=1;j<p.casts.length;j++){
        const a=p.casts[j-1],b=p.casts[j];
        if(Math.hypot(b[1]-a[1],b[2]-a[2])>D.teleport) continue;
        s+=`<line x1="${a[1]}" y1="${a[2]}" x2="${b[1]}" y2="${b[2]}"
          stroke="var(--clc-actual)" stroke-opacity=".3" stroke-width="${(u*0.3).toFixed(2)}"/>`;
      }
      for(const e2 of p.errs){ if(e2[3]!=='blind_spender') continue;
        s+=`<circle cx="${e2[1]}" cy="${e2[2]}" r="${(u*0.85).toFixed(2)}"
          fill="var(--clc-error)" fill-opacity=".5"/>`;}
    }
    const bl=r.pulls.reduce((n,p)=>n+p.errs.filter(x=>x[3]==='blind_spender').length,0);
    return `<div><h4>+${r.key} · ${esc(r.iso)}</h4><div class="clc-chart">${s}</g></svg></div>
      <p class="clc-hint">${bl} blinde tryk · ${r.pulls.length} pulls</p></div>`;
  }).join('');
}
// ---- Gruppelag --------------------------------------------------------
let gi = D.group.length - 1, gm = null, gNames = false;
const ROLE = {tank:'Tank', healer:'Healer', dps:'DPS'};
const MCOL = i => `var(--clc-c${(i%7)+1})`;
const label = m => gNames ? m.name : m.alias;

function gRuns(){
  $('#g-runs').innerHTML = D.group.map((g,i)=>
    `<button data-g="${i}" aria-pressed="${i===gi}">${esc(g.zone)} +${g.key}</button>`).join('');
}
// Rollelaget: metrikkerne der vises afhænger af rollen, fordi de metrikker
// der giver mening gør det. Kun dem der kan beregnes UDEN spec-config kan
// vises for andre end dig selv.
const ROLEMETRICS = {
  all:    [['dealt','Skade',m=>(m.dealt/1e6).toFixed(1)+' mio'],
           ['taken','Taget',m=>(m.taken/1e6).toFixed(1)+' mio'],
           ['kicks','Kicks',m=>m.kicks], ['deaths','Døde',m=>m.deaths]],
  tank:   [['taken','Skade taget',m=>(m.taken/1e6).toFixed(1)+' mio'],
           ['spikiness','Spidshed',m=>m.spikiness!=null?m.spikiness+'x':'—'],
           ['healed','Selvheal',m=>(m.healed/1e6).toFixed(1)+' mio'],
           ['deaths','Døde',m=>m.deaths]],
  healer: [['healed','Heling',m=>(m.healed/1e6).toFixed(1)+' mio'],
           ['overheal','Overheal',m=>m.overheal!=null?(m.overheal*100).toFixed(0)+' %':'—'],
           ['dealt','Skade',m=>(m.dealt/1e6).toFixed(1)+' mio'],
           ['deaths','Døde',m=>m.deaths]],
  dps:    [['dealt','Skade',m=>(m.dealt/1e6).toFixed(1)+' mio'],
           ['taken','Taget',m=>(m.taken/1e6).toFixed(1)+' mio'],
           ['kicks','Kicks',m=>m.kicks], ['deaths','Døde',m=>m.deaths]],
};
const ROLENOTE = {
  all: 'Alle fem, sorteret efter skade. Vælg en rolle for at se de metrikker der gælder netop den.',
  tank: 'Spidshed = største 5 s-vindue af skade taget divideret med medianvinduet. Høj værdi betyder pukler frem for jævn skade — det afgør om defensiver skal bruges proaktivt.',
  healer: 'Overheal er ikke i sig selv en fejl: forudsigende heals på indkommende skade overhealer nødvendigvis.',
  dps: 'Rotationsmetrikker (blinde spenders, proc-udnyttelse) kræver spec-config og findes kun for dig.',
};
let grole = 'all';

function gRoles(){
  const g = D.group[gi];
  const have = new Set(g.members.map(m=>m.role));
  const tabs = [['all','Alle']].concat(
    [['tank','Tank'],['healer','Healer'],['dps','DPS']].filter(([k])=>have.has(k)));
  $('#g-roles').innerHTML = tabs.map(([k,t])=>
    `<button role="tab" data-role="${k}" aria-selected="${grole===k}">${t}</button>`).join('');
  $('#g-rolenote').textContent = ROLENOTE[grole];
}
function gMembers(){
  const g = D.group[gi], top = Math.max(...g.members.map(m=>m.dealt));
  const shown = g.members.map((m,i)=>({m,i}))
    .filter(({m})=> grole==='all' || m.role===grole);
  const cols = ROLEMETRICS[grole];
  $('#g-members').innerHTML = shown.map(({m,i})=>`
    <div class="clc-card clc-member${m.me?' clc-me':''}" role="button" tabindex="0"
      data-m="${i}" aria-pressed="${gm===i}">
      <h3>${esc(label(m))}${m.me&&gNames?' <span class="clc-badge clc-badge--measured">dig</span>':''}</h3>
      <p class="clc-dim" style="margin:.1rem 0 .4rem">${ROLE[m.role]}</p>
      <div class="clc-mbar"><span style="width:${(m.dealt/top*100).toFixed(1)}%;
        background:${MCOL(i)}"></span></div>
      <dl class="clc-kv">
        ${cols.map(([,lab,fn])=>`<dt>${lab}</dt><dd>${fn(m)}</dd>`).join('')}
      </dl></div>`).join('');
  $('#g-legend').innerHTML = g.members.map((m,i)=>
    `<span><i style="background:${MCOL(i)}"></i>${esc(label(m))}</span>`).join('');
}
function gPulls(){
  const g = D.group[gi], W=560, rowH=26;
  const maxDur = Math.max(...g.pulls.map(p=>p.dur), 1);
  let o = `<svg viewBox="0 0 ${W} ${g.pulls.length*rowH+8}" role="img"
    aria-label="Skadefordeling pr. pull">`;
  g.pulls.forEach((p,pi2)=>{
    const y = pi2*rowH+4, w = 40 + (p.dur/maxDur)*(W-60);
    const tot = g.members.reduce((s,m)=>s+(m.perPull[pi2]||0),0) || 1;
    let x = 34;
    o += `<text x="0" y="${y+13}" class="clc-axis-label"
      dominant-baseline="middle">${p.id}</text>`;
    g.members.forEach((m,mi)=>{
      const share = (m.perPull[pi2]||0)/tot, seg = share*(w-34);
      if(seg <= 0) return;
      o += `<rect x="${x.toFixed(1)}" y="${y+3}" width="${seg.toFixed(1)}" height="16"
        fill="${MCOL(mi)}" fill-opacity="${gm===null||gm===mi?0.85:0.2}"
        ><title>Pull ${p.id} · ${esc(label(m))} · ${(share*100).toFixed(0)} % · ${fmt(m.perPull[pi2]||0)}</title></rect>`;
      x += seg;
    });
  });
  $('#g-pulls').innerHTML = o + `</svg>`;
}
function gDetail(){
  const g = D.group[gi];
  if(gm === null){
    const tot = g.members.reduce((s,m)=>s+m.dealt,0);
    const me = g.members.find(m=>m.me);
    $('#g-detail').innerHTML = `<h3>Holdet samlet</h3>
      <dl class="clc-kv">
        <dt>Gruppeskade</dt><dd>${(tot/1e6).toFixed(0)} mio</dd>
        <dt>Din andel</dt><dd>${(me.dealt/tot*100).toFixed(1)} %</dd>
        <dt>Din placering</dt><dd>${g.members.indexOf(me)+1} af ${g.members.length}</dd>
        <dt>Døde i alt</dt><dd>${g.members.reduce((s,m)=>s+m.deaths,0)}</dd>
        <dt>Kicks i alt</dt><dd>${g.members.reduce((s,m)=>s+m.kicks,0)}</dd>
      </dl>
      <p class="clc-hint">Klik et medlem ovenfor for at folde det ud.</p>`;
    return;
  }
  const m = g.members[gm], tot = g.members.reduce((s,x)=>s+x.dealt,0);
  const best = g.members.reduce((a,b)=>a.dealt>b.dealt?a:b);
  const shares = g.pulls.map((p,i)=>{
    const t = g.members.reduce((s,x)=>s+(x.perPull[i]||0),0)||1;
    return (m.perPull[i]||0)/t;
  });
  const hi = shares.indexOf(Math.max(...shares)), lo = shares.indexOf(Math.min(...shares));
  $('#g-detail').innerHTML = `<h3>${esc(label(m))} · ${ROLE[m.role]}</h3>
    <dl class="clc-kv">
      <dt>Andel af gruppeskade</dt><dd>${(m.dealt/tot*100).toFixed(1)} %</dd>
      <dt>Mod holdets bedste</dt><dd>${(m.dealt/best.dealt*100).toFixed(0)} %</dd>
      <dt>Skade taget</dt><dd>${fmt(m.taken)}</dd>
      <dt>Healing</dt><dd>${fmt(m.healed)}</dd>
      <dt>Kicks</dt><dd>${m.kicks}</dd><dt>Døde</dt><dd>${m.deaths}</dd>
      <dt>Stærkeste pull</dt><dd>#${g.pulls[hi].id} (${(shares[hi]*100).toFixed(0)} %)</dd>
      <dt>Svageste pull</dt><dd>#${g.pulls[lo].id} (${(shares[lo]*100).toFixed(0)} %)</dd>
    </dl>
    ${m.me ? `<p class="clc-hint"><a class="clc-jump" href="#diagnosis">Din egen diagnose →</a></p>`
           : `<p class="clc-hint">Kun dine egne fejl er analyseret — de andres
              rotationer kræver deres spec-config, og coachingen er din.</p>`}`;
}
function gRender(){ gRuns(); gRoles(); gMembers(); gPulls(); gDetail(); }
document.addEventListener('click', e=>{
  const gb = e.target.closest('[data-g]');
  if(gb){ gi = +gb.dataset.g; gm = null; gRender(); return; }
  const rb2 = e.target.closest('[data-role]');
  if(rb2){ grole = rb2.dataset.role; gm = null; gRender(); return; }
  const mb = e.target.closest('[data-m]');
  if(mb){ const k = +mb.dataset.m; gm = (gm === k ? null : k); gRender(); return; }
  if(e.target.id === 'g-names'){
    gNames = !gNames;
    e.target.setAttribute('aria-pressed', String(gNames));
    e.target.textContent = gNames ? 'Skjul navne' : 'Vis rigtige navne';
    gRender();
  }
});
document.addEventListener('keydown', e=>{
  const mb = e.target.closest('[data-m]');
  if(mb && (e.key==='Enter'||e.key===' ')){ e.preventDefault();
    const k=+mb.dataset.m; gm=(gm===k?null:k); gRender(); }
});

function render(){runButtons();strips();route();replay();panel();phases();}
document.addEventListener('click',e=>{
  const rb=e.target.closest('[data-r]'); if(rb){ri=+rb.dataset.r;pi=0;render();return;}
  const sb=e.target.closest('[data-p]'); if(sb){pi=+sb.dataset.p;render();}
});
document.addEventListener('keydown',e=>{
  const sb=e.target.closest('[data-p]');
  if(sb&&(e.key==='Enter'||e.key===' ')){e.preventDefault();pi=+sb.dataset.p;render();}
});
// markér den sektion man er i, i navigationen
const secs=[...document.querySelectorAll('main section[id]')];
const links=new Map([...document.querySelectorAll('.clc-nav a')].map(a=>[a.getAttribute('href').slice(1),a]));
new IntersectionObserver(es=>{
  for(const en of es){ if(!en.isIntersecting) continue;
    links.forEach(a=>a.removeAttribute('aria-current'));
    const a=links.get(en.target.id); if(a) a.setAttribute('aria-current','true'); }
},{rootMargin:'-20% 0px -70% 0px'}).observe ? secs.forEach(s=>{}) : null;
const io=new IntersectionObserver(es=>{
  for(const en of es){ if(!en.isIntersecting) continue;
    links.forEach(a=>a.removeAttribute('aria-current'));
    const a=links.get(en.target.id); if(a) a.setAttribute('aria-current','true'); }
},{rootMargin:'-20% 0px -70% 0px'});
secs.forEach(s=>io.observe(s));
render(); compare(); gRender();
"""

if __name__ == "__main__":
    runs, profile, baseline = load()
    payload = map_payload(runs)
    out = OUT / "coach.html"
    out.write_text(build(runs, profile, baseline, payload, group_payload(runs)), encoding="utf-8")
    print(f"skrev {out} ({out.stat().st_size:,} bytes)")
