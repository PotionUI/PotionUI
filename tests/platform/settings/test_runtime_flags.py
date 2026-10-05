import inspect
import logging
import os
import re
from datetime import datetime
from pathlib import Path

import pytest

from src.platform.settings import runtime_flags
from src.platform.settings.records import Setting, SettingType, SettingValueType
from src.platform.settings.settings import Settings

_FRONTEND_DESCRIPTORS = (
    Path(__file__).resolve().parents[3]
    / "frontend" / "src" / "routes" / "admin" / "components" / "settings" / "runtimeSettings.ts"
)


class _FakeRepository:
    def __init__(self, values):
        self.rows = {
            key: Setting(
                id=key, key=key, value=Setting.serialize_value(value, SettingValueType(runtime_flags.RUNTIME_FLAG_BY_KEY[key].value_type)),
                value_type=SettingValueType(runtime_flags.RUNTIME_FLAG_BY_KEY[key].value_type),
                description=None, type=SettingType.SYSTEM, created_at=datetime.now(), updated_at=datetime.now(),
            )
            for key, value in values.items()
        }
        self.reads = 0

    def get_setting_by_key(self, key):
        self.reads += 1
        return self.rows.get(key)

    def update_setting_value(self, setting_id, value):
        self.rows[setting_id].value = value
        return True


@pytest.fixture(autouse=True)
def _clean_flags(monkeypatch):
    runtime_flags.reset_runtime_flags()
    monkeypatch.setattr(runtime_flags, "_settings", None)
    monkeypatch.setattr(runtime_flags, "_warned_env", set())
    yield


def test_every_flag_starts_at_its_default():
    for flag in runtime_flags.RUNTIME_FLAGS:
        assert runtime_flags.runtime_flag(flag.key) == flag.default


def test_unknown_key_is_a_loud_error():
    with pytest.raises(KeyError):
        runtime_flags.runtime_flag("native_fp8_matmull")


def test_app_flags_load_from_settings_once_and_reads_never_touch_the_db():
    repository = _FakeRepository({"profiling_enabled": True, "profiling_census": False})
    runtime_flags.configure_runtime_flags(Settings(repository))
    reads_after_configure = repository.reads

    for _ in range(1000):
        assert runtime_flags.runtime_flag("profiling_enabled") is True
        assert runtime_flags.runtime_flag("profiling_census") is False

    assert repository.reads == reads_after_configure


def test_saving_an_app_setting_updates_the_cache_without_a_reload():
    repository = _FakeRepository({"profiling_enabled": False, "profiling_census": True})
    settings = Settings(repository)
    runtime_flags.configure_runtime_flags(settings)
    assert runtime_flags.runtime_flag("profiling_enabled") is False

    assert settings.set_setting("profiling_enabled", True) is True

    assert runtime_flags.runtime_flag("profiling_enabled") is True
    assert repository.rows["profiling_enabled"].value == "true"


def test_bulk_save_path_updates_the_cache_and_ignores_unrelated_keys():
    runtime_flags.apply_saved_settings({"profiling_enabled": "true", "registration_policy": "open"})

    assert runtime_flags.runtime_flag("profiling_enabled") is True


def test_engine_keys_are_not_app_settings():
    runtime_flags.apply_saved_setting("native_fp8_matmul", True)

    assert runtime_flags.runtime_flag("native_fp8_matmul") is False
    assert runtime_flags.validate_runtime_flag("native_fp8_matmul", "nonsense") is None


def test_validation_rejects_bad_app_values():
    assert runtime_flags.validate_runtime_flag("profiling_enabled", "maybe")
    assert runtime_flags.validate_runtime_flag("profiling_enabled", True) is None


@pytest.mark.parametrize(
    ("key", "raw", "expected"),
    [
        ("native_fp8_matmul", "auto", True),
        ("native_fp8_matmul", "OFF", False),
        ("native_fp8_matmul", 1, True),
        ("native_fp8_quantize", " Force ", "force"),
        ("native_min_inference_memory_gb", "2.5", 2.5),
        ("native_ltx_decode_tile_px", 512.0, 512),
        ("native_attention_backend", "Auto", ""),
        ("native_attention_backend", "SAGE2", "sage2"),
    ],
)
def test_coercion(key, raw, expected):
    assert runtime_flags.coerce_runtime_flag(key, raw) == expected


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("native_fp8_matmul", "sometimes"),
        ("native_fp8_quantize", "always"),
        ("native_min_inference_memory_gb", -1),
        ("native_min_inference_memory_gb", "nan"),
        ("native_ltx_decode_tile_px", 1.5),
        ("native_ltx_decode_tile_px", True),
    ],
)
def test_coercion_rejects(key, raw):
    with pytest.raises(ValueError):
        runtime_flags.coerce_runtime_flag(key, raw)


def test_activating_a_backend_replaces_every_engine_value():
    runtime_flags.activate_engine_flags({"native_fp8_matmul": True, "native_min_inference_memory_gb": 4})
    assert runtime_flags.runtime_flag("native_fp8_matmul") is True
    assert runtime_flags.runtime_flag("native_min_inference_memory_gb") == 4.0

    runtime_flags.activate_engine_flags({})

    assert runtime_flags.runtime_flag("native_fp8_matmul") is False
    assert runtime_flags.runtime_flag("native_min_inference_memory_gb") == 1.0


def test_activating_engine_flags_leaves_app_flags_alone():
    runtime_flags.apply_saved_setting("profiling_enabled", True)

    runtime_flags.activate_engine_flags({"native_fp8_matmul": True, "profiling_enabled": False})

    assert runtime_flags.runtime_flag("profiling_enabled") is True


