#!/usr/bin/env python3
"""Streaming parser + segmentering for WoW advanced combat logs (F1).

Design (jf. PRD afsnit F1 og NFR):
  * Streaming line-parse — hele filen holdes aldrig i hukommelsen; slim events
    skrives løbende til en gzip-cache pr. run.
  * Versionsdrift-robust: felter mappes BAGFRA pr. event-type (feltantal
    varierer mellem patches). Fx SPELL_DAMAGE: amount = felt[-11]; casts med
    advanced block: x,y = felt[-5],[-4]. Fejlende konverteringer tælles som
    parse_warnings; ukendte event-typer ignoreres med tælling — aldrig crash.
  * Kun aggregater (summary JSON < 100 KB) er tænkt til modelkontekst; den
    fulde slim-eventstrøm ligger på disk til genkørbare linser (F3).

Cache-layout ( <cache>/<logstem>/ ):
  manifest.json   — kildefilens størrelse+mtime + parserversion (genbrugstjek)
  summary.json    — samme struktur som stdout-outputtet
  run-NNN.jsonl.gz — slim events for run NNN; én JSON-array pr. linje:
      [t, ev, sg, sn, dg, dn, sp, spn, amount, overkill, x, y, extra]
    t = epoch-sekunder (float). extra = dict med event-specifikke felter:
      auraType/stacks (auras), failedType (cast fail), pt/pc/pm (power),
      hp/hpmax (damage taget af spiller), payload (markør-events).

CLI:
  parse.py <logfil eller Logs-mappe> [--player NAVN] [--cache DIR] [--force]
  parse.py <Logs-mappe> --check      # friskheds-tjek: er der logget for nylig?
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

PARSER_VERSION = "0.1.0"

# Pull-heuristik (PRD kernebegreber): sammenhængende spiller-skade med < 6 s
# huller; en pull tæller kun hvis min. 150k skade ELLER 8 s varighed.
PULL_GAP_S = 6.0
PULL_MIN_DAMAGE = 150_000
PULL_MIN_DURATION_S = 8.0
# Kamp-klynger udenfor runs (delves/dummy): nyt "run" ved > 300 s stilhed.
CLUSTER_GAP_S = 300.0
CLUSTER_MIN_EVENTS = 25

# Unit-flag-bits (COMBATLOG_OBJECT_*)
AFFIL_MINE = 0x1
REACTION_FRIENDLY = 0x10
REACTION_HOSTILE = 0x40
TYPE_PLAYER = 0x400
TYPE_PET = 0x1000

# --- Event-klassifikation -------------------------------------------------
# Suffiks-længder (felter EFTER advanced-blokken) pr. event-familie.
# Advanced-blokken er 17 felter: infoGUID, ownerGUID, curHP, maxHP, AP, SP,
# armor, absorb, powerType, curPower, maxPower, powerCost, posX, posY,
# uiMapID, facing, level.
ADV_LEN = 17
DMG_SUFFIX = 11   # amount, base, overkill, school, resist, block, absorb, crit, glance, crush, offhand
HEAL_SUFFIX = 5   # amount, base, overheal, absorbed, crit
ENERGIZE_SUFFIX = 4  # amount, over, powerType, maxPower

DAMAGE_EVENTS = {
    "SPELL_DAMAGE", "SPELL_PERIODIC_DAMAGE", "SPELL_BUILDING_DAMAGE",
    "RANGE_DAMAGE", "SWING_DAMAGE", "DAMAGE_SHIELD", "DAMAGE_SPLIT",
}
HEAL_EVENTS = {"SPELL_HEAL", "SPELL_PERIODIC_HEAL"}
ENERGIZE_EVENTS = {"SPELL_ENERGIZE", "SPELL_PERIODIC_ENERGIZE"}
CAST_EVENTS = {"SPELL_CAST_START", "SPELL_CAST_SUCCESS", "SPELL_CAST_FAILED"}
AURA_EVENTS = {
    "SPELL_AURA_APPLIED", "SPELL_AURA_REMOVED", "SPELL_AURA_REFRESH",
    "SPELL_AURA_APPLIED_DOSE", "SPELL_AURA_REMOVED_DOSE",
}
MARKER_EVENTS = {
    "CHALLENGE_MODE_START", "CHALLENGE_MODE_END",
    "ENCOUNTER_START", "ENCOUNTER_END", "ZONE_CHANGE", "MAP_CHANGE",
}
OTHER_KEPT = {"UNIT_DIED", "SPELL_INTERRUPT", "SPELL_SUMMON", "SPELL_DISPEL"}

HANDLED_EVENTS = (
    DAMAGE_EVENTS | HEAL_EVENTS | ENERGIZE_EVENTS | CAST_EVENTS | AURA_EVENTS
    | MARKER_EVENTS | OTHER_KEPT | {"COMBAT_LOG_VERSION", "COMBATANT_INFO"}
)


class Warnings:
    """Tæller for parse-problemer — robusthed frem for crash (NFR)."""

    def __init__(self):
        self.counts: dict[str, int] = {}

    def bump(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1


def _num(fields: list[str], idx: int, warns: Warnings, key: str):
    """Hent numerisk felt via (negativt) indeks; None + warning ved drift."""
    try:
        v = fields[idx]
    except IndexError:
        warns.bump(f"{key}:index")
        return None
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            warns.bump(f"{key}:value")
            return None


_FLAG_CACHE: dict[str, int] = {}


def _flags(s: str) -> int:
    v = _FLAG_CACHE.get(s)
    if v is None:
        try:
            v = int(s, 16)
        except ValueError:
            v = 0
        _FLAG_CACHE[s] = v
    return v


def _unquote(s: str) -> str:
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


class TimestampParser:
    """Tåler både 'M/D/YYYY HH:MM:SS.mmm±Z' (moderne) og 'M/D HH:MM:SS.mmm'."""

    def __init__(self, default_year: int):
        self.default_year = default_year
        self._date_cache: dict[str, float] = {}

    def to_epoch(self, ts: str, warns: Warnings):
        date_s, _, time_s = ts.partition(" ")
        base = self._date_cache.get(date_s)
        if base is None:
            try:
                parts = date_s.split("/")
                m, d = int(parts[0]), int(parts[1])
                y = int(parts[2]) if len(parts) == 3 else self.default_year
                base = datetime(y, m, d).timestamp()
            except (ValueError, IndexError):
                warns.bump("timestamp:date")
                return None
            self._date_cache[date_s] = base
        try:
            hh, mm, rest = time_s.split(":")
            # klip evt. timezone-suffiks ('.123-4' / '.123+2') af sekunderne
            for sign in ("-", "+"):
                i = rest.find(sign)
                if i > 0:
                    rest = rest[:i]
                    break
            return base + int(hh) * 3600 + int(mm) * 60 + float(rest)
        except ValueError:
            warns.bump("timestamp:time")
            return None


class RunWriter:
    """Streaming-skrivning af slim events for ét run til jsonl.gz."""

    def __init__(self, path: Path):
        self.path = path
        self._fh = gzip.open(path, "wt", encoding="utf-8", compresslevel=6)
        self.rows = 0

    def write(self, row: list) -> None:
        self._fh.write(json.dumps(row, separators=(",", ":")))
        self._fh.write("\n")
        self.rows += 1

    def close(self) -> None:
        self._fh.close()

    def discard(self) -> None:
        self.close()
        try:
            self.path.unlink()
        except OSError:
            pass


class PullTracker:
    """Online pull-segmentering: gruppens skade mod fjendtlige mål."""

    def __init__(self):
        self.pulls: list[dict] = []
        self._t0 = None
        self._t_last = None
        self._damage: dict[str, int] = {}
        self._events = 0

    def feed(self, t: float, owner_guid: str, amount: int) -> None:
        if self._t0 is not None and t - self._t_last > PULL_GAP_S:
            self._close()
        if self._t0 is None:
            self._t0 = t
        self._t_last = t
        self._damage[owner_guid] = self._damage.get(owner_guid, 0) + amount
        self._events += 1

    def _close(self) -> None:
        if self._t0 is None:
            return
        total = sum(self._damage.values())
        dur = self._t_last - self._t0
        if total >= PULL_MIN_DAMAGE or dur >= PULL_MIN_DURATION_S:
            self.pulls.append({
                "t0": round(self._t0, 3),
                "t1": round(self._t_last, 3),
                "duration_s": round(dur, 1),
                "damage_total": total,
                "damage_by_player": {k: v for k, v in sorted(
                    self._damage.items(), key=lambda kv: -kv[1])},
                "damage_events": self._events,
            })
        self._t0 = self._t_last = None
        self._damage = {}
        self._events = 0

    def finish(self) -> list[dict]:
        self._close()
        for i, p in enumerate(self.pulls, 1):
            p["id"] = i
        return self.pulls


class Segmenter:
    """Runs (M+ / raid-encounter / kamp-klynge) med pulls, bosser og deaths."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.runs: list[dict] = []
        self.current: dict | None = None
        self.writer: RunWriter | None = None
        self.pulls: PullTracker | None = None
        self._run_seq = 0
        self._last_activity = None
        self._zone = None
        self._open_encounter = None

    # -- livscyklus --------------------------------------------------------
    def _open(self, rtype: str, t: float, meta: dict) -> None:
        self._close(t)
        self._run_seq += 1
        self.current = {
            "id": self._run_seq, "type": rtype, "t0": t,
            "zone": meta.get("zone") or self._zone,
            "bosses": [], "deaths": [], **meta,
        }
        self.pulls = PullTracker()
        self.writer = RunWriter(self.cache_dir / f"run-{self._run_seq:03d}.jsonl.gz")

    def _close(self, t: float | None) -> None:
        if self.current is None:
            return
        run = self.current
        run["t1"] = t if t is not None else self._last_activity
        if run.get("t1") is not None:
            run["duration_s"] = round(run["t1"] - run["t0"], 1)
        run["pulls"] = self.pulls.finish()
        rows = self.writer.rows
        # smid tomme klynger væk (zone-skift, løb gennem by osv.)
        if run["type"] == "other" and rows < CLUSTER_MIN_EVENTS:
            self.writer.discard()
            self._run_seq -= 1
        else:
            self.writer.close()
            run["event_rows"] = rows
            run["events_file"] = self.writer.path.name
            self.runs.append(run)
        self.current = self.writer = self.pulls = None

    def _ensure_cluster(self, t: float) -> None:
        """Kamp udenfor markerede runs → 'other'-klynge (delve/dummy)."""
        if self.current is None:
            self._open("other", t, {"zone": self._zone})
        elif (self.current["type"] == "other" and self._last_activity is not None
              and t - self._last_activity > CLUSTER_GAP_S):
            self._open("other", t, {"zone": self._zone})

    # -- event-hooks ---------------------------------------------------------
    def on_marker(self, t: float, ev: str, f: list[str], warns: Warnings) -> None:
        if ev == "CHALLENGE_MODE_START":
            self._open("mplus", t, {
                "zone": _unquote(f[1]) if len(f) > 1 else None,
                "key_level": _num(f, 4, warns, "cm:key"),
                # affix-arrayet '[160,9,10]' splittes af CSV — saml det igen
                "affixes": ",".join(f[5:]) if len(f) > 5 else None,
            })
        elif ev == "CHALLENGE_MODE_END":
            if self.current is not None and self.current["type"] == "mplus":
                self.current["success"] = _num(f, 2, warns, "cm:success")
                total_ms = _num(f, 4, warns, "cm:time")
                if total_ms:
                    self.current["timer_s"] = round(total_ms / 1000, 1)
            self._close(t)
        elif ev == "ENCOUNTER_START":
            name = _unquote(f[2]) if len(f) > 2 else "?"
            if self.current is None:
                self._open("raid", t, {"encounter": name})
            self._open_encounter = {"name": name, "t0": t,
                                    "encounter_id": _num(f, 1, warns, "enc:id")}
        elif ev == "ENCOUNTER_END":
            enc = self._open_encounter
            if enc is not None and self.current is not None:
                enc["t1"] = t
                enc["success"] = _num(f, 5, warns, "enc:success")
                self.current["bosses"].append(enc)
            self._open_encounter = None
            if self.current is not None and self.current["type"] == "raid":
                self._close(t)
        elif ev == "ZONE_CHANGE":
            self._zone = _unquote(f[2]) if len(f) > 2 else None
            if self.current is not None and self.current["type"] == "other":
                self._close(t)

    def on_damage(self, t: float, owner_guid: str, amount: int) -> None:
        self._ensure_cluster(t)
        self.pulls.feed(t, owner_guid, amount)
        self._last_activity = t

    def on_activity(self, t: float) -> None:
        self._ensure_cluster(t)
        self._last_activity = t

    def on_death(self, t: float, guid: str, name: str) -> None:
        if self.current is not None and guid.startswith("Player-"):
            self.current["deaths"].append({"t": round(t, 3), "guid": guid,
                                           "name": name})

    def write_row(self, row: list) -> None:
        if self.writer is not None:
            self.writer.write(row)

    def finish(self, t: float | None) -> list[dict]:
        self._close(t)
        return self.runs


