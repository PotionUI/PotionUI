"""`tail_log`: last-N over a file bigger than one read block, continuation
lines folded into the entry above, level filtering, the line cap, and the
no-file-yet empty result."""

from pathlib import Path

from src.features.logs.tail import MAX_LINES, tail_log


def _line(i: int, level: str = "INFO", message: str | None = None) -> str:
    return f"2026-09-09 10:00:{i:02d} | {level:>8} | is.logs | {message or f'line {i}'}"


class TestMissingFile:

    def test_a_none_path_comes_back_empty_with_no_file(self):
        result = tail_log(None, lines=10)

        assert result == {"lines": [], "truncated": False, "file": None, "size_bytes": 0}

    def test_a_path_that_does_not_exist_comes_back_empty_with_no_file(self, tmp_path: Path):
        result = tail_log(tmp_path / "does-not-exist.log", lines=10)

        assert result == {"lines": [], "truncated": False, "file": None, "size_bytes": 0}

    def test_an_empty_file_comes_back_with_no_lines_but_a_real_file_name(self, tmp_path: Path):
        path = tmp_path / "potionui.log"
        path.write_text("")

        result = tail_log(path, lines=10)

        assert result["lines"] == []
        assert result["truncated"] is False
        assert result["file"] == str(path)
        assert result["size_bytes"] == 0


class TestLastN:

    def test_it_returns_the_last_n_lines_in_order_over_a_file_larger_than_one_read_block(self, tmp_path: Path):
        path = tmp_path / "potionui.log"
        # Each line is short; force many read-block doublings by writing well
        # past the module's initial 64KB block.
        total = 20000
        path.write_text("\n".join(_line(i) for i in range(total)) + "\n")

        result = tail_log(path, lines=50)

        assert len(result["lines"]) == 50
        assert result["lines"][0]["message"] == f"line {total - 50}"
        assert result["lines"][-1]["message"] == f"line {total - 1}"
        assert result["truncated"] is True
        assert result["size_bytes"] == path.stat().st_size

    def test_a_file_with_fewer_lines_than_requested_is_not_marked_truncated(self, tmp_path: Path):
        path = tmp_path / "potionui.log"
        path.write_text("\n".join(_line(i) for i in range(5)) + "\n")

        result = tail_log(path, lines=50)

        assert len(result["lines"]) == 5
        assert result["truncated"] is False


class TestContinuationLines:

    def test_traceback_frames_attach_to_the_preceding_entrys_message(self, tmp_path: Path):
        path = tmp_path / "potionui.log"
        body = (
            _line(0, message="boom")
            + "\n"
            + "Traceback (most recent call last):\n"
            + '  File "x.py", line 1, in <module>\n'
            + "ValueError: bad\n"
            + _line(1, message="after")
            + "\n"
        )
        path.write_text(body)

        result = tail_log(path, lines=10)

        assert len(result["lines"]) == 2
        assert result["lines"][0]["message"] == (
            "boom\nTraceback (most recent call last):\n"
            '  File "x.py", line 1, in <module>\nValueError: bad'
        )
        assert result["lines"][1]["message"] == "after"


class TestLevelFilter:

    def test_it_keeps_only_entries_at_or_above_the_requested_level(self, tmp_path: Path):
        path = tmp_path / "potionui.log"
        lines = [
            _line(0, level="DEBUG", message="d"),
            _line(1, level="INFO", message="i"),
            _line(2, level="WARNING", message="w"),
            _line(3, level="ERROR", message="e"),
        ]
        path.write_text("\n".join(lines) + "\n")

        result = tail_log(path, lines=10, level="WARNING")

        assert [e["level"] for e in result["lines"]] == ["WARNING", "ERROR"]

    def test_an_unrecognized_level_string_is_treated_as_no_filter(self, tmp_path: Path):
        path = tmp_path / "potionui.log"
        path.write_text(_line(0, level="INFO", message="i") + "\n")

        result = tail_log(path, lines=10, level="NOT_A_LEVEL")

        assert len(result["lines"]) == 1


class TestLineCap:

    def test_a_request_above_the_cap_is_clamped_to_max_lines(self, tmp_path: Path):
        path = tmp_path / "potionui.log"
        path.write_text("\n".join(_line(i) for i in range(10)) + "\n")

        result = tail_log(path, lines=MAX_LINES * 10)

        assert len(result["lines"]) == 10

    def test_a_request_below_one_is_clamped_to_one(self, tmp_path: Path):
        path = tmp_path / "potionui.log"
        path.write_text("\n".join(_line(i) for i in range(10)) + "\n")

        result = tail_log(path, lines=0)

        assert len(result["lines"]) == 1
        assert result["lines"][0]["message"] == "line 9"