def test_engine_flag_values_drop_unknown_and_invalid_entries():
    resolved = runtime_flags.engine_flag_values({"native_fp8_matmul": "on", "native_fp8_quantize": "bogus", "x": 1})

    assert resolved["native_fp8_matmul"] is True
    assert resolved["native_fp8_quantize"] == "auto"
    assert "x" not in resolved
    assert set(resolved) == runtime_flags.ENGINE_FLAG_KEYS


def test_normalize_is_strict_for_api_input():
    with pytest.raises(ValueError):
        runtime_flags.normalize_engine_flags({"profiling_enabled": True})
    with pytest.raises(ValueError):
        runtime_flags.normalize_engine_flags({"native_fp8_matmul": "sometimes"})
    assert runtime_flags.normalize_engine_flags({"native_lora_fused": "off"}) == {"native_lora_fused": False}


def test_live_and_restart_classification():
    by_key = runtime_flags.RUNTIME_FLAG_BY_KEY
    assert by_key["native_sol_attn_backend"].applies == "restart"
    assert by_key["native_torch_compile"].applies == "next_load"
    assert by_key["native_fp8_quantize"].applies == "next_load"
    live = {f.key for f in runtime_flags.RUNTIME_FLAGS if f.applies == "live"}
    assert {"native_fp8_matmul", "native_nvfp4_matmul", "native_lora_fused", "native_stream_prefetch",
            "native_min_inference_memory_gb", "profiling_enabled", "profiling_census"} <= live


def test_only_profiling_is_app_wide():
    assert runtime_flags.APP_FLAG_KEYS == {"profiling_enabled", "profiling_census"}
    assert all(key.startswith("native_") for key in runtime_flags.ENGINE_FLAG_KEYS)


def test_seed_engine_flags_from_env_only_takes_set_and_usable_vars():
    seeded = runtime_flags.seed_engine_flags_from_env({
        "NATIVE_FP8_MATMUL": "on",
        "NATIVE_LTX_DIFFUSION_TILE_PX": "0",
        "NATIVE_FP8_QUANTIZE": "garbage",
        "POTIONUI_PROFILE": "1",
    })

    assert seeded == {"native_fp8_matmul": True}


def test_each_ignored_env_var_logs_once_with_its_new_home(caplog):
    environ = {"NATIVE_FP8_MATMUL": "on", "POTIONUI_PROFILE": "1", "UNRELATED": "x"}
    with caplog.at_level(logging.WARNING, logger=runtime_flags.logger.name):
        runtime_flags.warn_ignored_env_vars(environ)
        runtime_flags.warn_ignored_env_vars(environ)

    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 2
    assert any("NATIVE_FP8_MATMUL" in m and "Backends" in m and "Optimizations" in m for m in messages)
    assert any("POTIONUI_PROFILE" in m and "Diagnostics" in m for m in messages)


def test_vendor_ops_reads_the_bound_dict_and_never_the_environment(monkeypatch):
    from vendor.gpl.comfyui import ops

    source = inspect.getsource(ops)
    assert "os.environ" not in source
    assert "getenv" not in source

    monkeypatch.setattr(runtime_flags, "_vendor_bound", False)
    monkeypatch.setattr(ops, "_scaled_mm_supported_cache", True)
    monkeypatch.setattr(ops, "_nvfp4_scaled_mm_supported_cache", True)

    class _NoEnviron(dict):
        def __getitem__(self, key):
            raise AssertionError(f"read env {key}")

        def get(self, key, default=None):
            raise AssertionError(f"read env {key}")

        def __contains__(self, key):
            raise AssertionError(f"read env {key}")

    monkeypatch.setattr(os, "environ", _NoEnviron())

    runtime_flags.activate_engine_flags({"native_fp8_matmul": True, "native_lora_fused": False})
    assert ops._fp8_matmul_enabled() is True
    assert ops._nvfp4_matmul_enabled() is False
    assert ops._lora_fused_enabled() is False

    runtime_flags.activate_engine_flags({"native_nvfp4_matmul": True})
    assert ops._fp8_matmul_enabled() is False
    assert ops._nvfp4_matmul_enabled() is True
    assert ops._lora_fused_enabled() is True


def test_no_src_module_reads_a_moved_env_var():
    root = Path(__file__).resolve().parents[3]
    moved = [f.env_var for f in runtime_flags.RUNTIME_FLAGS if f.env_var]
    pattern = re.compile(r"(environ\.get|getenv|environ\[)\(?\s*['\"](" + "|".join(moved) + r")['\"]")
    offenders = []
    for base in ("src", "vendor"):
        for path in (root / base).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_frontend_descriptors_match_the_backend_registry():
    if not _FRONTEND_DESCRIPTORS.exists():
        pytest.skip("frontend descriptors not present")
    source = _FRONTEND_DESCRIPTORS.read_text(encoding="utf-8")
    keys = re.findall(r"^\s*key:\s*'([a-z0-9_]+)',", source, re.MULTILINE)
    applies = re.findall(r"^\s*applies:\s*'([a-z_]+)',", source, re.MULTILINE)
    scopes = re.findall(r"^\s*scope:\s*'([a-z]+)',", source, re.MULTILINE)
    assert len(keys) == len(applies) == len(scopes)
    described = dict(zip(keys, zip(applies, scopes)))
    expected = {f.key: (f.applies, f.scope) for f in runtime_flags.RUNTIME_FLAGS}
    assert described == expected
