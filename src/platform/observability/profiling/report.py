"""Render a text report from a per-generation ``profile.jsonl`` file.

This is the single source of truth for the profile row schema and the report
layout. ``scripts/profile_report.py`` is a thin CLI wrapper over these
functions, and the admin profile-viewer route
(``src/features/generation/``) renders the same text for the frontend.

The report has seven sections, each returned by a ``render_*`` function and
assembled by :func:`render_report`:

  (a) a stage table -- for each recorded event, the RSS/available-RAM/VRAM/
      pinned snapshot at that moment plus the delta since the previous event
  (b) the top 10 largest RSS jumps between consecutive rows (samples
      included), each annotated with the nearest preceding event
  (c) peak RSS/VRAM/swap, and final vs. start RSS
  (d) the aggregate tensor census (EVERY live tensor, no size floor, deduped
      by storage, grouped by dtype/owner/pinned -- the trustworthy "where did
      the memory go" answer; see ``profiler.py``'s ``_write_tensor_census``)
  (e) the detail tensor census (individual tensors > 64MB, kept for
      continuity with anything already matching on that row shape)
  (f) LOG HIGHLIGHTS -- when a ``generation.log`` cut (see
      ``profiler.py``) sits next to the jsonl, every WARNING/ERROR line plus
      anything mentioning LoRAs or the model cache

Kept stdlib-only (json/re/pathlib): this must not pull in torch/psutil so the
CLI stays cheap and the module is importable anywhere. See ``profiler.py`` for
the row schema these functions read.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

# Kept in lockstep with LOG_FILENAME in profiler.py -- not imported from there so
# this module stays stdlib-only (no torch/psutil pull-in).
LOG_FILENAME = "generation.log"
_LEVEL_RE = re.compile(r"\b(WARNING|ERROR|CRITICAL)\b")
_LORA_RE = re.compile(r"lora", re.IGNORECASE)
_MODEL_LIFECYCLE_TAG = "[MODEL_LIFECYCLE]"


def load_rows(
    path: Path,
    malformed: Optional[list[tuple[int, str]]] = None,
) -> list[dict[str, Any]]:
    """Parse a ``profile.jsonl`` file into rows sorted by monotonic time.

    Malformed lines are skipped. If a ``malformed`` list is passed, each skipped
    line's ``(line_number, error)`` is appended to it -- this module never writes
    to stdout/stderr itself; the CLI wrapper surfaces those to stderr.
    """
    rows = []
    with open(path, "r") as fh:
        for line_num, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                if malformed is not None:
                    malformed.append((line_num, str(e)))
    rows.sort(key=lambda r: r.get("t", 0))
    return rows


def _total_vram(row: dict[str, Any], field: str) -> float:
    values = row.get(field) or {}
    return sum(v for v in values.values() if isinstance(v, (int, float)))


def _fmt(v: Optional[float]) -> str:
    return f"{v:.3f}" if isinstance(v, (int, float)) else "-"


def _fmt_delta(v: Optional[float]) -> str:
    if not isinstance(v, (int, float)):
        return "-"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.3f}"


def _section(lines: list[str]) -> str:
    """Join section lines and reproduce the trailing blank line the original
    per-section ``print()`` emitted, so the assembled report is byte-identical
    to the old script output."""
    return "\n".join(lines + [""]) + "\n"


def render_stage_table(rows: list[dict[str, Any]]) -> str:
    events = [r for r in rows if r.get("kind") == "event"]
    lines = [
        "=" * 100,
        "STAGE TABLE (events only)",
        "=" * 100,
    ]
    # `anon_gb`/`file_gb` split a real live allocation from something mmap'd/
    # read off a big file, which ``rss_gb`` alone can't distinguish. Sourced
    # from profiler.py's `mark()`-only `rss_anon_gb`/`rss_file_gb` fields; `_fmt`
    # renders "-" for a row that predates this feature.
    header = (
        f"{'event':<30}{'t':>10}{'rss_gb':>10}{'d_rss':>10}{'anon_gb':>9}{'file_gb':>9}"
        f"{'avail_gb':>10}{'vram_gb':>10}{'d_vram':>10}{'pinned_cum_gb':>15}"
    )
    lines.append(header)
    lines.append("-" * len(header))

    prev_rss = None
    prev_vram = None
    for row in events:
        rss = row.get("rss_gb")
        vram = _total_vram(row, "vram_alloc_gb")
        d_rss = (rss - prev_rss) if isinstance(rss, (int, float)) and isinstance(prev_rss, (int, float)) else None
        d_vram = (vram - prev_vram) if prev_vram is not None else None
        name = row.get("event", "?")
        extra_keys = [
            k for k in row
            if k not in (
                "t", "wall", "kind", "event", "rss_gb", "avail_gb", "swap_gb", "cpu",
                "vram_alloc_gb", "vram_reserved_gb", "pinned_cum_gb",
                "rss_anon_gb", "rss_file_gb",
            )
        ]
        extras = " ".join(f"{k}={row[k]}" for k in extra_keys)
        lines.append(
            f"{name:<30}{_fmt(row.get('t')):>10}{_fmt(rss):>10}{_fmt_delta(d_rss):>10}"
            f"{_fmt(row.get('rss_anon_gb')):>9}{_fmt(row.get('rss_file_gb')):>9}"
            f"{_fmt(row.get('avail_gb')):>10}{_fmt(vram):>10}{_fmt_delta(d_vram):>10}"
            f"{_fmt(row.get('pinned_cum_gb')):>15}"
            + (f"  [{extras}]" if extras else "")
        )
        if isinstance(rss, (int, float)):
            prev_rss = rss
        prev_vram = vram
    return _section(lines)


def render_top_jumps(rows: list[dict[str, Any]], top_n: int = 10) -> str:
    lines = [
        "=" * 100,
        f"TOP {top_n} RSS JUMPS (consecutive rows, samples included)",
        "=" * 100,
    ]

    jumps = []
    last_event = None
    prev_row = None
    for row in rows:
        if row.get("kind") == "event":
            last_event = row.get("event")
        rss = row.get("rss_gb")
        if prev_row is not None and isinstance(rss, (int, float)):
            prev_rss = prev_row.get("rss_gb")
            if isinstance(prev_rss, (int, float)):
                jumps.append((rss - prev_rss, prev_row.get("t"), row.get("t"), last_event))
        prev_row = row

    jumps.sort(key=lambda x: abs(x[0]), reverse=True)
    header = f"{'delta_rss_gb':>13}{'t_from':>10}{'t_to':>10}  nearest_preceding_event"
    lines.append(header)
    lines.append("-" * len(header))
    for delta, t_from, t_to, event in jumps[:top_n]:
        lines.append(f"{_fmt_delta(delta):>13}{_fmt(t_from):>10}{_fmt(t_to):>10}  {event or '(none)'}")
    return _section(lines)


def render_summary(rows: list[dict[str, Any]]) -> str:
    lines = [
        "=" * 100,
        "SUMMARY",
        "=" * 100,
    ]
    if not rows:
        lines.append("(no rows)")
        return _section(lines)

    rss_values = [r["rss_gb"] for r in rows if isinstance(r.get("rss_gb"), (int, float))]
    swap_values = [r["swap_gb"] for r in rows if isinstance(r.get("swap_gb"), (int, float))]
    vram_values = [_total_vram(r, "vram_alloc_gb") for r in rows]

    start_rss = rss_values[0] if rss_values else None
    final_rss = rss_values[-1] if rss_values else None

    lines.append(f"peak_rss_gb:    {_fmt(max(rss_values, default=None))}")
    lines.append(f"peak_vram_gb:   {_fmt(max(vram_values, default=None))}")
    lines.append(f"peak_swap_gb:   {_fmt(max(swap_values, default=None))}")
    lines.append(f"start_rss_gb:   {_fmt(start_rss)}")
    lines.append(f"final_rss_gb:   {_fmt(final_rss)}")
    if isinstance(start_rss, (int, float)) and isinstance(final_rss, (int, float)):
        lines.append(f"net_rss_delta:  {_fmt_delta(final_rss - start_rss)}")
    return _section(lines)


def _render_tensor_census(
    rows: list[dict[str, Any]], *, device_kind: str, title: str, top_n: int = 15,
) -> str:
    """Shared renderer behind :func:`render_cpu_tensor_census` and
    :func:`render_cuda_tensor_census`. A row with no ``"device"`` key at all
    predates the CUDA census and is treated as CPU
    -- every census row written before that change was CPU-only, so this
    keeps old ``profile.jsonl`` files rendering exactly as before."""
    census = [
        r for r in rows
        if r.get("kind") == "census" and r.get("device", "cpu") == device_kind
    ]
    lines = [
        "=" * 100,
        f"{title} (generation.end snapshot, tensors > 64MB)",
        "=" * 100,
    ]
    if not census:
        lines.append(f"(no census rows for {device_kind} -- profiling disabled, no big {device_kind} tensors "
                     "found, or run predates this feature)")
        return _section(lines)

    total_gb = sum(r.get("nbytes_gb", 0) or 0 for r in census)
    lines.append(f"count: {len(census)}    total_gb: {total_gb:.3f}")
    lines.append("")
    header = f"{'nbytes_gb':>10}  {'pinned':>6}  {'dtype':<16}{'shape':<24}owner"
    lines.append(header)
    lines.append("-" * len(header))
    for r in sorted(census, key=lambda r: r.get("nbytes_gb", 0) or 0, reverse=True)[:top_n]:
        pinned = r.get("is_pinned")
        pinned_s = "yes" if pinned is True else ("no" if pinned is False else "?")
        shape = str(r.get("shape"))
        lines.append(
            f"{_fmt(r.get('nbytes_gb')):>10}  {pinned_s:>6}  "
            f"{str(r.get('dtype')):<16}{shape:<24}{r.get('owner', '?')}"
        )
    return _section(lines)


def render_cpu_tensor_census(rows: list[dict[str, Any]], top_n: int = 15) -> str:
    return _render_tensor_census(rows, device_kind="cpu", title="CPU TENSOR CENSUS", top_n=top_n)


def render_cuda_tensor_census(rows: list[dict[str, Any]], top_n: int = 15) -> str:
    """GPU-side mirror of :func:`render_cpu_tensor_census` (a production
    capture found ~28GB still allocated by PyTorch on
    the GPU from an orphaned previous generation's DiT, invisible to the
    CPU-only census -- see ``profiler.py``'s ``_write_cuda_tensor_census``)."""
    return _render_tensor_census(rows, device_kind="cuda", title="CUDA TENSOR CENSUS", top_n=top_n)


def _render_tensor_census_groups(
    rows: list[dict[str, Any]], *, device_kind: str, title: str, top_n: int = 30,
) -> str:
    """Renderer for the aggregate ``kind: "census_group"`` rows (see
    ``profiler.py``'s ``_write_tensor_census`` -- the LTX RAM-ratchet
    follow-up to the >64MB detail census above). Unlike the detail census,
    these rows have NO size floor and are already deduped by storage, so
    ``sum(nbytes_gb)`` here is a trustworthy answer to "how much of this
    device's memory does the census account for", which the detail-only
    census could never claim (it structurally missed most of a resident
    model whose individual weights sit at/under the 64MB floor)."""
    groups = [
        r for r in rows
        if r.get("kind") == "census_group" and r.get("device") == device_kind
    ]
    lines = [
        "=" * 100,
        f"{title} (generation.end snapshot, EVERY live tensor, deduped by storage)",
        "=" * 100,
    ]
    if not groups:
        lines.append(f"(no census_group rows for {device_kind} -- profiling disabled, no {device_kind} "
                      "tensors found, or run predates this feature)")
        return _section(lines)

    total_gb = sum(r.get("nbytes_gb", 0) or 0 for r in groups)
    total_count = sum(r.get("count", 0) or 0 for r in groups)
    lines.append(f"groups: {len(groups)}    tensors: {total_count}    total_gb: {total_gb:.3f}")
    lines.append("")
    header = f"{'nbytes_gb':>10}  {'count':>7}  {'pinned':>6}  {'dtype':<20}owner"
    lines.append(header)
    lines.append("-" * len(header))
    for r in sorted(groups, key=lambda r: r.get("nbytes_gb", 0) or 0, reverse=True)[:top_n]:
        pinned = r.get("is_pinned")
        pinned_s = "yes" if pinned is True else ("no" if pinned is False else "?")
        lines.append(
            f"{_fmt(r.get('nbytes_gb')):>10}  {r.get('count', '?'):>7}  {pinned_s:>6}  "
            f"{str(r.get('dtype')):<20}{r.get('owner', '?')}"
        )
    return _section(lines)


def render_cpu_tensor_census_groups(rows: list[dict[str, Any]], top_n: int = 30) -> str:
    return _render_tensor_census_groups(rows, device_kind="cpu", title="CPU TENSOR CENSUS (aggregate)", top_n=top_n)


def render_cuda_tensor_census_groups(rows: list[dict[str, Any]], top_n: int = 30) -> str:
    return _render_tensor_census_groups(rows, device_kind="cuda", title="CUDA TENSOR CENSUS (aggregate)", top_n=top_n)


def _render_tensor_census_groups_now(
    rows: list[dict[str, Any]], *, device_kind: str, title: str, top_n: int = 30,
) -> str:
    """Renderer for ad-hoc mid-generation ``kind: "census_group_now"`` rows
    (see ``profiler.py``'s ``GenerationProfiler.census_now`` -- fired from a
    caller-chosen point mid-run, e.g. right after an eviction that should
    have freed real RAM, so a killed run's ``profile.jsonl`` still carries at
    least one census taken near the point of death).

    Deliberately a SEPARATE row kind from the end-of-run ``"census_group"``
    rows above (never mixed into that section) -- summing groups from two
    different points in time as if they were one snapshot would silently
    double-count any tensor still live at both. Grouped by ``tag`` since one
    run may call ``census_now`` more than once at different phase
    boundaries; each tag renders as its own labeled table."""
    entries = [
        r for r in rows
        if r.get("kind") == "census_group_now" and r.get("device") == device_kind
    ]
    lines = [
        "=" * 100,
        f"{title} (ad-hoc census_now snapshots, by tag)",
        "=" * 100,
    ]
    if not entries:
        lines.append(f"(no census_group_now rows for {device_kind} -- no census_now() call fired, "
                      "profiling disabled, or run predates this feature)")
        return _section(lines)

    tags = sorted({r.get("tag") for r in entries}, key=lambda t: (t is None, t))
    for tag in tags:
        tag_groups = [r for r in entries if r.get("tag") == tag]
        total_gb = sum(r.get("nbytes_gb", 0) or 0 for r in tag_groups)
        total_count = sum(r.get("count", 0) or 0 for r in tag_groups)
        lines.append(f"-- tag={tag!r}    groups: {len(tag_groups)}    tensors: {total_count}    total_gb: {total_gb:.3f}")
        header = f"{'nbytes_gb':>10}  {'count':>7}  {'pinned':>6}  {'dtype':<20}owner"
        lines.append(header)
        lines.append("-" * len(header))
        for r in sorted(tag_groups, key=lambda r: r.get("nbytes_gb", 0) or 0, reverse=True)[:top_n]:
            pinned = r.get("is_pinned")
            pinned_s = "yes" if pinned is True else ("no" if pinned is False else "?")
            lines.append(
                f"{_fmt(r.get('nbytes_gb')):>10}  {r.get('count', '?'):>7}  {pinned_s:>6}  "
                f"{str(r.get('dtype')):<20}{r.get('owner', '?')}"
            )
        lines.append("")
    return _section(lines)


def render_cpu_tensor_census_groups_now(rows: list[dict[str, Any]], top_n: int = 30) -> str:
    return _render_tensor_census_groups_now(
        rows, device_kind="cpu", title="CPU TENSOR CENSUS (aggregate, mid-run)", top_n=top_n,
    )


def render_cuda_tensor_census_groups_now(rows: list[dict[str, Any]], top_n: int = 30) -> str:
    return _render_tensor_census_groups_now(
        rows, device_kind="cuda", title="CUDA TENSOR CENSUS (aggregate, mid-run)", top_n=top_n,
    )


def _is_log_highlight(line: str) -> bool:
    return bool(_LEVEL_RE.search(line)) or bool(_LORA_RE.search(line)) or _MODEL_LIFECYCLE_TAG in line


def render_log_highlights(jsonl_path: Path) -> str:
    """Render the LOG HIGHLIGHTS section from the ``generation.log`` sitting
    next to ``jsonl_path``. Returns ``""`` when no log file is present (the
    section is omitted entirely, matching the original behaviour)."""
    log_path = Path(jsonl_path).parent / LOG_FILENAME
    if not log_path.is_file():
        return ""

    lines = [
        "=" * 100,
        "LOG HIGHLIGHTS",
        "=" * 100,
    ]

    found = False
    with open(log_path, "r") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line and _is_log_highlight(line):
                lines.append(line)
                found = True
    if not found:
        lines.append("(no WARNING/ERROR/lora/[MODEL_LIFECYCLE] lines found)")
    return _section(lines)


def render_report(rows: list[dict[str, Any]], jsonl_path: Optional[Path] = None) -> str:
    """Assemble the full text report from already-loaded ``rows``. When
    ``jsonl_path`` is given, the LOG HIGHLIGHTS section is appended from the
    sibling ``generation.log`` (omitted when there is none)."""
    parts = [
        render_stage_table(rows),
        render_top_jumps(rows),
        render_summary(rows),
        render_cpu_tensor_census_groups(rows),
        render_cuda_tensor_census_groups(rows),
        render_cpu_tensor_census(rows),
        render_cuda_tensor_census(rows),
        render_cpu_tensor_census_groups_now(rows),
        render_cuda_tensor_census_groups_now(rows),
    ]
    if jsonl_path is not None:
        parts.append(render_log_highlights(jsonl_path))
    return "".join(parts)


def render_report_from_file(path: Path) -> str:
    """Load ``path`` and render the full report (including log highlights).
    Convenience for callers that only have the profile path."""
    return render_report(load_rows(path), path)
