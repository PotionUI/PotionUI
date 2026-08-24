"""Reporter for the preset E2E test suite.

Writes each case's outputs + metadata under a (caller-timestamped) run directory
and renders one self-contained ``index.html`` gallery for eyeballing — grouped by
preset, PASS/FAIL/SKIP badges, thumbnails linking to the full PNGs. No external
assets (inline CSS, no CDN); all user text is HTML-escaped.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import List

from src.features.preset_suite.models import FAIL, PASS, SKIP, CaseResult


_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize(name: str) -> str:
    """Turn an id like 'native/SDXL/realistic' or a case name into a safe path
    segment (no slashes, no surprises). Empty -> 'unnamed'."""
    cleaned = _SAFE.sub("_", str(name)).strip("_")
    return cleaned or "unnamed"


def _badge_color(verdict: str) -> str:
    return {PASS: "#1a7f37", FAIL: "#cf222e", SKIP: "#6e7781"}.get(verdict, "#6e7781")


def write_run(run_dir: Path, results: List[CaseResult]) -> Path:
    """Write per-case images + metadata under ``run_dir`` and an ``index.html``
    gallery at its root. Mutates each result's ``image_paths`` with the written
    (run-root-relative) PNG paths. Returns the ``index.html`` path.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    for result in results:
        preset_seg = _sanitize(result.preset_id)
        case_seg = _sanitize(result.case_name)
        case_dir = run_dir / preset_seg / case_seg
        case_dir.mkdir(parents=True, exist_ok=True)

        rel_paths: List[str] = []
        for i, image in enumerate(result.outcome.images):
            fname = f"image{i}.png"
            try:
                image.save(case_dir / fname)
            except Exception as e:  # a broken image must not sink the whole report
                (case_dir / f"image{i}.error.txt").write_text(f"could not save image: {e}")
                continue
            rel_paths.append(f"{preset_seg}/{case_seg}/{fname}")
        result.image_paths = rel_paths

        _write_case_metadata(case_dir, result, rel_paths)

    index_path = run_dir / "index.html"
    index_path.write_text(_render_index(results), encoding="utf-8")
    return index_path


def _write_case_metadata(case_dir: Path, result: CaseResult, rel_paths: List[str]) -> None:
    outcome = result.outcome
    meta = {
        "preset_id": result.preset_id,
        "case_name": result.case_name,
        "verdict": result.verdict,
        "reason": result.reason,
        "seed": result.seed,
        "mode": result.mode,
        "tags": list(result.tags),
        "prompt": outcome.prompt,
        "negative_prompt": outcome.negative_prompt,
        "status": outcome.status,
        "seconds": outcome.seconds,
        "error": outcome.error,
        "skip_reason": outcome.skip_reason,
        "submitted_form": outcome.submitted_form,
        "checks": [asdict(c) for c in result.checks],
        "images": rel_paths,
    }
    (case_dir / "metadata.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")


# --- HTML rendering -----------------------------------------------------------


_CSS = """
body{font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f6f8fa;color:#1f2328}
header{background:#24292f;color:#fff;padding:16px 24px}
header h1{margin:0;font-size:18px}
.summary{padding:8px 24px;background:#eaeef2;border-bottom:1px solid #d0d7de;font-size:13px}
.preset{margin:24px}
.preset h2{font-size:15px;border-bottom:1px solid #d0d7de;padding-bottom:6px;color:#57606a;font-family:ui-monospace,monospace}
.cards{display:flex;flex-wrap:wrap;gap:16px}
.card{background:#fff;border:1px solid #d0d7de;border-radius:8px;padding:12px;width:280px}
.card h3{margin:0 0 6px;font-size:14px;display:flex;justify-content:space-between;align-items:center;gap:8px}
.badge{color:#fff;border-radius:12px;padding:2px 10px;font-size:11px;font-weight:600;white-space:nowrap}
.thumbs{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0}
.thumbs img{width:80px;height:80px;object-fit:cover;border:1px solid #d0d7de;border-radius:4px;background:#f0f0f0}
.reason{color:#cf222e;font-size:12px;margin:4px 0 0;word-break:break-word}
.reason.skip{color:#6e7781}
.meta{color:#57606a;font-size:11px;margin-top:6px}
.empty{padding:48px 24px;text-align:center;color:#6e7781}
"""


def _render_index(results: List[CaseResult]) -> str:
    n_pass = sum(1 for r in results if r.verdict == PASS)
    n_fail = sum(1 for r in results if r.verdict == FAIL)
    n_skip = sum(1 for r in results if r.verdict == SKIP)

    parts: List[str] = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>Preset test suite</title>",
        f"<style>{_CSS}</style></head><body>",
        "<header><h1>Preset E2E test suite</h1></header>",
        (
            f"<div class='summary'><b>{len(results)}</b> case(s): "
            f"<b style='color:#1a7f37'>{n_pass} PASS</b>, "
            f"<b style='color:#cf222e'>{n_fail} FAIL</b>, "
            f"<b style='color:#6e7781'>{n_skip} SKIP</b></div>"
        ),
    ]

    if not results:
        parts.append("<div class='empty'>No test cases were discovered or run.</div></body></html>")
        return "".join(parts)

    # Group by preset, preserving first-seen order.
    groups: dict = {}
    for r in results:
        groups.setdefault(r.preset_id, []).append(r)

    for preset_id, cases in groups.items():
        parts.append(f"<section class='preset'><h2>{html.escape(str(preset_id))}</h2><div class='cards'>")
        for r in cases:
            parts.append(_render_card(r))
        parts.append("</div></section>")

    parts.append("</body></html>")
    return "".join(parts)


def _render_card(r: CaseResult) -> str:
    color = _badge_color(r.verdict)
    out: List[str] = ["<div class='card'>"]
    out.append(
        f"<h3><span>{html.escape(str(r.case_name))}</span>"
        f"<span class='badge' style='background:{color}'>{html.escape(r.verdict)}</span></h3>"
    )

    if r.image_paths:
        out.append("<div class='thumbs'>")
        for p in r.image_paths:
            esc = html.escape(p)
            out.append(f"<a href='{esc}'><img src='{esc}' alt='{esc}' loading='lazy'></a>")
        out.append("</div>")

    if r.reason and r.verdict in (FAIL, SKIP):
        cls = "reason skip" if r.verdict == SKIP else "reason"
        out.append(f"<p class='{cls}'>{html.escape(str(r.reason))}</p>")

    bits = []
    if r.mode:
        bits.append(html.escape(str(r.mode)))
    if r.seed is not None:
        bits.append(f"seed {html.escape(str(r.seed))}")
    if r.outcome.seconds is not None:
        bits.append(f"{r.outcome.seconds:.1f}s")
    if r.tags:
        bits.append("tags: " + html.escape(", ".join(str(t) for t in r.tags)))
    if bits:
        out.append(f"<div class='meta'>{' · '.join(bits)}</div>")

    out.append("</div>")
    return "".join(out)
