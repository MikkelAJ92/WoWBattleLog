#!/usr/bin/env python3
"""Spillestils-profil (F2): byg/opdatér profile.json fra linse-output.

Profilen er produktets kerneaktiv: persistent, spec-agnostisk i sine
kategorier (proc-refleks, tempo, bevægelse, defensiv timing, swap-disciplin,
sustain), så den kan forudsige og måles på tværs af specs/klasser. Hver
måling bærer fuld proveniens (kind/metric/measured_at/sample/source_spec)
jf. profile.schema.json — værdier uden proveniens skrives aldrig.

Aggregering på tværs af runs i samme linse-output vægtes med målingens
datagrundlag (procs, casts, tabt tid, pulls) — aldrig simpelt gennemsnit.

CLI:
  profile.py update <profile.json> --lens-output <out.json> --spec "Frost Mage"
  profile.py show <profile.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1


def _meas(value, metric, kind="measured", sample=None, source_spec=None,
          measured_at=None, assumptions=None, interval=None):
    out = {"value": value, "metric": metric, "kind": kind,
           "measured_at": measured_at}
    if sample:
        out["sample"] = sample
    if source_spec:
        out["source_spec"] = source_spec
    if kind == "modeled":
        out["assumptions"] = assumptions or []
        out["interval"] = interval or []
    return out


def _wavg(pairs):
    """Vægtet gennemsnit af (værdi, vægt); None-værdier ignoreres."""
    num = den = 0.0
    for v, w in pairs:
        if v is None or not w:
            continue
        num += v * w
        den += w
    return round(num / den, 3) if den else None


def extract_measurements(lens_result: dict, source_spec: str | None,
                         measured_at: str) -> dict:
    """Map linse-output → profilens spec-agnostiske målinger."""
    runs = lens_result.get("runs", [])
    m: dict = {}

    def rget(run, *path):
        cur = run
        for p in path:
            if not isinstance(cur, dict) or p not in cur:
                return None
            cur = cur[p]
        return cur

    # proc-refleks — vægtet med procs opnået
    pairs = [(rget(r, "rotation", "proc_stats", "value", "utilization"),
              rget(r, "rotation", "proc_stats", "sample", "procs_gained") or 0)
             for r in runs]
    procs_total = sum(w for _, w in pairs)
    if procs_total:
        m["proc_utilization"] = _meas(
            _wavg(pairs), "procs forbrugt før udløb / procs opnået",
            sample={"procs_gained": procs_total, "runs": len(runs)},
            source_spec=source_spec, measured_at=measured_at)

    # tempo — casts/min vægtet med aktiv tid; spildte tryk kan ikke måles i log
    cpm_pairs = [(rget(r, "rotation", "cpm", "value"),
                  rget(r, "rotation", "cpm", "sample", "active_s") or 0)
                 for r in runs]
    casts = sum(rget(r, "rotation", "cpm", "sample", "casts") or 0 for r in runs)
    if casts:
        m["apm"] = _meas(
            {"casts_per_min": _wavg(cpm_pairs),
             "presses_per_gcd": None, "wasted_presses_per_min": None},
            "cast-successes pr. minut aktiv pull-tid; tryk-felter kræver "
            "klient-input og kan ikke måles i combat log",
            sample={"casts": casts}, source_spec=source_spec,
            measured_at=measured_at)

    # bevægelse — share vægtet med tabt tid
    mv_pairs = [(rget(r, "movement", "lost_moving_share", "value"),
                 rget(r, "movement", "lost_cast_seconds", "value") or 0)
                for r in runs]
    lost_total = round(sum(w for _, w in mv_pairs), 1)
    cancels = sum(rget(r, "movement", "selfcancelled_hardcasts", "value") or 0
                  for r in runs)
    if any(v is not None for v, _ in mv_pairs) or cancels:
        m["movement_cost"] = _meas(
            {"lost_cast_seconds_moving_share": _wavg(mv_pairs),
             "selfcancelled_hardcasts": cancels},
            "tabt casttid i bevægelse / al tabt casttid; selvafbrudte casts talt",
            sample={"lost_cast_seconds": lost_total, "runs": len(runs)},
            source_spec=source_spec, measured_at=measured_at)

    # defensiv timing — kun hvis linsen faktisk målte (kræver spec-config)
    for r in runs:
        dt = rget(r, "survival", "defensive_timing")
        if isinstance(dt, dict) and dt.get("kind") == "measured":
            m["defensive_timing"] = _meas(
                dt["value"], dt.get("metric") or "median s fra stort hit til defensiv",
                sample=dt.get("sample"), source_spec=source_spec,
                measured_at=measured_at)
            break

    # CD-disciplin — gennemsnit af pr.-CD-udnyttelse vægtet med casts
    cd_pairs = []
    for r in runs:
        for cd in (rget(r, "rotation", "cd_discipline", "value") or {}).values():
            if cd.get("utilization") is not None:
                cd_pairs.append((cd["utilization"], cd.get("casts") or 1))
    if cd_pairs:
        m["cd_discipline"] = _meas(
            _wavg(cd_pairs), "on-CD casts / mulige casts (median-cadence-metode)",
            sample={"cds": len(cd_pairs)}, source_spec=source_spec,
            measured_at=measured_at)

    # swap-disciplin — vægtet med hhv. st-/aoe-casts
    st_pairs, aoe_pairs = [], []
    spender_casts = 0
    for r in runs:
        v = rget(r, "rotation", "blind_spenders", "value") or {}
        st_pairs.append((v.get("blind_spender_rate_st"), v.get("st_casts") or 0))
        aoe_pairs.append((v.get("blind_spender_rate_aoe"), v.get("aoe_casts") or 0))
        spender_casts += v.get("total_spender_casts") or 0
    if spender_casts:
        m["target_swap_discipline"] = _meas(
            {"blind_spender_rate_st": _wavg(st_pairs),
             "blind_spender_rate_aoe": _wavg(aoe_pairs)},
            "spender-casts uden krævet debuff/proc-state / alle spender-casts",
            sample={"spender_casts": spender_casts},
            source_spec=source_spec, measured_at=measured_at)

    # sustain — fase-kurver midlet elementvist, vægtet med antal pulls
    curves = []
    for r in runs:
        c = rget(r, "sustain", "phase_share_curve", "value")
        w = rget(r, "sustain", "phase_share_curve", "sample", "pulls") or 0
        if c:
            curves.append((c, w))
    if curves:
        max_len = max(len(c) for c, _ in curves)
        curve = []
        for i in range(max_len):
            curve.append(_wavg([(c[i], w) for c, w in curves
                                if i < len(c) and c[i] is not None]))
        m["sustain"] = _meas(
            {"phase_share_curve": curve, "phase_width_s": 10},
            "egen skade / gruppens skade pr. pull-fase (pulls ≥ 45 s)",
            sample={"pulls": sum(w for _, w in curves)},
            source_spec=source_spec, measured_at=measured_at)
    return m


def load_profile(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"schema_version": SCHEMA_VERSION, "player": {"name": None},
            "measurements": {}}


def update_profile(path: Path, lens_result: dict, source_spec: str | None,
                   measured_at: str | None = None) -> dict:
    profile = load_profile(path)
    measured_at = measured_at or datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    if not profile["player"].get("name"):
        profile["player"]["name"] = (lens_result.get("player") or {}).get("name")
    if (lens_result.get("player") or {}).get("guid"):
        profile["player"].setdefault("guid", lens_result["player"]["guid"])
    if source_spec:
        seen = profile["player"].setdefault("specs_seen", [])
        if source_spec not in seen:
            seen.append(source_spec)
    new = extract_measurements(lens_result, source_spec, measured_at)
    profile["measurements"].update(new)   # nyeste måling vinder pr. kategori
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=1))
    return profile


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("update", help="Opdatér profil fra linse-output")
    up.add_argument("profile", help="Sti til profile.json")
    up.add_argument("--lens-output", required=True)
    up.add_argument("--spec", help="Spec målingerne stammer fra")
    up.add_argument("--measured-at", help="ISO-tidsstempel (default: nu, UTC)")
    sh = sub.add_parser("show", help="Vis profil")
    sh.add_argument("profile")
    args = ap.parse_args(argv)

    if args.cmd == "update":
        lens_result = json.loads(Path(args.lens_output).read_text())
        profile = update_profile(Path(args.profile), lens_result,
                                 args.spec, args.measured_at)
        json.dump(profile, sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        print(Path(args.profile).read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
