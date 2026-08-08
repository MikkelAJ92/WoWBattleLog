#!/usr/bin/env python3
"""Kontrafaktisk model (v2): "samme run, spillet rigtigt" — som overslag.

Σ(fejl × empirisk pris), event-placeret. Ufravigelige regler (PRD risiko 3):
  * Intervaller er obligatoriske på alle modellerede tal.
  * Komponent-overlap deklareres — og fratrækkes i den event-placerede sum,
    hvor det kan afgøres (fx selvafbrud inde i et talt cast-hul).
  * Alt output mærkes "overslag, ikke sim".

Komponenter:
  blind_spenders   — blinde casts × empirisk pris (målt kontrast i egne hits:
                     gennemsnitshit med krævet state − uden). Kræver
                     spec-config. Ved for få samples: fallback-pris med bredt
                     interval og deklareret antagelse.
  lost_cast_time   — tabt casttid (huller 1,7–30 s) × egen aktiv-DPS ×
                     [0,6; 1,0] (tvunget løb kan ikke altid omsættes).
  selfcancels      — selvafbrudte hard-casts × middelhit for spell'et ×
                     [0,5; 1,0].

Waterfall = komponentsummen (komponenter beregnet uafhængigt — kan
overlappe). Event-placeret sum = samme events med overlap fratrukket.
Differencen deklareres i `reconciliation` (jf. acceptancetest #3).

CLI:
  counterfactual.py <cache-root> <log-stem> [--run N] [--spec-config x.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import parse as parse_mod
import lenses

T, EV, SG, SN, DG, DN, SP, SPN, AMT, OVK, X, Y, EX = range(13)

MOVE_CONVERT = (0.6, 0.8, 1.0)    # andel af tabt tid der reelt kan omsættes
CANCEL_CONVERT = (0.5, 0.75, 1.0)
BLIND_SPREAD = (0.5, 1.0, 1.5)    # interval-faktor ved målt kontrast
MIN_SAMPLES = 5
LINK_WINDOW_S = 1.0               # cast → damage-kobling
BUCKET_S = 10.0

BASE_ASSUMPTIONS = [
    "overslag, ikke sim — komponenter er beregnet uafhængigt og kan overlappe",
    "priser er empiriske (målt i spillerens egen log), ikke teoretiske",
]


def _interval(mid_dmg: float, factors) -> list[float]:
    return [round(mid_dmg * factors[0], 1), round(mid_dmg * factors[2], 1)]


def _blind_component(rd: lenses.RunData, spec: lenses.SpecConfig,
                     blind_events: list[dict]) -> dict | None:
    if not blind_events:
        return None
    blind_by_spell: dict[int, list[dict]] = {}
    for e in blind_events:
        blind_by_spell.setdefault(e["spell_id"], []).append(e)

    events_out = []
    total = [0.0, 0.0, 0.0]
    assumptions = []
    for sp_id, evs in blind_by_spell.items():
        # Cast-ID og skade-ID er sjældent det samme spell (Ice Lance castes
        # som 30455, men rammer som 228598 og udløser Shatter 1246949).
        # spec-config kan derfor angive damage_ids; ellers antages cast-ID'et.
        req = spec.spenders.get(sp_id, {})
        dmg_ids = set(req.get("damage_ids") or [sp_id])
        cast_times = sorted((c[T], c[DG]) for c in rd.casts if c[SP] == sp_id)
        blind_keys = {(e["t"]) for e in evs}
        # Summér pr. cast: ét cast kan give flere skade-events (selve hittet
        # plus Shatter). Prisen er kontrasten pr. CAST, ikke pr. event —
        # ellers udvander de små hits gennemsnittet.
        per_cast: dict[float, float] = {}
        for r in rd.dmg_out:
            if r[SP] not in dmg_ids or r[AMT] is None:
                continue
            src_cast = None
            for ct, cdg in reversed(cast_times):
                if ct <= r[T] <= ct + LINK_WINDOW_S:
                    src_cast = ct
                    break
                if ct < r[T] - LINK_WINDOW_S:
                    break
            if src_cast is None:
                continue
            per_cast[src_cast] = per_cast.get(src_cast, 0.0) + r[AMT]
        blind_hits = [v for ct, v in per_cast.items() if ct in blind_keys]
        clean_hits = [v for ct, v in per_cast.items() if ct not in blind_keys]
        name = evs[0].get("spell") or str(sp_id)
        if len(blind_hits) >= MIN_SAMPLES and len(clean_hits) >= MIN_SAMPLES:
            price = max(0.0, sum(clean_hits) / len(clean_hits)
                        - sum(blind_hits) / len(blind_hits))
            lo, mid, hi = (price * BLIND_SPREAD[0], price,
                           price * BLIND_SPREAD[2])
            assumptions.append(
                f"{name}: pris = målt kontrast ({len(clean_hits)} casts med "
                f"state vs. {len(blind_hits)} uden), interval ×0,5–×1,5")
        else:
            mean_hit = (sum(blind_hits + clean_hits)
                        / max(len(blind_hits + clean_hits), 1))
            lo, mid, hi = 0.0, mean_hit * 0.5, mean_hit
            assumptions.append(
                f"{name}: for få samples til målt kontrast "
                f"(<{MIN_SAMPLES}) — fallback-pris ½ middelhit, "
                "interval [0; middelhit]")
        for e in evs:
            events_out.append({"t": e["t"], "type": "blind_spender",
                               "gain_dmg_mid": round(mid, 1)})
            total = [total[0] + lo, total[1] + mid, total[2] + hi]

    return {"id": "blind_spenders", "count": len(events_out),
            "gain_dmg": [round(total[0], 1), round(total[1], 1),
                         round(total[2], 1)],
            "assumptions": assumptions, "events": events_out}


def _movement_component(gap_events: list[dict], active_dps: float) -> dict | None:
    if not gap_events:
        return None
    events_out = []
    total = [0.0, 0.0, 0.0]
    for g in gap_events:
        base = g["lost_s"] * active_dps
        events_out.append({"t": g["t"], "type": "cast_gap",
                           "gain_dmg_mid": round(base * MOVE_CONVERT[1], 1),
                           "gap_s": g["gap_s"], "moving": g["moving"]})
        for i in range(3):
            total[i] += base * MOVE_CONVERT[i]
    return {"id": "lost_cast_time", "count": len(events_out),
            "gain_dmg": [round(v, 1) for v in total],
            "assumptions": [
                f"tabt tid × aktiv-DPS × [{MOVE_CONVERT[0]}; {MOVE_CONVERT[2]}] "
                "— tvunget løb kan ikke altid omsættes til casts"],
            "events": events_out}


def _cancel_component(rd: lenses.RunData, cancel_events: list[dict],
                      active_dps: float) -> dict | None:
    if not cancel_events:
        return None
    sums: dict[int, list] = {}
    for r in rd.dmg_out:
        if r[SP] is not None and r[AMT]:
            s = sums.setdefault(r[SP], [0, 0])
            s[0] += r[AMT]
            s[1] += 1
    overall = (sum(s[0] for s in sums.values())
               / max(sum(s[1] for s in sums.values()), 1))
    events_out = []
    total = [0.0, 0.0, 0.0]
    for c in cancel_events:
        s = sums.get(c.get("spell_id"))
        price = (s[0] / s[1]) if s else overall
        events_out.append({"t": c["t"], "type": "selfcancel",
                           "gain_dmg_mid": round(price * CANCEL_CONVERT[1], 1)})
        for i in range(3):
            total[i] += price * CANCEL_CONVERT[i]
    return {"id": "selfcancels", "count": len(events_out),
            "gain_dmg": [round(v, 1) for v in total],
            "assumptions": [
                f"pris = middelhit for det afbrudte spell × "
                f"[{CANCEL_CONVERT[0]}; {CANCEL_CONVERT[2]}]"],
            "events": events_out}


def model_run(rd: lenses.RunData, spec: lenses.SpecConfig) -> dict:
    run_info = {k: rd.run.get(k) for k in
                ("id", "type", "zone", "key_level", "success")}
    pulls = rd.run.get("pulls", [])
    active_s = sum(p["duration_s"] for p in pulls)
    own_dmg = sum(r[AMT] for r in rd.dmg_out if r[AMT])
    if not active_s or not own_dmg:
        return {"run": run_info,
                "unavailable": "ingen pulls/egen skade i dette run"}
    active_dps = own_dmg / active_s

    movement = lenses.lens_movement(rd, spec)
    rotation = lenses.lens_rotation(rd, spec)
    gap_events = movement.get("_gap_events", []) if isinstance(movement, dict) else []
    cancel_events = movement.get("_cancelled_events", []) if isinstance(movement, dict) else []
    blind_events = ((rotation.get("blind_spenders") or {}).get("_events")
                    or []) if isinstance(rotation, dict) else []

    components = [c for c in (
        _blind_component(rd, spec, blind_events),
        _movement_component(gap_events, active_dps),
        _cancel_component(rd, cancel_events, active_dps),
    ) if c]
    if not components:
        return {"run": run_info,
                "unavailable": "ingen målte fejl-events at modellere på"}

    # waterfall: uafhængig komponentsum
    wf = [round(sum(c["gain_dmg"][i] for c in components), 1)
          for i in range(3)]

    # event-placeret sum: fratræk deklarerbart overlap — selvafbrud der
    # ligger inde i et talt cast-hul er allerede dækket af lost_cast_time
    gap_spans = [(g["t"], g["t"] + g["gap_s"]) for g in gap_events]
    overlap_dmg = 0.0
    placed: list[dict] = []
    for c in components:
        for e in c["events"]:
            if (c["id"] == "selfcancels"
                    and any(a <= e["t"] <= b for a, b in gap_spans)):
                overlap_dmg += e["gain_dmg_mid"]
                continue
            placed.append(e)
    placed.sort(key=lambda e: e["t"])
    eventsum_mid = round(sum(e["gain_dmg_mid"] for e in placed), 1)

    # kontrafaktisk kurve: faktisk DPS pr. 10 s-bucket + event-placerede
    # rettelser lagt på fejl-tidspunkterne
    curve = []
    for p in pulls:
        t = p["t0"]
        while t < p["t1"]:
            w1 = min(t + BUCKET_S, p["t1"] + 1e-6)
            dmg = sum(r[AMT] for r in rd.dmg_out
                      if r[AMT] and t <= r[T] < w1)
            gain = sum(e["gain_dmg_mid"] for e in placed if t <= e["t"] < w1)
            dur = w1 - t
            curve.append({"pull": p["id"], "t0": round(t - p["t0"], 1),
                          "actual_dps": round(dmg / dur, 1),
                          "modeled_dps": round((dmg + gain) / dur, 1)})
            t = w1

    assumptions = BASE_ASSUMPTIONS + sum(
        (c["assumptions"] for c in components), [])
    total = lenses.modeled(
        {"actual_dps": round(active_dps, 1),
         "waterfall_dps_gain": [round(v / active_s, 1) for v in wf],
         "waterfall_pct_gain": [round(v / own_dmg * 100, 1) for v in wf],
         "event_placed_dps_gain": round(eventsum_mid / active_s, 1),
         "event_placed_pct_gain": round(eventsum_mid / own_dmg * 100, 1)},
        "counterfactual_gain",
        assumptions=assumptions,
        interval=[round(wf[0] / active_s, 1), round(wf[2] / active_s, 1)])

    return {
        "run": run_info,
        "actual": lenses.measured(
            {"dps": round(active_dps, 1), "damage": own_dmg,
             "active_s": round(active_s, 1)}, "counterfactual_gain",
            metric="egen skade / aktiv pull-tid (grundlinjen for modellen)"),
        "components": components,
        "total": total,
        "curve": curve,
        "events": placed,
        "reconciliation": {
            "waterfall_dmg_mid": wf[1],
            "event_placed_dmg_mid": eventsum_mid,
            "overlap_deducted_dmg": round(overlap_dmg, 1),
            "note": "event-placeret sum ≤ waterfall: deklarerbart overlap er "
                    "fratrukket; resterende difference er interval-usikkerhed. "
                    "Overslag, ikke sim.",
        },
    }


def run_model(cache_root: Path, log_stem: str, run_ids=None,
              spec_config: dict | None = None) -> dict:
    summary = parse_mod.load_summary(cache_root, log_stem)
    spec = lenses.SpecConfig(spec_config)
    self_guid = summary["player"]["guid"]
    group = {p["guid"] for p in summary["group"]}
    pets = summary.get("pets", {})
    out = {"log": log_stem, "player": summary["player"], "runs": []}
    for run in summary["runs"]:
        if run_ids and run["id"] not in run_ids:
            continue
        rows = list(parse_mod.iter_run_events(cache_root, log_stem, run["id"]))
        rd = lenses.RunData(run, rows, self_guid, group, pets)
        out["runs"].append(model_run(rd, spec))
    return out


def main(argv=None) -> int:
    parse_mod.utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cache_root")
    ap.add_argument("log_stem")
    ap.add_argument("--run", action="append", type=int)
    ap.add_argument("--spec-config")
    args = ap.parse_args(argv)
    cfg = json.loads(Path(args.spec_config).read_text(encoding="utf-8")) \
        if args.spec_config else None
    result = run_model(Path(args.cache_root), args.log_stem,
                       run_ids=args.run, spec_config=cfg)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=1)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
