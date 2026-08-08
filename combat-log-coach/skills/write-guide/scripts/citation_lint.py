#!/usr/bin/env python3
"""Citations-lint (F6): håndhæv guide-guardrails maskinelt.

Referencesessionen fangede selv en ikke-verificeret kilde-attribution —
derfor er reglerne kode, ikke stil. En guide skal markere hver påstand:

  <span data-claim="stable">…grundmekanik…</span>
  <span data-claim="external" data-source-url="https://…">…tuning…</span>
  <span data-claim="inference">…slutning fra profilen…</span>

Regler:
  R1  Enhver attribution-sætning ("X anbefaler Y", "according to…", "top
      parses viser…") skal (delvist) ligge i en data-claim="external".
  R2  Hver external-claim skal have en http(s) data-source-url OG et synligt
      <a href> til samme URL et sted i dokumentet.
  R3  Tuning-indhold (tier/buff/nerf/BiS/procenter) må ikke stå i
      data-claim="stable" (ERROR) og bør ikke stå umarkeret (WARNING).
  R4  Dokumentet skal bære et verifikations-stempel:
      <time data-clc-verified="ÅÅÅÅ-MM-DD">.

Exit-kode 0 = ren (warnings tilladt), 1 = fejl. `--list-sources` udskriver
alle kilde-URL'er (input til re-verifikationsjobbet).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ATTRIBUTION_RE = re.compile(
    r"\b(anbefaler|anbefalet af|anbefales af|ifølge|"
    r"recommends?|recommended by|according to|"
    r"top[- ]?(parses?|logs?)\w*\s+(viser|shows?))\b", re.IGNORECASE)
TUNING_RE = re.compile(
    r"\b([SABCD][- ]?tier|tier[- ]?list\w*|buff\w*|nerf\w*|"
    r"best in slot|BiS)\b|\d+(?:[.,]\d+)?\s?%", re.IGNORECASE)
SKIP_TAGS = {"script", "style"}


class GuideParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fragments: list[tuple[str, str | None]] = []  # (tekst, claim-ctx)
        self.claim_stack: list[str] = []
        self.skip_depth = 0
        self.externals: list[dict] = []
        self.links: set[str] = set()
        self.verified: str | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in SKIP_TAGS:
            self.skip_depth += 1
        claim = a.get("data-claim")
        if claim:
            self.claim_stack.append(claim)
            if claim == "external":
                self.externals.append({"source_url": a.get("data-source-url"),
                                       "line": self.getpos()[0]})
        else:
            self.claim_stack.append(self.claim_stack[-1]
                                    if self.claim_stack else None)
        if tag == "a" and a.get("href"):
            self.links.add(a["href"])
        if tag == "time" and a.get("data-clc-verified"):
            self.verified = a["data-clc-verified"]

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
        if self.claim_stack:
            self.claim_stack.pop()

    def handle_data(self, data):
        if self.skip_depth or not data.strip():
            return
        ctx = self.claim_stack[-1] if self.claim_stack else None
        self.fragments.append((data, ctx))


def lint(html_text: str) -> dict:
    p = GuideParser()
    p.feed(html_text)

    errors: list[dict] = []
    warnings: list[dict] = []

    # fuldtekst med kontekst-spor pr. tegn (sætninger krydser inline-tags)
    full = ""
    ctx_ranges: list[tuple[int, int, str | None]] = []
    for text, ctx in p.fragments:
        start = len(full)
        full += text
        ctx_ranges.append((start, len(full), ctx))

    def contexts_in(a: int, b: int) -> set:
        return {c for s, e, c in ctx_ranges if s < b and e > a}

    # R1: attribution kræver external-kontekst i sætningen
    for m in re.finditer(r"[^.!?\n]+[.!?]?", full):
        sent = m.group(0)
        if not sent.strip():
            continue
        if ATTRIBUTION_RE.search(sent) \
                and "external" not in contexts_in(m.start(), m.end()):
            errors.append({"rule": "R1", "message":
                           "attribution-sætning uden data-claim=\"external\" "
                           "(fetch-beleg mangler)",
                           "snippet": sent.strip()[:120]})

    # R2: external-claims skal have kilde-URL og synligt link
    sources = []
    for ext in p.externals:
        url = ext["source_url"]
        if not url or not url.startswith(("http://", "https://")):
            errors.append({"rule": "R2", "message":
                           "data-claim=\"external\" uden gyldig "
                           "data-source-url", "snippet": f"linje {ext['line']}"})
            continue
        sources.append(url)
        if url not in p.links:
            errors.append({"rule": "R2", "message":
                           f"kilde-URL har intet synligt <a href>-link: {url}",
                           "snippet": f"linje {ext['line']}"})

    # R3: tuning-indhold i forkert spor
    for text, ctx in p.fragments:
        m = TUNING_RE.search(text)
        if not m:
            continue
        if ctx == "stable":
            errors.append({"rule": "R3", "message":
                           "tuning-indhold i data-claim=\"stable\" — stabil "
                           "mekanik må ikke indeholde tuning-tal",
                           "snippet": text.strip()[:120]})
        elif ctx is None:
            warnings.append({"rule": "R3", "message":
                             "muligt tuning-indhold uden claim-markering",
                             "snippet": text.strip()[:120]})

    # R4: verifikations-stempel
    if not p.verified:
        errors.append({"rule": "R4", "message":
                       "mangler <time data-clc-verified=\"ÅÅÅÅ-MM-DD\">-stempel",
                       "snippet": None})

    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "sources": sorted(set(sources)), "verified": p.verified}


def utf8_stdio() -> None:
    """Windows-stdio defaulter til cp1252; lint-output indeholder danske tegn."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def main(argv=None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("guide", help="HTML-fil at linte")
    ap.add_argument("--json", action="store_true", help="JSON-output")
    ap.add_argument("--list-sources", action="store_true",
                    help="Udskriv kun kilde-URL'er (til re-verifikation)")
    args = ap.parse_args(argv)

    result = lint(Path(args.guide).read_text(encoding="utf-8"))
    if args.list_sources:
        print("\n".join(result["sources"]))
        return 0
    if args.json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=1)
        print()
    else:
        for e in result["errors"]:
            print(f"FEJL [{e['rule']}] {e['message']}"
                  + (f" — »{e['snippet']}«" if e["snippet"] else ""))
        for w in result["warnings"]:
            print(f"advarsel [{w['rule']}] {w['message']} — »{w['snippet']}«")
        status = "REN" if result["ok"] else "FEJL"
        print(f"{status}: {len(result['errors'])} fejl, "
              f"{len(result['warnings'])} advarsler, "
              f"{len(result['sources'])} kilder, "
              f"verificeret: {result['verified'] or 'ALDRIG'}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
