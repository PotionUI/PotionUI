"""Cgroup-v2-aware system RAM reading (src.platform.runtime.system_memory)."""

from collections import namedtuple

import pytest

from src.platform.runtime import system_memory

_FakeVirtualMemory = namedtuple("_FakeVirtualMemory", ["total", "available"])

_GB = 1024 ** 3


@pytest.fixture(autouse=True)
def _reset_warning():
    system_memory.reset_warning_for_tests()
    yield
    system_memory.reset_warning_for_tests()


def _fake_psutil(monkeypatch, total_gb: float, available_gb: float):
    monkeypatch.setattr(
        system_memory.psutil,
        "virtual_memory",
        lambda: _FakeVirtualMemory(total=int(total_gb * _GB), available=int(available_gb * _GB)),
    )


class TestNoCgroupLimit:
    def test_missing_cgroup_files_pass_through_psutil(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        mem = system_memory.get_system_memory(
            cgroup_max_path=tmp_path / "does_not_exist_max",
            cgroup_current_path=tmp_path / "does_not_exist_current",
        )
        assert mem.total == int(64.0 * _GB)
        assert mem.available == int(40.0 * _GB)

    def test_unlimited_cgroup_max_passes_through_psutil(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        max_path = tmp_path / "memory.max"
        max_path.write_text("max\n")
        current_path = tmp_path / "memory.current"
        current_path.write_text(str(int(5 * _GB)))

        mem = system_memory.get_system_memory(cgroup_max_path=max_path, cgroup_current_path=current_path)

        assert mem.total == int(64.0 * _GB)
        assert mem.available == int(40.0 * _GB)


class TestCgroupLimitPresent:
    def test_limit_below_host_ram_clamps_total_and_available(self, monkeypatch, tmp_path):
        # Host has 64GB; container is capped to 16GB via cgroup v2, 10GB already used.
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        max_path = tmp_path / "memory.max"
        max_path.write_text(str(16 * _GB))
        current_path = tmp_path / "memory.current"
        current_path.write_text(str(10 * _GB))

        mem = system_memory.get_system_memory(cgroup_max_path=max_path, cgroup_current_path=current_path)

        assert mem.total == 16 * _GB
        assert mem.available == 6 * _GB  # 16GB limit - 10GB current usage

    def test_available_never_exceeds_psutils_own_reading(self, monkeypatch, tmp_path):
        # cgroup says lots of headroom, but psutil (host-wide) reports less
        # available than the cgroup math alone would suggest — never claim
        # more available RAM than psutil itself sees.
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=2.0)
        max_path = tmp_path / "memory.max"
        max_path.write_text(str(32 * _GB))
        current_path = tmp_path / "memory.current"
        current_path.write_text(str(1 * _GB))

        mem = system_memory.get_system_memory(cgroup_max_path=max_path, cgroup_current_path=current_path)

        assert mem.total == 32 * _GB
        assert mem.available == 2 * _GB

    def test_missing_current_file_falls_back_to_psutil_available(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=12.0)
        max_path = tmp_path / "memory.max"
        max_path.write_text(str(16 * _GB))

        mem = system_memory.get_system_memory(
            cgroup_max_path=max_path,
            cgroup_current_path=tmp_path / "no_current_file",
        )

        assert mem.total == 16 * _GB
        assert mem.available == 12 * _GB

    def test_current_exceeding_limit_clamps_available_to_zero(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        max_path = tmp_path / "memory.max"
        max_path.write_text(str(4 * _GB))
        current_path = tmp_path / "memory.current"
        current_path.write_text(str(6 * _GB))  # over the limit (transient/rounding)

        mem = system_memory.get_system_memory(cgroup_max_path=max_path, cgroup_current_path=current_path)

        assert mem.total == 4 * _GB
        assert mem.available == 0

    def test_unreadable_limit_value_passes_through_psutil(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        max_path = tmp_path / "memory.max"
        max_path.write_text("not-a-number\n")

        mem = system_memory.get_system_memory(
            cgroup_max_path=max_path,
            cgroup_current_path=tmp_path / "memory.current",
        )

        assert mem.total == int(64.0 * _GB)
        assert mem.available == int(40.0 * _GB)

    def test_logs_loudly_exactly_once(self, monkeypatch, tmp_path, caplog):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        max_path = tmp_path / "memory.max"
        max_path.write_text(str(16 * _GB))
        current_path = tmp_path / "memory.current"
        current_path.write_text(str(10 * _GB))

        with caplog.at_level("WARNING", logger=system_memory.__name__):
            system_memory.get_system_memory(cgroup_max_path=max_path, cgroup_current_path=current_path)
            system_memory.get_system_memory(cgroup_max_path=max_path, cgroup_current_path=current_path)

        limit_warnings = [r for r in caplog.records if "cgroup v2 memory limit detected" in r.message]
        assert len(limit_warnings) == 1


class TestSystemMemoryProperties:
    def test_total_gb_and_available_gb_convert_bytes(self):
        mem = system_memory.SystemMemory(total=8 * _GB, available=4 * _GB)
        assert mem.total_gb == pytest.approx(8.0)
        assert mem.available_gb == pytest.approx(4.0)
