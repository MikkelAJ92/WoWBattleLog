#!/usr/bin/env python3
"""Generér den obligatoriske forbehold-sektion (F4/NFR) fra metrik-kataloget.

NFR-kravet: "Forbehold-sektionen er obligatorisk i alle artifacts og
genereres fra metrik-katalogets fejlkilder — ikke håndskrevet." Dette script
er dén generator: det scanner et linse-/analyse-output (JSON) rekursivt for
`metric_id`-referencer og modellerede antagelser, slår fejlkilderne op i
metrics.json og udskriver en færdig <section> til indlejring i dashboardet.

CLI:
  python3 caveats.py <lens-output.json> [--metrics <metrics.json>]
  cat output.json | python3 caveats.py -
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

_DEFAULT_METRICS = Path(__file__).resolve().parents[3] / "metrics.json"

KIND_LABEL = {
    "measured": "målt i loggen",
    "modeled": "modelleret (antagelser + interval påkrævet)",
    "external": "hentet eksternt (kilde påkrævet)",
}


def collect(obj, metric_ids=None, assumptions=None, externals=None):
    """Rekursiv scanning: metric_id-referencer, modeled-antagelser, kilder."""
    if metric_ids is None:
        metric_ids, assumptions, externals = set(), [], []
    if isinstance(obj, dict):
        mid = obj.get("metric_id")
        if mid:
            metric_ids.add(mid)
        if obj.get("kind") == "modeled":
            for a in obj.get("assumptions") or []:
                if a not in assumptions:
                    assumptions.append(a)
        if obj.get("kind") == "external" and obj.get("source_url"):
            if obj["source_url"] not in externals:
                externals.append(obj["source_url"])
        for k, v in obj.items():
            if k == "assumptions" and isinstance(v, list) and "kind" not in obj:
                for a in v:
                    if isinstance(a, str) and a not in assumptions:
                        assumptions.append(a)
            else:
                collect(v, metric_ids, assumptions, externals)
    elif isinstance(obj, list):
        for v in obj:
            collect(v, metric_ids, assumptions, externals)
    return metric_ids, assumptions, externals


def render(metric_ids: set[str], assumptions: list[str],
           externals: list[str], catalog: dict) -> str:
    """Byg forbehold-sektionen som selvstændig HTML-fragment."""
    order = list(catalog)  # katalogets rækkefølge er normativ
    used = [catalog[m] for m in order if m in metric_ids]
    missing = sorted(metric_ids - set(catalog))

    lines = ['<section data-clc-section="caveats" class="clc-caveats">',
             "<h2>Forbehold</h2>",
             '<p class="clc-dim">Auto-genereret fra metrik-kataloget — '
             "hver metrik i dette dashboard har en kendt fejlkilde, som du "
             "bør kende, før du handler på tallet. Mærkning: "
             '<span class="clc-badge clc-badge--measured">målt</span> '
             '<span class="clc-badge clc-badge--modeled">modelleret</span> '
             '<span class="clc-badge clc-badge--external">eksternt</span>.'
             "</p>"]
    if used:
        lines.append("<ul>")
        for m in used:
            badge = {"measured": "clc-badge--measured",
                     "modeled": "clc-badge--modeled",
                     "external": "clc-badge--external"}.get(m.get("kind", ""),
                                                            "clc-badge--measured")
            lines.append(
                f"<li><b>{html.escape(m['name'])}</b>"
                f'<span class="clc-badge {badge}">'
                f'{html.escape(KIND_LABEL.get(m.get("kind", "measured"), ""))}'
                f"</span><br>"
                f'<span class="clc-dim">{html.escape(m["definition"])}</span>'
                f"<br>Fejlkilde: {html.escape(m['error_source'])}</li>")
        lines.append("</ul>")
    if assumptions:
        lines.append("<h3>Modellerede antagelser i dette dashboard</h3><ul>")
        lines.extend(f"<li>{html.escape(a)}</li>" for a in assumptions)
        lines.append("</ul>")
    if externals:
        lines.append("<h3>Eksterne kilder</h3><ul>")
        lines.extend(
            f'<li><a href="{html.escape(u)}">{html.escape(u)}</a></li>'
            for u in externals)
        lines.append("</ul>")
    if missing:
        lines.append(
            '<p class="clc-dim">Metrikker uden katalogopslag (tilføj til '
            f"metrics.json): {html.escape(', '.join(missing))}</p>")
    lines.append("</section>")
    return "\n".join(lines)


def build(analysis: dict | list, metrics_path: Path = _DEFAULT_METRICS) -> str:
    data = json.loads(Path(metrics_path).read_text(encoding="utf-8"))
    catalog = {m["id"]: m for m in data["metrics"]}
    metric_ids, assumptions, externals = collect(analysis)
    return render(metric_ids, assumptions, externals, catalog)


def utf8_stdio() -> None:
    """Windows-stdio defaulter til cp1252; HTML-output og danske tekster er UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def main(argv=None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("input", help="Linse-/analyse-output JSON ('-' = stdin)")
    ap.add_argument("--metrics", default=str(_DEFAULT_METRICS),
                    help="Sti til metrics.json")
    args = ap.parse_args(argv)
    raw = sys.stdin.read() if args.input == "-" \
        else Path(args.input).read_text(encoding="utf-8")
    print(build(json.loads(raw), Path(args.metrics)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
