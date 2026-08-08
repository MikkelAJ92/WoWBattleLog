---
name: write-guide
description: >-
  Skriv selvstændige HTML-guides (spec-guide, alt-guide, patch-guide) bygget
  af live-hentede kilder plus spillerens målte profil. Brug når brugeren
  spørger om spec-/klassevalg ("passer en anden spec bedre til mig?"),
  gearing, tierlister, patch-ændringer, eller beder om en guide/opslagsværk.
  Eneste skill med websøgning — analysemoduler er offline pr. design.
---

# Skriv guide (F6)

Guides er selvstændige HTML-artifacts (samme designsystem som dashboardet,
`templates/dashboard.css`) der kombinerer **live-hentede kilder** med
**spillerens målte profil**. En guide uden citationer er ikke en guide —
det er en hallucination med pæn typografi.

## Den hårde tredeling (datastruktur, ikke stil)

Hver påstand i guiden ligger i ét af tre spor, markeret i selve HTML'en:

| Spor | Markup | Krav |
|---|---|---|
| **Stabil mekanik** (fra modelviden: klassens grundmekanik, ressourcesystem) | `<span data-claim="stable">` | Ingen kilde påkrævet, men må ikke indeholde tuning-tal |
| **Aktuel tuning** (tierlister, procenter, buffs/nerfs, "X anbefaler Y") | `<span data-claim="external" data-source-url="https://…">` | SKAL hentes live i denne session + synligt kildelink |
| **Udokumenteret slutning** (din egen inferens fra profilen) | `<span data-claim="inference">` | Skal formuleres som slutning ("ud fra din målte…") |

**Lint-reglen fra referencesessionen:** enhver "X anbefaler Y"-sætning
kræver fetch-beleg. Referencesessionen fangede selv et brud (en
ikke-verificeret kilde-attribution) — derfor er linten kode, ikke princip:

```bash
python3 scripts/citation_lint.py guide.html
```

Kør linten FØR guiden leveres/opdateres. Fejl = ret markup eller hent
kilden; publicér aldrig med lint-fejl. `--list-sources` udskriver alle
kilde-URL'er til re-verifikation.

## Arbejdsgang

1. **Profil først:** læs `profile.json` — guiden skal svare på "passer X til
   den spiller, jeg måleligt er?", ikke "hvad er bedst i abstraktion".
   Match profilens spec-agnostiske kategorier (proc-refleks, tempo,
   bevægelse, swap-disciplin) mod spec'ens krav, og markér disse koblinger
   som `data-claim="inference"`.
2. **Hent kilder live** (websøgning er tilladt i dette skill): officielle
   patch notes, top-logs/statistik-sider, tierlister, gearing-data. Notér
   URL + hentedato for hver. Brug flere uafhængige kilder til tuning-påstande.
3. **Skriv guiden** som sektioneret HTML (`data-clc-section` som dashboardet)
   med tydelig adskillelse: "det her er mekanik / det her er aktuel tuning /
   det her er min slutning fra din profil".
4. **Stamp verifikation:** guiden SKAL indeholde
   `<time data-clc-verified="ÅÅÅÅ-MM-DD">` i headeren (linten håndhæver det).
5. **Lint → levér.** Forbehold-sektion genereres med `caveats.py` hvis guiden
   citerer målte metrikker.

## Re-verifikationsjobbet

Ved patch-datoer (eller på brugerens forlangende) genverificeres guiden:

1. `python3 scripts/citation_lint.py guide.html --list-sources` → hent hver
   kilde igen.
2. Diff anbefalingerne: uændret / ændret / kilden død.
3. Stemp ændringer i artifacten: opdatér `data-clc-verified`-datoen, markér
   ændrede anbefalinger med `<ins data-reverified="ÅÅÅÅ-MM-DD">` og en kort
   ændringslog-sektion (`data-clc-section="changelog"`). Døde kilder:
   erstat eller degradér påstanden til `data-claim="inference"` med
   eksplicit "kilde forsvundet"-note.
4. Planlæg jobbet som scheduled task når en patch-dato kendes — én kørsel
   pr. patch, ikke løbende polling.

## Grænser

- Ingen scraping-afhængighed i kernen: kan en benchmark-kilde ikke hentes,
  degraderer guiden pænt — alt kan måles absolut + mod egen baseline.
  Sig "benchmark utilgængelig", opfind aldrig percentiler.
- Rå logs og gruppemedlemmers navne hører ikke hjemme i guides.
- Output på brugerens sprog; spell-/item-navne forbliver engelske.
