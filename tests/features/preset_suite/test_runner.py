"""Tests for the pure preset-suite runner orchestration (fake client + resolver,
no real generation)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from src.features.preset_suite.models import CaseOutcome, FAIL, PASS, SKIP
from src.features.preset_suite.resolver import ResolveResult
from src.features.preset_suite.runner import PresetSuiteRunner


def _gradient_img(w=64, h=64):
    arr = np.tile(np.linspace(0, 255, w, dtype=np.uint8), (h, 1))
    return Image.fromarray(np.stack([arr, arr, arr], axis=-1))


def _black_img(w=64, h=64):
    return Image.fromarray(np.zeros((h, w, 3), dtype=np.uint8))


def _case(name="c", mode="txt2img", form=None, models=None, checks=None, tags=None, seed=1):
    return SimpleNamespace(
        name=name, mode=mode, form=form or {}, models=models or {},
        checks=checks or SimpleNamespace(min_outputs=1, resolution=None, not_black=True, max_seconds=None),
        tags=tags or [], seed=seed,
    )


class _Client:
    def __init__(self, outcome=None, can=(True, ""), raise_exc=None):
        self._outcome = outcome or CaseOutcome(status="completed", images=[_gradient_img()])
        self._can = can
        self._raise = raise_exc
        self.received_form = None

        self.received_prompt = None
        self.received_negative = None
        self.received_preset_id = None
        self.received_engine = None

    def can_run(self, preset_id, engine):
        self.received_engine = engine
        return self._can

    def run_case(self, preset_id, mode, form_data, *, prompt=None, negative_prompt=None, max_seconds=None):
        self.received_preset_id = preset_id
        self.received_form = dict(form_data)
        self.received_prompt = prompt
        self.received_negative = negative_prompt
        self.received_max_seconds = max_seconds
        if self._raise:
            raise self._raise
        return self._outcome


class _Resolver:
    def __init__(self, result):
        self._result = result

    def resolve(self, ref, *, model_type=None):
        return self._result


def _runner(client, resolver=None, presets_root="presets"):
    return PresetSuiteRunner(client, resolver or _Resolver(ResolveResult("/x")), presets_root=presets_root)


_PRESET_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def _preset_dir(tmp_path, engine="native", pid=_PRESET_ULID):
    d = tmp_path / "presets" / "native" / "Foo" / "std"
    d.mkdir(parents=True)
    body = f"engine: {engine}\n"
    if pid is not None:
        body += f"id: {pid}\n"
    (d / "preset.yml").write_text(body)
    return d


# --- verdict flows ------------------------------------------------------------


def test_completed_all_checks_pass_is_pass(tmp_path):
    d = _preset_dir(tmp_path)
    client = _Client(CaseOutcome(status="completed", images=[_gradient_img()]))
    r = _runner(client, presets_root=tmp_path / "presets")
    res = r.run_case(d, "native/Foo/std", _case())
    assert res.verdict == PASS and res.checks and all(c.passed for c in res.checks)


def test_completed_black_image_fails_not_black_check(tmp_path):
    d = _preset_dir(tmp_path)
    client = _Client(CaseOutcome(status="completed", images=[_black_img()]))
    r = _runner(client, presets_root=tmp_path / "presets")
    res = r.run_case(d, "native/Foo/std", _case())
    assert res.verdict == FAIL and "not_black" in res.reason


def test_generation_failure_is_fail_with_error(tmp_path):
    d = _preset_dir(tmp_path)
    # A NaN/Inf watchdog abort surfaces here as a failed generation + message.
    client = _Client(CaseOutcome(status="failed", error="NaN/Inf detected at step 7"))
    r = _runner(client, presets_root=tmp_path / "presets")
    res = r.run_case(d, "native/Foo/std", _case())
    assert res.verdict == FAIL and "NaN/Inf" in res.reason


def test_client_exception_is_case_fail_not_suite_crash(tmp_path):
    d = _preset_dir(tmp_path)
    client = _Client(raise_exc=RuntimeError("boom"))
    r = _runner(client, presets_root=tmp_path / "presets")
    res = r.run_case(d, "native/Foo/std", _case())
    assert res.verdict == FAIL and "boom" in res.reason


# --- gating + resolution ------------------------------------------------------


def test_engine_gating_skips_when_client_cannot_run(tmp_path):
    d = _preset_dir(tmp_path, engine="comfyui")
    client = _Client(can=(False, "no configured backend for engine 'comfyui'"))
    r = _runner(client, presets_root=tmp_path / "presets")
    res = r.run_case(d, "native/Foo/std", _case())
    assert res.verdict == SKIP and "comfyui" in res.reason


def test_unresolved_model_skips_case(tmp_path):
    d = _preset_dir(tmp_path)
    client = _Client()
    r = _runner(client, resolver=_Resolver(ResolveResult(None, reason="not found locally")),
                presets_root=tmp_path / "presets")
    res = r.run_case(d, "native/Foo/std", _case(models={"checkpoint": SimpleNamespace(sha256="x", hf=None)}))
    assert res.verdict == SKIP and "checkpoint" in res.reason and "not found locally" in res.reason


def test_resolved_model_injected_into_form(tmp_path):
    d = _preset_dir(tmp_path)
    client = _Client()
    r = _runner(client, resolver=_Resolver(ResolveResult("/models/sd.safetensors", source="db")),
                presets_root=tmp_path / "presets")
    r.run_case(d, "native/Foo/std", _case(form={"steps": 8},
                                          models={"checkpoint": SimpleNamespace(sha256="x", hf=None)}))
    assert client.received_form["checkpoint"] == "/models/sd.safetensors"
    assert client.received_form["steps"] == 8   # existing form values preserved


def test_prompt_is_lifted_from_form_onto_the_request(tmp_path):
    d = _preset_dir(tmp_path)
    client = _Client()
    r = _runner(client, presets_root=tmp_path / "presets")
    res = r.run_case(d, "native/Foo/std", _case(
        form={"prompt": "a red fox", "negative_prompt": "blurry", "steps": 20}))
    # The prompt reached the request (client) separately from form_data...
    assert client.received_prompt == "a red fox"
    assert client.received_negative == "blurry"
    # ...and was removed from the submitted form (it's not a preset form field).
    assert "prompt" not in client.received_form and "negative_prompt" not in client.received_form
    assert client.received_form["steps"] == 20
    # Captured on the outcome for the report/metadata.
    assert res.outcome.prompt == "a red fox" and res.outcome.negative_prompt == "blurry"


def test_no_prompt_in_form_lifts_nothing(tmp_path):
    d = _preset_dir(tmp_path)
    client = _Client()
    r = _runner(client, presets_root=tmp_path / "presets")
    r.run_case(d, "native/Foo/std", _case(form={"steps": 8}))
    assert client.received_prompt is None and client.received_negative is None


def test_request_uses_preset_yml_id_display_stays_path(tmp_path):
    # The request must carry the preset.yml ULID id, while reports/gallery keep the
    # human-readable directory path.
    d = _preset_dir(tmp_path, pid=_PRESET_ULID)
    client = _Client()
    r = _runner(client, presets_root=tmp_path / "presets")
    res = r.run_case(d, "native/Foo/std", _case())
    assert client.received_preset_id == _PRESET_ULID     # request → ULID
    assert res.preset_id == "native/Foo/std"             # display → path


def test_missing_preset_id_fails_with_clear_reason(tmp_path):
    d = _preset_dir(tmp_path, pid=None)                   # preset.yml has no id:
    client = _Client()
    r = _runner(client, presets_root=tmp_path / "presets")
    res = r.run_case(d, "native/Foo/std", _case())
    assert res.verdict == FAIL and "no 'id'" in res.reason
    assert client.received_preset_id is None             # never submitted
    assert res.preset_id == "native/Foo/std"             # display id preserved


# --- discovery + tag filtering ------------------------------------------------


def test_discovery_and_tag_filter(tmp_path):
    root = tmp_path / "presets"
    for pid in ("native/A/std", "native/B/std"):
        pd = root / pid
        pd.mkdir(parents=True)
        (pd / "tests.yml").write_text("x")
        (pd / "preset.yml").write_text("engine: native\n")

    def _loader(preset_dir):
        return SimpleNamespace(cases=[_case(name="fast1", tags=["fast"]), _case(name="slow1", tags=["slow"])])

    r = PresetSuiteRunner(_Client(), _Resolver(ResolveResult("/x")), presets_root=root, loader=_loader)
    all_cases = list(r.iter_cases())
    assert len(all_cases) == 4                    # 2 presets x 2 cases
    fast = list(r.iter_cases(tag="fast"))
    assert len(fast) == 2 and all(c.name == "fast1" for _d, _pid, c in fast)
    only_a = list(r.iter_cases(preset_filter="native/A/std"))
    assert len(only_a) == 2 and all(pid == "native/A/std" for _d, pid, _c in only_a)