def parse_file(log_path: Path, cache_root: Path, player_name: str | None = None,
               force: bool = False) -> dict:
    """Parse én logfil (med cache-genbrug) og returnér summary-dict."""
    cache_dir = cache_root / log_path.stem
    manifest_path = cache_dir / "manifest.json"
    stat = log_path.stat()
    manifest = {"source": log_path.name, "size": stat.st_size,
                "mtime": int(stat.st_mtime), "parser_version": PARSER_VERSION}
    if not force and manifest_path.exists():
        try:
            if json.loads(manifest_path.read_text()) == manifest:
                summary = json.loads((cache_dir / "summary.json").read_text())
                summary["cache_hit"] = True
                return summary
        except (OSError, json.JSONDecodeError):
            pass

    cache_dir.mkdir(parents=True, exist_ok=True)
    for old in cache_dir.glob("run-*.jsonl.gz"):
        old.unlink()

    t_wall = time.monotonic()
    warns = Warnings()
    tsp = TimestampParser(default_year=datetime.fromtimestamp(stat.st_mtime).year)
    seg = Segmenter(cache_dir)

    event_counts: dict[str, int] = {}
    players: dict[str, str] = {}          # guid -> navn (venlige spillere)
    self_score: dict[str, int] = {}       # guid -> antal MINE-flaggede events
    pets: dict[str, str] = {}             # pet-guid -> ejer-guid
    log_meta = {"advanced_logging": None, "log_version": None,
                "build_version": None}
    total_lines = 0
    pos_count = 0
    last_t = None

    def slim(t, ev, sg, sn, dg, dn, sp=None, spn=None, amount=None,
             overkill=None, x=None, y=None, extra=None):
        seg.write_row([round(t, 3), ev, sg, sn, dg, dn, sp, spn, amount,
                       overkill, x, y, extra])

    with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
        # Timestamp og payload adskilles af dobbelt-space; payload er CSV.
        # Ét C-hastigheds csv.reader-kald over hele filen: erstat første '  '
        # med ',' så række[0] bliver timestampen.
        reader = csv.reader((ln.replace("  ", ",", 1) for ln in fh))
        for row in reader:
            total_lines += 1
            if len(row) < 2:
                warns.bump("line:short")
                continue
            ev = row[1]
            event_counts[ev] = event_counts.get(ev, 0) + 1

            if ev == "COMBAT_LOG_VERSION":
                for i, tok in enumerate(row):
                    if tok == "ADVANCED_LOG_ENABLED" and i + 1 < len(row):
                        log_meta["advanced_logging"] = row[i + 1] == "1"
                    elif tok == "BUILD_VERSION" and i + 1 < len(row):
                        log_meta["build_version"] = row[i + 1]
                if len(row) > 2:
                    log_meta["log_version"] = row[2]
                continue

            t = tsp.to_epoch(row[0], warns)
            if t is None:
                continue
            last_t = t
            f = row[1:]  # f[0] = event-navn; matcher log-feltindeks

            if ev in MARKER_EVENTS:
                seg.on_activity(t)
                seg.on_marker(t, ev, f, warns)
                slim(t, ev, None, None, None, None,
                     extra={"payload": f[1:9]})
                continue
            if ev == "COMBATANT_INFO":
                continue
            if ev not in HANDLED_EVENTS:
                continue  # ukendt/uunderstøttet — talt i event_counts

            if len(f) < 9:
                warns.bump(f"{ev}:fields")
                continue
            sg, sn = f[1], _unquote(f[2])
            dg, dn = f[5], _unquote(f[6])
            sflags = _flags(f[3])
            dflags = _flags(f[7])

            src_is_player = sg.startswith("Player-")
            if src_is_player and (sflags & REACTION_FRIENDLY):
                players.setdefault(sg, sn)
                if sflags & AFFIL_MINE:
                    self_score[sg] = self_score.get(sg, 0) + 1
            src_is_pet = bool(sflags & TYPE_PET)

            spell_id = spell_name = None
            if ev.startswith(("SPELL_", "RANGE_")):
                spell_id = _num(f, 9, warns, "spell:id") if len(f) > 11 else None
                spell_name = _unquote(f[10]) if len(f) > 11 else None

            if ev in DAMAGE_EVENTS:
                amount = _num(f, -DMG_SUFFIX, warns, f"{ev}:amount")
                overkill = _num(f, -DMG_SUFFIX + 2, warns, f"{ev}:overkill")
                x = y = None
                extra = None
                adv = len(f) >= 9 + ADV_LEN + DMG_SUFFIX
                if adv:
                    x = _num(f, -(DMG_SUFFIX + 5), warns, f"{ev}:x")
                    y = _num(f, -(DMG_SUFFIX + 4), warns, f"{ev}:y")
                    if x is not None:
                        pos_count += 1
                    # ejer-mapping kun når advanced-blokken beskriver source
                    info = f[-(DMG_SUFFIX + ADV_LEN)]
                    owner = f[-(DMG_SUFFIX + ADV_LEN) + 1]
                    if (info == sg and owner.startswith("Player-")
                            and not src_is_player):
                        pets[sg] = owner
                if amount is not None:
                    owner_guid = pets.get(sg, sg)
                    src_counts = (src_is_player and (sflags & REACTION_FRIENDLY)) \
                        or owner_guid.startswith("Player-") or src_is_pet
                    if src_counts and (dflags & REACTION_HOSTILE):
                        seg.on_damage(t, owner_guid, amount)
                    elif dg.startswith("Player-"):
                        seg.on_activity(t)
                        if adv:
                            hp = _num(f, -(DMG_SUFFIX + 15), warns, f"{ev}:hp")
                            hpmax = _num(f, -(DMG_SUFFIX + 14), warns, f"{ev}:hpmax")
                            extra = {"hp": hp, "hpmax": hpmax}
                    else:
                        continue  # irrelevant skade (mob vs. mob m.m.)
                    slim(t, ev, sg, sn, dg, dn, spell_id, spell_name,
                         amount, overkill, x, y, extra)
                continue

            if ev in CAST_EVENTS:
                if not src_is_player and not src_is_pet:
                    continue
                seg.on_activity(t)
                x = y = None
                extra = None
                if ev == "SPELL_CAST_SUCCESS" and len(f) >= 9 + 3 + ADV_LEN:
                    x = _num(f, -5, warns, "cast:x")
                    y = _num(f, -4, warns, "cast:y")
                    if x is not None:
                        pos_count += 1
                    extra = {"pt": _num(f, -9, warns, "cast:pt"),
                             "pc": _num(f, -8, warns, "cast:pc"),
                             "pm": _num(f, -7, warns, "cast:pm")}
                    # advanced-blokken beskriver casteren → ejer-felt for pets
                    owner = f[-(ADV_LEN - 1)]
                    if src_is_pet and owner.startswith("Player-"):
                        pets[sg] = owner
                elif ev == "SPELL_CAST_FAILED":
                    extra = {"failedType": _unquote(f[-1])}
                slim(t, ev, sg, sn, dg, dn, spell_id, spell_name,
                     x=x, y=y, extra=extra)
                continue

            if ev in AURA_EVENTS:
                if not (src_is_player or src_is_pet or dg.startswith("Player-")):
                    continue
                if f[-1] in ("BUFF", "DEBUFF"):
                    extra = {"auraType": f[-1]}
                else:
                    extra = {"auraType": f[-2] if len(f) > 12 else None,
                             "stacks": _num(f, -1, warns, "aura:stacks")}
                slim(t, ev, sg, sn, dg, dn, spell_id, spell_name, extra=extra)
                continue

            if ev in ENERGIZE_EVENTS:
                if not dg.startswith("Player-"):
                    continue
                amount = _num(f, -ENERGIZE_SUFFIX, warns, "energize:amount")
                extra = {"pt": _num(f, -2, warns, "energize:pt"),
                         "pm": _num(f, -1, warns, "energize:pm")}
                slim(t, ev, sg, sn, dg, dn, spell_id, spell_name, amount,
                     extra=extra)
                continue

            if ev in HEAL_EVENTS:
                if not dg.startswith("Player-"):
                    continue
                amount = _num(f, -HEAL_SUFFIX, warns, "heal:amount")
                overheal = _num(f, -3, warns, "heal:overheal")
                slim(t, ev, sg, sn, dg, dn, spell_id, spell_name, amount,
                     extra={"overheal": overheal})
                continue

            if ev == "UNIT_DIED":
                seg.on_death(t, dg, dn)
                slim(t, ev, sg, sn, dg, dn)
                continue

            if ev == "SPELL_SUMMON":
                if src_is_player:
                    pets[dg] = sg
                slim(t, ev, sg, sn, dg, dn, spell_id, spell_name)
                continue

            if ev in ("SPELL_INTERRUPT", "SPELL_DISPEL"):
                if src_is_player or dg.startswith("Player-"):
                    slim(t, ev, sg, sn, dg, dn, spell_id, spell_name)
                continue

    runs = seg.finish(last_t)

    # Selv-detektion: flest MINE-flaggede events; --player overstyrer.
    self_guid = None
    if player_name:
        for g, n in players.items():
            if n.split("-")[0].lower() == player_name.split("-")[0].lower():
                self_guid = g
                break
    if self_guid is None and self_score:
        self_guid = max(self_score, key=self_score.get)

    unknown = {e: c for e, c in sorted(event_counts.items(), key=lambda kv: -kv[1])
               if e not in HANDLED_EVENTS}
    if log_meta["advanced_logging"] is None:
        # fallback-detektion: blev der faktisk udtrukket positioner?
        log_meta["advanced_logging"] = pos_count > 0

    warnings_out = []
    if not log_meta["advanced_logging"]:
        warnings_out.append(
            "Advanced combat logging ser ud til at være slået FRA — positioner "
            "og ressource-data mangler. Slå det til: Esc → Options → Gameplay → "
            "Network → 'Advanced Combat Logging'.")

    summary = {
        "parser_version": PARSER_VERSION,
        "source": {"file": log_path.name, "size_bytes": stat.st_size,
                   "lines": total_lines, **log_meta},
        "player": {
            "guid": self_guid,
            "name": players.get(self_guid),
            "auto_detected": player_name is None,
        },
        "group": [{"guid": g, "name": n} for g, n in players.items()],
        "pets": pets,
        "runs": runs,
        "counts": {
            "events_total": total_lines,
            "by_event": dict(sorted(event_counts.items(), key=lambda kv: -kv[1])[:25]),
            "unknown_events": unknown,
            "parse_warnings": warns.counts,
        },
        "warnings": warnings_out,
        "parse_seconds": round(time.monotonic() - t_wall, 2),
        "cache_dir": str(cache_dir),
        "cache_hit": False,
    }
    (cache_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1))
    manifest_path.write_text(json.dumps(manifest))
    return summary


