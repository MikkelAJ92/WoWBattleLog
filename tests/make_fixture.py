#!/usr/bin/env python3
"""Deterministisk generator for den syntetiske fixture-log (regressionstest, PRD 9.1).

Genererer en lille men realistisk WoW advanced combat log:
  * 1 M+ run (Ara-Kara +12): trash-pull, boss-pull (Avanoxx) med spillerdød
  * 1 dummy-klynge i Valdrakken > 5 min senere
  * bevidst støj: ukendt event-type, linje uden felter, ikke-numerisk amount

Kendte totaler (bruges af tests/test_parse.py):
  pull 1: mage 30x6000=180000, hunter 10x3000=30000, wolf 15x2000=30000
  pull 2: mage 40x6000=240000, hunter 20x3000=60000
  dummy : mage 28x1000=28000
"""

from pathlib import Path

MAGE = ("Player-1403-0A1B2C3D", "Mikkel-TarrenMill", "0x511")
HUNTER = ("Player-1403-0E4F5A6B", "Jaeger-Ravencrest", "0x512")
WOLF = ("Creature-0-1403-2660-1-165189-000012AB34", "Ulv", "0x1112")
MOB = ("Creature-0-1403-2660-1-217039-0000AA0001", "Jabbering Perciever", "0xa48")
BOSS = ("Creature-0-1403-2660-1-215405-0000BB0001", "Avanoxx", "0xa48")
DUMMY = ("Creature-0-1469-2112-1-194648-0000CC0001", "Training Dummy", "0xa48")
NIL = "0000000000000000"

FROSTBOLT = (116, '"Frostbolt"', 16)
ARCANE_SHOT = (185358, '"Arcane Shot"', 64)
BITE = (17253, '"Bite"', 1)
BOSS_HIT = (438471, '"Alerting Shrill"', 8)


def ts(h, m, s):
    return f"8/8/2026 {h:02d}:{m:02d}:{s:06.3f}-4"


# --- Wire-geometri pr. log-version ------------------------------------------
# Feltlayoutet driver mellem patches, og parseren skal følge med. v22 (build
# 12.0.x) udvidede advanced-blokken fra 17 til 19 felter og gav spell-/range-
# skade en afsluttende ST/AOE-markør, som SWING_DAMAGE ikke har (10 suffiks-
# felter mod 11). Fixturen kan emitteres i begge geometrier, så testene kan
# kræve identiske parse-resultater på tværs af dem.
WIRE = {
    "v21": {"log_version": 21, "adv_pad": [], "spell_tail": "nil",
            "swing_tail": ["nil"]},
    "v22": {"log_version": 22, "adv_pad": [111, 222], "spell_tail": "ST",
            "swing_tail": []},
}
_wire = WIRE["v22"]


def adv(info, owner, hp, hpmax, pt=0, pc=250000, pm=250000, x=0, y=0,
        map_id=2660, facing=1.23, lvl=80):
    return [info, owner, hp, hpmax, 5000, 9000, 3000, *_wire["adv_pad"], 0,
            pt, pc, pm, 0, x, y, map_id, facing, lvl]


def dmg_suffix(amount, overkill=-1, school=16, crit="nil", swing=False):
    core = [amount, amount, overkill, school, 0, 0, 0, crit, "nil", "nil"]
    return core + (_wire["swing_tail"] if swing else [_wire["spell_tail"]])


def line(t, *fields):
    return t + "  " + ",".join(str(f) for f in fields)


def spell_damage(t, src, dst, spell, amount, dst_hp, dst_hpmax, x, y,
                 event="SPELL_DAMAGE", overkill=-1):
    sid, sname, school = spell
    # advanced-blokken beskriver DEST-unit (hp/position hører til målet)
    return line(t, event, src[0], f'"{src[1]}"', src[2], "0x0",
                dst[0], f'"{dst[1]}"', dst[2], "0x0", sid, sname, school,
                *adv(dst[0], NIL, dst_hp, dst_hpmax, x=x, y=y),
                *dmg_suffix(amount, overkill=overkill, school=school))


def swing_damage(t, src, dst, amount, src_hp, src_hpmax, x, y, owner=NIL):
    """Nærkamp fra ANGRIBERENS side — advanced-blokken beskriver angriberen."""
    return line(t, "SWING_DAMAGE", src[0], f'"{src[1]}"', src[2], "0x0",
                dst[0], f'"{dst[1]}"', dst[2], "0x0",
                *adv(src[0], owner, src_hp, src_hpmax, x=x, y=y),
                *dmg_suffix(amount, school=1, swing=True))


def swing_damage_landed(t, src, dst, amount, dst_hp, dst_hpmax, x, y):
    """Samme slag fra OFFERETS side — blokken beskriver offeret (bærer dets HP).
    Spillet logger begge; parseren skal tælle slaget præcis én gang."""
    return line(t, "SWING_DAMAGE_LANDED", src[0], f'"{src[1]}"', src[2], "0x0",
                dst[0], f'"{dst[1]}"', dst[2], "0x0",
                *adv(dst[0], NIL, dst_hp, dst_hpmax, x=x, y=y),
                *dmg_suffix(amount, school=1, swing=True))


