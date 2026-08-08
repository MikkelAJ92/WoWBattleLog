"""Tests for F3-linserne: enheds-tests på håndbyggede slim-rækker plus
integration mod fixture-loggen med Frost Mage-agtig spec-config."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent
                       / "combat-log-coach" / "skills" / "analyze-log" / "scripts"))

import make_fixture  # noqa: E402
import parse  # noqa: E402
import lenses  # noqa: E402

MAGE = make_fixture.MAGE[0]
HUNTER = make_fixture.HUNTER[0]

SPEC = {
    "spec": "Frost Mage (test)",
    "spenders": {"116": {"target_debuff": [228358]}},
    "procs": [44544],
    "defensives": {"45438": {"name": "Ice Block", "cd_s": 240}},
}


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    root = tmp_path_factory.mktemp("clc-lens")
    log = make_fixture.write(root / "WoWCombatLog-fixture.txt")
    parse.parse_file(log, root / "cache")
    return lenses.run_lenses(root / "cache", "WoWCombatLog-fixture",
                             spec_config=SPEC)


def run1(result):
    return result["runs"][0]


# --- enheds-tests på håndbyggede rækker -------------------------------------

def _row(t, ev, sg="P1", dg="Creature-1", sp=1, spn="Spell", amt=None,
         x=None, y=None, ex=None, dn="Mob", sn="Me"):
    return [t, ev, sg, sn, dg, dn, sp, spn, amt, None, x, y, ex]


def _rd(rows, pulls, self_guid="P1", group=None, pets=None):
    run = {"pulls": pulls, "bosses": [], "deaths": []}
    return lenses.RunData(run, rows, self_guid, group or {"P1"}, pets or {})


def test_movement_gap_attribution():
    # cast ved t=0 (x=0) → cast ved t=5 (x=100): hul 5 s, flyttet → moving
    # cast ved t=6.5 → t=11 (x uændret): hul 4,5 s, stillestående
    rows = [
        _row(0.0, "SPELL_CAST_SUCCESS", x=0, y=0),
        _row(5.0, "SPELL_CAST_SUCCESS", x=100, y=0),
        _row(6.5, "SPELL_CAST_SUCCESS", x=100, y=0),
        _row(11.0, "SPELL_CAST_SUCCESS", x=100, y=0),
        _row(0.5, "SPELL_DAMAGE", amt=200_000),
        _row(11.0, "SPELL_DAMAGE", amt=200_000),
    ]
    pulls = [{"id": 1, "t0": 0.0, "t1": 11.0, "duration_s": 11.0}]
    out = lenses.lens_movement(_rd(rows, pulls), lenses.SpecConfig(None))
    # (5-1.5) + (4.5-1.5) = 6.5; hullet på 1.5 s er under 1.7 s-grænsen
    assert out["lost_cast_seconds"]["value"] == pytest.approx(6.5)
    assert out["lost_moving_share"]["value"] == pytest.approx(3.5 / 6.5, abs=0.001)
    assert out["lost_cast_seconds"]["kind"] == "measured"
    assert out["lost_cast_seconds"]["error_source"]  # fra metrics.json


def test_movement_teleport_clipped():
    rows = [
        _row(0.0, "SPELL_CAST_SUCCESS", x=0, y=0),
        _row(5.0, "SPELL_CAST_SUCCESS", x=5000, y=0),  # portal-spring
        _row(0.5, "SPELL_DAMAGE", amt=200_000),
    ]
    pulls = [{"id": 1, "t0": 0.0, "t1": 5.0, "duration_s": 5.0}]
    out = lenses.lens_movement(_rd(rows, pulls), lenses.SpecConfig(None))
    assert out["per_pull"][0]["yards"] == 0  # klippet, ikke 5000
    # hullet tælles, men ikke som bevægelse (teleport ≠ løb)
    assert out["lost_moving_share"]["value"] == 0


def test_selfcancelled_casts():
    rows = [
        _row(0.0, "SPELL_CAST_START", sp=116, spn="Frostbolt"),
        _row(1.0, "SPELL_CAST_START", sp=116, spn="Frostbolt"),  # forrige afbrudt
        _row(3.0, "SPELL_CAST_SUCCESS", sp=116, spn="Frostbolt"),
        _row(4.0, "SPELL_CAST_START", sp=116, spn="Frostbolt"),
        _row(4.5, "SPELL_CAST_FAILED", sp=116, spn="Frostbolt",
             ex={"failedType": "Interrupted"}),
    ]
    out = lenses._selfcancelled(rows)
    assert len(out) == 2
    assert out[1]["failed"] == "Interrupted"


def test_sustain_share_flat():
    rows = []
    for i in range(10):  # 100 s pull, 10 faser
        t = i * 10 + 1.0
        rows.append(_row(t, "SPELL_DAMAGE", sg="P1", amt=100))
        rows.append(_row(t + 1, "SPELL_DAMAGE", sg="P2", amt=100))
    pulls = [{"id": 1, "t0": 0.0, "t1": 100.0, "duration_s": 100.0}]
    rd = _rd(rows, pulls, group={"P1", "P2"})
    out = lenses.lens_sustain(rd, lenses.SpecConfig(None))
    curve = out["phase_share_curve"]["value"]
    assert len(curve) == 10
    assert all(v == pytest.approx(0.5) for v in curve)
    assert out["phase_share_curve"]["metric_id"] == "phase_share"


def test_sustain_requires_long_pull():
    rows = [_row(1.0, "SPELL_DAMAGE", amt=100)]
    pulls = [{"id": 1, "t0": 0.0, "t1": 20.0, "duration_s": 20.0}]
    out = lenses.lens_sustain(_rd(rows, pulls), lenses.SpecConfig(None))
    assert "unavailable" in out


# --- integration mod fixturen -------------------------------------------------

def test_blind_spender_rate(result):
    rot = run1(result)["rotation"]
    bs = rot["blind_spenders"]
    # 30 + 40 spender-casts; blinde: 2 før Winter's Chill + alle 40 på bossen
    assert bs["value"]["total_spender_casts"] == 70
    assert bs["value"]["blind"] == 42
    assert bs["value"]["rate"] == pytest.approx(0.6)
    assert bs["value"]["blind_spender_rate_st"] == pytest.approx(0.6)
    assert bs["value"]["blind_spender_rate_aoe"] is None  # kun ét mål ad gangen
    assert bs["kind"] == "measured"


def test_target_buckets(result):
    tg = run1(result)["targets"]["buckets"]
    assert list(tg["value"].keys()) == ["1"]  # aldrig mere end ét mål
    b1 = tg["value"]["1"]
    assert b1["windows"] == 8
    assert b1["damage"] == 420_000
    assert b1["errors"] == 40  # blinde casts der falder i talte vinduer


def test_proc_stats(result):
    procs = run1(result)["rotation"]["proc_stats"]
    fof = procs["value"]["per_aura"]["44544"]
    assert fof["gained"] == 1
    assert fof["active_at_end"] is True
    assert procs["value"]["scope"] == "spec-config procs"


def test_cpm_sample(result):
    cpm = run1(result)["rotation"]["cpm"]
    assert cpm["sample"]["casts"] == 70
    assert cpm["value"] > 0


def test_death_recap(result):
    sur = run1(result)["survival"]
    recaps = sur["death_recaps"]["value"]
    assert len(recaps) == 1
    r = recaps[0]
    # to spell-hits à 260k + to nærkampsslag à 40k i 6 s-vinduet
    assert r["last6s_total"] == 600_000
    assert "Alerting Shrill (Avanoxx)" in r["last6s_sources"]
    assert r["last6s_sources"]["Melee (Avanoxx)"] == 80_000
    # alle fire hits bidrager til kurven — også nærkampen, hvis HP kommer
    # fra offerets side af logget
    assert len(r["hp_curve"]) == 4
    assert all(p["hpmax"] == 2_800_000 for p in r["hp_curve"])
    assert r["defensive_availability"]["Ice Block"]["ready_at_death"] is True


def test_sustain_fixture(result):
    sus = run1(result)["sustain"]
    curve = sus["phase_share_curve"]["value"]
    assert len(curve) == 5  # boss-pullen er 55,1 s → 5 hele faser
    assert all(v is not None and 0.7 < v <= 1.0 for v in curve)


def test_context_attribution(result):
    ctx = run1(result)["context"]
    shares = ctx["context_shares"]["value"]
    assert ctx["context_shares"]["sample"]["error_events"] == 42
    assert shares["opening_5s"] == pytest.approx(7 / 42, abs=0.001)
    assert shares["late_pull"] == pytest.approx(12 / 42, abs=0.001)
    assert shares["moving"] == 0.0
    # events bærer tid + pull til dashboard-prikker
    assert all("t" in e and "contexts" in e for e in ctx["events"])


def test_movement_fixture(result):
    mov = run1(result)["movement"]
    assert mov["per_pull"][0]["yards"] == pytest.approx(29.0)
    assert mov["selfcancelled_hardcasts"]["value"] == 0
    assert mov["boss_scatter"][0]["boss"] == "Avanoxx"


def test_dummy_run_lenses(result):
    dummy = result["runs"][1]
    tg = dummy["targets"]["buckets"]["value"]
    assert tg["1"]["damage"] == 28_000


def test_lens_cli(tmp_path):
    log = make_fixture.write(tmp_path / "WoWCombatLog-fixture.txt")
    parse.parse_file(log, tmp_path / "cache")
    import json
    cfg = tmp_path / "spec.json"
    cfg.write_text(json.dumps(SPEC), encoding="utf-8")
    rc = lenses.main([str(tmp_path / "cache"), "WoWCombatLog-fixture",
                      "--lens", "sustain", "--run", "1",
                      "--spec-config", str(cfg)])
    assert rc == 0


def test_blind_spender_stack_threshold(tmp_path):
    """Nogle specs kræver et antal stakke, ikke blot at debuffen er på
    (Spellslinger: Ice Lance ved >= 6 Freezing-stakke). Fixturens rampe går
    2 -> 8, så tærsklen skal flytte grænsen monotont."""
    log = make_fixture.write(tmp_path / "WoWCombatLog-fixture.txt")
    parse.parse_file(log, tmp_path / "cache")

    def blind(need):
        spec = {"spec": "t",
                "spenders": {"116": {"target_debuff": [228358],
                                     "min_stacks": need}}}
        res = lenses.run_lenses(tmp_path / "cache", "WoWCombatLog-fixture",
                                spec_config=spec, run_ids=[1])
        return res["runs"][0]["rotation"]["blind_spenders"]["value"]["blind"]

    assert blind(1) == 42    # uændret: samme som uden tærskel (bagudkompatibel)
    assert blind(4) == 45
    assert blind(6) == 47
    assert blind(9) == 70    # rampen topper på 8 → hver eneste cast er blind