# --- API til linser (F3) ---------------------------------------------------

def load_summary(cache_root: Path, log_stem: str) -> dict:
    return json.loads((Path(cache_root) / log_stem / "summary.json").read_text())


def iter_run_events(cache_root: Path, log_stem: str, run_id: int):
    """Yield slim event-rækker (lists, jf. modul-docstring) for ét run."""
    path = Path(cache_root) / log_stem / f"run-{run_id:03d}.jsonl.gz"
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            yield json.loads(line)


# --- Friskheds-tjek (F1-detektionskrav) -------------------------------------

def check_logs_dir(logs_dir: Path, max_age_hours: float = 48.0) -> tuple[int, str]:
    """Returnerer (exit_code, besked). Kode 0 = friske logs fundet."""
    files = sorted(logs_dir.glob("WoWCombatLog*.txt"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return 1, (
            f"Ingen WoWCombatLog-*.txt fundet i {logs_dir}.\n"
            "Combat logging er sandsynligvis ikke slået til. I spillet:\n"
            "  1. Skriv /combatlog i chatten FØR du starter din nøgle/encounter\n"
            "  2. Slå 'Advanced Combat Logging' til under Esc → Options → "
            "Gameplay → Network (kræves for positioner)\n"
            "Kør analysen igen efter næste session.")
    newest = files[0]
    age_h = (time.time() - newest.stat().st_mtime) / 3600
    if age_h > max_age_hours:
        return 2, (
            f"Nyeste log er {newest.name} ({age_h:.0f} timer gammel). "
            "Hvis du har spillet siden, har logging ikke været slået til — "
            "husk /combatlog før næste session.")
    return 0, f"Frisk log fundet: {newest.name} ({age_h:.1f} timer gammel, " \
              f"{newest.stat().st_size / 1e6:.1f} MB)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("path", help="Logfil eller Logs-mappe")
    ap.add_argument("--player", help="Karakternavn (ellers auto-detektion)")
    ap.add_argument("--cache", default=".clc-cache",
                    help="Cache-mappe (default: .clc-cache)")
    ap.add_argument("--force", action="store_true",
                    help="Ignorér cache og parse forfra")
    ap.add_argument("--check", action="store_true",
                    help="Tjek kun om der findes friske logs i mappen")
    args = ap.parse_args(argv)

    path = Path(args.path)
    if args.check:
        code, msg = check_logs_dir(path if path.is_dir() else path.parent)
        print(msg)
        return code

    if path.is_dir():
        files = sorted(path.glob("WoWCombatLog*.txt"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            code, msg = check_logs_dir(path)
            print(msg, file=sys.stderr)
            return code or 1
        path = files[0]
        print(f"Bruger nyeste log: {path.name}", file=sys.stderr)

    summary = parse_file(path, Path(args.cache), player_name=args.player,
                         force=args.force)
    json.dump(summary, sys.stdout, ensure_ascii=False, indent=1)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
