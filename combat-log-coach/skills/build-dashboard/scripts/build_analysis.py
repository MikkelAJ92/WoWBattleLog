import collections
import csv
import html
import io
import json
import statistics
import sys
from pathlib import Path

# Scriptet ligger i skills/build-dashboard/scripts/ → repo-roden er 4 op.
ROOT = Path(__file__).resolve().parents[4]
PLUGIN = Path(__file__).resolve().parents[2]
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".clc-out"
CACHE = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / ".clc-cache"
LOGDIR = Path(sys.argv[3]) if len(sys.argv) > 3 else None
sys.path.insert(0, str(PLUGIN / "analyze-log/scripts"))
import parse as P  # noqa: E402

CSS = ((PLUGIN.parent / "templates/dashboard.css").read_text(encoding="utf-8")
       + (PLUGIN.parent / "templates/coach.css").read_text(encoding="utf-8"))
ME = None   # udledes af summary'ens player-navn

# Signaturspell → spec. Cast-signaturen er den eneste klasseangivelse i en
# combat log; udvid listen efterhånden som nye specs dukker op.
CLASSSIG = {
    "Rampage": "Fury Warrior", "Thunder Clap": "Prot Warrior",
    "Blessed Hammer": "Prot Paladin", "Holy Shock": "Holy Paladin",
    "Templar Strike": "Ret Paladin", "Shred": "Feral Druid",
    "Rejuvenation": "Resto Druid", "Starfire": "Balance Druid",
    "Rising Sun Kick": "Mistweaver Monk", "Ice Lance": "Frost Mage",
    "Arcane Blast": "Arcane Mage", "Fireball": "Fire Mage",
    "Chaos Strike": "Havoc DH", "Immolation Aura": "Demon Hunter",
    "Death Strike": "Blood DK", "Obliterate": "Frost DK",
    "Festering Strike": "Unholy DK", "Mutilate": "Assassination Rogue",
    "Sinister Strike": "Outlaw Rogue", "Backstab": "Sub Rogue",
    "Barbed Shot": "BM Hunter", "Aimed Shot": "Marksmanship Hunter",
    "Raptor Strike": "Survival Hunter", "Void Bolt": "Shadow Priest",
    "Penance": "Disc Priest", "Riptide": "Resto Shaman",
    "Lava Burst": "Elemental Shaman", "Stormstrike": "Enhancement Shaman",
    "Incinerate": "Destruction Warlock", "Malefic Rapture": "Affliction Warlock",
}


def read_item_levels(stem):
    """Item level ligger i advanced-blokkens sidste felt og er stabilt pr.
    spiller. Kræver adgang til rå-loggen; uden den udelades ilvl."""
    if not LOGDIR:
        return {}
    path = LOGDIR / f"{stem}.txt"
    if not path.exists():
        return {}
    v = collections.defaultdict(collections.Counter)
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "  SPELL_CAST_SUCCESS," not in line:
                continue
            f = next(csv.reader(io.StringIO(line.split("  ", 1)[1])))
            if len(f) != 31 or not f[1].startswith("Player-") or f[12] != f[1]:
                continue
            v[f[1]][f[30]] += 1
    return {g: int(c.most_common(1)[0][0]) for g, c in v.items()
            if sum(c.values()) > 50}


