"""Tests for the preset E2E test schema (src.features.presets.tests_schema)."""

from __future__ import annotations

import pytest

from src.features.presets.tests_schema import (
    NEEDS_MODEL_TAG,
    PLACEHOLDER_SHA256,
    Checks,
    HFRef,
    ModelRef,
    PresetTests,
    TestCase,
    TestsYmlError,
    load_tests_yml,
    validate_tests_yml,
)

VALID_SHA = "a" * 64


def valid_case(**overrides):
    data = {"name": "fast-case", "mode": "txt2img", "seed": 42}
    data.update(overrides)
    return data


def valid_tests_data(**overrides):
    data = {"schema": 1, "cases": [valid_case()]}
    data.update(overrides)
    return data


# --------------------------------------------------------------------------- #
# ModelRef / HFRef
# --------------------------------------------------------------------------- #


class TestModelRef:
    def test_sha256_only(self):
        ref = ModelRef(sha256=VALID_SHA)
        assert ref.sha256 == VALID_SHA
        assert ref.hf is None

    def test_sha256_with_hf_fallback(self):
        ref = ModelRef(sha256=VALID_SHA, hf={"repo": "org/repo", "file": "model.safetensors"})
        assert ref.hf == HFRef(repo="org/repo", file="model.safetensors")

    def test_sha256_required(self):
        with pytest.raises(Exception):
            ModelRef()

    def test_extra_key_rejected(self):
        with pytest.raises(Exception):
            ModelRef(sha256=VALID_SHA, unexpected_key="x")

    def test_hf_extra_key_rejected(self):
        with pytest.raises(Exception):
            HFRef(repo="org/repo", file="a.safetensors", revision="main")

    def test_hf_missing_file_rejected(self):
        with pytest.raises(Exception):
            HFRef(repo="org/repo")


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


class TestChecks:
    def test_defaults(self):
        checks = Checks()
        assert checks.min_outputs == 1
        assert checks.resolution is None
        assert checks.not_black is True
        assert checks.max_seconds is None

    def test_overrides(self):
        checks = Checks(min_outputs=2, resolution="768x768", not_black=False, max_seconds=30.0)
        assert checks.min_outputs == 2
        assert checks.resolution == "768x768"
        assert checks.not_black is False
        assert checks.max_seconds == 30.0

    def test_extra_key_rejected(self):
        with pytest.raises(Exception):
            Checks(min_outputs=1, typo_key=True)

    def test_resolution_accepts_a_list_for_mixed_size_batches(self):
        checks = Checks(resolution=["1024x1024", "2048x2048"])
        assert checks.resolution == ["1024x1024", "2048x2048"]

    def test_resolution_still_accepts_a_bare_string(self):
        checks = Checks(resolution="1024x1024")
        assert checks.resolution == "1024x1024"


# --------------------------------------------------------------------------- #
# TestCase
# --------------------------------------------------------------------------- #


class TestTestCase:
    def test_minimal_valid_case(self):
        case = TestCase(**valid_case())
        assert case.name == "fast-case"
        assert case.mode == "txt2img"
        assert case.seed == 42
        assert case.kind == "image"
        assert case.form == {}
        assert case.tags == ["fast"]
        assert case.models == {}
        assert isinstance(case.checks, Checks)

    def test_kind_defaults_to_image(self):
        assert TestCase(**valid_case()).kind == "image"

    def test_kind_video_accepted(self):
        assert TestCase(**valid_case(kind="video")).kind == "video"

    def test_kind_rejects_unknown_value(self):
        with pytest.raises(Exception):
            TestCase(**valid_case(kind="audio"))

    def test_seed_is_required(self):
        with pytest.raises(Exception):
            TestCase(name="x", mode="txt2img")

    def test_name_is_required(self):
        with pytest.raises(Exception):
            TestCase(mode="txt2img", seed=1)

    def test_mode_is_required(self):
        with pytest.raises(Exception):
            TestCase(name="x", seed=1)

    def test_form_and_models_and_tags_and_checks(self):
        case = TestCase(**valid_case(
            form={"prompt": "a cat", "steps": 8},
            tags=["fast", "sprint"],
            models={"diffusion_model": {"sha256": VALID_SHA}},
            checks={"min_outputs": 2, "not_black": False},
        ))
        assert case.form == {"prompt": "a cat", "steps": 8}
        assert case.tags == ["fast", "sprint"]
        assert case.models["diffusion_model"].sha256 == VALID_SHA
        assert case.checks.min_outputs == 2
        assert case.checks.not_black is False

    def test_extra_key_rejected(self):
        with pytest.raises(Exception):
            TestCase(**valid_case(typo_field="x"))


# --------------------------------------------------------------------------- #
# PresetTests / validate_tests_yml
# --------------------------------------------------------------------------- #


