import importlib.util
import json
from pathlib import Path

import pytest

from src.platform.observability.profiling import report as _report

_SCRIPT_PATH = Path(__file__).resolve().parents[4] / "scripts" / "profile_report.py"
_spec = importlib.util.spec_from_file_location("profile_report", _SCRIPT_PATH)
profile_report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(profile_report)


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


@pytest.fixture()
def synthetic_profile(tmp_path):
    rows = [
        _row(0.0, "event", event="generation.start", rss_gb=2.0),
        _row(0.25, "sample", rss_gb=2.1),
        _row(0.5, "event", event="pipe.start", rss_gb=2.2, pipe_name="checkpoint_loader"),
        _row(1.0, "event", event="native.move_to", rss_gb=21.5, kind_field="dit"),
        _row(1.25, "sample", rss_gb=21.6),
        _row(1.5, "event", event="pipe.end", rss_gb=21.4, pipe_name="checkpoint_loader"),
        _row(2.0, "event", event="generation.end", rss_gb=3.0),
    ]
    path = tmp_path / "profile.jsonl"
    with open(path, "w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return path


def test_load_rows_sorted_by_time(synthetic_profile):
    rows = profile_report.load_rows(synthetic_profile)
    assert len(rows) == 7
    assert [r["t"] for r in rows] == sorted(r["t"] for r in rows)


def test_load_rows_skips_malformed_lines(tmp_path, capsys):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"t": 0, "kind": "event", "event": "generation.start"}\nnot json\n')
    rows = profile_report.load_rows(path)
    assert len(rows) == 1
    captured = capsys.readouterr()
    assert "malformed" in captured.err


def test_load_rows_tolerates_truncated_tail_line(tmp_path, capsys):
    """A crash mid-pipe can kill the process mid-``fh.write`` of the last row
    (the writer is line-buffered/appended, never atomic per-line), leaving a
    partial JSON object as the file's last line. The reader must still return
    every earlier, complete row instead of failing the whole load."""
    path = tmp_path / "truncated.jsonl"
    good_rows = [
        _row(0.0, "event", event="generation.start", rss_gb=2.0),
        _row(0.25, "sample", rss_gb=2.1),
        _row(0.5, "event", event="pipe.start", rss_gb=2.2, pipe_name="checkpoint_loader"),
    ]
    with open(path, "w") as fh:
        for row in good_rows:
            fh.write(json.dumps(row) + "\n")
        # Simulate a crash mid-write: a partial line with no closing brace/newline.
        fh.write('{"t": 0.75, "kind": "sample", "rss_g')

    rows = profile_report.load_rows(path)
    assert len(rows) == 3
    assert [r["t"] for r in rows] == [0.0, 0.25, 0.5]

    captured = capsys.readouterr()
    assert "malformed" in captured.err

    # The report renderer built on top of load_rows must also tolerate it end
    # to end (this is the same code path the admin profile-viewer route uses
    # via render_report_from_file).
    from src.platform.observability.profiling import report as _report
    text = _report.render_report_from_file(path)
    assert "STAGE TABLE" in text
    assert "generation.start" in text


def test_main_prints_stage_table_jumps_and_summary(synthetic_profile, capsys):
    rc = profile_report.main(["profile_report.py", str(synthetic_profile)])
    assert rc == 0
    out = capsys.readouterr().out

    assert "STAGE TABLE" in out
    assert "generation.start" in out
    assert "pipe.start" in out
    assert "TOP 10 RSS JUMPS" in out
    assert "SUMMARY" in out
    # The big native.move_to jump (2.2 -> 21.5) should show up as a delta.
    assert "native.move_to" in out
    assert "peak_rss_gb" in out


def test_main_missing_file_returns_error(tmp_path, capsys):
    rc = profile_report.main(["profile_report.py", str(tmp_path / "nope.jsonl")])
    assert rc == 1
    assert "does not exist" in capsys.readouterr().err


