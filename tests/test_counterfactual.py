"""Tests for den kontrafaktiske model: empiriske priser, intervaller,
overlap-deklaration og event-placeret kurve. Præcisions-tests kører på et
håndbygget scenarie med kendt kontrast; fixture-testen tjekker integration."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent
                       / "combat-log-coach" / "skills" / "analyze-log" / "scripts"))

import make_fixture  # noqa: E402
import parse  # noqa: E402
import lenses  # noqa: E402
import counterfactual as cf  # noqa: E402

LANCE, BOLT, CHILL = 30455, 116, 228358


def _row(t, ev, sg="P1", dg="Boss-1", sp=None, spn=None, amt=None,
         x=None, y=None, ex=None):
    return [t, ev, sg, "Me", dg, "Boss", sp, spn, amt, None, x, y, ex]


def _scenario():
    """60 s pull med kendt kontrast: lance-hits 9000 med Winter's Chill,
    6000 uden; ét 10 s bevægelses-hul (30→40) med et selvafbrud i (overlap)."""
    rows = []
    # Winter's Chill på målet fra t=10 til t=25
    rows.append(_row(10.0, "SPELL_AURA_APPLIED", sp=CHILL, spn="Winter's Chill",
                     ex={"auraType": "DEBUFF"}))
    rows.append(_row(25.0, "SPELL_AURA_REMOVED", sp=CHILL, spn="Winter's Chill",
                     ex={"auraType": "DEBUFF"}))
    # frostbolt-tæppe (positioner: står stille på x=100 før hullet, 200 efter)
    for i in range(25):
        t = i * 1.2
        rows.append(_row(t, "SPELL_CAST_SUCCESS", sp=BOLT, spn="Frostbolt",
                         x=100, y=0))
    for i in range(17):
        t = 40 + i * 1.2
        rows.append(_row(t, "SPELL_CAST_SUCCESS", sp=BOLT, spn="Frostbolt",
                         x=200, y=0))
    for i in range(20):  # 20 frostbolt-hits à 5000
        rows.append(_row(1.0 + i * 2.0, "SPELL_DAMAGE", sp=BOLT,
                         spn="Frostbolt", amt=5000))
    # lances: 4 blinde før debuff, 7 rene under, 3 blinde efter
    lance_times = ([2 + i * 2 for i in range(4)]
                   + [11 + i * 2 for i in range(7)]
                   + [45 + i * 2 for i in range(3)])
    for t in lance_times:
        rows.append(_row(t, "SPELL_CAST_SUCCESS", sp=LANCE, spn="Ice Lance",
                         x=100 if t < 30 else 200, y=0))
        clean = 10 <= t < 25
        rows.append(_row(t + 0.2, "SPELL_DAMAGE", sp=LANCE, spn="Ice Lance",
                         amt=9000 if clean else 6000))
    # selvafbrud inde i hullet 30–40 (overlap med lost_cast_time)
    rows.append(_row(31.0, "SPELL_CAST_START", sp=BOLT, spn="Frostbolt"))
    rows.append(_row(33.0, "SPELL_CAST_START", sp=BOLT, spn="Frostbolt"))
    rows.append(_row(33.5, "SPELL_CAST_SUCCESS", sp=BOLT, spn="Frostbolt",
                     x=150, y=0))
    run = {"pulls": [{"id": 1, "t0": 0.0, "t1": 60.0, "duration_s": 60.0}],
           "bosses": [], "deaths": [], "id": 1, "type": "mplus"}
    rd = lenses.RunData(run, rows, "P1", {"P1"}, {})
    spec = lenses.SpecConfig({"spenders": {str(LANCE):
                                           {"target_debuff": [CHILL]}}})
    return rd, spec


@pytest.fixture(scope="module")
def model():
    rd, spec = _scenario()
    return cf.model_run(rd, spec)


def test_components_present(model):
    ids = {c["id"] for c in model["components"]}
    assert ids == {"blind_spenders", "lost_cast_time", "selfcancels"}


def test_blind_component_empirical_price(model):
    blind = next(c for c in model["components"] if c["id"] == "blind_spenders")
    assert blind["count"] == 7  # 4 før debuff + 3 efter
    # målt kontrast: 9000 − 6000 = 3000 pr. blind cast → 21000 mid
    lo, mid, hi = blind["gain_dmg"]
    assert mid == pytest.approx(21000)
    assert lo == pytest.approx(10500) and hi == pytest.approx(31500)
    assert any("målt kontrast" in a for a in blind["assumptions"])


def test_movement_component(model):
    mv = next(c for c in model["components"] if c["id"] == "lost_cast_time")
    # succes'en midt i hullet (33.5) deler 28.8→40 i to talte huller:
    # 4.7 s og 6.5 s — modellen tæller pr. cast-par
    assert mv["count"] == 2
    # tabt tid = (4.7-1.5) + (6.5-1.5) = 8.2 s × aktiv-DPS × 0.8 (mid)
    assert mv["gain_dmg"][1] == pytest.approx(8.2 * 205_000 / 60 * 0.8,
                                              rel=0.01)
    assert any("aktiv-DPS" in a for a in mv["assumptions"])


def test_overlap_deducted(model):
    rec = model["reconciliation"]
    assert rec["overlap_deducted_dmg"] > 0  # selvafbruddet lå i hullet
    assert rec["event_placed_dmg_mid"] == pytest.approx(
        rec["waterfall_dmg_mid"] - rec["overlap_deducted_dmg"], abs=0.5)
    assert "ikke sim" in rec["note"]


def test_total_is_modeled_with_interval(model):
    tot = model["total"]
    assert tot["kind"] == "modeled"
    assert tot["metric_id"] == "counterfactual_gain"
    lo, hi = tot["interval"]
    assert lo < hi
    wf = tot["value"]["waterfall_pct_gain"]
    assert wf[0] < wf[1] < wf[2]
    assert any("ikke sim" in a for a in tot["assumptions"])


def test_curve_event_placed(model):
    curve = model["curve"]
    assert len(curve) == 6  # 60 s / 10 s
    assert all(b["modeled_dps"] >= b["actual_dps"] for b in curve)
    # blinde lances ligger i bucket 0 (t=2..8) → modelleret løft dér
    assert curve[0]["modeled_dps"] > curve[0]["actual_dps"]


def test_actual_is_measured(model):
    assert model["actual"]["kind"] == "measured"
    # 20×5000 + 7×9000 + 7×6000 = 205000 over 60 s
    assert model["actual"]["value"]["damage"] == 205_000
    assert model["actual"]["value"]["dps"] == pytest.approx(3416.7)


def test_fixture_integration(tmp_path):
    log = make_fixture.write(tmp_path / "WoWCombatLog-fixture.txt")
    parse.parse_file(log, tmp_path / "cache")
    out = cf.run_model(tmp_path / "cache", "WoWCombatLog-fixture",
                       spec_config={"spenders":
                                    {"116": {"target_debuff": [228358]}}})
    r1 = out["runs"][0]
    blind = next(c for c in r1["components"] if c["id"] == "blind_spenders")
    assert blind["count"] == 42
    # fixturens hits er uniforme (6000 = 6000) → ærlig pris: 0, ikke opfundet
    assert blind["gain_dmg"][1] == pytest.approx(0.0)
    assert r1["total"]["kind"] == "modeled"


def test_cli(tmp_path, capsys):
    log = make_fixture.write(tmp_path / "WoWCombatLog-fixture.txt")
    parse.parse_file(log, tmp_path / "cache")
    cfg = tmp_path / "spec.json"
    cfg.write_text(json.dumps({"spenders": {"116": {"target_debuff": [228358]}}}), encoding="utf-8")
    rc = cf.main([str(tmp_path / "cache"), "WoWCombatLog-fixture",
                  "--run", "1", "--spec-config", str(cfg)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["runs"][0]["run"]["id"] == 1


def test_no_errors_returns_unavailable_with_run_info():
    # run uden fejl-events (ingen huller, ingen blinde, ingen afbrud)
    rows = [_row(i * 1.2, "SPELL_CAST_SUCCESS", sp=BOLT, spn="Frostbolt",
                 x=100, y=0) for i in range(10)]
    rows += [_row(1.0 + i, "SPELL_DAMAGE", sp=BOLT, spn="Frostbolt", amt=5000)
             for i in range(10)]
    run = {"pulls": [{"id": 1, "t0": 0.0, "t1": 11.0, "duration_s": 11.0}],
           "bosses": [], "deaths": [], "id": 7, "type": "mplus"}
    rd = lenses.RunData(run, rows, "P1", {"P1"}, {})
    out = cf.model_run(rd, lenses.SpecConfig(None))
    assert out["unavailable"]
    assert out["run"]["id"] == 7


# --- cast-ID != skade-ID -----------------------------------------------------
# I 12.0.7 castes Ice Lance som 30455, men skaden lander som 228598 og den
# forbrugte Freezing udbetales som Shatter (1246949). Matcher modellen på
# cast-ID'et, finder den nul hits og rapporterer 0 i gevinst — i stilhed.

LANCE_DMG, SHATTER = 228598, 1246949


def _split_id_scenario(damage_ids):
    rows = [
        _row(10.0, "SPELL_AURA_APPLIED", sp=CHILL, spn="Winter's Chill",
             ex={"auraType": "DEBUFF"}),
        _row(25.0, "SPELL_AURA_REMOVED", sp=CHILL, spn="Winter's Chill",
             ex={"auraType": "DEBUFF"}),
    ]
    # 6 rene casts (under debuff) og 6 blinde (efter). Rene giver lance +
    # shatter; blinde giver kun lance.
    for t in [11 + i * 2 for i in range(6)] + [30 + i * 2 for i in range(6)]:
        clean = 10 <= t < 25
        rows.append(_row(t, "SPELL_CAST_SUCCESS", sp=LANCE, spn="Ice Lance",
                         x=100, y=0))
        rows.append(_row(t + 0.2, "SPELL_DAMAGE", sp=LANCE_DMG,
                         spn="Ice Lance", amt=6000))
        if clean:
            rows.append(_row(t + 0.3, "SPELL_DAMAGE", sp=SHATTER,
                             spn="Shatter", amt=9000))
    run = {"pulls": [{"id": 1, "t0": 0.0, "t1": 60.0, "duration_s": 60.0}],
           "bosses": [], "deaths": [], "id": 1, "type": "mplus"}
    rd = lenses.RunData(run, rows, "P1", {"P1"}, {})
    spender = {"target_debuff": [CHILL]}
    if damage_ids:
        spender["damage_ids"] = damage_ids
    spec = lenses.SpecConfig({"spenders": {str(LANCE): spender}})
    return cf.model_run(rd, spec)


def test_damage_ids_enable_measured_contrast():
    model = _split_id_scenario([LANCE_DMG, SHATTER])
    blind = next(c for c in model["components"] if c["id"] == "blind_spenders")
    assert blind["count"] == 6
    # pr. CAST: rene 6000+9000=15000, blinde 6000 → kontrast 9000
    lo, mid, hi = blind["gain_dmg"]
    assert mid == pytest.approx(6 * 9000)
    assert lo == pytest.approx(6 * 4500) and hi == pytest.approx(6 * 13500)
    assert any("målt kontrast" in a for a in blind["assumptions"])


def test_missing_damage_ids_is_not_silently_zero():
    """Uden damage_ids findes ingen hits. Modellen må ikke lade som om
    prisen er nul — den skal falde tilbage og sige det i sine antagelser."""
    model = _split_id_scenario(None)
    blind = next(c for c in model["components"] if c["id"] == "blind_spenders")
    assert blind["count"] == 6
    assert blind["gain_dmg"][1] == 0
    assert any("for få samples" in a for a in blind["assumptions"])