class TestPresetTests:
    def test_valid(self):
        tests, errors = validate_tests_yml(valid_tests_data())
        assert errors == []
        assert tests is not None
        assert tests.schema_version == 1
        assert len(tests.cases) == 1

    def test_empty_cases_is_valid(self):
        tests, errors = validate_tests_yml({"schema": 1, "cases": []})
        assert errors == []
        assert tests.cases == []

    def test_cases_defaults_to_empty_list(self):
        tests, errors = validate_tests_yml({"schema": 1})
        assert errors == []
        assert tests.cases == []

    def test_unsupported_schema_version_rejected(self):
        tests, errors = validate_tests_yml(valid_tests_data(schema=2))
        assert tests is None
        assert any("Unsupported tests.yml schema version" in e for e in errors)

    def test_schema_field_required(self):
        tests, errors = validate_tests_yml({"cases": []})
        assert tests is None
        assert errors != []

    def test_extra_top_level_key_rejected(self):
        tests, errors = validate_tests_yml(valid_tests_data(unexpected="x"))
        assert tests is None
        assert errors != []

    def test_error_message_names_the_case_by_name(self):
        # Case has a `name` but is missing the required `seed`.
        data = {"schema": 1, "cases": [{"name": "broken-case", "mode": "txt2img"}]}
        tests, errors = validate_tests_yml(data, prefix="tests.yml")
        assert tests is None
        assert any("broken-case" in e for e in errors)
        assert any("seed" in e for e in errors)

    def test_error_message_falls_back_to_index_when_name_is_missing(self):
        # Case is missing `name` itself -- error must still be attributable.
        data = {"schema": 1, "cases": [{"mode": "txt2img", "seed": 1}]}
        tests, errors = validate_tests_yml(data)
        assert tests is None
        assert any("index 0" in e for e in errors)

    def test_multiple_cases_collect_all_errors(self):
        data = {
            "schema": 1,
            "cases": [
                {"name": "case-a", "mode": "txt2img"},  # missing seed
                {"name": "case-b", "seed": 1},  # missing mode
            ],
        }
        tests, errors = validate_tests_yml(data)
        assert tests is None
        assert any("case-a" in e for e in errors)
        assert any("case-b" in e for e in errors)


# --------------------------------------------------------------------------- #
# load_tests_yml
# --------------------------------------------------------------------------- #


class TestLoadTestsYml:
    def test_no_file_returns_none(self, tmp_path):
        assert load_tests_yml(tmp_path) is None

    def test_valid_file_loads(self, tmp_path):
        (tmp_path / "tests.yml").write_text(
            "schema: 1\ncases:\n  - name: fast-case\n    mode: txt2img\n    seed: 1\n"
        )
        tests = load_tests_yml(tmp_path)
        assert tests is not None
        assert tests.cases[0].name == "fast-case"

    def test_malformed_yaml_raises(self, tmp_path):
        (tmp_path / "tests.yml").write_text("schema: 1\ncases: [\n")  # unterminated
        with pytest.raises(TestsYmlError):
            load_tests_yml(tmp_path)

    def test_non_mapping_top_level_raises(self, tmp_path):
        (tmp_path / "tests.yml").write_text("- just\n- a\n- list\n")
        with pytest.raises(TestsYmlError):
            load_tests_yml(tmp_path)

    def test_schema_validation_failure_raises_with_preset_and_case_context(self, tmp_path):
        (tmp_path / "tests.yml").write_text(
            "schema: 1\ncases:\n  - name: no-seed-case\n    mode: txt2img\n"
        )
        with pytest.raises(TestsYmlError) as exc_info:
            load_tests_yml(tmp_path)
        message = str(exc_info.value)
        assert "no-seed-case" in message
        assert "tests.yml" in message

    def test_unsupported_schema_version_raises(self, tmp_path):
        (tmp_path / "tests.yml").write_text("schema: 99\ncases: []\n")
        with pytest.raises(TestsYmlError):
            load_tests_yml(tmp_path)


# --------------------------------------------------------------------------- #
# Placeholder sha256 / needs-model convention
# --------------------------------------------------------------------------- #


class TestPlaceholderConvention:
    def test_placeholder_constant_is_64_chars(self):
        assert len(PLACEHOLDER_SHA256) == 64
        assert set(PLACEHOLDER_SHA256) == {"0"}

    def test_placeholder_passes_schema_validation(self):
        """The schema itself doesn't special-case the placeholder (format
        checking is a linter concern, see linter.py) -- it's just a string
        that happens to be a convention. Confirm it loads fine here."""
        tests, errors = validate_tests_yml(valid_tests_data(
            cases=[valid_case(models={"diffusion_model": {"sha256": PLACEHOLDER_SHA256}}, tags=[NEEDS_MODEL_TAG])]
        ))
        assert errors == []
        assert tests.cases[0].models["diffusion_model"].sha256 == PLACEHOLDER_SHA256