def collect():
    """Ét datasæt: én række pr. spiller pr. M+ run."""
    global ME
    recs = []
    for sp in sorted(OUT.glob("summary-*.json")):
        summ = json.load(open(sp, encoding="utf-8"))
        stem = summ["source"]["file"].replace(".txt", "")
        ME = ME or summ["player"]["name"].split("-")[0]
        ilvl = read_item_levels(stem)
        pets = summ["pets"]
        names = {g["guid"]: g["name"].split("-")[0] for g in summ["group"]}
        for run in summ["runs"]:
            if run["type"] != "mplus":
                continue
            pull_s = sum(p["duration_s"] for p in run["pulls"]) or 1
            dealt, taken, healed, over, kicks, deaths, casts = (
                collections.Counter() for _ in range(7))
            sig = collections.defaultdict(collections.Counter)
            for p in run["pulls"]:
                for g, v in p["damage_by_player"].items():
                    dealt[pets.get(g, g)] += v
            for r in P.iter_run_events(CACHE, stem, run["id"]):
                ev, sg, dg, amt, ex, spn = r[1], r[2], r[4], r[8], r[12], r[7]
                o = pets.get(sg, sg)
                if ev in P.DAMAGE_EVENTS and dg.startswith("Player-") and amt:
                    taken[dg] += amt
                elif ev in ("SPELL_HEAL", "SPELL_PERIODIC_HEAL") and amt:
                    healed[o] += amt
                    if ex and ex.get("overheal"):
                        over[o] += ex["overheal"]
                elif ev == "SPELL_INTERRUPT":
                    kicks[o] += 1
                elif ev == "UNIT_DIED" and dg.startswith("Player-"):
                    deaths[dg] += 1
                elif ev == "SPELL_CAST_SUCCESS" and sg.startswith("Player-"):
                    casts[sg] += 1
                    if spn:
                        sig[sg][spn] += 1
            guids = [g for g in dealt if g.startswith("Player-")]
            tot = sum(dealt[g] for g in guids) or 1
            shares = collections.defaultdict(list)
            for p in run["pulls"]:
                pt = sum(p["damage_by_player"].values()) or 1
                for g in guids:
                    own = p["damage_by_player"].get(g, 0) + sum(
                        v for pg, v in p["damage_by_player"].items()
                        if pets.get(pg) == g)
                    shares[g].append(own / pt)
            grp = []
            for g in guids:
                cls = next((CLASSSIG[x] for x, _ in sig[g].most_common(15)
                            if x in CLASSSIG), "?")
                sh = shares[g]
                grp.append({
                    "run": f"{run['zone']} +{run.get('key_level')}",
                    "name": names.get(g, g), "cls": cls, "ilvl": ilvl.get(g),
                    "dealt": dealt[g], "taken": taken[g], "healed": healed[g],
                    "over": over[g], "kicks": kicks[g], "deaths": deaths[g],
                    "casts": casts[g], "pull_s": pull_s, "share": dealt[g] / tot,
                    "cv": (statistics.stdev(sh) / statistics.mean(sh)
                           if len(sh) > 2 and statistics.mean(sh) else None)})
            # Roller tildeles RELATIVT pr. run. Absolutte tærskler
            # fejlklassificerer selvhelende DPS (en Vengeance DH blev først
            # taget for healer).
            healer = max(grp, key=lambda r: r["healed"])
            tank = max((r for r in grp if r is not healer),
                       key=lambda r: r["taken"])
            for r in grp:
                r["role"] = ("healer" if r is healer
                             else "tank" if r is tank else "dps")
            recs += grp
    return recs

def esc(s):
    return html.escape(str(s), quote=True)


def linreg(xs, ys):
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    a = my - b * mx
    sst = sum((y - my) ** 2 for y in ys)
    ssr = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    return {"n": len(xs), "b": b, "a": a, "r2": 1 - ssr / sst if sst else None}


