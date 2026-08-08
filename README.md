# WoWBattleLog — Combat Log Coach

Claude Code-plugin til personlig WoW-præstationsanalyse og coaching: en
**coach, ikke et scoreboard**. Plugin'et læser rå combat logs lokalt, bygger
en persistent spillestils-profil og leverer visuelle analyser hvor hver fejl
har et tidspunkt og en koordinat — oversat til dine egne keybinds og din
UI-stack, med målbare benchmarks der efterprøves i næste log.

Bygget efter Combat Log Coach PRD v1.0 (8. august 2026).

## Struktur

```
combat-log-coach/                  # selve plugin'et
├── .claude-plugin/plugin.json
├── skills/
│   ├── analyze-log/               # F1 ingest/parsing + F3 linser (offline)
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       ├── parse.py           # streaming parser, segmentering, cache
│   │       ├── lenses.py          # 6 genkørbare analyselinser
│   │       ├── profile.py         # F2: spillestils-profil (kerneaktivet)
│   │       └── baseline.py        # F7: progression, delta-rapport, goals
│   ├── build-dashboard/           # F4: visuel artifact
│   │   ├── SKILL.md               # komponentbibliotek + dataviz-regler
│   │   └── scripts/caveats.py     # auto-genereret forbehold-sektion
│   ├── coach/SKILL.md             # F5: keybinds, cues, øveplan
│   └── write-guide/               # F6: vidensliag (eneste online-skill)
│       ├── SKILL.md
│       └── scripts/citation_lint.py  # "X anbefaler Y" kræver fetch-beleg
├── templates/dashboard.css        # delt designsystem (mørkt, CVD-sikkert)
├── metrics.json                   # normativt metrik-katalog (PRD §6)
└── profile.schema.json            # F2-skema: målt ≠ modelleret ≠ eksternt

tests/                             # regressionstests + syntetisk fixture-log
```

## Kom i gang

```bash
# 1. Friskheds-tjek af din Logs-mappe (opdager manglende /combatlog)
python3 combat-log-coach/skills/analyze-log/scripts/parse.py \
    "/sti/til/World of Warcraft/_retail_/Logs" --check

# 2. Parse (summary-JSON < 100 KB; fuld eventstrøm caches på disk)
python3 combat-log-coach/skills/analyze-log/scripts/parse.py \
    "/sti/til/Logs" --cache .clc-cache > summary.json

# 3. Kør linser (genkørbare datasnit — re-parser aldrig rå-loggen)
python3 combat-log-coach/skills/analyze-log/scripts/lenses.py \
    .clc-cache WoWCombatLog-<stem> --spec-config spec.json > lens.json

# 4. Opdatér profil + tag baseline-snapshot
python3 combat-log-coach/skills/analyze-log/scripts/profile.py \
    update profile.json --lens-output lens.json --spec "Frost Mage"
python3 combat-log-coach/skills/analyze-log/scripts/baseline.py \
    snapshot baseline.json --lens-output lens.json

# 5. Progression mod sidste log (samme dungeon prioriteres)
python3 combat-log-coach/skills/analyze-log/scripts/baseline.py report baseline.json
```

Kræver `ADVANCED_LOG_ENABLED` i spillet (Options → Gameplay → Network) for
positioner og ressourcedata. Rå logs forlader aldrig maskinen — kun
aggregater indgår i modelkontekst.

## Tests

```bash
pytest tests/ -q
```

Fixture-loggen (`tests/fixtures/`) er syntetisk og deterministisk
(`tests/make_fixture.py`) og fungerer som regressionsværn mod
log-formatdrift (PRD §9.1). Parseren mapper felter bagfra pr. event-type og
tåler ukendte events/felter uden crash. Ydelse: ~100 MB log parses på ~8 s.

## Milepælsstatus (PRD §8)

| Fase | Indhold | Status |
|---|---|---|
| MVP | F1 + F3 (linser) + F4 (dashboard + forbehold) | ✅ værktøjer bygget |
| v1 | F2-profil, F5-coaching, F7-baseline | ✅ værktøjer bygget |
| v2 | Kontrafaktisk model, rutekort, F6-guides m. citations-lint, re-verifikation | 🔶 F6 + rutekort-spec klar; kontrafaktisk model undervejs |

## Hårde designprincipper

- **Målt ≠ modelleret ≠ eksternt** er datastruktur (`kind`-felter, badges,
  lint) — ikke stilistiske forbehold.
- Forbehold-sektionen i artifacts **genereres** fra `metrics.json`
  (`caveats.py`); den håndskrives aldrig.
- Enhver "X anbefaler Y"-sætning i guides kræver fetch-beleg
  (`citation_lint.py`, exit 1 ved brud).
- Gruppemedlemmer pseudonymiseres i delte artifacts ("de fire andre").
