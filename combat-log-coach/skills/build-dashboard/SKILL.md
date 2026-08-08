---
name: build-dashboard
description: >-
  Byg eller opdatér det visuelle analyse-dashboard (selvstændig HTML-artifact)
  fra parse- og linse-output. Brug efter analyze-log når brugeren skal se
  resultater: master-tidslinjer, rutekort, pull-strips, replay-kort,
  waterfall og benchmark-grafer. Trigger på "vis mig", "dashboard",
  "lav en rapport", eller når en analyse er færdig og skal leveres visuelt.
---

# Byg dashboard (F4)

Dashboardet er **én selvstændig HTML-fil**: inline CSS/JS/SVG, ingen eksterne
dependencies, ingen netværkskald. Persistér den som opdaterbar artifact —
samme fil/URL gennem hele sessionen og på tværs af sessioner.

## Grundstruktur (inkrementel re-generering — hårdt krav)

Hver analyse-sektion er en selvstændig `<section data-clc-section="<id>">`.
Nye spørgsmål fra brugeren = **tilføj/erstat én sektion**; regenerér aldrig
hele filen for at tilføje ét snit. Stabile sektions-id'er:
`overview`, `timeline-run-<N>`, `route-run-<N>`, `pulls`, `replay-<N>-<M>`,
`waterfall`, `targets`, `movement`, `rotation`, `survival`, `sustain`,
`context`, `coaching`, `progression`, `caveats`.

1. Indlæs designsystemet fra `templates/dashboard.css` og inline det i
   `<style>` — genopfind ikke tokens, brug `--clc-*`-variablerne.
2. Datagrundlag: summary-JSON fra `analyze-log` + linse-output. Slå aldrig
   tal op i rå-loggen under HTML-skrivning.
3. Afslut ALTID med forbehold-sektionen — genereret, ikke håndskrevet:
   ```bash
   python3 scripts/caveats.py <linse-output.json> > caveats.html
   ```
   Indsæt outputtet som sidste sektion. Mangler en metrik i kataloget, siger
   scriptet det — tilføj den i `metrics.json`, gæt ikke.

## Hårde dataviz-regler

- **Ét aksesystem pr. graf.** Aldrig dobbelt-y. Hellere to grafer.
- **Legende er obligatorisk** på enhver graf med mere end én serie
  (`.clc-legend`).
- **CVD-sikker palette:** brug `--clc-c1`…`--clc-c7` i rækkefølge; semantik
  via `--clc-actual`/`--clc-potential`/`--clc-error`/`--clc-death`.
  Kod aldrig betydning i rød/grøn alene — brug form + tekst.
- **Mørkt tema er default**; temaskift kun via `[data-theme="light"]`.
- **Proveniens er visuel:** målte kurver = fuldt optrukne; modellerede =
  stiplede (`.clc-modeled-stroke`) + badge `≈`; eksterne tal = badge `↗` med
  klikbart kildelink. Tal uden mærkning må ikke forekomme — brug `kind` fra
  linse-outputtet.
- **Pseudonymisering:** gruppemedlemmer omtales aggregeret ("de fire andre")
  eller som Spiller B/C/D/E. Fulde navne kun ved eksplicit opt-in.
- Brede visualiseringer ligger i `.clc-chart` (scroller selv); siden må
  aldrig scrolle vandret.

## Komponentbibliotek

### 1 · Master-tidslinje pr. run (`timeline-run-<N>`)
Lagdelt SVG, fælles tidsakse (run-relativ tid):
- **Areal:** faktisk DPS (glidende 10 s-vindue) — `.clc-actual-area`
- **Kurve:** kontrafaktisk potentiale — `.clc-potential-band` +
  `.clc-modeled-stroke` (findes kun når kontrafaktik-modellen er kørt)
- **Bånd:** boss-segmenter som `rect` i `--clc-boss-band` med navn
- **Markører:** deaths (`.clc-death-marker`, ▼) på dødstidspunkter
- **Under-strips** (30–40 px høje, samme tidsakse, stakket under grafen):
  bevægelses-heat (yd pr. 10 s som opacity på `--clc-movement`),
  CD-ticks (`--clc-cd` lodrette streger), fejl-prikker (`.clc-error-dot`
  på event-tidspunkter fra `context.events` — hver prik har `<title>` med
  tid + typen + kontekst-tags).

