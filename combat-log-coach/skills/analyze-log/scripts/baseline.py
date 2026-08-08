#!/usr/bin/env python3
"""Progression (F7): baseline pr. metrik, delta-rapporter og benchmarks.

Hver ny log snapshottes som en entry i baseline.json; rapporten sammenligner
nyeste entry mod en reference — samme-dungeon-sammenligninger prioriteres
automatisk (PRD F7: "samme dungeon, +10 → +11"). Øveplanens målbare
benchmarks ("< 10 % blinde") gemmes som goals og evalueres mod hver ny log.

Datamodel (baseline.json):
  {"goals": [{"metric", "op", "target", "note"}],
   "entries": [{"measured_at", "log", "spec", "runs": [{zone,key_level,...}],
                "metrics": {<flad metrik>: værdi}}]}

CLI:
  baseline.py snapshot <baseline.json> --lens-output out.json [--spec X]
  baseline.py report <baseline.json> [--format json|text]
  baseline.py set-goal <baseline.json> --metric M --op "<" --target 0.10
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import profile as profile_mod

# retning pr. metrik: skal værdien op eller ned for at være en forbedring?
LOWER_IS_BETTER = {
    "blind_spender_rate_st", "blind_spender_rate_aoe",
    "lost_moving_share", "selfcancelled_hardcasts", "deaths",
}
HIGHER_IS_BETTER = {
    "proc_utilization", "cd_discipline", "casts_per_min",
    "sustain_min_phase_share",
}

OPS = {"<": lambda v, t: v < t, "<=": lambda v, t: v <= t,
       ">": lambda v, t: v > t, ">=": lambda v, t: v >= t}


def extract_metrics(lens_result: dict) -> dict:
    """Flad metrik-dict fra linse-output (genbruger profil-aggregeringen)."""
    meas = profile_mod.extract_measurements(lens_result, None, "-")
    flat: dict = {}

    def val(cat, *path):
        cur = (meas.get(cat) or {}).get("value")
        for p in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(p)
        return cur

    flat["proc_utilization"] = val("proc_utilization")
    flat["casts_per_min"] = val("apm", "casts_per_min")
    flat["lost_moving_share"] = val("movement_cost",
                                    "lost_cast_seconds_moving_share")
    flat["selfcancelled_hardcasts"] = val("movement_cost",
                                          "selfcancelled_hardcasts")
    flat["cd_discipline"] = val("cd_discipline")
    flat["blind_spender_rate_st"] = val("target_swap_discipline",
                                        "blind_spender_rate_st")
    flat["blind_spender_rate_aoe"] = val("target_swap_discipline",
                                         "blind_spender_rate_aoe")
    curve = val("sustain", "phase_share_curve") or []
    vals = [v for v in curve if v is not None]
    flat["sustain_min_phase_share"] = min(vals) if vals else None

    deaths = 0
    for r in lens_result.get("runs", []):
        recaps = (((r.get("survival") or {}).get("death_recaps") or {})
                  .get("value"))
        if recaps:
            deaths += len(recaps)
    flat["deaths"] = deaths
    return {k: v for k, v in flat.items() if v is not None}


def _load(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"goals": [], "entries": []}


def snapshot(path: Path, lens_result: dict, spec: str | None = None,
             measured_at: str | None = None) -> dict:
    data = _load(path)
    entry = {
        "measured_at": measured_at or datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "log": lens_result.get("log"),
        "spec": spec,
        "runs": [r.get("run") for r in lens_result.get("runs", [])],
        "metrics": extract_metrics(lens_result),
    }
    data["entries"].append(entry)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return entry


def _zones(entry: dict) -> set[str]:
    return {r.get("zone") for r in entry.get("runs", []) if r.get("zone")}


def _pick_reference(entries: list[dict]) -> tuple[dict, bool]:
    """Nyeste tidligere entry med zone-overlap; ellers blot nyeste tidligere."""
    latest = entries[-1]
    zones = _zones(latest)
    for e in reversed(entries[:-1]):
        if zones & _zones(e):
            return e, True
    return entries[-2], False


def report(path: Path) -> dict:
    data = _load(path)
    entries = data["entries"]
    if not entries:
        return {"error": "ingen entries — kør snapshot først"}
    latest = entries[-1]

    out: dict = {"latest": {k: latest[k] for k in
                            ("measured_at", "log", "spec")},
                 "deltas": [], "goals": [], "same_dungeon_runs": []}
    if len(entries) >= 2:
        ref, same = _pick_reference(entries)
        out["reference"] = {"measured_at": ref["measured_at"],
                            "log": ref["log"], "same_dungeon": same}
        for metric, after in sorted(latest["metrics"].items()):
            before = ref["metrics"].get(metric)
            if before is None:
                continue
            delta = round(after - before, 3)
            improved = None
            if delta != 0:
                if metric in LOWER_IS_BETTER:
                    improved = delta < 0
                elif metric in HIGHER_IS_BETTER:
                    improved = delta > 0
            out["deltas"].append({"metric": metric, "before": before,
                                  "after": after, "delta": delta,
                                  "improved": improved})
        # samme-dungeon-par: zone for zone, nøgleniveau før → efter
        ref_by_zone = {r["zone"]: r for r in ref.get("runs", [])
                       if r.get("zone")}
        for r in latest.get("runs", []):
            prev = ref_by_zone.get(r.get("zone"))
            # kun nøgle-runs — klynger (delve/dummy) har intet nøgleniveau
            if prev and (r.get("key_level") is not None
                         or prev.get("key_level") is not None):
                out["same_dungeon_runs"].append({
                    "zone": r["zone"],
                    "before_key": prev.get("key_level"),
                    "after_key": r.get("key_level"),
                    "before_success": prev.get("success"),
                    "after_success": r.get("success"),
                })
    for g in data.get("goals", []):
        v = latest["metrics"].get(g["metric"])
        met = OPS[g["op"]](v, g["target"]) if v is not None else None
        out["goals"].append({**g, "value": v, "met": met})
    return out


def render_text(rep: dict) -> str:
    if "error" in rep:
        return rep["error"]
    lines = [f"Progression — {rep['latest']['log']} "
             f"({rep['latest']['measured_at']})"]
    ref = rep.get("reference")
    if ref:
        tag = "samme dungeon(s)" if ref["same_dungeon"] else "seneste log"
        lines.append(f"Reference: {ref['log']} ({tag})")
    for d in rep["deltas"]:
        arrow = {True: "✓ forbedret", False: "✗ tilbagegang",
                 None: "· neutral"}[d["improved"]]
        if abs(d["before"]) <= 1 and abs(d["after"]) <= 1 \
                and d["metric"] != "deaths":
            lines.append(f"  {d['metric']}: {d['before']:.1%} → "
                         f"{d['after']:.1%}  {arrow}")
        else:
            lines.append(f"  {d['metric']}: {d['before']} → {d['after']}  {arrow}")
    for r in rep["same_dungeon_runs"]:
        lines.append(f"  {r['zone']}: +{r['before_key']} → +{r['after_key']}")
    for g in rep["goals"]:
        status = {True: "✓ nået", False: "✗ ikke nået",
                  None: "· ingen måling"}[g["met"]]
        lines.append(f"  Mål: {g['metric']} {g['op']} {g['target']} — "
                     f"{status} (målt: {g['value']})")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sn = sub.add_parser("snapshot")
    sn.add_argument("baseline")
    sn.add_argument("--lens-output", required=True)
    sn.add_argument("--spec")
    sn.add_argument("--measured-at")
    rp = sub.add_parser("report")
    rp.add_argument("baseline")
    rp.add_argument("--format", choices=("json", "text"), default="text")
    sg = sub.add_parser("set-goal")
    sg.add_argument("baseline")
    sg.add_argument("--metric", required=True)
    sg.add_argument("--op", required=True, choices=sorted(OPS))
    sg.add_argument("--target", required=True, type=float)
    sg.add_argument("--note")
    args = ap.parse_args(argv)

    path = Path(args.baseline)
    if args.cmd == "snapshot":
        entry = snapshot(path, json.loads(Path(args.lens_output).read_text()),
                         spec=args.spec, measured_at=args.measured_at)
        json.dump(entry, sys.stdout, ensure_ascii=False, indent=1)
        print()
    elif args.cmd == "report":
        rep = report(path)
        if args.format == "json":
            json.dump(rep, sys.stdout, ensure_ascii=False, indent=1)
            print()
        else:
            print(render_text(rep))
    else:
        data = _load(path)
        data["goals"] = [g for g in data["goals"]
                         if g["metric"] != args.metric] + [{
                             "metric": args.metric, "op": args.op,
                             "target": args.target, "note": args.note}]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        print(f"Mål sat: {args.metric} {args.op} {args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