def cast_success(t, src, dst, spell, x, y, pc=250000, pm=250000, pt=0,
                 owner=NIL):
    sid, sname, school = spell
    dstg, dstn, dstf = (dst[0], f'"{dst[1]}"', dst[2]) if dst else (NIL, "nil", "0x0")
    # advanced-blokken beskriver CASTEREN (position = spillerens sti)
    return line(t, "SPELL_CAST_SUCCESS", src[0], f'"{src[1]}"', src[2], "0x0",
                dstg, dstn, dstf, "0x0", sid, sname, school,
                *adv(src[0], owner, 2500000, 2800000, pt=pt, pc=pc, pm=pm,
                     x=x, y=y))


def aura(t, src, dst, spell, kind, event="SPELL_AURA_APPLIED"):
    sid, sname, school = spell
    return line(t, event, src[0], f'"{src[1]}"', src[2], "0x0",
                dst[0], f'"{dst[1]}"', dst[2], "0x0", sid, sname, school, kind)


def unit_died(t, unit):
    return line(t, "UNIT_DIED", NIL, "nil", "0x80000000", "0x80000000",
                unit[0], f'"{unit[1]}"', unit[2], "0x0", 0)


def build(wire: str = "v22") -> str:
    """Byg fixturen som (tid, linje)-par og sortér globalt — ægte combat
    logs er kronologiske, og pull-segmenteringen afhænger af det.

    ``wire`` vælger feltgeometri (se WIRE). Indholdet er identisk på tværs af
    geometrier; kun kodningen på wire adskiller sig."""
    global _wire
    _wire = WIRE[wire]
    L: list[tuple[float, str]] = []

    def emit(sec: float, text: str) -> None:
        L.append((sec, text))

    def tsec(h, m, s) -> float:
        return h * 3600 + m * 60 + s

    emit(tsec(20, 0, 0), line(ts(20, 0, 0), "COMBAT_LOG_VERSION",
                              _wire["log_version"],
                              "ADVANCED_LOG_ENABLED", 1, "BUILD_VERSION",
                              "11.2.0", "PROJECT_ID", 1))
    emit(tsec(20, 0, 1), line(ts(20, 0, 1), "ZONE_CHANGE", 2660,
                              '"Ara-Kara, City of Echoes"', 23))
    emit(tsec(20, 0, 5), line(ts(20, 0, 5), "CHALLENGE_MODE_START",
                              '"Ara-Kara, City of Echoes"', 2660, 503, 12,
                              "[160,9,10]"))

    # --- Pull 1: trash (20:00:10 – 20:00:37.55) -----------------------------
    emit(tsec(20, 0, 9.5), cast_success(ts(20, 0, 9.5), WOLF, MOB, BITE,
                                        1002, 2001, owner=HUNTER[0]))
    mob_hp = 3_000_000
    for i in range(30):
        t = 10 + i * 0.95
        emit(tsec(20, 0, t - 0.1),
             cast_success(ts(20, 0, t - 0.1), MAGE, MOB, FROSTBOLT,
                          1000 + i, 2000, pc=250000 - i * 800))
        mob_hp -= 6000
        emit(tsec(20, 0, t), spell_damage(ts(20, 0, t), MAGE, MOB, FROSTBOLT,
                                          6000, mob_hp, 3_000_000, 3000, 4000))
    emit(tsec(20, 0, 11), aura(ts(20, 0, 11), MAGE, MOB,
                               (228358, '"Winter\'s Chill"', 16), "DEBUFF"))
    # stak-rampe 2→8 (DOSE-events bærer det aktuelle stak-antal). Specs med
    # stak-tærskel skal kunne skelne "debuffen er på" fra "nok stakke".
    for n in range(2, 9):
        emit(tsec(20, 0, 10 + n), aura(ts(20, 0, 10 + n), MAGE, MOB,
                                       (228358, '"Winter\'s Chill"', 16),
                                       f"DEBUFF,{n}",
                                       event="SPELL_AURA_APPLIED_DOSE"))
    emit(tsec(20, 0, 12), aura(ts(20, 0, 12), MAGE, MAGE,
                               (44544, '"Fingers of Frost"', 16), "BUFF"))
    emit(tsec(20, 0, 13), line(ts(20, 0, 13), "SPELL_ENERGIZE", MAGE[0],
                               f'"{MAGE[1]}"', MAGE[2], "0x0", MAGE[0],
                               f'"{MAGE[1]}"', MAGE[2], "0x0",
                               190446, '"Brain Freeze"', 16,
                               *adv(MAGE[0], NIL, 2500000, 2800000,
                                    x=1010, y=2000),
                               500, 0, 0, 250000))
    for i in range(10):
        t = 12 + i * 2.5
        mob_hp -= 3000
        emit(tsec(20, 0, t), spell_damage(ts(20, 0, t), HUNTER, MOB,
                                          ARCANE_SHOT, 3000, mob_hp,
                                          3_000_000, 3010, 4010))
    # ulven slår mobben — spillet logger slaget fra BEGGE sider; skaden må
    # kun tælle én gang (15 x 2000 = 30000, jf. de kendte totaler)
    for i in range(15):
        t = 11 + i * 1.8
        mob_hp -= 2000
        emit(tsec(20, 0, t), swing_damage(ts(20, 0, t), WOLF, MOB, 2000,
                                          900_000, 900_000, 3005, 4005,
                                          owner=HUNTER[0]))
        emit(tsec(20, 0, t), swing_damage_landed(ts(20, 0, t), WOLF, MOB, 2000,
                                                 mob_hp, 3_000_000, 3005, 4005))
    emit(tsec(20, 0, 39), unit_died(ts(20, 0, 39), MOB))

    # --- støj mellem pulls ---------------------------------------------------
    emit(tsec(20, 0, 50), line(ts(20, 0, 50), "TOTALLY_NEW_EVENT", "foo",
                               '"bar"', 1, 2, 3))
    emit(tsec(20, 0, 50.5), "garbage line without separator")

    # --- Pull 2: boss (20:01:02 – 20:01:57.1) -------------------------------
    emit(tsec(20, 1, 0), line(ts(20, 1, 0), "ENCOUNTER_START", 2926,
                              '"Avanoxx"', 8, 5, 2660))
    boss_hp = 25_000_000
    for i in range(40):
        t = 2 + i * 1.2
        emit(tsec(20, 1, t - 0.1),
             cast_success(ts(20, 1, t - 0.1), MAGE, BOSS, FROSTBOLT,
                          1100 + i, 2100))
        boss_hp -= 6000
        emit(tsec(20, 1, t), spell_damage(ts(20, 1, t), MAGE, BOSS, FROSTBOLT,
                                          6000, boss_hp, 25_000_000,
                                          3100, 4100))
    # ikke-numerisk amount → parse_warning, må ikke crashe
    bad = spell_damage(ts(20, 1, 10), MAGE, BOSS, FROSTBOLT, 6000,
                       boss_hp, 25_000_000, 3100, 4100)
    emit(tsec(20, 1, 10.05),
         bad.replace(",6000,6000,-1,16,", ",NaNsense,6000,-1,16,", 1))
    for i in range(20):
        t = 2 + i * 2.9
        boss_hp -= 3000
        emit(tsec(20, 1, t), spell_damage(ts(20, 1, t), HUNTER, BOSS,
                                          ARCANE_SHOT, 3000, boss_hp,
                                          25_000_000, 3110, 4110))
    # boss slår spilleren — hp falder, advanced beskriver spilleren (dest)
    player_hp = 2_800_000
    for i in range(10):
        t = 20 + i * 3
        player_hp -= 260_000
        emit(tsec(20, 1, t), spell_damage(ts(20, 1, t), BOSS, MAGE, BOSS_HIT,
                                          260_000, max(player_hp, 0),
                                          2_800_000, 1105 + i, 2105))
        # nærkamp oveni: kun LANDED bærer spillerens HP
        emit(tsec(20, 1, t + 0.4),
             swing_damage(ts(20, 1, t + 0.4), BOSS, MAGE, 40_000,
                          25_000_000, 25_000_000, 1105 + i, 2105))
        emit(tsec(20, 1, t + 0.4),
             swing_damage_landed(ts(20, 1, t + 0.4), BOSS, MAGE, 40_000,
                                 max(player_hp, 0), 2_800_000, 1105 + i, 2105))
    emit(tsec(20, 1, 49), unit_died(ts(20, 1, 49), MAGE))
    emit(tsec(20, 2, 1), line(ts(20, 2, 1), "ENCOUNTER_END", 2926,
                              '"Avanoxx"', 8, 5, 1, 59000))
    emit(tsec(20, 2, 30), line(ts(20, 2, 30), "CHALLENGE_MODE_END", 2660, 1,
                               12, 1830000))

    # --- Dummy-klynge i Valdrakken (> 5 min senere) --------------------------
    emit(tsec(20, 40, 0), line(ts(20, 40, 0), "ZONE_CHANGE", 2112,
                               '"Valdrakken"', 0))
    dummy_hp = 5_000_000
    for i in range(28):
        t = 60 + i * 2.0
        mc, sc = divmod(t - 0.1, 60)
        emit(tsec(20, 40, t - 0.1),
             cast_success(ts(20, 40 + int(mc), sc), MAGE, DUMMY, FROSTBOLT,
                          500 + i, 600))
        m, s = divmod(t, 60)
        dummy_hp -= 1000
        emit(tsec(20, 40, t), spell_damage(ts(20, 40 + int(m), s), MAGE,
                                           DUMMY, FROSTBOLT, 1000, dummy_hp,
                                           5_000_000, 700, 800))
    L.sort(key=lambda p: p[0])
    return "\n".join(text for _, text in L) + "\n"


def write(path: Path, wire: str = "v22") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build(wire), encoding="utf-8")
    return path


if __name__ == "__main__":
    out = Path(__file__).parent / "fixtures" / "WoWCombatLog-fixture.txt"
    print(f"Skrev {write(out)} ({out.stat().st_size} bytes)")