### 2 · Rutekort (`route-run-<N>`)
`<path>` gennem spillerens cast-koordinater (x,y fra `SPELL_CAST_SUCCESS`):
- Tidsgradient langs stien (start: dæmpet → slut: fuld `--clc-actual`;
  brug segmenterede `<line>`-stykker med stigende opacity — SVG-gradients
  følger ikke path-længde pålideligt)
- **Klip spring > 150 yd** (portaler/graveyard) — afbryd stien, tegn ikke
  linjen; marker med lille ✂-tekst
- Fejl-events plottet geografisk som `.clc-error-dot` med `<title>`
- Y-aksen i WoW-koordinater peger modsat SVG: spejl med
  `transform="scale(1,-1)"` på plotgruppen
- To runs af samme dungeon side om side muliggør visuel klynge-genkendelse —
  samme viewBox-skala på begge

### 3 · Pull-strips (`pulls`)
Én række pr. run, flexbox (`.clc-strips`): hver pull en `.clc-strip`-div,
bredde ∝ varighed (px = sekunder), højde fast, baggrundsfarve = modelleret
tab-andel (interpolér `--clc-surface-2` → `--clc-error`; ved manglende
kontrafaktik: farv efter fejl-events pr. minut). `<title>`/tooltip med:
pull-id, varighed, egen skade, fejl-liste, tab-estimat (mærket ≈).

### 4 · Replay-kort (`replay-<run>-<pull>`)
Cast-for-cast tidslinje for én udvalgt pull (typisk den dyreste):
- 4 lanes (`builders`/`spenders`/`CDs`/`defensiver`) som rækker af små
  rects på tidsaksen; farv spender-casts der var blinde med `--clc-error`
- Lane 5: skade-taget som nedadvendte søjler; lane 6: bevægelse (yd mellem
  casts som heat)
- Under kortet: fase-tabel "det skete / spil det sådan" — to kolonner,
  venstre = målt sekvens, højre = anbefalet sekvens i **brugerens keybinds**
  (fra coach-skill'ets oversætter; udelad kolonnen hvis binds ukendte)

### 5 · Waterfall (`waterfall`)
DPS-bro fra faktisk → modelleret potentiale:
- Startsøjle (målt, fuldt optrukket) → komponentsøjler pr. fejlklasse
  (stiplet kant, `≈`-badge, interval som error-bar) → slutsøjle
- **Obligatorisk fodnote i selve grafen:** "komponenter kan overlappe;
  overslag, ikke sim" + summen af intervaller — hentes fra
  kontrafaktik-outputtets antagelser, ikke håndskrevet
- Afstem mod tidslinjens kurve: nævn eksplicit differencen hvis
  event-placeret sum ≠ waterfall-total (referencens +7–9,5 % vs. +13,5 %)

### 6 · Standardgrafer
- **Benchmark-barer:** parvise barer (din værdi vs. benchmark/baseline);
  benchmark-baren mærket med proveniens-badge (ekstern kilde = `↗` + link)
- **Histogrammer:** fx cast-hullers længdefordeling; markér 1,7 s-grænsen
- **Scatter:** fx yd/min vs. DPS pr. boss (fra `movement.boss_scatter`)
- **Stacked shares:** fx skadefordeling pr. spell (`rotation.damage_share_by_spell`)

## KPI-oversigt (`overview`)

Øverst: `.clc-kpis` med 4–8 nøgletal (DPS, blind-rate, tabt casttid,
proc-udnyttelse, deaths, key-niveau/resultat). Hvert tal med badge for
proveniens og delta mod baseline hvis `progression`-data findes
("31 % → 20 %").

## Sprog og ærlighed

- Output følger brugerens sprog; spell-navne forbliver engelske.
- Ingen tal uden proveniens; ingen anbefaling uden det målte belæg ved
  siden af. Hvis en linse siger `unavailable` (fx ingen positioner), vis
  sektionen med forklaringen — skjul den ikke.
