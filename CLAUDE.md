# Combat Log Coach — sessionskontekst

Dette repo er et Claude Code-plugin til personlig WoW-analyse og coaching.
Kør altid analyser **lokalt** — rå combat logs er for store til upload
(> 100 MB) og må aldrig forlade maskinen eller læses ind i modelkonteksten.
Kun aggregater (summary/linse-JSON < 100 KB) må ind i konteksten.

## Brugerens miljø

- **OS:** Windows. Projekter ligger i `F:\MIAJ\` (dette repo:
  `F:\MIAJ\WoWBattleLog`).
- **Logs-mappe:** `<WoW-installation>\_retail_\Logs\` — spørg efter den
  præcise sti første gang, og skriv den ind her.
- **Python:** brug `py -3` (eller `python`) på Windows; scripts kræver
  Python 3.10+. Ingen tredjeparts-dependencies.

## Arbejdsgang (skills i combat-log-coach/)

1. `analyze-log` — friskheds-tjek → parse → linser. Cache i `.clc-cache/`
   (gitignoret). Re-parse aldrig for opfølgende spørgsmål; kør linser mod
   cachen.
2. `build-dashboard` — selvstændig HTML-artifact; forbehold-sektionen
   genereres med `caveats.py`, aldrig håndskrevet.
3. `coach` — anvisninger på brugerens keybinds/UI-stack; benchmarks gemmes
   som goals i `baseline.json`.
4. `write-guide` — eneste skill med websøgning; al tuning skal hentes live
   og bestå `citation_lint.py` før levering.

Typisk pipeline (fra repo-roden, Windows):

```powershell
py -3 combat-log-coach/skills/analyze-log/scripts/parse.py "<Logs-sti>" --check
py -3 combat-log-coach/skills/analyze-log/scripts/parse.py "<Logs-sti>" --cache .clc-cache > summary.json
py -3 combat-log-coach/skills/analyze-log/scripts/lenses.py .clc-cache <logstem> --spec-config spec-configs/<spec>.json > lens.json
py -3 combat-log-coach/skills/analyze-log/scripts/profile.py update profile.json --lens-output lens.json --spec "<Spec>"
py -3 combat-log-coach/skills/analyze-log/scripts/baseline.py snapshot baseline.json --lens-output lens.json
py -3 combat-log-coach/skills/analyze-log/scripts/baseline.py report baseline.json
```

## Persistente filer (projektroden)

- `profile.json` — spillestils-profilen (F2). Opdateres pr. analyse;
  `player.keybinds` og `player.ui_stack` udfyldes i dialog én gang og må
  ikke overskrives af scripts.
- `baseline.json` — progression + goals (F7).
- `spec-configs/<spec>.json` — spell-id'er pr. spec (spenders/procs/
  defensives/major_cds). Byg sammen med brugeren; verificér id'erne mod
  brugerens egen log (spell-id'er ses i linse-output), ikke fra hukommelse.
- Første rigtige log: tjek `counts.parse_warnings` i summary. Systematisk
  høje tal på ét event-navn = log-formatdrift → justér suffix-konstanter i
  `parse.py` og opdatér fixturen (`tests/make_fixture.py`).

## Brugerprofil (udfyldes løbende)

- **Karakter/main:** (udfyld ved første analyse)
- **Keybinds (finger-roller):** (registreres af coach-skill'et)
- **UI-stack:** (fx NaowhUI, native Cooldown Manager — spørg)
- **Mål:** (fx "time +12 stabilt" — spørg)

## Hårde regler

- Målt ≠ modelleret ≠ eksternt — brug `kind`-felterne fra linse-output;
  intet tal uden proveniens.
- Live-tuning (tierlister, patch-tal) hentes ALTID live med kilde — aldrig
  fra modelviden. Min viden om spilversioner er forældet pr. design.
- Gruppemedlemmer pseudonymiseres i artifacts ("de fire andre").
- Output på dansk; spell-/addon-navne forbliver engelske.
- Tests: `pytest tests/ -q` skal være grøn før push.
