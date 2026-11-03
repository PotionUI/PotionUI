import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.platform.runtime import cuda_allocator
from src.platform.runtime.cuda_allocator import (
    DRIVER_OOM_POLICY_ENV,
    RESTART_HINT,
    CudaAllocatorDamaged,
    allocator_damage,
    driver_oom_is_fatal,
    driver_oom_retries,
    expandable_segments_enabled,
    is_driver_oom,
    raise_if_allocator_damaged,
    record_driver_oom,
)
from src.platform.runtime.cuda_env import ALLOCATOR_CONF_VARS

DEVICE_NOT_READY = RuntimeError("CUDA driver error: device not ready")
LINUX = "6.8.0-85-generic"
WSL2 = "6.18.54.1-microsoft-standard-WSL2"


@pytest.fixture(autouse=True)
def clean_process(monkeypatch):
    monkeypatch.setattr(cuda_allocator, "_damage", None)
    for var in (*ALLOCATOR_CONF_VARS, DRIVER_OOM_POLICY_ENV):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def fake_torch(monkeypatch):
    settings = Mock()
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(memory=SimpleNamespace(_set_allocator_settings=settings))))
    return settings


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, False),
        ({"PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"}, True),
        ({"PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:False"}, False),
        ({"PYTORCH_CUDA_ALLOC_CONF": " max_split_size_mb : 128 , expandable_segments : True "}, True),
        ({"PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True,expandable_segments:False"}, False),
        ({"PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:true"}, False),
        ({"PYTORCH_ALLOC_CONF": "expandable_segments:True"}, True),
        ({"PYTORCH_CUDA_ALLOC_CONF": "", "PYTORCH_ALLOC_CONF": "expandable_segments:True"}, False),
        ({"PYTORCH_HIP_ALLOC_CONF": "expandable_segments:False", "PYTORCH_ALLOC_CONF": "expandable_segments:True"}, False),
    ],
)
def test_expandable_segments_follow_the_variable_torch_reads(env, expected):
    assert expandable_segments_enabled(env) is expected


@pytest.mark.parametrize(
    ("value", "release", "expected"),
    [
        (None, WSL2, True),
        (None, LINUX, False),
        ("", WSL2, True),
        ("anything", LINUX, False),
        ("fatal", LINUX, True),
        (" FATAL ", LINUX, True),
        ("retry", WSL2, False),
        (" Retry ", WSL2, False),
    ],
)
def test_driver_oom_policy(value, release, expected):
    env = {} if value is None else {DRIVER_OOM_POLICY_ENV: value}
    assert driver_oom_is_fatal(env, release) is expected


def test_retry_policy_retries_and_records_nothing(monkeypatch, fake_torch):
    monkeypatch.setenv(DRIVER_OOM_POLICY_ENV, "retry")
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    record_driver_oom(DEVICE_NOT_READY)

    assert driver_oom_retries()
    assert allocator_damage() is None
    fake_torch.assert_not_called()


@pytest.mark.parametrize(("wsl", "retried"), [(True, False), (False, True)])
def test_without_a_policy_only_wsl_stops_retrying(monkeypatch, fake_torch, wsl, retried):
    monkeypatch.setattr(cuda_allocator, "is_wsl", lambda kernel_release=None: wsl)
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    record_driver_oom(DEVICE_NOT_READY)

    assert driver_oom_retries() is retried
    assert (allocator_damage() is None) is retried


def test_native_windows_never_stops_retrying(monkeypatch, fake_torch):
    monkeypatch.delattr(cuda_allocator.os, "uname", raising=False)

    record_driver_oom(DEVICE_NOT_READY)

    assert not driver_oom_is_fatal()
    assert driver_oom_retries()
    assert allocator_damage() is None
    fake_torch.assert_not_called()


def test_fatal_policy_without_expandable_segments_retries(monkeypatch, fake_torch):
    monkeypatch.setenv(DRIVER_OOM_POLICY_ENV, "fatal")

    record_driver_oom(DEVICE_NOT_READY)

    assert driver_oom_retries()
    assert allocator_damage() is None
    fake_torch.assert_not_called()


def test_wsl_with_the_segments_turned_off_by_the_user_retries(monkeypatch, fake_torch):
    monkeypatch.setattr(cuda_allocator, "is_wsl", lambda kernel_release=None: True)
    monkeypatch.setenv("PYTORCH_ALLOC_CONF", "expandable_segments:False")

    record_driver_oom(DEVICE_NOT_READY)

    assert driver_oom_retries()
    assert allocator_damage() is None
    fake_torch.assert_not_called()


def test_fatal_policy_records_the_first_damage_and_stops_growing_segments(monkeypatch, fake_torch):
    monkeypatch.setenv(DRIVER_OOM_POLICY_ENV, "fatal")
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "max_split_size_mb:128, expandable_segments:True")

    record_driver_oom(DEVICE_NOT_READY)
    record_driver_oom(RuntimeError("CUDA driver error: out of memory"))

    assert not driver_oom_retries()
    assert allocator_damage() == "CUDA driver error: device not ready"
    fake_torch.assert_called_once_with("max_split_size_mb:128,expandable_segments:False")


def test_only_a_driver_oom_is_recorded_as_damage(monkeypatch, fake_torch):
    monkeypatch.setenv(DRIVER_OOM_POLICY_ENV, "fatal")
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    record_driver_oom(RuntimeError("CUDA driver error: invalid argument"))
    record_driver_oom(RuntimeError("wrapped: CUDA driver error: device not ready"))
    record_driver_oom(ValueError("CUDA driver error: device not ready"))

    assert allocator_damage() is None
    fake_torch.assert_not_called()


def test_a_failed_switch_still_records_the_damage(monkeypatch, fake_torch):
    monkeypatch.setenv(DRIVER_OOM_POLICY_ENV, "fatal")
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    fake_torch.side_effect = RuntimeError("cannot change")

    record_driver_oom(DEVICE_NOT_READY)

    assert allocator_damage() == "CUDA driver error: device not ready"


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (DEVICE_NOT_READY, True),
        (RuntimeError("CUDA driver error: out of memory"), True),
        (RuntimeError("CUDA driver error: invalid argument"), False),
        (RuntimeError("wrapped: CUDA driver error: device not ready"), False),
        (ValueError("CUDA driver error: device not ready"), False),
    ],
)
def test_is_driver_oom(exc, expected):
    assert is_driver_oom(exc) is expected


def test_raise_if_allocator_damaged(monkeypatch):
    raise_if_allocator_damaged()

    monkeypatch.setattr(cuda_allocator, "_damage", "CUDA driver error: device not ready")
    with pytest.raises(CudaAllocatorDamaged) as caught:
        raise_if_allocator_damaged()

    assert "CUDA driver error: device not ready" in str(caught.value)
    assert str(caught.value).endswith(RESTART_HINT)
    assert isinstance(caught.value, RuntimeError)