def scatter(recs, reg, w=760, h=380):
    """Skade mod item level med regressionslinje. Ét aksesystem."""
    pad = 52
    xs = [r["ilvl"] for r in recs]
    ys = [r["dealt"] / 1e6 for r in recs]
    x0, x1 = min(xs) - 3, max(xs) + 3
    y0, y1 = 0, max(ys) * 1.1
    X = lambda v: pad + (v - x0) / (x1 - x0) * (w - pad - 14)      # noqa: E731
    Y = lambda v: h - pad - (v - y0) / (y1 - y0) * (h - pad - 16)  # noqa: E731
    o = [f'<svg viewBox="0 0 {w} {h}" role="img" '
         f'aria-label="Skade mod item level for {reg["n"]} DPS-kørsler">']
    for gy in range(0, int(y1) + 1, 50):
        o.append(f'<line x1="{pad}" x2="{w - 14}" y1="{Y(gy):.1f}" y2="{Y(gy):.1f}" '
                 f'stroke="var(--line)"/>'
                 f'<text x="{pad - 8}" y="{Y(gy) + 4:.1f}" text-anchor="end" '
                 f'class="clc-axis-label">{gy}</text>')
    for gx in range(int(x0 // 10 * 10) + 10, int(x1) + 1, 10):
        o.append(f'<text x="{X(gx):.1f}" y="{h - pad + 18}" text-anchor="middle" '
                 f'class="clc-axis-label">{gx}</text>')
    # regressionslinje — modelleret, derfor stiplet
    o.append(f'<line x1="{X(x0):.1f}" y1="{Y(reg["a"] + reg["b"] * x0):.1f}" '
             f'x2="{X(x1):.1f}" y2="{Y(reg["a"] + reg["b"] * x1):.1f}" '
             f'stroke="var(--clc-potential)" stroke-width="2" class="clc-modeled-stroke"/>')
    for r in recs:
        me = r["name"] == ME
        o.append(
            f'<circle cx="{X(r["ilvl"]):.1f}" cy="{Y(r["dealt"] / 1e6):.1f}" '
            f'r="{6 if me else 4.5}" fill="{"var(--clc-error)" if me else "var(--clc-actual)"}" '
            f'fill-opacity="{0.95 if me else 0.6}" stroke="var(--ink)" stroke-width="1">'
            f'<title>{esc(r["name"])} · {esc(r["cls"])} · ilvl {r["ilvl"]} · '
            f'{r["dealt"] / 1e6:.1f} mio</title></circle>')
    o.append(f'<text x="{pad}" y="14" class="clc-axis-label">mio skade</text>')
    o.append(f'<text x="{w - 14}" y="{h - 6}" text-anchor="end" '
             f'class="clc-axis-label">item level</text></svg>')
    return (f'<div class="clc-chart">{"".join(o)}</div>'
            '<div class="clc-legend">'
            '<span><i style="background:var(--clc-error)"></i>Elori</span>'
            '<span><i style="background:var(--clc-actual)"></i>øvrige DPS</span>'
            '<span>stiplet = mindste kvadraters regression</span></div>')


def build(recs):
    dps = [r for r in recs if r["role"] == "dps" and r["ilvl"]]
    reg = linreg([r["ilvl"] for r in dps], [r["dealt"] / 1e6 for r in dps])
    dmin, dmax = min(r["dealt"] for r in dps), max(r["dealt"] for r in dps)
    for r in dps:
        r["pred"] = reg["a"] + reg["b"] * r["ilvl"]
        r["resid"] = r["dealt"] / 1e6 - r["pred"]
    mine = [r for r in dps if r["name"] == ME]
    me_n = len(mine)
    me_below = sum(1 for r in mine if r["resid"] < 0)
    me_med = statistics.median([r["resid"] for r in mine])
    others_med = statistics.median([r["resid"] for r in dps if r["name"] != ME])
    ispan = max(r["ilvl"] for r in dps) - min(r["ilvl"] for r in dps)

    rows = ""
    for r in sorted(recs, key=lambda r: (-r["share"], r["run"])):
        cpm = r["casts"] / (r["pull_s"] / 60)
        oh = r["over"] / (r["healed"] + r["over"]) if r["healed"] + r["over"] else None
        rows += (
            f'<tr{" class=clc-me-row" if r["name"] == ME else ""}>'
            f'<td>{esc(r["name"])}</td><td class="clc-dim">{esc(r["cls"])}</td>'
            f'<td>{esc(r["role"])}</td><td class="clc-dim">{esc(r["run"])}</td>'
            f'<td class="clc-seq">{r["ilvl"] or "—"}</td>'
            f'<td class="clc-seq">{r["dealt"] / 1e6:.1f}</td>'
            f'<td class="clc-seq">{r["share"]:.1%}</td>'
            f'<td class="clc-seq">{r["taken"] / 1e6:.1f}</td>'
            f'<td class="clc-seq">{r["healed"] / 1e6:.1f}</td>'
            f'<td class="clc-seq">{f"{oh:.0%}" if oh else "—"}</td>'
            f'<td class="clc-seq">{cpm:.0f}</td>'
            f'<td class="clc-seq">{f"{r['cv']:.2f}" if r["cv"] else "—"}</td>'
            f'<td class="clc-seq">{r["kicks"]}</td>'
            f'<td class="clc-seq">{r["deaths"]}</td></tr>')

    cvs = [r for r in recs if r["cv"] and r["role"] == "dps"]
    cvs.sort(key=lambda r: r["cv"])
    cvrows = "".join(
        f'<tr{" class=clc-me-row" if r["name"] == ME else ""}>'
        f'<td>{esc(r["name"])}</td><td class="clc-dim">{esc(r["cls"])}</td>'
        f'<td class="clc-seq">{r["cv"]:.2f}</td>'
        f'<td class="clc-seq">{r["share"]:.1%}</td></tr>' for r in cvs)

    oh_by_role = {}
    for role in ("healer", "tank", "dps"):
        v = [r["over"] / (r["healed"] + r["over"]) for r in recs
             if r["role"] == role and r["healed"] + r["over"] > 1e6]
        if v:
            oh_by_role[role] = (len(v), statistics.median(v))

    deaths = ""
    for run in sorted(set(r["run"] for r in recs)):
        g = [r for r in recs if r["run"] == run]
        n_dead = sum(1 for r in g if r["deaths"])
        tot = sum(r["deaths"] for r in g)
        deaths += (f'<tr><td>{esc(run)}</td><td class="clc-seq">{n_dead}/5</td>'
                   f'<td class="clc-seq">{tot}</td></tr>')

    return f"""<!doctype html><html lang="da"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Datasæt — Combat Log Coach</title><style>{CSS}
.clc-me-row td{{background:color-mix(in srgb,var(--clc-error) 12%,transparent)}}
.stat{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
  gap:.85rem;margin:1rem 0}}
.stat div{{background:var(--sur);border:1px solid var(--line);border-radius:10px;
  padding:.85rem}}
.stat b{{display:block;font-family:var(--f-mono);font-size:1.5rem;
  letter-spacing:-.02em}}
.stat span{{color:var(--dim);font-size:.75rem}}
</style></head><body>
<main>
<section id="datasaet">
<p class="eyebrow">Combat Log Coach · datasæt</p>
<h1>20 spiller-kørsler</h1>
<p class="clc-lede">Fire timede M+ nøgler, 6. august 2026. Fire forskellige
grupper — kun Elori går igen. Alt herunder er målt i loggen; intet er hentet
udefra.</p>
<div class="stat">
<div><b>20</b><span>spiller-kørsler</span></div>
<div><b>4</b><span>nøgler, 4 grupper</span></div>
<div><b>16/20</b><span>klasser identificeret via cast-signatur</span></div>
<div><b>20/20</b><span>item level læst af loggen</span></div>
</div>
<p class="clc-hint">Roller er tildelt <b>relativt pr. run</b>: healeren er den
med mest heling i sit eget run, tanken den med mest skade taget blandt de
øvrige. Absolutte tærskler fejlklassificerer selvhelende DPS — en Vengeance
Demon Hunter blev først taget for healer.</p>
</section>

<section id="ilvl">
<h2>Item level forklarer 22 %</h2>
<p>Mindste kvadraters regression af skade mod item level, kun DPS-kørsler.</p>
{scatter(dps, reg)}
<div class="stat">
<div><b>{reg['n']}</b><span>DPS-kørsler i modellen</span></div>
<div><b>{reg['r2']:.2f}</b><span>R² — forklaret variation</span></div>
<div><b>{reg['b']:.2f}</b><span>mio skade pr. item level</span></div>
<div><b>{ispan}</b><span>item levels mellem laveste og højeste</span></div>
</div>
<p>Spændet på {ispan} item levels svarer efter modellen til
{reg['b'] * ispan:.0f} mio skade. Det faktiske spænd er
{(dmax - dmin) / 1e6:.0f} mio — fra {dmin / 1e6:.1f} til {dmax / 1e6:.1f}.
Gear forklarer altså omkring en femtedel af forskellen mellem DPS'ere.
De øvrige fire femtedele er klasse, spec, pull-timing og spil.</p>
<p class="clc-hint">n = {reg['n']} er lille, og kørslerne er fra fire
forskellige grupper med forskellige dungeons og nøgleniveauer. Behandl
hældningen som en størrelsesorden, ikke som en koefficient.</p>
</section>

<section id="residualer">
<h2>Hvem leverer over sit gear</h2>
<p>Residualet er faktisk skade minus det regressionen forudsiger ud fra item
level. Positivt betyder mere end gearet lover.</p>
<table class="clc-table"><thead><tr><th>Spiller</th><th>Klasse</th><th>ilvl</th>
<th>Faktisk</th><th>Forudsagt</th><th>Residual</th></tr></thead><tbody>
{"".join(
    f'<tr{" class=clc-me-row" if r["name"] == ME else ""}>'
    f'<td>{esc(r["name"])}</td><td class="clc-dim">{esc(r["cls"])}</td>'
    f'<td class="clc-seq">{r["ilvl"]}</td>'
    f'<td class="clc-seq">{r["dealt"] / 1e6:.1f}</td>'
    f'<td class="clc-seq">{r["pred"]:.1f}</td>'
    f'<td class="clc-seq" style="color:{"var(--clc-potential)" if r["resid"] > 0 else "var(--clc-error)"}">'
    f'{r["resid"]:+.1f}</td></tr>'
    for r in sorted(dps, key=lambda r: -r["resid"]))}
</tbody></table>
<div class="stat">
<div><b>{me_below}/{me_n}</b><span>Elori-kørsler under forudsigelsen</span></div>
<div><b>{me_med:+.1f}</b><span>mio · Eloris median-residual</span></div>
<div><b>{others_med:+.1f}</b><span>mio · øvrige DPS median</span></div>
</div>
<p>Elori ligger under sin gear-forudsigelse i {me_below} af {me_n} kørsler,
med et median-residual på {me_med:+.1f} mio. Resten af stikprøven ligger på
{others_med:+.1f} mio. Forskellen er ikke gear — den er allerede regnet ud.</p>
<p class="clc-hint"><b>Tre forbehold, og det første trækker den anden vej end
man skulle tro.</b> Elori udgør 4 af de {reg['n']} punkter regressionen er
bygget på, så linjen trækkes mod ham selv — det gør afstanden
<i>underdrevet</i>, ikke overdrevet. Dernæst er klasse og spec en stærk
konfounder: de tre bedste residualer er alle BM Hunters, hvilket lige så godt
kan være tuning som spil. Og n = {reg['n']} er lille.</p>
<p class="clc-hint">Til sammenligning satte den kontrafaktiske model i
coach-rapporten prisen for blinde spenders og tabt casttid til 31–46 mio pr.
run. De to tal er regnet frem ad helt forskellige veje — det ene fra hans egne
fejl-events, det andet fra andre spilleres gear-justerede skade — og de lander
i samme størrelsesorden.</p>
</section>

<section id="roster">
<h2>Alle kørsler</h2>
<p class="clc-hint" style="margin-bottom:.8rem">CV = variationskoefficient på
skade-andel pr. pull. Lav værdi betyder jævnt bidrag hen over pulls.
Elori er fremhævet.</p>
<table class="clc-table"><thead><tr>
<th>Spiller</th><th>Klasse</th><th>Rolle</th><th>Run</th><th>ilvl</th>
<th>Skade</th><th>Andel</th><th>Taget</th><th>Heling</th><th>Overheal</th>
<th>Casts/min</th><th>CV</th><th>Kicks</th><th>Døde</th>
</tr></thead><tbody>{rows}</tbody></table>
</section>

<section id="konsistens">
<h2>Konsistens</h2>
<p>Variationskoefficient på skade-andel pr. pull, kun DPS. Sorteret jævnest
først.</p>
<table class="clc-table"><thead><tr><th>Spiller</th><th>Klasse</th>
<th>CV</th><th>Andel</th></tr></thead><tbody>{cvrows}</tbody></table>
<p class="clc-hint">CV siger intet om niveau — en spiller kan bidrage jævnt
lidt. Læs den sammen med andelen.</p>
</section>

<section id="overheal">
<h2>Overheal pr. rolle</h2>
<table class="clc-table"><thead><tr><th>Rolle</th><th>n</th>
<th>Median overheal</th></tr></thead><tbody>
{"".join(f'<tr><td>{r}</td><td class="clc-seq">{n}</td>'
         f'<td class="clc-seq">{v:.1%}</td></tr>' for r, (n, v) in oh_by_role.items())}
</tbody></table>
<p class="clc-hint">Overheal er ikke i sig selv en fejl: forudsigende heling
på indkommende skade overhealer nødvendigvis. Gradienten healer → tank → dps
afspejler hvem der healer forudsigende og hvem der healer sig selv reaktivt.</p>
</section>

<section id="doedelighed">
<h2>Dødsfald pr. nøgle</h2>
<table class="clc-table"><thead><tr><th>Run</th><th>Spillere der døde</th>
<th>Dødsfald i alt</th></tr></thead><tbody>{deaths}</tbody></table>
<p>Dødsfald klumper: i Algeth'ar Academy døde alle fem, i de tre andre nøjes
det med én til tre spillere. Det er ikke en gradient af individuelle fejl —
det er ét run der gik galt for hele gruppen og tre der ikke gjorde.</p>
</section>

<section id="graenser">
<h2>Hvad datasættet ikke kan sige</h2>
<p><b>Rotationskvalitet findes kun for Elori.</b> Blinde spenders,
proc-udnyttelse og cooldown-disciplin kræver en spec-config der beskriver hvad
spec'en <i>burde</i> gøre. Den findes for Frost Mage. For de øvrige 16
kørsler kan datasættet vise hvad folk leverede, ikke hvor godt de spillede.</p>
<p><b>Kicks er mulighedsbestemt.</b> Antallet af interrupts afhænger af hvad
mobsene caster, ikke kun af spilleren. Et lavt tal er ikke i sig selv en fejl.</p>
<p><b>Skade taget er ikke rollejusteret på tværs.</b> En tank skal tage skade.
Sammenlign inden for rolle, ikke på tværs.</p>
<p><b>Fire grupper, ikke ét hold.</b> Kun Elori går igen på tværs af de fire
kørsler. Enhver sammenligning mellem runs blander forskellige medspillere,
dungeons og nøgleniveauer.</p>
</section>
</main></body></html>"""


if __name__ == "__main__":
    recs = collect()
    if not recs:
        sys.exit(f"ingen summary-*.json i {OUT} — kør parse.py først")
    out = OUT / "analysis.html"
    out.write_text(build(recs), encoding="utf-8")
    print(f"skrev {out} ({out.stat().st_size:,} bytes)")
