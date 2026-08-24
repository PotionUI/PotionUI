"""Unit tests for the extracted report renderer.

The CLI wrapper is exercised by ``test_profile_report.py``; this file pins the
module API the admin profile-viewer route depends on (``render_report`` /
``render_report_from_file`` returning a text string rather than printing).
"""

import json
from pathlib import Path

from src.platform.observability.profiling import report


def _row(t, kind, event=None, rss_gb=1.0, vram=None, **extra):
    row = {
        "t": t,
        "wall": t,
        "kind": kind,
        "rss_gb": rss_gb,
        "avail_gb": 10.0,
        "swap_gb": 0.0,
        "cpu": 5.0,
        "vram_alloc_gb": {"0": vram} if vram is not None else {},
        "vram_reserved_gb": {},
        "pinned_cum_gb": 0.0,
    }
    if event is not None:
        row["event"] = event
    row.update(extra)
    return row


def _write(tmp_path, rows) -> Path:
    path = tmp_path / "profile.jsonl"
    with open(path, "w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return path


def test_render_report_contains_all_sections(tmp_path):
    rows = [
        _row(0.0, "event", event="generation.start", rss_gb=2.0),
        _row(1.0, "event", event="native.move_to", rss_gb=21.5, kind_field="dit"),
        _row(2.0, "event", event="generation.end", rss_gb=3.0),
    ]
    path = _write(tmp_path, rows)

    text = report.render_report_from_file(path)

    assert isinstance(text, str)
    assert "STAGE TABLE" in text
    assert "TOP 10 RSS JUMPS" in text
    assert "SUMMARY" in text
    assert "CPU TENSOR CENSUS" in text
    assert "CUDA TENSOR CENSUS" in text
    assert "native.move_to" in text
    assert "peak_rss_gb" in text


def test_stage_table_shows_anon_and_file_rss_when_present(tmp_path):
    """The "34GB conditioning zombie" follow-up: the anon/file RSS split
    (profiler.py's `mark()`-only `rss_anon_gb`/`rss_file_gb` fields) must
    render as its own columns in the stage table, not get buried in the
    generic `[k=v ...]` extras tail."""
    rows = [
        _row(0.0, "event", event="generation.start", rss_gb=2.0),
        _row(1.0, "event", event="native.move_to", rss_gb=21.5, rss_anon_gb=18.25, rss_file_gb=2.5),
    ]
    path = _write(tmp_path, rows)

    text = report.render_stage_table(rows)

    assert "anon_gb" in text and "file_gb" in text
    assert "18.250" in text
    assert "2.500" in text
    # Must not ALSO show up duplicated in the generic extras tail.
    assert "rss_anon_gb=" not in text
    assert "rss_file_gb=" not in text


def test_stage_table_shows_dash_for_rows_without_anon_file_split(tmp_path):
    """A row/run that predates this feature (or a non-Linux box where the
    split is unavailable) must render "-" for the two columns, not crash or
    misalign the table."""
    rows = [_row(0.0, "event", event="generation.start", rss_gb=2.0)]

    text = report.render_stage_table(rows)

    assert "anon_gb" in text and "file_gb" in text
    lines = [l for l in text.splitlines() if "generation.start" in l]
    assert lines
    assert "-" in lines[0]


def test_cpu_and_cuda_tensor_census_render_separately_by_device(tmp_path):
    """A census row now carries a `device` field
    (cpu/cuda); each renderer must only show its own device's rows, and a
    legacy row with no `device` key at all (a profile.jsonl from before this
    field existed) must still render as CPU, unchanged."""
    rows = [
        _row(0.0, "census", device="cpu", nbytes_gb=1.0, dtype="torch.bfloat16",
             shape=[1, 2], owner="CpuHolder.weight", is_pinned=False),
        _row(0.0, "census", device="cuda", nbytes_gb=27.14, dtype="torch.float8_e4m3fn",
             shape=[3, 4], owner="CudaHolder.weight", is_pinned=None),
        _row(0.0, "census", nbytes_gb=0.5, dtype="torch.float32",
             shape=[5, 6], owner="LegacyHolder.weight", is_pinned=True),  # no "device" key
    ]
    path = _write(tmp_path, rows)
    text = report.render_report_from_file(path)

    assert "CpuHolder.weight" in text
    assert "LegacyHolder.weight" in text  # legacy no-device row treated as CPU
    assert "CudaHolder.weight" in text

    cpu_section = report.render_cpu_tensor_census(rows)
    cuda_section = report.render_cuda_tensor_census(rows)
    assert "CudaHolder.weight" not in cpu_section
    assert "CpuHolder.weight" not in cuda_section
    assert "LegacyHolder.weight" not in cuda_section


def test_render_report_contains_aggregate_census_sections(tmp_path):
    rows = [
        _row(0.0, "event", event="generation.start", rss_gb=2.0),
        _row(0.0, "census_group", device="cpu", nbytes_gb=21.9, count=140,
             dtype="torch.bfloat16", owner="Gemma3.ffn", is_pinned=False),
        _row(0.0, "census_group", device="cuda", nbytes_gb=23.3, count=400,
             dtype="torch.float8_e4m3fn", owner="LTXModel.dit", is_pinned=None),
    ]
    path = _write(tmp_path, rows)

    text = report.render_report_from_file(path)

    assert "CPU TENSOR CENSUS (aggregate)" in text
    assert "CUDA TENSOR CENSUS (aggregate)" in text
    assert "Gemma3.ffn" in text
    assert "LTXModel.dit" in text


def test_aggregate_census_groups_render_separately_by_device_and_are_bounded(tmp_path):
    """Aggregate rows must filter by device the same way the detail census
    does, and the renderer must cap how many groups it prints (largest-first)
    even though the writer itself already bounds group count."""
    rows = [
        _row(0.0, "census_group", device="cpu", nbytes_gb=5.0, count=10,
             dtype="torch.bfloat16", owner="CpuHolder.big", is_pinned=True),
        _row(0.0, "census_group", device="cuda", nbytes_gb=1.0, count=1,
             dtype="torch.float32", owner="CudaHolder.small", is_pinned=None),
    ]
    path = _write(tmp_path, rows)

    cpu_section = report.render_cpu_tensor_census_groups(rows)
    cuda_section = report.render_cuda_tensor_census_groups(rows)
    assert "CpuHolder.big" in cpu_section
    assert "CudaHolder.small" not in cpu_section
    assert "CudaHolder.small" in cuda_section
    assert "CpuHolder.big" not in cuda_section

    top_1 = report.render_cpu_tensor_census_groups(
        rows + [_row(0.0, "census_group", device="cpu", nbytes_gb=99.0, count=1,
                     dtype="torch.float32", owner="Bigger.owner", is_pinned=False)],
        top_n=1,
    )
    assert "Bigger.owner" in top_1
    assert "CpuHolder.big" not in top_1  # smaller group dropped once top_n=1


def test_render_report_appends_log_highlights_when_log_present(tmp_path):
    rows = [_row(0.0, "event", event="generation.start", rss_gb=2.0)]
    path = _write(tmp_path, rows)
    (tmp_path / report.LOG_FILENAME).write_text(
        "2026-07-12 10:00:00,000 ERROR some.module: boom\n"
        "2026-07-12 10:00:00,100 INFO  some.module: quiet\n"
    )

    text = report.render_report_from_file(path)

    assert "LOG HIGHLIGHTS" in text
    assert "boom" in text
    assert "quiet" not in text


def test_render_log_highlights_empty_without_log(tmp_path):
    rows = [_row(0.0, "event", event="generation.start")]
    path = _write(tmp_path, rows)
    assert report.render_log_highlights(path) == ""


def test_load_rows_reports_malformed(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"t": 0, "kind": "event"}\ngarbage\n')
    malformed: list[tuple[int, str]] = []
    rows = report.load_rows(path, malformed=malformed)
    assert len(rows) == 1
    assert len(malformed) == 1
    assert malformed[0][0] == 2
