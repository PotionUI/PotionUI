"""Tests for the shared teaching-error helper."""

from src.features.llm.tools.errors import teach, unexpected


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
        result = unexpected("write_memory", "save", RuntimeError("db error"))
        assert "db error" in result

    def test_is_not_a_bare_stringified_exception(self):
        error = RuntimeError("db error")
        result = unexpected("write_memory", "save", error)
        assert result != str(error)
        assert result != f"Failed: {error}"

    def test_signals_retrying_the_call_will_not_help(self):
        result = unexpected("run_generation", "start the generation", RuntimeError("boom"))
        assert "not something you can fix" in result.lower()
