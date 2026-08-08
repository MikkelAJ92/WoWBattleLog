"""Regressionstests for F1-parseren mod den syntetiske fixture-log.

Kendte totaler er dokumenteret i make_fixture.py — ændrer du fixturen,
skal tallene her opdateres bevidst (det er pointen med regressionstesten).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent
                       / "combat-log-coach" / "skills" / "analyze-log" / "scripts"))

import make_fixture  # noqa: E402
import parse  # noqa: E402

MAGE_GUID = make_fixture.MAGE[0]
HUNTER_GUID = make_fixture.HUNTER[0]
WOLF_GUID = make_fixture.WOLF[0]


@pytest.fixture(scope="module")
def summary(tmp_path_factory):
    root = tmp_path_factory.mktemp("clc")
    log = make_fixture.write(root / "WoWCombatLog-fixture.txt")
    return parse.parse_file(log, root / "cache")


def test_run_segmentation(summary):
    runs = summary["runs"]
    assert len(runs) == 2
    mplus, other = runs
    assert mplus["type"] == "mplus"
    assert mplus["zone"] == "Ara-Kara, City of Echoes"
    assert mplus["key_level"] == 12
    assert mplus["success"] == 1
    assert mplus["timer_s"] == 1830.0
    assert mplus["affixes"] == "[160,9,10]"
    assert other["type"] == "other"
    assert other["zone"] == "Valdrakken"


def test_boss_segmentation(summary):
    bosses = summary["runs"][0]["bosses"]
    assert len(bosses) == 1
    assert bosses[0]["name"] == "Avanoxx"
    assert bosses[0]["success"] == 1


def test_pull_segmentation_and_damage(summary):
    pulls = summary["runs"][0]["pulls"]
    assert len(pulls) == 2
    trash, boss = pulls
    assert trash["damage_by_player"][MAGE_GUID] == 180_000
    # pet-skade (ulven) attribueres til hunteren: 30k egen + 30k pet
    assert trash["damage_by_player"][HUNTER_GUID] == 60_000
    assert trash["damage_total"] == 240_000
    assert boss["damage_by_player"][MAGE_GUID] == 240_000
    assert boss["damage_total"] == 300_000


def test_dummy_cluster_pull(summary):
    pulls = summary["runs"][1]["pulls"]
    assert len(pulls) == 1
    # under 150k skade, men > 8 s varighed → beholdes
    assert pulls[0]["damage_total"] == 28_000
    assert pulls[0]["duration_s"] >= 8


def test_self_detection_and_group(summary):
    assert summary["player"]["guid"] == MAGE_GUID
    assert summary["player"]["name"] == "Mikkel-TarrenMill"
    assert summary["player"]["auto_detected"] is True
    assert {p["guid"] for p in summary["group"]} == {MAGE_GUID, HUNTER_GUID}


def test_pet_attribution(summary):
    assert summary["pets"][WOLF_GUID] == HUNTER_GUID


def test_player_death_recorded(summary):
    deaths = summary["runs"][0]["deaths"]
    assert len(deaths) == 1
    assert deaths[0]["guid"] == MAGE_GUID


def test_version_drift_tolerated(summary):
    counts = summary["counts"]
    assert counts["unknown_events"].get("TOTALLY_NEW_EVENT") == 1
    assert counts["parse_warnings"].get("SPELL_DAMAGE:amount:value") == 1
    assert counts["parse_warnings"].get("line:short") == 1


def test_advanced_logging_detected(summary):
    assert summary["source"]["advanced_logging"] is True
    assert summary["source"]["log_version"] == "22"


def test_slim_event_stream(summary, tmp_path_factory):
    cache = Path(summary["cache_dir"]).parent
    rows = list(parse.iter_run_events(cache, "WoWCombatLog-fixture", 1))
    assert rows, "run-001 skal have slim events"
    evs = {r[1] for r in rows}
    assert {"CHALLENGE_MODE_START", "ENCOUNTER_START", "SPELL_CAST_SUCCESS",
            "SPELL_DAMAGE", "UNIT_DIED", "SPELL_AURA_APPLIED"} <= evs
    # cast-positioner (spillerens sti, jf. rutekort-kravet)
    cast = next(r for r in rows if r[1] == "SPELL_CAST_SUCCESS"
                and r[2] == MAGE_GUID)
    assert cast[10] == 1000 and cast[11] == 2000
    assert cast[12]["pc"] == 250_000
    # damage-taget bærer hp til death recap
    hit = next(r for r in rows if r[1] == "SPELL_DAMAGE"
               and r[4] == MAGE_GUID)
    assert hit[12]["hpmax"] == 2_800_000


def test_cache_reuse(summary):
    cache_root = Path(summary["cache_dir"]).parent
    log = cache_root.parent / "WoWCombatLog-fixture.txt"
    again = parse.parse_file(log, cache_root)
    assert again["cache_hit"] is True
    assert again["runs"] == summary["runs"]
    forced = parse.parse_file(log, cache_root, force=True)
    assert forced["cache_hit"] is False
    assert forced["runs"] == summary["runs"]


def test_check_mode_missing_logs(tmp_path):
    code, msg = parse.check_logs_dir(tmp_path)
    assert code == 1
    assert "/combatlog" in msg
    assert "Advanced Combat Logging" in msg


def test_check_mode_fresh_log(tmp_path):
    make_fixture.write(tmp_path / "WoWCombatLog-fixture.txt")
    code, msg = parse.check_logs_dir(tmp_path)
    assert code == 0


# --- Versionsdrift: samme indhold, to wire-geometrier ------------------------
# v22 (build 12.0.x) udvidede advanced-blokken 17 → 19 felter og gav spell-
# skade en afsluttende ST-markør, som SWING_DAMAGE ikke har. Parseren skal
# kalibrere sig ud af forskellen, ikke antage en fast længde.

def _parse_wire(tmp_path, wire):
    log = make_fixture.write(tmp_path / f"WoWCombatLog-{wire}.txt", wire=wire)
    return parse.parse_file(log, tmp_path / f"cache-{wire}")


def test_adv_block_length_is_calibrated_per_log(tmp_path):
    for wire, expected in (("v21", 17), ("v22", 19)):
        log = make_fixture.write(tmp_path / f"cal-{wire}.txt", wire=wire)
        assert parse._calibrate_adv_len(log) == (expected, "log")


def test_wire_geometries_parse_identically(tmp_path):
    a = _parse_wire(tmp_path, "v21")
    b = _parse_wire(tmp_path, "v22")
    assert a["source"]["adv_block_len"] == 17
    assert b["source"]["adv_block_len"] == 19
    # Skadetotaler, pulls, bosser og døde skal være bit-identiske.
    assert a["runs"] == b["runs"]
    assert a["counts"]["by_event"] == b["counts"]["by_event"]
    assert a["player"] == b["player"]


def test_v22_reads_hp_and_swing_amount(tmp_path):
    """hp/hpmax ligger i blokkens start, swing-amount efter blokkens slut —
    begge blev læst forkert da blokken voksede."""
    summary = _parse_wire(tmp_path, "v22")
    run = summary["runs"][0]
    cache = Path(summary["cache_dir"]).parent
    rows = list(parse.iter_run_events(cache, "WoWCombatLog-v22", run["id"]))
    hit = next(r for r in rows if r[1] == "SPELL_DAMAGE" and r[4] == MAGE_GUID)
    assert hit[12]["hpmax"] == 2_800_000
    swing = next(r for r in rows if r[1] == "SWING_DAMAGE")
    assert swing[8] == 2000            # ulvens bid, ikke et felt fra blokken


def test_multi_resource_power_fields(tmp_path):
    """Units med flere ressourcer logger dem pipe-separeret (paladin:
    powerType='9|0'). Primærressourcen læses; ingen parse-warning."""
    log = tmp_path / "WoWCombatLog-power.txt"
    body = make_fixture.build()
    piped = body.replace(",0,250000,250000,0,", ",9|0,5|250000,5|250000,0,")
    assert piped != body, "fixturen skal indeholde ressourcefelter at pipe'e"
    log.write_text(piped, encoding="utf-8")
    summary = parse.parse_file(log, tmp_path / "cache")
    assert not any(k.startswith(("cast:p", "energize:p"))
                   for k in summary["counts"]["parse_warnings"])
    # primærressourcen (før '|') skal nå frem til slim-strømmen
    rows = list(parse.iter_run_events(tmp_path / "cache",
                                      "WoWCombatLog-power", 1))
    cast = next(r for r in rows if r[1] == "SPELL_CAST_SUCCESS"
                and r[2] == MAGE_GUID and r[12].get("pt") is not None)
    assert cast[12]["pt"] == 9 and cast[12]["pc"] == 5


def test_melee_counted_once_and_carries_victim_hp(summary):
    """Nærkamp logges fra begge sider. Skaden må tælles én gang, og HP i
    death recap skal komme fra offerets side (SWING_DAMAGE_LANDED)."""
    trash = summary["runs"][0]["pulls"][0]
    assert trash["damage_by_player"][HUNTER_GUID] == 60_000   # 30k egen + 30k pet
    assert trash["damage_total"] == 240_000

    cache = Path(summary["cache_dir"]).parent
    rows = list(parse.iter_run_events(cache, "WoWCombatLog-fixture", 1))
    taken = [r for r in rows if r[4] == MAGE_GUID
             and r[1] in ("SWING_DAMAGE", "SWING_DAMAGE_LANDED")]
    assert taken, "nærkamp mod spilleren skal nå slim-strømmen"
    assert {r[1] for r in taken} == {"SWING_DAMAGE_LANDED"}
    assert sum(r[8] for r in taken) == 10 * 40_000            # ingen dobbelttælling
    assert all(r[12]["hpmax"] == 2_800_000 for r in taken)    # offerets HP, ikke bossens
