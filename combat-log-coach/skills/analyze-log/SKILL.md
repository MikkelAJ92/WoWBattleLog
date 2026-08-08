---
name: analyze-log
description: >-
  Analysér WoW combat logs lokalt: parse og segmentér rå WoWCombatLog-filer
  til runs/bosser/pulls med positioner og cast-data. Brug når brugeren beder
  om at analysere sine logs ("analysér mine logs", "hvordan gik min nøgle?"),
  når der ligger nye WoWCombatLog-*.txt i den forbundne Logs-mappe, eller som
  datagrundlag for dashboard (build-dashboard), coaching (coach) og
  progression. Kør også ved spørgsmål om DPS, deaths, bevægelse, procs eller
  rotation i brugerens eget gameplay.
---

# Analysér combat log (F1 + F3)

Du parser brugerens rå combat logs **lokalt** og løfter kun aggregater ind i
konteksten. Rå logfiler må aldrig uploades eller citeres i fuld længde —
det er både privatlivs- og kontekst-økonomi.

## Trin 1 · Find loggen

Brugerens `Logs`-mappe ligger typisk i
`World of Warcraft/_retail_/Logs/`. Tjek friskhed først:

```bash
python3 scripts/parse.py <Logs-mappe> --check
```

- **Exit 1 (ingen filer):** Combat logging er ikke slået til. Giv brugeren
  instruktionen fra outputtet (`/combatlog` i spillet + Advanced Combat
  Logging under Options → Gameplay → Network) og stop her.
- **Exit 2 (gammel log):** Spørg om brugeren har spillet siden sidst — hvis
  ja, mindes de om `/combatlog`. Analysér evt. den gamle log alligevel, hvis
  brugeren ønsker det.

## Trin 2 · Parse (med cache)

```bash
python3 scripts/parse.py <logfil eller Logs-mappe> [--player NAVN] [--cache .clc-cache]
```

- Output på stdout er et **summary-JSON < 100 KB** — det er den eneste del,
  du læser ind i konteksten. Den fulde eventstrøm ligger i cachen
  (`.clc-cache/<logstem>/run-NNN.jsonl.gz`) til opfølgende datasnit.
- Cachen genbruges automatisk (manifest-tjek på størrelse+mtime).
  Opfølgende spørgsmål må **ikke** re-parse rå-loggen — kør linser mod
  cachen (< 15 s-kravet). `--force` kun ved mistanke om korrupt cache.
- Uden `--player` auto-detekteres spilleren via MINE-flagget; verificér
  navnet med brugeren første gang og gem det i CLAUDE.md/profilen.

## Trin 3 · Læs summary kritisk

Tjek altid inden videre analyse:

1. `source.advanced_logging` — hvis `false`: positioner og ressourcedata
   mangler. Sig det tydeligt; bevægelses- og rutekort-analyser er umulige,
   resten degraderer pænt.
2. `counts.unknown_events` og `counts.parse_warnings` — versionsdrift.
   Få warnings er normalt; systematisk høje tal på ét event = formatændring,
   nævn det i forbeholdene.
3. `runs[].pulls` — segmenteringen (< 6 s huller; min. 150k skade eller 8 s)
   er en heuristik. Ved mærkelige pulls: sig det, i stedet for at tvinge
   fortolkning.

## Trin 4 · Linser (F3)

Genkørbare datasnit over cachen — re-parse aldrig rå-loggen for et
opfølgende spørgsmål:

```bash
python3 scripts/lenses.py <cache-root> <logstem> \
    [--lens targets|movement|rotation|survival|sustain|context]... \
    [--run N]... [--spec-config spec.json]
```

| Linse | Svarer på |
|---|---|
| `targets` | DPS/fejlrate pr. target-bucket (1/2/3–5/6+) |
| `movement` | Tabt casttid i bevægelse vs. stillestående; yd/min; selvafbrudte casts |
| `rotation` | CPM, skadefordeling, proc-forbrug, CD-udnyttelse, blind spender-rate |
| `survival` | Death recaps (sidste 6 s), defensiv-timing og -tilgængelighed |
| `sustain` | Fase-andel af **gruppens** skade (obligatorisk metode; egen kurve er sekundær) |
| `context` | Fejl attribueret pr. situation (åbning/pakkevækst/bevægelse/sen-pull — ikke-eksklusiv) |

- Hvert resultat-tal bærer `kind`/`metric`/`error_source` fra `metrics.json` —
  brug dem direkte i artifacts; find aldrig selv på forbehold.
- **Spec-config** (spenders/procs/defensives/major_cds med spell-id'er) låser
  blind spender-rate, CD-disciplin og defensiv-timing op. Byg den sammen med
  brugeren én gang pr. spec og gem den i projektmappen (fx
  `spec-configs/frost-mage.json`). Uden config degraderer linserne pænt og
  siger eksplicit hvad der mangler.
- Til frie hypotese-spørgsmål ("er mine blind casts i samle-fasen?") der ikke
  dækkes af en linse: skriv et lille snit mod `iter_run_events` i
  `scripts/parse.py` — samme cache, samme rækkelayout.

## Hårde regler

- **Målt ≠ modelleret:** hvert tal du rapporterer er enten (a) talt i loggen,
  (b) modelleret med eksplicitte antagelser + interval, eller (c) hentet
  eksternt med kilde. Mærk det — som datastruktur/label, ikke som stilistisk
  forbehold.
- **Ingen websøgning i dette skill** — analysen er offline pr. design.
  Benchmarks mod eksterne kilder hører til i `write-guide`.
- Gruppemedlemmers navne pseudonymiseres i delte artifacts (vis aggregeret
  "de fire andre" — aldrig pr. navn som default).
- Spell-navne forbliver engelske; øvrig output følger brugerens sprog.
