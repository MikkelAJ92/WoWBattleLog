#!/usr/bin/env python3
"""Linse-bibliotek (F3): genkørbare datasnit over den parsede event-cache.

Hver linse er en ren funktion over ét runs slim-eventstrøm (se parse.py for
rækkelayoutet) og returnerer JSON-bare dicts. Hårdt designprincip: hvert tal
mærkes som talt/modelleret/eksternt via provenance-strukturen — det er data,
ikke stil. Forbehold hentes fra metrics.json (PRD afsnit 6) via metric-id.

Linser:
  targets    — DPS/fejlrate pr. target-bucket (1/2/3–5/6+), 10 s-vinduer
  movement   — tabt casttid i bevægelse vs. stillestående; yd/min; selvafbrud
  rotation   — CPM, skadefordeling, proc-forbrug, CD-udnyttelse, blind spender
  survival   — death recaps (sidste 6 s) + defensiv-timing/-tilgængelighed
  sustain    — fase-andel af gruppens skade (obligatorisk metode) + egen kurve
  context    — fejl-events attribueret pr. situation (ikke-eksklusiv)

Spec-config (valgfri JSON, --spec-config) låser spec-afhængige analyser op:
  {
    "spec": "Frost Mage",
    "spenders": {"116": {"target_debuff": [228358], "self_buff": [44544]}},
    "procs": [44544],
    "defensives": {"45438": {"name": "Ice Block", "cd_s": 240}},
    "major_cds": {"12472": {"name": "Icy Veins"}}
  }
Uden config degraderer linserne pænt: spec-afhængige felter udelades med
eksplicit note i stedet for at gætte.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

import parse as parse_mod

# Slim-række-kolonner (jf. parse.py docstring)
T, EV, SG, SN, DG, DN, SP, SPN, AMT, OVK, X, Y, EX = range(13)

DAMAGE_EVENTS = parse_mod.DAMAGE_EVENTS
WINDOW_S = 10.0
PHASE_S = 10.0
SUSTAIN_MIN_PULL_S = 45.0
GAP_MIN_S = 1.7          # metrik-katalog: cast-huller 1,7–30 s
GAP_MAX_S = 30.0
GCD_BASELINE_S = 1.5     # antaget GCD — deklareret antagelse
MOVE_EPS_YD = 3.0        # positionsdelta > dette ⇒ "i bevægelse"
TELEPORT_YD = 150.0      # klip portaler/graveyard-spring
BIG_HIT_SHARE = 0.20     # "stort hit" = > 20 % af max-HP
OPENING_S = 5.0          # samle-fasen: pullens første 5 s
PACK_GROWTH_S = 10.0     # nyt mål first-hit inden for de seneste 10 s
LATE_PULL_SHARE = 0.60   # sen-pull: relativ tid > 60 %

_METRICS_PATH = Path(__file__).resolve().parents[3] / "metrics.json"


def _load_catalog() -> dict:
    try:
        data = json.loads(_METRICS_PATH.read_text())
        return {m["id"]: m for m in data["metrics"]}
    except (OSError, json.JSONDecodeError, KeyError):
        return {}


CATALOG = _load_catalog()


def measured(value, metric_id: str, metric: str | None = None, sample=None):
    cat = CATALOG.get(metric_id, {})
    out = {"kind": "measured", "value": value, "metric_id": metric_id,
           "metric": metric or cat.get("definition"),
           "error_source": cat.get("error_source")}
    if sample:
        out["sample"] = sample
    return out


def modeled(value, metric_id: str, assumptions: list[str],
            interval: list | None = None, metric: str | None = None):
    cat = CATALOG.get(metric_id, {})
    return {"kind": "modeled", "value": value, "metric_id": metric_id,
            "metric": metric or cat.get("definition"),
            "assumptions": assumptions, "interval": interval,
            "error_source": cat.get("error_source")}


class SpecConfig:
    def __init__(self, raw: dict | None):
        raw = raw or {}
        self.spec = raw.get("spec")
        self.spenders = {int(k): v for k, v in raw.get("spenders", {}).items()}
        self.procs = {int(p) for p in raw.get("procs", [])}
        self.defensives = {int(k): v for k, v in raw.get("defensives", {}).items()}
        self.major_cds = {int(k): v for k, v in raw.get("major_cds", {}).items()}

    def __bool__(self):
        return bool(self.spenders or self.procs or self.defensives
                    or self.major_cds)


class RunData:
    """Ét runs slim events, indekseret til linsebrug."""

    def __init__(self, run: dict, rows: list, self_guid: str,
                 group_guids: set[str], pets: dict[str, str]):
        self.run = run
        self.self_guid = self_guid
        self.pets = pets
        self.group_guids = group_guids
        self.self_units = {self_guid} | {p for p, o in pets.items()
                                         if o == self_guid}
        rows.sort(key=lambda r: r[T])
        self.rows = rows

        self.dmg_out = []       # egne (inkl. pets) skade-events mod fjender
        self.dmg_group = []     # hele gruppens skade mod fjender
        self.dmg_taken = []     # skade taget af self
        self.casts = []         # egne SPELL_CAST_SUCCESS
        self.cast_stream = []   # egne CAST_START/SUCCESS/FAILED (til selvafbrud)
        self.auras_self = []    # aura-events på self
        self.aura_stream = []   # egne debuff-applikationer på mål m.m.
        for r in rows:
            ev = r[EV]
            if ev in DAMAGE_EVENTS:
                if r[DG].startswith("Player-"):
                    if r[DG] == self_guid:
                        self.dmg_taken.append(r)
                else:
                    owner = pets.get(r[SG], r[SG])
                    if owner in group_guids:
                        self.dmg_group.append(r)
                        if owner in {self_guid}:
                            self.dmg_out.append(r)
                        elif r[SG] in self.self_units:
                            self.dmg_out.append(r)
            elif ev == "SPELL_CAST_SUCCESS":
                if r[SG] == self_guid:
                    self.casts.append(r)
                    self.cast_stream.append(r)
            elif ev in ("SPELL_CAST_START", "SPELL_CAST_FAILED"):
                if r[SG] == self_guid:
                    self.cast_stream.append(r)
            elif ev.startswith("SPELL_AURA_"):
                if r[DG] == self_guid:
                    self.auras_self.append(r)
                if r[SG] in self.self_units:
                    self.aura_stream.append(r)

        # first-hit-tider pr. mål pr. pull (pakkevækst-attribution)
        self.first_hit: dict[int, dict[str, float]] = {}
        for p in run.get("pulls", []):
            fh: dict[str, float] = {}
            for r in self.dmg_group:
                if p["t0"] - 1 <= r[T] <= p["t1"]:
                    fh.setdefault(r[DG], r[T])
            self.first_hit[p["id"]] = fh

    def pull_of(self, t: float) -> dict | None:
        for p in self.run.get("pulls", []):
            if p["t0"] - OPENING_S <= t <= p["t1"] + 1:
                return p
        return None

    def targets_hit(self, t0: float, t1: float) -> set[str]:
        return {r[DG] for r in self.dmg_out if t0 <= r[T] < t1}


def _bucket(n: int) -> str:
    if n <= 1:
        return "1"
    if n == 2:
        return "2"
    if n <= 5:
        return "3-5"
    return "6+"


# --- Linse: mål-antal --------------------------------------------------------

def lens_targets(rd: RunData, spec: SpecConfig, blind_casts=None) -> dict:
    buckets: dict[str, dict] = {}
    for p in rd.run.get("pulls", []):
        t = p["t0"]
        while t < p["t1"]:
            # epsilon så pullens sidste event (t == t1) lander i slutvinduet
            w1 = min(t + WINDOW_S, p["t1"] + 1e-6)
            hit = rd.targets_hit(t, w1)
            if hit:
                b = buckets.setdefault(_bucket(len(hit)), {
                    "windows": 0, "seconds": 0.0, "damage": 0, "errors": 0})
                b["windows"] += 1
                b["seconds"] += w1 - t
                b["damage"] += sum(r[AMT] for r in rd.dmg_out
                                   if t <= r[T] < w1)
                if blind_casts:
                    b["errors"] += sum(1 for c in blind_casts
                                       if t <= c["t"] < w1)
            t = w1
    for b in buckets.values():
        b["dps"] = round(b["damage"] / b["seconds"], 1) if b["seconds"] else 0
        b["seconds"] = round(b["seconds"], 1)
    return {
        "buckets": measured(buckets, "target_buckets",
                            sample={"pulls": len(rd.run.get("pulls", []))}),
        "note": None if blind_casts is not None else
                "fejlrate pr. bucket kræver spec-config (blind spender)",
    }


# --- Linse: bevægelse --------------------------------------------------------

def lens_movement(rd: RunData, spec: SpecConfig) -> dict:
    pos_casts = [r for r in rd.casts if r[X] is not None]
    per_pull = []
    tot = {"lost_s": 0.0, "lost_moving_s": 0.0, "lost_stationary_s": 0.0,
           "yards": 0.0, "gaps": 0}
    gap_events = []  # til kontekst-linsen
    for p in rd.run.get("pulls", []):
        cs = [r for r in pos_casts if p["t0"] - OPENING_S <= r[T] <= p["t1"]]
        yards = 0.0
        lost = lost_mov = lost_stat = 0.0
        gaps = 0
        for a, b in zip(cs, cs[1:]):
            gap = b[T] - a[T]
            dist = math.hypot(b[X] - a[X], b[Y] - a[Y])
            if dist <= TELEPORT_YD:
                yards += dist
            if GAP_MIN_S <= gap <= GAP_MAX_S:
                gaps += 1
                lost_i = gap - GCD_BASELINE_S
                lost += lost_i
                moving = dist > MOVE_EPS_YD and dist <= TELEPORT_YD
                if moving:
                    lost_mov += lost_i
                else:
                    lost_stat += lost_i
                gap_events.append({"t": round(a[T], 3), "gap_s": round(gap, 2),
                                   "lost_s": round(lost_i, 2),
                                   "moving": moving, "pull": p["id"],
                                   "x": a[X], "y": a[Y]})
        dur = max(p["duration_s"], 1e-9)
        per_pull.append({
            "pull": p["id"], "yards": round(yards, 1),
            "yd_per_min": round(yards / dur * 60, 1),
            "lost_cast_s": round(lost, 1), "gaps": gaps,
        })
        tot["lost_s"] += lost
        tot["lost_moving_s"] += lost_mov
        tot["lost_stationary_s"] += lost_stat
        tot["yards"] += yards
        tot["gaps"] += gaps

    cancelled = _selfcancelled(rd.cast_stream)

    # boss-scatter: yd/min vs. egen DPS pr. boss-segment
    boss_scatter = []
    for b in rd.run.get("bosses", []):
        cs = [r for r in pos_casts if b["t0"] <= r[T] <= b["t1"]]
        yards = sum(min(math.hypot(q[X] - a[X], q[Y] - a[Y]), TELEPORT_YD)
                    for a, q in zip(cs, cs[1:])
                    if math.hypot(q[X] - a[X], q[Y] - a[Y]) <= TELEPORT_YD)
        dur = max(b["t1"] - b["t0"], 1e-9)
        dmg = sum(r[AMT] for r in rd.dmg_out if b["t0"] <= r[T] <= b["t1"])
        boss_scatter.append({"boss": b["name"], "yd_per_min": round(yards / dur * 60, 1),
                             "dps": round(dmg / dur, 1)})

    share = (tot["lost_moving_s"] / tot["lost_s"]) if tot["lost_s"] else None
    if not pos_casts:
        return {"unavailable": "ingen positioner — advanced logging er slået fra"}
    return {
        "lost_cast_seconds": measured(
            round(tot["lost_s"], 1), "lost_cast_time_moving",
            sample={"gaps": tot["gaps"], "casts": len(pos_casts)}),
        "lost_moving_share": measured(
            round(share, 3) if share is not None else None,
            "lost_cast_time_moving",
            metric="tabt casttid i bevægelse / al tabt casttid "
                   f"(bevægelse = positionsdelta > {MOVE_EPS_YD} yd)"),
        "selfcancelled_hardcasts": measured(
            len(cancelled), "selfcancelled_casts"),
        "per_pull": per_pull,
        "boss_scatter": boss_scatter,
        "assumptions": [f"GCD-baseline {GCD_BASELINE_S} s trukket fra hvert hul",
                        f"teleport-klipning ved > {TELEPORT_YD} yd"],
        "_gap_events": gap_events,
        "_cancelled_events": cancelled,
    }


def _selfcancelled(cast_stream: list) -> list[dict]:
    """CAST_START uden SUCCESS af samme spell inden næste cast / 6 s."""
    out = []
    pending = None  # (row)
    for r in cast_stream:
        if r[EV] == "SPELL_CAST_START":
            if pending is not None:
                out.append({"t": round(pending[T], 3), "spell": pending[SPN],
                            "spell_id": pending[SP]})
            pending = r
        elif r[EV] == "SPELL_CAST_SUCCESS":
            if pending is not None and (r[SP] == pending[SP]
                                        or r[T] - pending[T] > 6.0):
                if r[SP] != pending[SP]:
                    out.append({"t": round(pending[T], 3),
                                "spell": pending[SPN],
                                "spell_id": pending[SP]})
                pending = None
        elif r[EV] == "SPELL_CAST_FAILED":
            # eksplicit fail (fx bevægelse) — tæl som afbrud af pending
            if pending is not None and r[SP] == pending[SP]:
                out.append({"t": round(pending[T], 3), "spell": pending[SPN],
                            "spell_id": pending[SP],
                            "failed": (r[EX] or {}).get("failedType")})
                pending = None
    if pending is not None:
        out.append({"t": round(pending[T], 3), "spell": pending[SPN],
                    "spell_id": pending[SP]})
    return out


# --- Linse: rotation ---------------------------------------------------------

def lens_rotation(rd: RunData, spec: SpecConfig) -> dict:
    pulls = rd.run.get("pulls", [])
    active_s = sum(p["duration_s"] for p in pulls) or 1e-9
    in_pull = [c for c in rd.casts if rd.pull_of(c[T])]
    cpm = len(in_pull) / active_s * 60

    by_spell: dict[str, int] = {}
    for r in rd.dmg_out:
        key = r[SPN] or "Melee"
        by_spell[key] = by_spell.get(key, 0) + r[AMT]
    total_dmg = sum(by_spell.values()) or 1
    dmg_share = {k: round(v / total_dmg, 3) for k, v in
                 sorted(by_spell.items(), key=lambda kv: -kv[1])[:12]}

    procs = _proc_stats(rd, spec)
    blind = _blind_spenders(rd, spec) if spec.spenders else None
    cds = _cd_discipline(rd, spec) if spec.major_cds else None

    out = {
        "cpm": measured(round(cpm, 1),
                        "target_buckets",
                        metric="egne cast-successes pr. minut aktiv pull-tid",
                        sample={"casts": len(in_pull),
                                "active_s": round(active_s, 1)}),
        "casts_per_gcd_time": modeled(
            round(len(in_pull) / (active_s / GCD_BASELINE_S), 2),
            "target_buckets",
            assumptions=[f"teoretisk GCD {GCD_BASELINE_S} s; haste ignoreret"],
            metric="casts / (aktiv tid ÷ GCD)"),
        "damage_share_by_spell": measured(dmg_share, "target_buckets",
                                          metric="egen skade pr. spell / total"),
        "proc_stats": procs,
        "note_wasted_presses": "spildte tryk/min kan ikke måles i combat log "
                               "(kræver klient-input); feltet udfyldes ikke",
    }
    if blind is not None:
        out["blind_spenders"] = blind
    else:
        out["note_blind"] = "blind spender-rate kræver spec-config"
    if cds is not None:
        out["cd_discipline"] = cds
    return out


def _proc_stats(rd: RunData, spec: SpecConfig) -> dict:
    """Aura-state-maskine på self-buffs: gained/consumed/expired/munch."""
    cast_times = [c[T] for c in rd.casts]
    per_aura: dict[int, dict] = {}
    active: dict[int, float] = {}
    for r in rd.auras_self:
        if ((r[EX] or {}).get("auraType")) != "BUFF":
            continue
        sp = r[SP]
        st = per_aura.setdefault(sp, {"name": r[SPN], "gained": 0,
                                      "consumed": 0, "expired": 0, "munch": 0})
        if r[EV] == "SPELL_AURA_APPLIED":
            st["gained"] += 1
            active[sp] = r[T]
        elif r[EV] == "SPELL_AURA_REFRESH":
            st["gained"] += 1
            if sp in active:
                st["munch"] += 1   # refresh mens aktiv = potentielt munch
            active[sp] = r[T]
        elif r[EV] == "SPELL_AURA_REMOVED":
            if _near_cast(r[T], cast_times):
                st["consumed"] += 1
            else:
                st["expired"] += 1
            active.pop(sp, None)
    for sp, t0 in active.items():
        per_aura[sp]["active_at_end"] = True

    focus = {sp: st for sp, st in per_aura.items()
             if not spec.procs or sp in spec.procs}
    gained = sum(s["gained"] for s in focus.values())
    consumed = sum(s["consumed"] for s in focus.values())
    util = round(consumed / gained, 3) if gained else None
    return measured(
        {"utilization": util, "per_aura": {str(k): v for k, v in focus.items()},
         "scope": "spec-config procs" if spec.procs else "alle self-buffs"},
        "proc_utilization",
        sample={"procs_gained": gained})


def _near_cast(t: float, cast_times: list[float], eps: float = 0.3) -> bool:
    import bisect
    i = bisect.bisect_left(cast_times, t)
    for j in (i - 1, i):
        if 0 <= j < len(cast_times) and abs(cast_times[j] - t) <= eps:
            return True
    return False


def _blind_spenders(rd: RunData, spec: SpecConfig) -> dict:
    """Spender-casts uden krævet debuff/proc-state. Returnerer også
    event-listen (_events) til kontekst-linse og dashboard-prikker."""
    # aura-state pr. mål (egne debuffs) og self-buffs
    target_debuffs: dict[str, set[int]] = {}
    self_buffs: set[int] = set()
    events = []
    blind = st_casts = aoe_casts = st_blind = aoe_blind = 0
    aura_iter = iter(rd.aura_stream + [r for r in rd.auras_self])
    stream = sorted(rd.rows, key=lambda r: r[T])
    for r in stream:
        ev = r[EV]
        if ev.startswith("SPELL_AURA_"):
            at = (r[EX] or {}).get("auraType")
            if r[SG] in rd.self_units and at == "DEBUFF":
                s = target_debuffs.setdefault(r[DG], set())
                if ev in ("SPELL_AURA_APPLIED", "SPELL_AURA_REFRESH",
                          "SPELL_AURA_APPLIED_DOSE"):
                    s.add(r[SP])
                elif ev == "SPELL_AURA_REMOVED":
                    s.discard(r[SP])
            if r[DG] == rd.self_guid and at == "BUFF":
                if ev in ("SPELL_AURA_APPLIED", "SPELL_AURA_REFRESH",
                          "SPELL_AURA_APPLIED_DOSE"):
                    self_buffs.add(r[SP])
                elif ev == "SPELL_AURA_REMOVED":
                    self_buffs.discard(r[SP])
        elif ev == "UNIT_DIED":
            target_debuffs.pop(r[DG], None)
        elif ev == "SPELL_CAST_SUCCESS" and r[SG] == rd.self_guid \
                and r[SP] in spec.spenders:
            req = spec.spenders[r[SP]]
            ok = False
            if req.get("target_debuff"):
                ok |= bool(set(req["target_debuff"])
                           & target_debuffs.get(r[DG], set()))
            if req.get("self_buff"):
                ok |= bool(set(req["self_buff"]) & self_buffs)
            n_targets = len(rd.targets_hit(r[T] - WINDOW_S / 2,
                                           r[T] + WINDOW_S / 2))
            is_aoe = n_targets >= 3
            if is_aoe:
                aoe_casts += 1
            else:
                st_casts += 1
            if not ok:
                blind += 1
                if is_aoe:
                    aoe_blind += 1
                else:
                    st_blind += 1
                p = rd.pull_of(r[T])
                events.append({"t": round(r[T], 3), "spell": r[SPN],
                               "spell_id": r[SP], "target": r[DN],
                               "pull": p["id"] if p else None,
                               "x": r[X], "y": r[Y]})
    total = st_casts + aoe_casts
    return {
        **measured({
            "rate": round(blind / total, 3) if total else None,
            "blind_spender_rate_st": round(st_blind / st_casts, 3) if st_casts else None,
            "blind_spender_rate_aoe": round(aoe_blind / aoe_casts, 3) if aoe_casts else None,
            "blind": blind, "total_spender_casts": total,
            "st_casts": st_casts, "aoe_casts": aoe_casts,
            "st_blind": st_blind, "aoe_blind": aoe_blind,
        }, "blind_spender_rate", sample={"spender_casts": total}),
        "_events": events,
    }


def _cd_discipline(rd: RunData, spec: SpecConfig) -> dict:
    per_cd = {}
    for sp, meta in spec.major_cds.items():
        times = [c[T] for c in rd.casts if c[SP] == sp]
        if len(times) < 2:
            per_cd[str(sp)] = {"name": meta.get("name"), "casts": len(times),
                               "note": "for få casts til cadence"}
            continue
        cadence = statistics.median(b - a for a, b in zip(times, times[1:]))
        actual = possible = 0
        for p in rd.run.get("pulls", []):
            n = sum(1 for t in times if p["t0"] <= t <= p["t1"])
            actual += n
            possible += int(p["duration_s"] // cadence) + 1
        per_cd[str(sp)] = {
            "name": meta.get("name"),
            "casts": len(times),
            "median_cadence_s": round(cadence, 1),
            "utilization": round(actual / possible, 3) if possible else None,
        }
    return measured(per_cd, "cd_discipline")


# --- Linse: overlevelse ------------------------------------------------------

def lens_survival(rd: RunData, spec: SpecConfig) -> dict:
    recaps = []
    for d in rd.run.get("deaths", []):
        if d["guid"] != rd.self_guid:
            continue
        t_death = d["t"]
        hits = [r for r in rd.dmg_taken if t_death - 6 <= r[T] <= t_death]
        sources: dict[str, int] = {}
        hp_curve = []
        for r in hits:
            key = f"{r[SPN] or 'Melee'} ({r[SN]})"
            sources[key] = sources.get(key, 0) + r[AMT]
            ex = r[EX] or {}
            if ex.get("hp") is not None:
                hp_curve.append({"t": round(r[T] - t_death, 2),
                                 "hp": ex["hp"], "hpmax": ex.get("hpmax")})
        recap = {"t": t_death, "last6s_sources": dict(
            sorted(sources.items(), key=lambda kv: -kv[1])),
            "last6s_total": sum(sources.values()), "hp_curve": hp_curve}
        if spec.defensives:
            avail = {}
            for sp, meta in spec.defensives.items():
                uses = [c[T] for c in rd.casts if c[SP] == sp
                        and c[T] <= t_death]
                cd = meta.get("cd_s", 120)
                ready = not uses or (t_death - uses[-1]) >= cd
                avail[meta.get("name", str(sp))] = {
                    "ready_at_death": ready,
                    "last_use_s_before": round(t_death - uses[-1], 1) if uses else None}
            recap["defensive_availability"] = avail
        recaps.append(recap)

    timing = None
    if spec.defensives:
        deltas = []
        def_times = sorted(c[T] for c in rd.casts if c[SP] in spec.defensives)
        for r in rd.dmg_taken:
            ex = r[EX] or {}
            if ex.get("hpmax") and r[AMT] > BIG_HIT_SHARE * ex["hpmax"]:
                nxt = next((t for t in def_times if 0 <= t - r[T] <= 5), None)
                if nxt is not None:
                    deltas.append(nxt - r[T])
        if deltas:
            timing = measured(
                {"median_s_from_hit_to_defensive": round(statistics.median(deltas), 2),
                 "style": "reactive" if statistics.median(deltas) > 0.8 else "proactive"},
                "defensive_timing", sample={"reactions": len(deltas)})

    return {
        "death_recaps": measured(recaps, "defensive_timing",
                                 metric="skadekilder i de sidste 6 s før død + HP-kurve",
                                 sample={"deaths": len(recaps)}),
        "defensive_timing": timing if timing else
            "kræver spec-config med defensiv-liste (eller ingen store hits målt)",
    }


# --- Linse: sustain ----------------------------------------------------------

def lens_sustain(rd: RunData, spec: SpecConfig) -> dict:
    curves = []
    per_pull = []
    for p in rd.run.get("pulls", []):
        if p["duration_s"] < SUSTAIN_MIN_PULL_S:
            continue
        n_phases = int(p["duration_s"] // PHASE_S)
        own = [0] * n_phases
        grp = [0] * n_phases
        for r in rd.dmg_group:
            i = int((r[T] - p["t0"]) // PHASE_S)
            if 0 <= i < n_phases:
                grp[i] += r[AMT]
                owner = rd.pets.get(r[SG], r[SG])
                if owner == rd.self_guid:
                    own[i] += r[AMT]
        shares = [round(o / g, 3) if g else None for o, g in zip(own, grp)]
        own_dps = [round(o / PHASE_S, 1) for o in own]
        curves.append(shares)
        per_pull.append({"pull": p["id"], "phase_share": shares,
                         "own_dps_per_phase": own_dps})
    if not curves:
        return {"unavailable": f"ingen pulls ≥ {SUSTAIN_MIN_PULL_S:.0f} s"}

    max_len = max(len(c) for c in curves)
    mean_curve = []
    for i in range(max_len):
        vals = [c[i] for c in curves if i < len(c) and c[i] is not None]
        mean_curve.append(round(sum(vals) / len(vals), 3) if vals else None)

    return {
        "phase_share_curve": measured(
            mean_curve, "phase_share",
            sample={"pulls": len(curves), "phase_s": int(PHASE_S)}),
        "per_pull": per_pull,
        "note": "andels-metoden er primær; normaliseret egen-kurve vildleder "
                "ved ramp-specs (PRD F3) — own_dps_per_phase er sekundær",
    }


# --- Linse: kontekst-attribution ---------------------------------------------

def lens_context(rd: RunData, spec: SpecConfig, error_events: list[dict],
                 pos_casts: list | None = None) -> dict:
    """Attribuér fejl-events (blind casts, huller, selvafbrud) pr. situation.
    Kontekster er IKKE-eksklusive — andele summer bevidst > 100 %."""
    if pos_casts is None:
        pos_casts = [r for r in rd.casts if r[X] is not None]
    ctx = {"opening_5s": 0, "pack_growth": 0, "moving": 0, "late_pull": 0}
    detailed = []
    for e in error_events:
        p = rd.pull_of(e["t"])
        tags = []
        if p:
            rel = e["t"] - p["t0"]
            if rel <= OPENING_S:
                tags.append("opening_5s")
            if p["duration_s"] and rel / p["duration_s"] > LATE_PULL_SHARE:
                tags.append("late_pull")
            fh = rd.first_hit.get(p["id"], {})
            if any(0 <= e["t"] - t0 <= PACK_GROWTH_S for g, t0 in fh.items()
                   if t0 > p["t0"] + 0.5):
                tags.append("pack_growth")
        if _moving_at(e, pos_casts):
            tags.append("moving")
        for tag in tags:
            ctx[tag] += 1
        detailed.append({**{k: v for k, v in e.items()
                            if not k.startswith("_")}, "contexts": tags})
    n = len(error_events) or 1
    shares = {k: round(v / n, 3) for k, v in ctx.items()}
    return {
        "context_shares": measured(
            shares, "context_attribution",
            sample={"error_events": len(error_events)}),
        "events": detailed,
    }


def _moving_at(e: dict, pos_casts: list) -> bool:
    t = e["t"]
    before = None
    after = None
    for r in pos_casts:
        if r[T] <= t:
            before = r
        elif after is None:
            after = r
            break
    if before is None or after is None:
        return False
    dist = math.hypot(after[X] - before[X], after[Y] - before[Y])
    return MOVE_EPS_YD < dist <= TELEPORT_YD


# --- Orkestrering ------------------------------------------------------------

ALL_LENSES = ("targets", "movement", "rotation", "survival", "sustain",
              "context")


def run_lenses(cache_root: Path, log_stem: str, lenses=None, run_ids=None,
               spec_config: dict | None = None) -> dict:
    summary = parse_mod.load_summary(cache_root, log_stem)
    spec = SpecConfig(spec_config)
    self_guid = summary["player"]["guid"]
    group = {p["guid"] for p in summary["group"]}
    pets = summary.get("pets", {})
    lenses = list(lenses or ALL_LENSES)

    out = {"log": log_stem, "player": summary["player"],
           "spec_config": spec.spec, "runs": []}
    for run in summary["runs"]:
        if run_ids and run["id"] not in run_ids:
            continue
        rows = list(parse_mod.iter_run_events(cache_root, log_stem, run["id"]))
        rd = RunData(run, rows, self_guid, group, pets)
        res = {"run": {k: run.get(k) for k in
                       ("id", "type", "zone", "key_level", "success")}}
        movement = rotation = None
        if "movement" in lenses or "context" in lenses:
            movement = lens_movement(rd, spec)
        if "rotation" in lenses or "context" in lenses or "targets" in lenses:
            rotation = lens_rotation(rd, spec)
        blind_events = ((rotation or {}).get("blind_spenders") or {}).get("_events")
        if "targets" in lenses:
            res["targets"] = lens_targets(rd, spec, blind_casts=blind_events)
        if "movement" in lenses:
            res["movement"] = {k: v for k, v in movement.items()
                               if not k.startswith("_")}
        if "rotation" in lenses:
            res["rotation"] = {
                k: ({kk: vv for kk, vv in v.items() if not kk.startswith("_")}
                    if isinstance(v, dict) else v)
                for k, v in rotation.items()}
        if "survival" in lenses:
            res["survival"] = lens_survival(rd, spec)
        if "sustain" in lenses:
            res["sustain"] = lens_sustain(rd, spec)
        if "context" in lenses:
            errors = []
            if blind_events:
                errors += [{**e, "type": "blind_spender"} for e in blind_events]
            if movement and "_gap_events" in movement:
                errors += [{**e, "type": "cast_gap"}
                           for e in movement["_gap_events"]]
                errors += [{**e, "type": "selfcancel"}
                           for e in movement["_cancelled_events"]]
            res["context"] = lens_context(rd, spec, sorted(
                errors, key=lambda e: e["t"]))
        out["runs"].append(res)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="F3-linser over parsed cache")
    ap.add_argument("cache_root", help="Cache-rod (fx .clc-cache)")
    ap.add_argument("log_stem", help="Logstem (mappenavn i cachen)")
    ap.add_argument("--lens", action="append", choices=ALL_LENSES,
                    help="Kør kun udvalgte linser (default: alle)")
    ap.add_argument("--run", action="append", type=int,
                    help="Kør kun udvalgte run-id'er")
    ap.add_argument("--spec-config", help="JSON-fil med spec-konfiguration")
    args = ap.parse_args(argv)

    cfg = None
    if args.spec_config:
        cfg = json.loads(Path(args.spec_config).read_text())
    result = run_lenses(Path(args.cache_root), args.log_stem,
                        lenses=args.lens, run_ids=args.run, spec_config=cfg)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=1)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
