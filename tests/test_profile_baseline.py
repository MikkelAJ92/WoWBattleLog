"""Tests for F2 (profil-writer) og F7 (baseline/progression)."""

import copy
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
import profile as profile_mod  # noqa: E402
import baseline as baseline_mod  # noqa: E402

SPEC = {"spec": "Frost Mage (test)",
        "spenders": {"116": {"target_debuff": [228358]}},
        "procs": [44544]}


@pytest.fixture(scope="module")
def lens_result(tmp_path_factory):
    root = tmp_path_factory.mktemp("clc-prof")
    log = make_fixture.write(root / "WoWCombatLog-fixture.txt")
    parse.parse_file(log, root / "cache")
    return lenses.run_lenses(root / "cache", "WoWCombatLog-fixture",
                             spec_config=SPEC)


# --- F2: profil ---------------------------------------------------------------

def test_profile_categories_and_provenance(tmp_path, lens_result):
    p = tmp_path / "profile.json"
    prof = profile_mod.update_profile(p, lens_result, "Frost Mage",
                                      measured_at="2026-08-08T14:00:00+00:00")
    m = prof["measurements"]
    for cat in ("proc_utilization", "apm", "movement_cost",
                "target_swap_discipline", "sustain"):
        assert cat in m, f"mangler kategori {cat}"
        # proveniens er obligatorisk (schema-krav): value/metric/kind/measured_at
        for field in ("value", "metric", "kind", "measured_at"):
            assert field in m[cat], f"{cat} mangler {field}"
        assert m[cat]["kind"] == "measured"
        assert m[cat]["source_spec"] == "Frost Mage"
    assert prof["player"]["name"] == "Mikkel-TarrenMill"
    assert prof["player"]["specs_seen"] == ["Frost Mage"]


def test_profile_values_match_lenses(tmp_path, lens_result):
    p = tmp_path / "profile.json"
    prof = profile_mod.update_profile(p, lens_result, "Frost Mage")
    m = prof["measurements"]
    # vægtet på tværs af runs: run 1 (42/70 blinde) + dummy-run (28/28 blinde)
    # → (42+28)/(70+28) = 0.714
    assert m["target_swap_discipline"]["value"]["blind_spender_rate_st"] \
        == pytest.approx(0.714)
    assert m["proc_utilization"]["value"] == 0.0  # FoF aldrig forbrugt
    assert m["sustain"]["value"]["phase_share_curve"][0] is not None
    # tryk-felter kan ikke måles i log → eksplicit None, ikke gæt
    assert m["apm"]["value"]["presses_per_gcd"] is None


def test_profile_update_is_merge_not_replace(tmp_path, lens_result):
    p = tmp_path / "profile.json"
    profile_mod.update_profile(p, lens_result, "Frost Mage")
    # simulér profil-berigelse (binds) og ny analyse på anden spec
    prof = json.loads(p.read_text(encoding="utf-8"))
    prof["player"]["keybinds"] = {"spender": {"key": "Shift-3"}}
    p.write_text(json.dumps(prof), encoding="utf-8")
    prof2 = profile_mod.update_profile(p, lens_result, "Blood DK")
    assert prof2["player"]["keybinds"]["spender"]["key"] == "Shift-3"
    assert prof2["player"]["specs_seen"] == ["Frost Mage", "Blood DK"]


# --- F7: baseline og progression -----------------------------------------------

def _improved(lens_result):
    """Simulér en bedre session: færre blinde, bedre proc-udnyttelse."""
    r2 = copy.deepcopy(lens_result)
    bs = r2["runs"][0]["rotation"]["blind_spenders"]["value"]
    bs.update({"blind_spender_rate_st": 0.2, "st_blind": 14, "blind": 14})
    r2["runs"][0]["rotation"]["proc_stats"]["value"]["utilization"] = 0.9
    return r2


def test_snapshot_and_report_same_dungeon(tmp_path, lens_result):
    b = tmp_path / "baseline.json"
    baseline_mod.snapshot(b, lens_result, spec="Frost Mage",
                          measured_at="2026-08-01T20:00:00+00:00")
    baseline_mod.snapshot(b, _improved(lens_result), spec="Frost Mage",
                          measured_at="2026-08-08T20:00:00+00:00")
    rep = baseline_mod.report(b)
    assert rep["reference"]["same_dungeon"] is True  # samme zone i begge
    deltas = {d["metric"]: d for d in rep["deltas"]}
    # run 1 forbedret til 0.2 → aggregeret (0.2*70 + 1.0*28)/98 = 0.429
    assert deltas["blind_spender_rate_st"]["before"] == pytest.approx(0.714)
    assert deltas["blind_spender_rate_st"]["after"] == pytest.approx(0.429)
    assert deltas["blind_spender_rate_st"]["improved"] is True
    assert deltas["proc_utilization"]["improved"] is True
    # uændret metrik er neutral, ikke tilbagegang
    assert deltas["sustain_min_phase_share"]["improved"] is None
    sd = rep["same_dungeon_runs"]
    assert any(r["zone"] == "Ara-Kara, City of Echoes" for r in sd)
    # klynge-runs uden nøgleniveau parres ikke
    assert not any(r["zone"] == "Valdrakken" for r in sd)


def test_goals_evaluated(tmp_path, lens_result):
    b = tmp_path / "baseline.json"
    baseline_mod.snapshot(b, lens_result)
    data = json.loads(b.read_text(encoding="utf-8"))
    data["goals"] = [{"metric": "blind_spender_rate_st", "op": "<",
                      "target": 0.10, "note": None},
                     {"metric": "deaths", "op": "<=", "target": 1,
                      "note": None}]
    b.write_text(json.dumps(data), encoding="utf-8")
    rep = baseline_mod.report(b)
    goals = {g["metric"]: g for g in rep["goals"]}
    assert goals["blind_spender_rate_st"]["met"] is False  # 0.6 er ikke < 0.10
    assert goals["deaths"]["met"] is True                   # 1 død ≤ 1


def test_report_text_format(tmp_path, lens_result):
    b = tmp_path / "baseline.json"
    baseline_mod.snapshot(b, lens_result,
                          measured_at="2026-08-01T20:00:00+00:00")
    baseline_mod.snapshot(b, _improved(lens_result),
                          measured_at="2026-08-08T20:00:00+00:00")
    txt = baseline_mod.render_text(baseline_mod.report(b))
    assert "→" in txt and "forbedret" in txt
    assert "71.4%" in txt and "42.9%" in txt


def test_extract_metrics_deaths(lens_result):
    flat = baseline_mod.extract_metrics(lens_result)
    assert flat["deaths"] == 1
    assert flat["blind_spender_rate_st"] == pytest.approx(0.714)
    assert 0 < flat["sustain_min_phase_share"] <= 1


def test_cli_set_goal_and_report(tmp_path, lens_result, capsys):
    b = tmp_path / "baseline.json"
    out = tmp_path / "lens.json"
    out.write_text(json.dumps(lens_result), encoding="utf-8")
    assert baseline_mod.main(["snapshot", str(b), "--lens-output",
                              str(out)]) == 0
    assert baseline_mod.main(["set-goal", str(b), "--metric",
                              "blind_spender_rate_st", "--op", "<",
                              "--target", "0.10"]) == 0
    capsys.readouterr()
    assert baseline_mod.main(["report", str(b)]) == 0
    txt = capsys.readouterr().out
    assert "ikke nået" in txt
