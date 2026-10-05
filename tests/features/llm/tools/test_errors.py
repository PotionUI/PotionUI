"""Tests for the shared teaching-error helper."""

from src.features.llm.tools.errors import scrub_paths, teach, unexpected


class TestTeach:
    def test_joins_problem_and_expected(self):
        result = teach("no file exists at 'x.png'", "it must be a storage-root-relative path")
        assert result == "no file exists at 'x.png'. it must be a storage-root-relative path."

    def test_appends_next_step_when_given(self):
        result = teach(
            "no installed model matches 'foo.safetensors'",
            "use the exact filename or `model:<id>` reference",
            "call list_models to see what's actually installed",
        )
        assert result == (
            "no installed model matches 'foo.safetensors'. use the exact filename or "
            "`model:<id>` reference. call list_models to see what's actually installed."
        )

    def test_omits_next_step_when_not_given(self):
        result = teach("problem", "expected")
        assert result == "problem. expected."
        assert result.count(".") == 2

    def test_strips_trailing_periods_from_clauses_before_joining(self):
        """Callers should be able to pass clauses with or without a trailing
        period without ending up with a double '..'"""
        result = teach("problem.", "expected.", "next step.")
        assert result == "problem. expected. next step."
        assert ".." not in result


class TestUnexpected:
    def test_names_tool_and_operation(self):
        result = unexpected("write_memory", "save", RuntimeError("db error"))
        assert "write_memory" in result
        assert "save" in result

    def test_keeps_the_exception_detail(self):
        """The exception message is real diagnostic value for a genuine
        backend failure - it must not be discarded, only framed."""
        result = unexpected("write_memory", "save", RuntimeError("db error"), True)
        assert "db error" in result

    def test_is_not_a_bare_stringified_exception(self):
        error = RuntimeError("db error")
        result = unexpected("write_memory", "save", error, True)
        assert result != str(error)
        assert result != f"Failed: {error}"

    def test_signals_retrying_the_call_will_not_help(self):
        result = unexpected("run_generation", "start the generation", RuntimeError("boom"))
        assert "not something you can fix" in result.lower()


class TestScrubPaths:
    def test_replaces_absolute_posix_and_windows_paths(self):
        text = scrub_paths("missing under /home/u/models/siglip/x and C:\\Users\\me\\m.bin")
        assert "/home/" not in text and "C:\\" not in text
        assert text.count("<server path>") == 2

    def test_keeps_relative_paths_urls_and_repo_ids(self):
        text = "see https://hf.co/a/b, generations/2026/x.png and google/siglip-base-patch16-224"
        assert scrub_paths(text) == text


class TestUnexpectedDisclosure:
    def test_regular_users_get_no_exception_detail(self):
        result = unexpected("write_memory", "save", RuntimeError("cannot open /srv/db.sqlite"))
        assert "write_memory" in result
        assert "db.sqlite" not in result
        assert "cannot open" not in result

    def test_admins_get_detail_with_paths_scrubbed(self):
        result = unexpected("write_memory", "save", RuntimeError("cannot open /srv/app/db.sqlite"), True)
        assert "cannot open" in result
        assert "/srv/app" not in result
