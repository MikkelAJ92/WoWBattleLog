"""Tests for citations-linten (F6): 'X anbefaler Y' kræver fetch-beleg."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "combat-log-coach"
                       / "skills" / "write-guide" / "scripts"))

import citation_lint  # noqa: E402

STAMP = '<time data-clc-verified="2026-08-08">8. august 2026</time>'


def test_clean_guide_passes():
    html = f"""
    <header>{STAMP}</header>
    <p><span data-claim="stable">Frost bygger mod Shatter-vinduer via
    Winter's Chill.</span></p>
    <p><span data-claim="external"
             data-source-url="https://example.com/tierlist">
    Wowhead anbefaler Frost i M+ denne uge
    (<a href="https://example.com/tierlist">kilde</a>).</span></p>
    <p><span data-claim="inference">Ud fra din målte proc-refleks (98,6 %)
    passer spec'en til dig.</span></p>
    """
    r = citation_lint.lint(html)
    assert r["ok"], r["errors"]
    assert r["sources"] == ["https://example.com/tierlist"]
    assert r["verified"] == "2026-08-08"


def test_attribution_without_external_fails():
    html = f"{STAMP}<p>Archon anbefaler Deathbringer til M+.</p>"
    r = citation_lint.lint(html)
    assert not r["ok"]
    assert any(e["rule"] == "R1" for e in r["errors"])


def test_attribution_keyword_outside_span_still_ok():
    # "Ifølge" står udenfor span'et, men sætningen HAR external-kontekst
    html = f"""{STAMP}<p>Ifølge <span data-claim="external"
    data-source-url="https://x.dk/notes">patch notes er Frost buffet
    (<a href="https://x.dk/notes">kilde</a>)</span>.</p>"""
    r = citation_lint.lint(html)
    assert not any(e["rule"] == "R1" for e in r["errors"])


def test_external_without_url_or_link_fails():
    html = f"""{STAMP}
    <p><span data-claim="external">Murlok viser 60 % pickrate.</span></p>
    <p><span data-claim="external" data-source-url="https://y.dk/data">
    top parses viser mere haste.</span></p>
    """
    r = citation_lint.lint(html)
    rules = [e["rule"] for e in r["errors"]]
    assert rules.count("R2") == 2  # mangler URL hhv. synligt link


def test_tuning_in_stable_is_error_untagged_is_warning():
    html = f"""{STAMP}
    <p><span data-claim="stable">Ice Lance er 15 % buffet.</span></p>
    <p>Spec'en er S-tier lige nu.</p>
    """
    r = citation_lint.lint(html)
    assert any(e["rule"] == "R3" for e in r["errors"])
    assert any(w["rule"] == "R3" for w in r["warnings"])


def test_missing_verified_stamp_fails():
    r = citation_lint.lint("<p><span data-claim='stable'>mekanik</span></p>")
    assert any(e["rule"] == "R4" for e in r["errors"])


def test_percentage_in_inference_allowed():
    # profilens egne målte tal i en slutning er ikke tuning-påstande
    html = f"""{STAMP}<p><span data-claim="inference">Din blind-rate på
    28 % i AoE forudsiger ressource-tørke på DK.</span></p>"""
    r = citation_lint.lint(html)
    assert r["ok"], r["errors"]


def test_cli_exit_codes_and_list_sources(tmp_path, capsys):
    good = tmp_path / "good.html"
    good.write_text(f"""{STAMP}<span data-claim="external"
      data-source-url="https://a.dk/x">ifølge kilden
      <a href="https://a.dk/x">a.dk</a></span>""", encoding="utf-8")
    bad = tmp_path / "bad.html"
    bad.write_text("<p>Archon anbefaler X.</p>", encoding="utf-8")

    assert citation_lint.main([str(good)]) == 0
    capsys.readouterr()
    assert citation_lint.main([str(bad)]) == 1
    capsys.readouterr()
    assert citation_lint.main([str(good), "--list-sources"]) == 0
    assert capsys.readouterr().out.strip() == "https://a.dk/x"
