---
name: coach
description: >-
  Oversæt målte fejl til konkret træning: anvisninger på brugerens egne
  keybinds, UI-cues i brugerens addon-stack og en øveplan med målbare
  benchmarks. Brug efter analyse når brugeren spørger "hvad skal jeg øve?",
  "hvordan fikser jeg det?", "lav cues/weakauras", "hvilke keybinds", eller
  når et dashboard skal have en coaching-sektion. Kræver linse-output; brug
  analyze-log først.
---

# Coach (F5)

En coach, ikke et scoreboard: hver anvisning udspringer af en **målt** fejl
(med tidspunkt/koordinat) og udtrykkes i **brugerens taster og UI** — aldrig
generisk "brug dine procs bedre".

## Keybind-oversætteren

**Registrér binds én gang**, gem i `profile.json → player.keybinds`
(skemafelter: `builder`, `spender`, `proc`, `cd_major`, `cd_minor`, `aoe`,
`defensive`, `emergency`, `movement`, `interrupt`) og resumér i CLAUDE.md.
Spørg kun om de roller, analysen faktisk skal bruge.

**Finger-roller bevares på tværs af specs/klasser** (valideret på 3 klasser):
rollen "spender" sidder på samme fysiske tast uanset om spell'et hedder Ice
Lance eller Death Coil. Konsekvenser:

- Alle anvisninger skrives "tast (spell)": *"Shift-3 (Ice Lance) kun når
  E-debuffen står på målet"* — aldrig spell-navnet alene.
- Ved spec-/klasseskift: oversæt anbefalinger via rollen, ikke spell-navnet.
  Mangler en rolle-bind på den nye spec, foreslå at genbruge tasten fra
  samme rolle — det er dét, der gør cross-spec-forudsigelser omsættelige.
- Profilens kategorier er spec-agnostiske: en målt proc-refleks eller
  spender-spam-tendens på én klasse **skal** nævnes som forudsigelse, når
  brugeren skifter (referencen: spender-spam → RP-tørke på Blood DK).

## Cue-generatoren

Hver målt fejl → én konkret cue i brugerens UI-stack. Levér altid som tabel:
**fejl (målt) → cue → byggeanvisning i brugerens stack → begrænsning**.

Regler (ufravigelige):

- **Maks ~3 lyd-cues pr. spec. Én lyd = én handling.** Genbrug indlærte lyde
  på tværs af specs ved samme handlingstype (proc-lyd forbliver proc-lyd).
- Visuelle cues udtrykkes i brugerens faktiske stack — native Cooldown
  Manager, EllesmereUI/NaowhUI-konventioner, stack-tracker-addons. Spørg om
  stacken én gang; gem i `profile.json → player.ui_stack`.
- **Sig hvad der IKKE kan bygges, og hvorfor** (secret values):
  præcise ressourcetal er skjult i kamp for addons — cues på "ved præcis X
  runic power" kan ikke laves; native lyd-alerts findes kun på castables.
  Tilbyd den nærmeste byggbare proxy i stedet (aura-stacks, spell-usable).
- En cue skal pege på en **beslutning**, ikke en tilstand: "lyd når proc'en
  er ved at udløbe" (handling: brug den), ikke "lyd når proc'en kommer".

## Øveplanen

1. **Pr. pull:** vælg de 1–2 dyreste fejl-klynger fra `context`-linsen og
   formulér "det du øver i den her": én sætning, én beslutning, på brugerens
   keybinds. Aldrig mere end to fokuspunkter ad gangen.
2. **Målbare benchmarks pr. metrik** — formulér som tal, gem som goals, så
   næste log evaluerer automatisk:
   ```bash
   python3 ../analyze-log/scripts/baseline.py set-goal baseline.json \
       --metric blind_spender_rate_st --op "<" --target 0.10 \
       --note "maks 10 % blinde lances"
   ```
   Typiske benchmarks: `blind_spender_rate_st < 0.10`,
   `lost_moving_share < 0.40`, `selfcancelled_hardcasts <= 5`,
   `proc_utilization >= 0.95`, `deaths <= 1`.
3. **Efter næste log:** kør `baseline.py snapshot` + `baseline.py report` —
   rapporten viser delta pr. metrik og goal-status, med samme-dungeon-
   sammenligninger prioriteret. Vis det i dashboardets
   `progression`-sektion ("blinde lances 31 % → 20 % ✓").

## Ærlighed

- Coaching bygger KUN på målte fejl (event-forankrede, fra linserne) —
  aldrig på generiske guide-råd. Guide-viden hører til `write-guide`.
- Hvis en fejl kan være et vilkår (target cap, bossdesign, gruppens
  pull-tempo), sig det eksplicit i stedet for at coache imod det.
- Output på brugerens sprog; spell-navne og addon-navne forbliver engelske.
