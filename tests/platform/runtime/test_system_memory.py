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


def _host(tmp_path, proc="0::/\n", files=None):
    root = tmp_path / "cgroup"
    root.mkdir()
    for name, text in (files or {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    proc_path = tmp_path / "proc-self-cgroup"
    proc_path.write_text(proc)
    return {"cgroup_root": root, "proc_cgroup_path": proc_path}


def _read(tmp_path, proc="0::/\n", files=None):
    return system_memory.get_system_memory(**_host(tmp_path, proc, files))


class TestNoCgroupLimit:
    def test_missing_cgroup_files_pass_through_psutil(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        mem = _read(tmp_path)
        assert mem.total == int(64.0 * _GB)
        assert mem.available == int(40.0 * _GB)

    def test_no_proc_file_and_no_mount_pass_through_psutil(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        mem = system_memory.get_system_memory(cgroup_root=tmp_path / "none", proc_cgroup_path=tmp_path / "none-proc")
        assert mem.total == int(64.0 * _GB)
        assert mem.available == int(40.0 * _GB)

    def test_unlimited_cgroup_max_passes_through_psutil(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        mem = _read(tmp_path, files={"memory.max": "max\n", "memory.current": str(int(5 * _GB))})

        assert mem.total == int(64.0 * _GB)
        assert mem.available == int(40.0 * _GB)

    def test_a_cgroup_v1_limit_is_not_read(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        mem = _read(
            tmp_path,
            proc="12:memory:/docker/abc\n",
            files={
                "memory/docker/abc/memory.limit_in_bytes": str(4 * _GB),
                "memory/docker/abc/memory.usage_in_bytes": str(3 * _GB),
            },
        )

        assert mem.total == int(64.0 * _GB)
        assert mem.available == int(40.0 * _GB)


class TestCgroupLimitPresent:
    def test_limit_below_host_ram_clamps_total_and_available(self, monkeypatch, tmp_path):
        # Host has 64GB; container is capped to 16GB via cgroup v2, 10GB already used.
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        mem = _read(tmp_path, files={"memory.max": str(16 * _GB), "memory.current": str(10 * _GB)})

        assert mem.total == 16 * _GB
        assert mem.available == 6 * _GB  # 16GB limit - 10GB current usage

    def test_available_never_exceeds_psutils_own_reading(self, monkeypatch, tmp_path):
        # cgroup says lots of headroom, but psutil (host-wide) reports less
        # available than the cgroup math alone would suggest — never claim
        # more available RAM than psutil itself sees.
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=2.0)
        mem = _read(tmp_path, files={"memory.max": str(32 * _GB), "memory.current": str(1 * _GB)})

        assert mem.total == 32 * _GB
        assert mem.available == 2 * _GB

    def test_missing_current_file_falls_back_to_psutil_available(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=12.0)
        mem = _read(tmp_path, files={"memory.max": str(16 * _GB)})

        assert mem.total == 16 * _GB
        assert mem.available == 12 * _GB

    def test_an_unreadable_usage_caps_psutils_free_reading_at_the_limit(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        mem = _read(tmp_path, files={"memory.max": str(16 * _GB)})

        assert mem.total == 16 * _GB
        assert mem.available == 16 * _GB

    def test_a_limit_above_the_host_ram_leaves_the_host_total(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        mem = _read(tmp_path, files={"memory.max": str(128 * _GB), "memory.current": str(1 * _GB)})

        assert mem.total == 64 * _GB
        assert mem.available == 40 * _GB

    def test_current_exceeding_limit_clamps_available_to_zero(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        mem = _read(tmp_path, files={"memory.max": str(4 * _GB), "memory.current": str(6 * _GB)})

        assert mem.total == 4 * _GB
        assert mem.available == 0

    def test_unreadable_limit_value_passes_through_psutil(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        mem = _read(tmp_path, files={"memory.max": "not-a-number\n"})

        assert mem.total == int(64.0 * _GB)
        assert mem.available == int(40.0 * _GB)

    def test_logs_loudly_exactly_once(self, monkeypatch, tmp_path, caplog):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        host = _host(tmp_path, files={"memory.max": str(16 * _GB), "memory.current": str(10 * _GB)})

        with caplog.at_level("WARNING", logger=system_memory.__name__):
            system_memory.get_system_memory(**host)
            system_memory.get_system_memory(**host)

        limit_warnings = [r for r in caplog.records if "cgroup v2 memory limit detected" in r.message]
        assert len(limit_warnings) == 1


class TestPageCache:
    def test_the_page_cache_of_a_file_read_in_a_two_gb_container_counts_as_free(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        stat = "anon 26034176\nfile 1259085824\nactive_file 28672\ninactive_file 1259057152\n"
        mem = _read(tmp_path, files={"memory.max": str(2 * _GB), "memory.current": "1285120000", "memory.stat": stat})

        assert mem.total == 2 * _GB
        assert mem.available == 2 * _GB - (1285120000 - 1259057152)

    def test_inactive_anon_pages_stay_used(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        stat = f"inactive_anon {1 * _GB}\nactive_anon 0\ninactive_file {2 * _GB}\nactive_file 0\n"
        mem = _read(tmp_path, files={"memory.max": str(8 * _GB), "memory.current": str(4 * _GB), "memory.stat": stat})

        assert mem.available == 6 * _GB

    def test_active_file_pages_stay_used(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        stat = f"active_file {3 * _GB}\ninactive_file 0\n"
        mem = _read(tmp_path, files={"memory.max": str(8 * _GB), "memory.current": str(4 * _GB), "memory.stat": stat})

        assert mem.available == 4 * _GB

    def test_each_level_takes_out_its_own_inactive_file(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        mem = _read(
            tmp_path,
            proc="0::/slice/scope\n",
            files={
                "slice/memory.max": str(8 * _GB),
                "slice/memory.current": str(7 * _GB),
                "slice/memory.stat": f"inactive_file {1 * _GB}\n",
                "slice/scope/memory.max": str(8 * _GB),
                "slice/scope/memory.current": str(5 * _GB),
                "slice/scope/memory.stat": f"inactive_file {4 * _GB}\n",
            },
        )

        assert mem.available == 2 * _GB

    @pytest.mark.parametrize("host_available_gb", [1.0, 40.0])
    @pytest.mark.parametrize("current_gb", [0.0, 3.0, 9.0])
    @pytest.mark.parametrize("inactive_gb", [0.0, 2.0, 12.0])
    def test_the_reading_stays_between_the_whole_usage_reading_and_the_limit_or_psutil(
        self, monkeypatch, tmp_path, host_available_gb, current_gb, inactive_gb
    ):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=host_available_gb)
        limit = 8 * _GB
        current = int(current_gb * _GB)
        host_available = int(host_available_gb * _GB)
        stat = f"inactive_file {int(inactive_gb * _GB)}\n"
        mem = _read(tmp_path, files={"memory.max": str(limit), "memory.current": str(current), "memory.stat": stat})

        whole_usage_reading = min(max(0, limit - current), host_available)
        assert whole_usage_reading <= mem.available <= min(limit, host_available)
        assert mem.total == limit

    @pytest.mark.parametrize(
        "stat",
        [None, "anon 1\nfile 2\n", "inactive_file many\n"],
        ids=["no_stat_file", "no_inactive_file_line", "not_a_number"],
    )
    def test_a_memory_stat_without_a_usable_inactive_file_counts_the_whole_usage_and_warns_once(
        self, monkeypatch, tmp_path, caplog, stat
    ):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        files = {"memory.max": str(8 * _GB), "memory.current": str(3 * _GB)}
        if stat is not None:
            files["memory.stat"] = stat
        host = _host(tmp_path, files=files)

        with caplog.at_level("WARNING", logger=system_memory.__name__):
            first = system_memory.get_system_memory(**host)
            second = system_memory.get_system_memory(**host)

        assert first.available == 5 * _GB
        assert second.available == 5 * _GB
        assert len([r for r in caplog.records if "no usable inactive_file" in r.message]) == 1

    def test_a_readable_inactive_file_logs_no_warning_about_it(self, monkeypatch, tmp_path, caplog):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        files = {"memory.max": str(8 * _GB), "memory.current": str(3 * _GB), "memory.stat": "inactive_file 0\n"}

        with caplog.at_level("WARNING", logger=system_memory.__name__):
            _read(tmp_path, files=files)

        assert not [r for r in caplog.records if "no usable inactive_file" in r.message]


class TestOwnCgroup:
    def test_a_container_in_the_hosts_cgroup_namespace_reads_the_limit_of_its_own_folder_not_the_mount_root(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=62.7, available_gb=59.8)
        own = "docker/ae445fa884de"
        mem = _read(
            tmp_path,
            proc=f"0::/{own}\n",
            files={f"{own}/memory.max": str(2 * _GB), f"{own}/memory.current": str(_GB // 2)},
        )

        assert mem.total == 2 * _GB
        assert mem.available == 2 * _GB - _GB // 2

    @pytest.mark.parametrize("tight_level", ["", "user.slice/", "user.slice/app.scope/"])
    def test_the_tightest_limit_wins_at_any_level_of_the_process_path(self, monkeypatch, tmp_path, tight_level):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        files = {}
        for level in ("", "user.slice/", "user.slice/app.scope/"):
            limit_gb, usage_gb = (6, 5) if level == tight_level else (8, 2)
            files[f"{level}memory.max"] = str(limit_gb * _GB)
            files[f"{level}memory.current"] = str(usage_gb * _GB)

        mem = _read(tmp_path, proc="0::/user.slice/app.scope\n", files=files)

        assert mem.total == 6 * _GB
        assert mem.available == 1 * _GB

    def test_the_smallest_limit_and_the_smallest_headroom_may_come_from_different_levels(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        mem = _read(
            tmp_path,
            proc="0::/slice/scope\n",
            files={
                "slice/memory.max": str(6 * _GB),
                "slice/memory.current": str(1 * _GB),
                "slice/scope/memory.max": str(8 * _GB),
                "slice/scope/memory.current": str(6 * _GB),
            },
        )

        assert mem.total == 6 * _GB
        assert mem.available == 2 * _GB

    def test_an_own_folder_missing_from_the_mount_reads_the_levels_it_can_see(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        mem = _read(
            tmp_path,
            proc="0::/kubepods/pod1/abc\n",
            files={"memory.max": str(4 * _GB), "memory.current": str(1 * _GB)},
        )

        assert mem.total == 4 * _GB
        assert mem.available == 3 * _GB

    def test_a_folder_under_a_parent_the_process_may_not_enter_reads_the_levels_it_can_see(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        host = _host(
            tmp_path,
            proc="0::/closed/own\n",
            files={"memory.max": str(4 * _GB), "memory.current": str(1 * _GB), "closed/memory.max": "max\n"},
        )
        closed = host["cgroup_root"] / "closed"
        real_stat = system_memory.Path.stat

        def stat_refused_under_closed(path, **kwargs):
            if closed in path.parents:
                raise PermissionError(str(path))
            return real_stat(path, **kwargs)

        monkeypatch.setattr(system_memory.Path, "stat", stat_refused_under_closed)
        mem = system_memory.get_system_memory(**host)

        assert mem.total == 4 * _GB
        assert mem.available == 3 * _GB

    def test_a_path_outside_the_namespace_reads_the_mount_root_only(self, monkeypatch, tmp_path):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        host = _host(tmp_path, proc="0::/../sibling\n", files={"memory.max": str(4 * _GB), "memory.current": str(1 * _GB)})
        sibling = host["cgroup_root"].parent / "sibling"
        sibling.mkdir()
        (sibling / "memory.max").write_text(str(1 * _GB))

        mem = system_memory.get_system_memory(**host)

        assert mem.total == 4 * _GB
        assert mem.available == 3 * _GB

    @pytest.mark.parametrize("proc", [None, "12:memory:/docker/abc\n"], ids=["no_proc_file", "no_unified_line"])
    def test_an_unknown_own_cgroup_reads_the_mount_root_only(self, monkeypatch, tmp_path, proc):
        _fake_psutil(monkeypatch, total_gb=64.0, available_gb=40.0)
        host = _host(
            tmp_path,
            proc=proc or "",
            files={
                "memory.max": str(4 * _GB),
                "memory.current": str(1 * _GB),
                "docker/abc/memory.max": str(1 * _GB),
            },
        )
        if proc is None:
            host["proc_cgroup_path"].unlink()

        mem = system_memory.get_system_memory(**host)

        assert mem.total == 4 * _GB
        assert mem.available == 3 * _GB


class TestSystemMemoryProperties:
    def test_total_gb_and_available_gb_convert_bytes(self):
        mem = system_memory.SystemMemory(total=8 * _GB, available=4 * _GB)
        assert mem.total_gb == pytest.approx(8.0)
        assert mem.available_gb == pytest.approx(4.0)
