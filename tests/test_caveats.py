"""Tests for forbehold-generatoren (F4/NFR: auto-genereret, ikke håndskrevet)."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent
                       / "combat-log-coach" / "skills" / "analyze-log" / "scripts"))
sys.path.insert(0, str(Path(__file__).parent.parent
                       / "combat-log-coach" / "skills" / "build-dashboard" / "scripts"))

import make_fixture  # noqa: E402
import parse  # noqa: E402
import lenses  # noqa: E402
import caveats  # noqa: E402

METRICS = (Path(__file__).parent.parent / "combat-log-coach" / "metrics.json")


def test_collect_finds_ids_assumptions_and_sources():
    doc = {
        "a": {"kind": "measured", "metric_id": "phase_share", "value": 1},
        "nested": [{"deep": {"kind": "modeled", "metric_id": "counterfactual_gain",
                             "assumptions": ["GCD 1,5 s"], "interval": [1, 2]}}],
        "ext": {"kind": "external", "metric_id": "cd_discipline",
                "source_url": "https://example.com/tierlist"},
    }
    ids, assumptions, externals = caveats.collect(doc)
    assert ids == {"phase_share", "counterfactual_gain", "cd_discipline"}
    assert assumptions == ["GCD 1,5 s"]
    assert externals == ["https://example.com/tierlist"]


def test_render_marks_kinds_and_missing():
    catalog = {m["id"]: m for m in json.loads(METRICS.read_text())["metrics"]}
    out = caveats.render({"phase_share", "counterfactual_gain", "ukendt_metrik"},
                         ["antagelse X"], [], catalog)
    assert 'data-clc-section="caveats"' in out
    assert "Fase-andel" in out
    assert "Kræver fuld gruppe-parse" in out          # fejlkilden fra kataloget
    assert "clc-badge--modeled" in out                # kontrafaktik er modeled
    assert "antagelse X" in out
    assert "ukendt_metrik" in out and "metrics.json" in out  # eksplicit hul


def test_html_escaping():
    catalog = {"x": {"id": "x", "name": "A<b>", "definition": "d&d",
                     "error_source": "<script>", "kind": "measured"}}
    out = caveats.render({"x"}, [], [], catalog)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_full_pipeline_from_lens_output(tmp_path):
    log = make_fixture.write(tmp_path / "WoWCombatLog-fixture.txt")
    parse.parse_file(log, tmp_path / "cache")
    result = lenses.run_lenses(tmp_path / "cache", "WoWCombatLog-fixture",
                               spec_config={"spenders":
                                            {"116": {"target_debuff": [228358]}}})
    html_out = caveats.build(result, METRICS)
    # linserne bruger disse metrikker → deres fejlkilder SKAL optræde
    assert "Blind spender-rate" in html_out
    assert "nye spawns uden logget applikation" in html_out
    assert "Proc-udnyttelse" in html_out
    assert "Refresh-semantik varierer" in html_out
    # modelleret GCD-antagelse fra rotation-linsen samles op
    assert "teoretisk GCD" in html_out


def test_cli_stdin(tmp_path, capsys, monkeypatch):
    doc = {"m": {"kind": "measured", "metric_id": "phase_share"}}
    p = tmp_path / "out.json"
    p.write_text(json.dumps(doc))
    rc = caveats.main([str(p), "--metrics", str(METRICS)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Fase-andel" in out