def test_log_highlights_prints_warning_error_lora_and_model_lifecycle_lines(synthetic_profile):
    log_path = synthetic_profile.parent / "generation.log"
    log_path.write_text(
        "2026-07-12 10:00:00,000 INFO    src.features.generation.generation: normal noise, skip me\n"
        "2026-07-12 10:00:00,100 WARNING src.platform.runtime.native.engine: falling back to manual_cast\n"
        "2026-07-12 10:00:00,200 ERROR   src.pipelines.pipes.generator.sdxl: OOM during sample\n"
        "2026-07-12 10:00:00,300 INFO    src.platform.runtime.native.lora.loader: applied 1 lora at weight 0.8\n"
        "2026-07-12 10:00:00,400 INFO    src.platform.runtime.model_lifecycle.manager: [MODEL_LIFECYCLE] Cache hit for key='dit'\n"
    )

    out = _report.render_log_highlights(synthetic_profile)

    assert "LOG HIGHLIGHTS" in out
    assert "normal noise, skip me" not in out
    assert "falling back to manual_cast" in out
    assert "OOM during sample" in out
    assert "applied 1 lora" in out
    assert "[MODEL_LIFECYCLE]" in out


def test_log_highlights_noop_when_no_log_file(synthetic_profile):
    out = _report.render_log_highlights(synthetic_profile)
    assert out == ""


def test_census_section_prints_rows_sorted_by_size(tmp_path):
    rows = [
        _row(0.0, "event", event="generation.start", rss_gb=2.0),
        _row(2.0, "event", event="generation.end", rss_gb=30.0),
        _row(
            2.0, "census", rss_gb=30.0, shape=[1000, 1000], dtype="torch.float16",
            nbytes_gb=0.5, is_pinned=False, owner="LTXModelBundle.diffusion_model",
        ),
        _row(
            2.0, "census", rss_gb=30.0, shape=[2000, 2000], dtype="torch.float32",
            nbytes_gb=11.4, is_pinned=None, owner="unknown",
        ),
    ]
    path = tmp_path / "profile.jsonl"
    with open(path, "w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    loaded = profile_report.load_rows(path)
    out = _report.render_cpu_tensor_census(loaded)

    assert "CPU TENSOR CENSUS" in out
    assert "count: 2" in out
    assert "total_gb: 11.900" in out
    assert "LTXModelBundle.diffusion_model" in out
    # Sorted largest-first: the 11.4GB row's owner ("unknown") must precede the
    # 0.5GB row's owner in the printed output.
    assert out.index("unknown") < out.index("LTXModelBundle.diffusion_model")


def test_census_section_no_rows_message(synthetic_profile):
    rows = profile_report.load_rows(synthetic_profile)  # no census rows in this fixture
    out = _report.render_cpu_tensor_census(rows)
    assert "CPU TENSOR CENSUS" in out
    assert "no census rows" in out


def test_main_includes_census_section(tmp_path, capsys):
    rows = [
        _row(0.0, "event", event="generation.start", rss_gb=2.0),
        _row(2.0, "event", event="generation.end", rss_gb=30.0),
        _row(
            2.0, "census", rss_gb=30.0, shape=[2000, 2000], dtype="torch.float32",
            nbytes_gb=11.4, is_pinned=False, owner="LTXModelBundle.diffusion_model",
        ),
    ]
    path = tmp_path / "profile.jsonl"
    with open(path, "w") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")

    rc = profile_report.main(["profile_report.py", str(path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "CPU TENSOR CENSUS" in out
    assert "LTXModelBundle.diffusion_model" in out


def test_main_includes_log_highlights_section(synthetic_profile, capsys):
    log_path = synthetic_profile.parent / "generation.log"
    log_path.write_text("2026-07-12 10:00:00,000 ERROR   some.module: boom\n")

    rc = profile_report.main(["profile_report.py", str(synthetic_profile)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "LOG HIGHLIGHTS" in out
    assert "boom" in out
