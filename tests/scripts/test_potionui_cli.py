"""Unit tests for the `./potionui` bootstrap CLI (scripts/potionui_cli.py).

These tests exercise the doctor checks and process-supervision logic with
fakes — no real subprocess, network, or filesystem side effects beyond
tmp_path. The real `start`/`start-docker` end-to-end paths (actually booting
api.py + the Vite dev server, or a real `docker compose up --build`) are NOT
exercised here — see README.md's quickstart for the manual walkthrough.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts import potionui_cli as cli


# ---------------------------------------------------------------------------
# FakeProbe
# ---------------------------------------------------------------------------

class _FakeDiskUsage:
    def __init__(self, free_bytes):
        self.total = free_bytes * 2
        self.used = free_bytes
        self.free = free_bytes


class FakeProbe:
    """Duck-types RealProbe. `run` is keyed by either the full command tuple
    (for check functions that call the same resolved binary with different
    subcommands, e.g. `docker info` vs `docker compose version`) or, for
    backward compatibility, just the resolved binary path (cmd[0]) — checked
    in that order."""

    def __init__(
        self, which=None, run=None, ports_free=None, exists=None, writable=None,
        disk_free_gb=500.0, disk_usage_error=None,
    ):
        self._which = which or {}
        self._run = run or {}
        self._ports_free = ports_free or {}
        self._exists = exists or set()
        self._writable = writable if writable is not None else {}
        self._disk_free_gb = disk_free_gb
        self._disk_usage_error = disk_usage_error

    def which(self, name):
        return self._which.get(name)

    def run(self, cmd, timeout=10.0):
        key_tuple = tuple(cmd)
        if key_tuple in self._run:
            return self._run[key_tuple]
        key = cmd[0]
        if key not in self._run:
            raise AssertionError(f"FakeProbe.run: no stubbed result for {cmd!r}")
        return self._run[key]

    def port_free(self, port, host="127.0.0.1"):
        return self._ports_free.get(port, True)

    def path_exists(self, path):
        return Path(path) in self._exists or str(path) in self._exists

    def is_writable_dir(self, path):
        return self._writable.get(str(path), True)

    def disk_usage(self, path):
        if self._disk_usage_error is not None:
            raise self._disk_usage_error
        return _FakeDiskUsage(self._disk_free_gb * (1024 ** 3))


def cp(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# check_python / probe_python_candidates
# ---------------------------------------------------------------------------

def test_check_python_finds_qualifying_interpreter():
    probe = FakeProbe(
        which={"python3.12": "/usr/bin/python3.12"},
        run={"/usr/bin/python3.12": cp(stdout="3.12.2\n")},
    )
    result = cli.check_python(probe)
    assert result.severity == cli.Severity.OK
    assert result.blocking is True
    assert "3.12.2" in result.message


def test_check_python_skips_too_old_interpreter():
    probe = FakeProbe(
        which={"python3": "/usr/bin/python3"},
        run={"/usr/bin/python3": cp(stdout="3.10.4\n")},
    )
    result = cli.check_python(probe)
    assert result.severity == cli.Severity.ERROR
    assert result.code == "PY312"
    assert result.repair is not None


def test_check_python_none_on_path():
    probe = FakeProbe()
    result = cli.check_python(probe)
    assert result.severity == cli.Severity.ERROR
    assert result.blocking is True


def test_check_python_prefers_newer_candidate_order():
    probe = FakeProbe(
        which={"python3.13": "/usr/bin/python3.13", "python3.12": "/usr/bin/python3.12"},
        run={
            "/usr/bin/python3.13": cp(stdout="3.13.0\n"),
            "/usr/bin/python3.12": cp(stdout="3.12.2\n"),
        },
    )
    result = cli.check_python(probe)
    assert "3.13.0" in result.message


# ---------------------------------------------------------------------------
# check_venv / check_backend_deps
# ---------------------------------------------------------------------------

def test_check_venv_missing(tmp_path):
    probe = FakeProbe()
    result = cli.check_venv(probe, tmp_path)
    assert result.severity == cli.Severity.WARNING
    assert result.blocking is False


def test_check_venv_present(tmp_path):
    venv_python = tmp_path / "venv" / "bin" / "python"
    probe = FakeProbe(exists={venv_python})
    result = cli.check_venv(probe, tmp_path)
    assert result.severity == cli.Severity.OK


def test_check_backend_deps_no_venv(tmp_path):
    probe = FakeProbe()
    result = cli.check_backend_deps(probe, tmp_path)
    assert result.severity == cli.Severity.WARNING
    assert "virtualenv" in result.message.lower()


def test_check_backend_deps_importable(tmp_path):
    venv_python = tmp_path / "venv" / "bin" / "python"
    probe = FakeProbe(exists={venv_python}, run={str(venv_python): cp(returncode=0)})
    result = cli.check_backend_deps(probe, tmp_path)
    assert result.severity == cli.Severity.OK


def test_check_backend_deps_missing(tmp_path):
    venv_python = tmp_path / "venv" / "bin" / "python"
    probe = FakeProbe(exists={venv_python}, run={str(venv_python): cp(returncode=1, stderr="ModuleNotFoundError")})
    result = cli.check_backend_deps(probe, tmp_path)
    assert result.severity == cli.Severity.WARNING
    assert result.repair


def test_check_backend_deps_repair_mentions_constraints(tmp_path):
    venv_python = tmp_path / "venv" / "bin" / "python"
    probe = FakeProbe(exists={venv_python}, run={str(venv_python): cp(returncode=1, stderr="ModuleNotFoundError")})
    result = cli.check_backend_deps(probe, tmp_path)
    assert "-c constraints.txt" in result.repair


# ---------------------------------------------------------------------------
# backend_pip_install_args
# ---------------------------------------------------------------------------

def test_backend_pip_install_args_adds_constraints_when_present(tmp_path):
    (tmp_path / "constraints.txt").write_text("torch==2.12.1\n")
    assert cli.backend_pip_install_args(tmp_path) == [
        "install", "-r", "requirements.txt", "-c", "constraints.txt",
    ]


def test_backend_pip_install_args_omits_constraints_when_absent(tmp_path):
    assert cli.backend_pip_install_args(tmp_path) == ["install", "-r", "requirements.txt"]


def test_backend_pip_install_args_no_gpu_uses_cpu_requirements_and_index():
    assert cli.backend_pip_install_args_no_gpu() == [
        "install", "-r", "requirements-cpu.txt",
        "--extra-index-url", "https://download.pytorch.org/whl/cpu",
    ]
    # never constraints.txt — its CUDA-13 transitive pins are unsatisfiable
    # without CUDA.
    assert "constraints.txt" not in cli.backend_pip_install_args_no_gpu()


# ---------------------------------------------------------------------------
# check_node / check_npm
# ---------------------------------------------------------------------------

def test_check_node_missing():
    probe = FakeProbe()
    result = cli.check_node(probe)
    assert result.severity == cli.Severity.ERROR
    assert result.blocking is True


def test_check_node_too_old():
    probe = FakeProbe(which={"node": "/usr/bin/node"}, run={"/usr/bin/node": cp(stdout="v16.20.0\n")})
    result = cli.check_node(probe)
    assert result.severity == cli.Severity.ERROR
    assert "16" in result.message


def test_check_node_ok():
    probe = FakeProbe(which={"node": "/usr/bin/node"}, run={"/usr/bin/node": cp(stdout="v20.20.0\n")})
    result = cli.check_node(probe)
    assert result.severity == cli.Severity.OK


def test_check_npm_missing():
    probe = FakeProbe()
    result = cli.check_npm(probe)
    assert result.severity == cli.Severity.ERROR


def test_check_npm_ok():
    probe = FakeProbe(which={"npm": "/usr/bin/npm"}, run={"/usr/bin/npm": cp(stdout="10.8.2\n")})
    result = cli.check_npm(probe)
    assert result.severity == cli.Severity.OK


# ---------------------------------------------------------------------------
# check_frontend_deps
# ---------------------------------------------------------------------------

def test_check_frontend_deps_missing(tmp_path):
    probe = FakeProbe()
    result = cli.check_frontend_deps(probe, tmp_path)
    assert result.severity == cli.Severity.WARNING
    assert "not installed" in result.message


def test_check_frontend_deps_incomplete(tmp_path):
    node_modules = tmp_path / "frontend" / "node_modules"
    probe = FakeProbe(exists={node_modules})
    result = cli.check_frontend_deps(probe, tmp_path)
    assert result.severity == cli.Severity.WARNING
    assert "incomplete" in result.message


def test_check_frontend_deps_present(tmp_path):
    vite_bin = tmp_path / "frontend" / "node_modules" / ".bin" / "vite"
    probe = FakeProbe(exists={vite_bin})
    result = cli.check_frontend_deps(probe, tmp_path)
    assert result.severity == cli.Severity.OK


# ---------------------------------------------------------------------------
# check_gpu
# ---------------------------------------------------------------------------

def test_check_gpu_absent():
    probe = FakeProbe()
    result = cli.check_gpu(probe)
    assert result.severity == cli.Severity.WARNING
    assert result.blocking is False


def test_check_gpu_present():
    probe = FakeProbe(
        which={"nvidia-smi": "/usr/bin/nvidia-smi"},
        run={"/usr/bin/nvidia-smi": cp(stdout="NVIDIA GeForce RTX 5090, 570.00\n")},
    )
    result = cli.check_gpu(probe)
    assert result.severity == cli.Severity.OK
    assert "RTX 5090" in result.message


def test_check_gpu_present_but_empty_output():
    probe = FakeProbe(which={"nvidia-smi": "/usr/bin/nvidia-smi"}, run={"/usr/bin/nvidia-smi": cp(stdout="")})
    result = cli.check_gpu(probe)
    assert result.severity == cli.Severity.WARNING


def test_check_gpu_absent_no_gpu_flag_off_is_a_warning():
    probe = FakeProbe()
    result = cli.check_gpu(probe, no_gpu=False)
    assert result.severity == cli.Severity.WARNING


def test_check_gpu_absent_no_gpu_flag_on_is_informational():
    probe = FakeProbe()
    result = cli.check_gpu(probe, no_gpu=True)
    assert result.severity == cli.Severity.INFO
    assert result.blocking is False
    assert "--no-gpu" in result.message


# ---------------------------------------------------------------------------
# check_disk
# ---------------------------------------------------------------------------

def test_check_disk_ok(tmp_path):
    probe = FakeProbe(disk_free_gb=100.0)
    result = cli.check_disk(probe, tmp_path)
    assert result.severity == cli.Severity.OK
    assert "100.0 GB free" in result.message
    assert result.blocking is False


def test_check_disk_warns_below_warn_threshold(tmp_path):
    probe = FakeProbe(disk_free_gb=10.0)
    result = cli.check_disk(probe, tmp_path)
    assert result.severity == cli.Severity.WARNING
    assert "starter model checkpoint" in result.message
    assert result.blocking is False


def test_check_disk_fails_below_fail_threshold(tmp_path):
    probe = FakeProbe(disk_free_gb=2.0)
    result = cli.check_disk(probe, tmp_path)
    assert result.severity == cli.Severity.ERROR
    assert "not enough space" in result.message
    assert result.blocking is True


def test_check_disk_handles_probe_error(tmp_path):
    probe = FakeProbe(disk_usage_error=OSError("no such device"))
    result = cli.check_disk(probe, tmp_path)
    assert result.severity == cli.Severity.WARNING
    assert result.blocking is False


# ---------------------------------------------------------------------------
# check_port
# ---------------------------------------------------------------------------

def test_check_port_free():
    probe = FakeProbe(ports_free={8005: True})
    result = cli.check_port(probe, 8005, "PORT_8005", "backend")
    assert result.severity == cli.Severity.OK
    assert result.blocking is True


def test_check_port_occupied():
    probe = FakeProbe(ports_free={8005: False})
    result = cli.check_port(probe, 8005, "PORT_8005", "backend")
    assert result.severity == cli.Severity.ERROR
    assert "in use" in result.message
    assert result.repair


# ---------------------------------------------------------------------------
# check_storage / check_env_file
# ---------------------------------------------------------------------------

def test_check_storage_writable(tmp_path):
    probe = FakeProbe(writable={str(tmp_path / "storage"): True})
    result = cli.check_storage(probe, tmp_path)
    assert result.severity == cli.Severity.OK


def test_check_storage_not_writable(tmp_path):
    probe = FakeProbe(writable={str(tmp_path / "storage"): False})
    result = cli.check_storage(probe, tmp_path)
    assert result.severity == cli.Severity.ERROR
    assert result.blocking is True


def test_check_env_file_present(tmp_path):
    env_path = tmp_path / ".env"
    probe = FakeProbe(exists={env_path})
    result = cli.check_env_file(probe, tmp_path)
    assert result.severity == cli.Severity.INFO


def test_check_env_file_absent(tmp_path):
    probe = FakeProbe()
    result = cli.check_env_file(probe, tmp_path)
    assert result.severity == cli.Severity.INFO
    assert result.blocking is False


# ---------------------------------------------------------------------------
# run_doctor: registry shape + blocking flags
# ---------------------------------------------------------------------------

def test_run_doctor_returns_all_codes_with_expected_blocking(tmp_path):
    probe = FakeProbe(ports_free={8005: True, 3001: True})
    results = cli.run_doctor(probe, tmp_path, 8005, 3001)
    codes = {r.code: r for r in results}
    assert set(codes) == {
        "PY312", "VENV", "BACKEND_DEPS", "NODE", "NPM", "FRONTEND_DEPS",
        "GPU", "DISK", "PORT_8005", "PORT_3001", "STORAGE", "ENV_FILE",
    }
    blocking_codes = {code for code, r in codes.items() if r.blocking}
    assert blocking_codes == {"PY312", "NODE", "NPM", "PORT_8005", "PORT_3001", "STORAGE"}


def test_run_doctor_passes_no_gpu_through_to_gpu_row(tmp_path):
    probe = FakeProbe(ports_free={8005: True, 3001: True})
    results = cli.run_doctor(probe, tmp_path, 8005, 3001, no_gpu=True)
    gpu_row = next(r for r in results if r.code == "GPU")
    assert gpu_row.severity == cli.Severity.INFO


def test_check_result_to_row_json_shape():
    result = cli.CheckResult("FOO", cli.Severity.ERROR, "bad", repair="fix it", blocking=True)
    row = result.to_row()
    assert row == {"code": "FOO", "severity": "error", "message": "bad", "repair": "fix it"}
    json.dumps(row)  # must be JSON-serializable


# ---------------------------------------------------------------------------
# guess_failed_requirement
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("ERROR: Could not find a version that satisfies the requirement torch==99.0\n", "torch==99.0"),
        ("ERROR: No matching distribution found for not-a-real-package\n", "not-a-real-package"),
        ("Failed building wheel for kornia\n", "kornia"),
        ("npm ERR! 404  'left-pad-nope' is not in this registry.\n", "left-pad-nope"),
        ("some unrelated noise\nwith no known pattern\n", None),
    ],
)
def test_guess_failed_requirement(text, expected):
    assert cli.guess_failed_requirement(text) == expected


# ---------------------------------------------------------------------------
# wait_for_ready — fake clock/sleep, no real sleeping
# ---------------------------------------------------------------------------

def test_wait_for_ready_succeeds_eventually():
    calls = {"n": 0}

    def check():
        calls["n"] += 1
        return calls["n"] >= 3

    fake_time = {"t": 0.0}

    def clock():
        return fake_time["t"]

    def sleeper(interval):
        fake_time["t"] += interval

    assert cli.wait_for_ready(check, timeout=10.0, interval=1.0, clock=clock, sleeper=sleeper) is True
    assert calls["n"] == 3


def test_wait_for_ready_times_out():
    fake_time = {"t": 0.0}

    def clock():
        return fake_time["t"]

    def sleeper(interval):
        fake_time["t"] += interval

    assert cli.wait_for_ready(lambda: False, timeout=3.0, interval=1.0, clock=clock, sleeper=sleeper) is False


# ---------------------------------------------------------------------------
# pid_alive / stop_process
# ---------------------------------------------------------------------------

def test_pid_alive_true_for_self():
    import os
    assert cli.pid_alive(os.getpid()) is True


def test_pid_alive_false_for_nonexistent_pid():
    # PID 2**30 is astronomically unlikely to exist on any real system.
    assert cli.pid_alive(2**30) is False


def test_stop_process_noop_if_already_dead():
    assert cli.stop_process(2**30) is True


# ---------------------------------------------------------------------------
# state file read/write/clear
# ---------------------------------------------------------------------------

def test_state_roundtrip(tmp_path):
    state_file = tmp_path / "state.json"
    assert cli.load_state(state_file) is None
    state = {"backend": {"pid": 123, "port": 8005, "log": "x", "started_at": "now"}}
    cli.save_state(state, state_file)
    assert cli.load_state(state_file) == state
    cli.clear_state(state_file)
    assert cli.load_state(state_file) is None
    # clearing twice must not raise
    cli.clear_state(state_file)


def test_load_state_corrupt_json_returns_none(tmp_path):
    state_file = tmp_path / "state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("{not valid json")
    assert cli.load_state(state_file) is None


# ---------------------------------------------------------------------------
# no-gpu profile marker (.runtime/no_gpu_profile)
# ---------------------------------------------------------------------------

def test_no_gpu_profile_inactive_when_marker_absent(tmp_path):
    marker = tmp_path / "no_gpu_profile"
    assert cli.no_gpu_profile_active(marker) is False


def test_no_gpu_profile_active_after_marking(tmp_path):
    marker = tmp_path / "no_gpu_profile"
    cli.mark_no_gpu_profile(marker)
    assert cli.no_gpu_profile_active(marker) is True
    data = json.loads(marker.read_text())
    assert data["no_gpu"] is True
    assert "marked_at" in data


# ---------------------------------------------------------------------------
# start_supervised: spawn is injectable
# ---------------------------------------------------------------------------

class FakePopen:
    def __init__(self, pid):
        self.pid = pid


def test_start_supervised_records_pid_and_port(tmp_path):
    def fake_spawn(cmd, cwd, env, log_path):
        return FakePopen(pid=4242)

    info = cli.start_supervised("backend", ["echo", "hi"], tmp_path, {}, 8005, spawn=fake_spawn)
    assert info["pid"] == 4242
    assert info["port"] == 8005
    assert info["log"].endswith("backend.log")
    assert "started_at" in info


# ---------------------------------------------------------------------------
# doctor report formatting
# ---------------------------------------------------------------------------

def test_print_doctor_report_json(capsys):
    results = [cli.CheckResult("X", cli.Severity.OK, "fine", blocking=False)]
    cli.print_doctor_report(results, as_json=True)
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed == [{"code": "X", "severity": "ok", "message": "fine", "repair": None}]


def test_print_doctor_report_human_shows_repair_for_failures(capsys):
    results = [
        cli.CheckResult("X", cli.Severity.OK, "fine"),
        cli.CheckResult("Y", cli.Severity.ERROR, "broken", repair="fix Y"),
    ]
    cli.print_doctor_report(results, as_json=False)
    out = capsys.readouterr().out
    assert "fine" in out
    assert "broken" in out
    assert "fix Y" in out


# ---------------------------------------------------------------------------
# claim-token hint (read-only peek at storage/setup_claim_token)
# ---------------------------------------------------------------------------

def test_print_claim_token_hint_prints_when_token_present(tmp_path, capsys):
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "setup_claim_token").write_text("abc123\n", encoding="utf-8")
    cli._print_claim_token_hint(tmp_path, 3001)
    out = capsys.readouterr().out
    assert "abc123" in out
    assert "http://localhost:3001" in out


def test_print_claim_token_hint_silent_when_absent(tmp_path, capsys):
    cli._print_claim_token_hint(tmp_path, 3001)
    out = capsys.readouterr().out
    assert out == ""


def test_read_claim_token_none_when_file_empty(tmp_path):
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "setup_claim_token").write_text("   \n", encoding="utf-8")
    assert cli._read_claim_token(tmp_path) is None


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def test_build_parser_defaults():
    parser = cli.build_parser()
    args = parser.parse_args(["doctor"])
    assert args.backend_port == cli.DEFAULT_BACKEND_PORT
    assert args.frontend_port == cli.DEFAULT_FRONTEND_PORT
    assert args.json is False


def test_build_parser_start_timeout_default():
    parser = cli.build_parser()
    args = parser.parse_args(["start"])
    assert args.timeout == cli.DEFAULT_START_TIMEOUT


def test_build_parser_requires_subcommand():
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_build_parser_port_overrides():
    parser = cli.build_parser()
    args = parser.parse_args(["--backend-port", "9000", "--frontend-port", "4000", "doctor"])
    assert args.backend_port == 9000
    assert args.frontend_port == 4000


def test_build_parser_start_no_gpu_defaults_false():
    parser = cli.build_parser()
    args = parser.parse_args(["start"])
    assert args.no_gpu is False


def test_build_parser_start_no_gpu_flag():
    parser = cli.build_parser()
    args = parser.parse_args(["start", "--no-gpu"])
    assert args.no_gpu is True


def test_build_parser_doctor_no_gpu_flag():
    parser = cli.build_parser()
    args = parser.parse_args(["doctor", "--no-gpu"])
    assert args.no_gpu is True


def test_build_parser_start_docker_defaults_no_extra_args():
    parser = cli.build_parser()
    args = parser.parse_args(["start-docker"])
    assert args.compose_args == []


def test_build_parser_start_docker_passes_through_service_name():
    parser = cli.build_parser()
    args = parser.parse_args(["start-docker", "rig-mid", "--force-recreate"])
    assert args.compose_args == ["rig-mid", "--force-recreate"]


# ---------------------------------------------------------------------------
# cmd_doctor / cmd_status / cmd_stop end-to-end against fakes/monkeypatch
# ---------------------------------------------------------------------------

def test_cmd_doctor_exit_code_reflects_errors(monkeypatch, capsys):
    monkeypatch.setattr(cli, "RealProbe", lambda: FakeProbe())  # nothing present -> several errors
    parser = cli.build_parser()
    args = parser.parse_args(["doctor", "--json"])
    code = cli.cmd_doctor(args)
    assert code == 1
    rows = json.loads(capsys.readouterr().out)
    assert any(r["severity"] == "error" for r in rows)


def test_cmd_status_not_running(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(cli, "load_state", lambda state_file=cli.STATE_FILE: None)
    parser = cli.build_parser()
    args = parser.parse_args(["status"])
    code = cli.cmd_status(args)
    assert code == 1
    assert "not running" in capsys.readouterr().out.lower()


def test_cmd_stop_noop_when_nothing_running(monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_state", lambda state_file=cli.STATE_FILE: None)
    parser = cli.build_parser()
    args = parser.parse_args(["stop"])
    code = cli.cmd_stop(args)
    assert code == 0
    assert "not running" in capsys.readouterr().out.lower()


def test_cmd_stop_stops_recorded_processes(monkeypatch, capsys):
    state = {
        "backend": {"pid": 111, "port": 8005, "log": "backend.log", "started_at": "now"},
        "frontend": {"pid": 222, "port": 3001, "log": "frontend.log", "started_at": "now"},
    }
    stopped = []

    monkeypatch.setattr(cli, "load_state", lambda state_file=cli.STATE_FILE: state)
    monkeypatch.setattr(cli, "clear_state", lambda state_file=cli.STATE_FILE: stopped.append("cleared"))

    def fake_stop(pid, **kwargs):
        stopped.append(pid)
        return True

    monkeypatch.setattr(cli, "stop_process", fake_stop)
    parser = cli.build_parser()
    args = parser.parse_args(["stop"])
    code = cli.cmd_stop(args)
    assert code == 0
    # frontend is stopped before backend
    assert stopped == [222, 111, "cleared"]


# ---------------------------------------------------------------------------
# Docker preflight checks (check_docker / check_docker_compose /
# check_nvidia_container_toolkit) and cmd_start_docker
# ---------------------------------------------------------------------------

def test_check_docker_missing():
    probe = FakeProbe()
    result = cli.check_docker(probe)
    assert result.severity == cli.Severity.ERROR
    assert result.blocking is True


def test_check_docker_daemon_not_responding():
    probe = FakeProbe(
        which={"docker": "/usr/bin/docker"},
        run={("/usr/bin/docker", "info"): cp(returncode=1, stderr="Cannot connect to the Docker daemon")},
    )
    result = cli.check_docker(probe)
    assert result.severity == cli.Severity.ERROR
    assert "daemon" in result.message.lower()


def test_check_docker_ok():
    probe = FakeProbe(
        which={"docker": "/usr/bin/docker"},
        run={("/usr/bin/docker", "info"): cp(returncode=0, stdout="Server Version: 27.0.3")},
    )
    result = cli.check_docker(probe)
    assert result.severity == cli.Severity.OK


def test_check_docker_compose_missing_docker():
    probe = FakeProbe()
    result = cli.check_docker_compose(probe)
    assert result.severity == cli.Severity.ERROR
    assert result.blocking is True


def test_check_docker_compose_v1_or_absent():
    probe = FakeProbe(
        which={"docker": "/usr/bin/docker"},
        run={("/usr/bin/docker", "compose", "version"): cp(returncode=1, stderr="docker: 'compose' is not a docker command")},
    )
    result = cli.check_docker_compose(probe)
    assert result.severity == cli.Severity.ERROR


def test_check_docker_compose_ok():
    probe = FakeProbe(
        which={"docker": "/usr/bin/docker"},
        run={("/usr/bin/docker", "compose", "version"): cp(returncode=0, stdout="Docker Compose version v2.29.0")},
    )
    result = cli.check_docker_compose(probe)
    assert result.severity == cli.Severity.OK
    assert "v2.29.0" in result.message


def test_check_nvidia_container_toolkit_via_runtime_binary():
    probe = FakeProbe(which={"nvidia-container-runtime": "/usr/bin/nvidia-container-runtime"})
    result = cli.check_nvidia_container_toolkit(probe)
    assert result.severity == cli.Severity.OK


def test_check_nvidia_container_toolkit_via_docker_runtimes():
    probe = FakeProbe(
        which={"docker": "/usr/bin/docker"},
        run={("/usr/bin/docker", "info"): cp(returncode=0, stdout="Runtimes: nvidia runc\n")},
    )
    result = cli.check_nvidia_container_toolkit(probe)
    assert result.severity == cli.Severity.OK


def test_check_nvidia_container_toolkit_absent():
    probe = FakeProbe(
        which={"docker": "/usr/bin/docker"},
        run={("/usr/bin/docker", "info"): cp(returncode=0, stdout="Runtimes: runc\n")},
    )
    result = cli.check_nvidia_container_toolkit(probe)
    assert result.severity == cli.Severity.ERROR
    assert result.blocking is True
    assert result.repair


def test_run_docker_preflight_returns_all_codes():
    probe = FakeProbe(
        which={"docker": "/usr/bin/docker", "nvidia-container-runtime": "/usr/bin/nvidia-container-runtime"},
        run={
            ("/usr/bin/docker", "info"): cp(returncode=0),
            ("/usr/bin/docker", "compose", "version"): cp(returncode=0, stdout="v2.29.0"),
        },
    )
    results = cli.run_docker_preflight(probe)
    assert {r.code for r in results} == {"DOCKER", "DOCKER_COMPOSE", "NVIDIA_CONTAINER_TOOLKIT"}
    assert all(r.severity == cli.Severity.OK for r in results)


def test_cmd_start_docker_blocks_on_preflight_failure(monkeypatch, capsys):
    monkeypatch.setattr(cli, "RealProbe", lambda: FakeProbe())  # nothing present -> all fail
    called = []
    monkeypatch.setattr(cli, "run_foreground", lambda cmd, cwd: called.append(cmd))
    parser = cli.build_parser()
    args = parser.parse_args(["start-docker"])
    code = cli.cmd_start_docker(args)
    assert code == 1
    assert called == []
    assert "cannot start" in capsys.readouterr().out.lower()


def test_cmd_start_docker_execs_compose_up_when_preflight_passes(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    docker_path = "/usr/bin/docker"
    probe = FakeProbe(
        which={"docker": docker_path, "nvidia-container-runtime": "/usr/bin/nvidia-container-runtime"},
        run={
            (docker_path, "info"): cp(returncode=0),
            (docker_path, "compose", "version"): cp(returncode=0, stdout="v2.29.0"),
        },
    )
    monkeypatch.setattr(cli, "RealProbe", lambda: probe)

    captured = {}

    def fake_run_foreground(cmd, cwd):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        return 0

    monkeypatch.setattr(cli, "run_foreground", fake_run_foreground)
    parser = cli.build_parser()
    args = parser.parse_args(["start-docker", "rig-mid"])
    code = cli.cmd_start_docker(args)

    assert code == 0
    assert captured["cmd"] == [
        "docker", "compose", "-f", str(tmp_path / "docker" / "docker-compose.yml"),
        "up", "--build", "rig-mid",
    ]
    assert captured["cwd"] == tmp_path


# ---------------------------------------------------------------------------
# frontend bind/probe host must never diverge (regression: a resolver that
# answers ::1 before 127.0.0.1 for the bare name "localhost" made Vite bind
# IPv6-only while `start`'s readiness probe hard-coded 127.0.0.1, so
# FRONTEND_START_TIMEOUT fired even though Vite was up and healthy)
# ---------------------------------------------------------------------------

def test_frontend_bind_host_is_a_numeric_loopback_address():
    # Not the DNS name "localhost" — resolvable to either 127.0.0.1 or ::1
    # depending on host configuration, which is exactly what caused the bug.
    assert cli.FRONTEND_BIND_HOST == "127.0.0.1"


def test_cmd_start_frontend_env_and_probe_url_share_the_same_host(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli, "load_state", lambda state_file=cli.STATE_FILE: None)
    monkeypatch.setattr(cli, "run_doctor", lambda *a, **k: [])
    monkeypatch.setattr(cli, "probe_python_candidates", lambda probe: ("python3.12", (3, 12)))
    # Isolate from the real .runtime/no_gpu_profile marker (NO_GPU_PROFILE_FILE
    # binds against the real REPO_ROOT at import time, not the monkeypatched one).
    monkeypatch.setattr(cli, "no_gpu_profile_active", lambda marker_file=cli.NO_GPU_PROFILE_FILE: False)

    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    vite_bin = tmp_path / "frontend" / "node_modules" / ".bin" / "vite"
    vite_bin.parent.mkdir(parents=True)
    vite_bin.touch()

    # Backend deps already importable, so `start` never shells out to pip.
    monkeypatch.setattr(
        cli, "RealProbe", lambda: FakeProbe(run={str(venv_python): cp(returncode=0)})
    )

    captured = {}

    def fake_start_supervised(name, cmd, cwd, env, port, spawn=None):
        if name == "frontend":
            captured["frontend_env"] = env
        return {"pid": 1, "port": port, "log": str(tmp_path / f"{name}.log"), "started_at": "now"}

    monkeypatch.setattr(cli, "start_supervised", fake_start_supervised)
    monkeypatch.setattr(cli, "save_state", lambda state, state_file=cli.STATE_FILE: None)
    monkeypatch.setattr(cli, "_install_signal_forwarding", lambda state: None)

    probed_urls = []

    def fake_wait_for_ready(check, timeout):
        probed_urls.append(check)
        return True

    monkeypatch.setattr(cli, "wait_for_ready", fake_wait_for_ready)

    called_urls = []

    def fake_http_ok(url, **kwargs):
        called_urls.append(url)
        return True

    monkeypatch.setattr(cli, "http_ok", fake_http_ok)
    monkeypatch.setattr(cli, "_print_claim_token_hint", lambda repo_root, port: None)

    parser = cli.build_parser()
    args = parser.parse_args(["start"])
    code = cli.cmd_start(args)

    assert code == 0
    # wait_for_ready was called twice (backend then frontend); invoke both
    # lambdas so fake_http_ok records the URLs each one probes.
    for check in probed_urls:
        check()

    frontend_probe_urls = [u for u in called_urls if str(args.frontend_port) in u]
    assert frontend_probe_urls, "expected a frontend readiness probe URL"
    probed_host = frontend_probe_urls[0].split("://", 1)[1].split(":", 1)[0]
    assert probed_host == captured["frontend_env"]["VITE_HOST"] == cli.FRONTEND_BIND_HOST


# ---------------------------------------------------------------------------
# cmd_start --no-gpu: installs requirements-cpu.txt against the PyTorch CPU
# index, never constraints.txt, and persists the profile marker
# ---------------------------------------------------------------------------

def test_cmd_start_no_gpu_installs_cpu_requirements_and_marks_profile(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli, "load_state", lambda state_file=cli.STATE_FILE: None)
    monkeypatch.setattr(cli, "run_doctor", lambda *a, **k: [])
    monkeypatch.setattr(cli, "probe_python_candidates", lambda probe: ("python3.12", (3, 12)))
    monkeypatch.setattr(cli, "no_gpu_profile_active", lambda marker_file=cli.NO_GPU_PROFILE_FILE: False)
    marked = []
    monkeypatch.setattr(cli, "mark_no_gpu_profile", lambda marker_file=cli.NO_GPU_PROFILE_FILE: marked.append(True))

    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    vite_bin = tmp_path / "frontend" / "node_modules" / ".bin" / "vite"
    vite_bin.parent.mkdir(parents=True)
    vite_bin.touch()

    # Backend deps NOT importable -> `start` must shell out to pip.
    monkeypatch.setattr(
        cli, "RealProbe", lambda: FakeProbe(run={str(venv_python): cp(returncode=1)})
    )

    captured = {}

    def fake_run_streamed(cmd, cwd, env, label):
        if label == "pip install":
            captured["pip_cmd"] = cmd
        return True

    monkeypatch.setattr(cli, "run_streamed", fake_run_streamed)
    monkeypatch.setattr(
        cli, "start_supervised",
        lambda name, cmd, cwd, env, port, spawn=None: {
            "pid": 1, "port": port, "log": str(tmp_path / f"{name}.log"), "started_at": "now"
        },
    )
    monkeypatch.setattr(cli, "save_state", lambda state, state_file=cli.STATE_FILE: None)
    monkeypatch.setattr(cli, "_install_signal_forwarding", lambda state: None)
    monkeypatch.setattr(cli, "wait_for_ready", lambda check, timeout: True)
    monkeypatch.setattr(cli, "http_ok", lambda url, **kwargs: True)
    monkeypatch.setattr(cli, "_print_claim_token_hint", lambda repo_root, port: None)

    parser = cli.build_parser()
    args = parser.parse_args(["start", "--no-gpu"])
    code = cli.cmd_start(args)

    assert code == 0
    assert marked == [True]
    pip_bin = str(tmp_path / "venv" / "bin" / "pip")
    assert captured["pip_cmd"][0] == pip_bin
    assert captured["pip_cmd"][1:] == cli.backend_pip_install_args_no_gpu()
    assert "constraints.txt" not in captured["pip_cmd"]


# ---------------------------------------------------------------------------
# frontend_build_is_fresh — the single-process/two-process decision
# ---------------------------------------------------------------------------

def _touch_with_mtime(path: Path, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    os.utime(path, (mtime, mtime))


def test_frontend_build_is_fresh_false_when_build_missing(tmp_path):
    assert cli.frontend_build_is_fresh(tmp_path) is False


def test_frontend_build_is_fresh_true_when_no_src_files_are_newer(tmp_path):
    _touch_with_mtime(tmp_path / "frontend" / "src" / "routes" / "+page.svelte", 100.0)
    _touch_with_mtime(tmp_path / "frontend" / "build" / "index.html", 200.0)
    assert cli.frontend_build_is_fresh(tmp_path) is True


def test_frontend_build_is_fresh_false_when_a_src_file_is_newer_than_the_build(tmp_path):
    _touch_with_mtime(tmp_path / "frontend" / "build" / "index.html", 100.0)
    _touch_with_mtime(tmp_path / "frontend" / "src" / "routes" / "+page.svelte", 200.0)
    assert cli.frontend_build_is_fresh(tmp_path) is False


def test_frontend_build_is_fresh_true_when_src_dir_is_absent(tmp_path):
    _touch_with_mtime(tmp_path / "frontend" / "build" / "index.html", 100.0)
    assert cli.frontend_build_is_fresh(tmp_path) is True


# ---------------------------------------------------------------------------
# _existing_running_and_healthy / _canonical_port on single-process state
# (no "frontend" key at all)
# ---------------------------------------------------------------------------

def test_existing_running_and_healthy_true_for_single_process_state(monkeypatch):
    monkeypatch.setattr(cli, "pid_alive", lambda pid: True)
    monkeypatch.setattr(cli, "http_ok", lambda url, **kwargs: True)
    state = {"backend": {"pid": 1, "port": 8005}}
    assert cli._existing_running_and_healthy(state) is True


def test_existing_running_and_healthy_false_when_backend_unhealthy(monkeypatch):
    monkeypatch.setattr(cli, "pid_alive", lambda pid: True)
    monkeypatch.setattr(cli, "http_ok", lambda url, **kwargs: False)
    state = {"backend": {"pid": 1, "port": 8005}}
    assert cli._existing_running_and_healthy(state) is False


def test_canonical_port_prefers_frontend_when_present():
    state = {"backend": {"port": 8005}, "frontend": {"port": 3001}}
    assert cli._canonical_port(state) == 3001


def test_canonical_port_falls_back_to_backend_for_single_process_state():
    state = {"backend": {"port": 8005}}
    assert cli._canonical_port(state) == 8005


# ---------------------------------------------------------------------------
# CLI argument parsing: --dev and the `build` subcommand
# ---------------------------------------------------------------------------

def test_build_parser_start_dev_defaults_false():
    parser = cli.build_parser()
    args = parser.parse_args(["start"])
    assert args.dev is False


def test_build_parser_start_dev_flag():
    parser = cli.build_parser()
    args = parser.parse_args(["start", "--dev"])
    assert args.dev is True


def test_build_parser_build_subcommand_parses():
    parser = cli.build_parser()
    args = parser.parse_args(["build"])
    assert args.command == "build"


# ---------------------------------------------------------------------------
# cmd_start: single-process mode when frontend/build is fresh
# ---------------------------------------------------------------------------

def _stub_cmd_start_prereqs(monkeypatch, tmp_path):
    """Shared fakes so cmd_start reaches the spawn step without touching the
    real venv/pip/npm — mirrors test_cmd_start_frontend_env_and_probe_url_share_the_same_host."""
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli, "load_state", lambda state_file=cli.STATE_FILE: None)
    monkeypatch.setattr(cli, "run_doctor", lambda *a, **k: [])
    monkeypatch.setattr(cli, "probe_python_candidates", lambda probe: ("python3.12", (3, 12)))
    monkeypatch.setattr(cli, "no_gpu_profile_active", lambda marker_file=cli.NO_GPU_PROFILE_FILE: False)

    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    monkeypatch.setattr(
        cli, "RealProbe", lambda: FakeProbe(run={str(venv_python): cp(returncode=0)})
    )
    monkeypatch.setattr(cli, "save_state", lambda state, state_file=cli.STATE_FILE: None)
    monkeypatch.setattr(cli, "_install_signal_forwarding", lambda state: None)
    monkeypatch.setattr(cli, "wait_for_ready", lambda check, timeout: True)
    monkeypatch.setattr(cli, "http_ok", lambda url, **kwargs: True)
    monkeypatch.setattr(cli, "_print_claim_token_hint", lambda repo_root, port: None)
    return venv_python


def test_cmd_start_single_process_when_build_is_fresh_spawns_backend_only(monkeypatch, tmp_path):
    venv_python = _stub_cmd_start_prereqs(monkeypatch, tmp_path)
    _touch_with_mtime(tmp_path / "frontend" / "src" / "routes" / "+page.svelte", 100.0)
    _touch_with_mtime(tmp_path / "frontend" / "build" / "index.html", 200.0)
    # No vite binary at all - single-process mode must never look for it.
    assert not (tmp_path / "frontend" / "node_modules").exists()

    spawned = []
    monkeypatch.setattr(
        cli, "start_supervised",
        lambda name, cmd, cwd, env, port, spawn=None: spawned.append(name) or {
            "pid": 1, "port": port, "log": str(tmp_path / f"{name}.log"), "started_at": "now"
        },
    )

    parser = cli.build_parser()
    args = parser.parse_args(["start"])
    code = cli.cmd_start(args)

    assert code == 0
    assert spawned == ["backend"]


def test_cmd_start_dev_flag_forces_two_process_even_with_a_fresh_build(monkeypatch, tmp_path):
    venv_python = _stub_cmd_start_prereqs(monkeypatch, tmp_path)
    _touch_with_mtime(tmp_path / "frontend" / "src" / "routes" / "+page.svelte", 100.0)
    _touch_with_mtime(tmp_path / "frontend" / "build" / "index.html", 200.0)
    vite_bin = tmp_path / "frontend" / "node_modules" / ".bin" / "vite"
    vite_bin.parent.mkdir(parents=True)
    vite_bin.touch()

    spawned = []
    monkeypatch.setattr(
        cli, "start_supervised",
        lambda name, cmd, cwd, env, port, spawn=None: spawned.append(name) or {
            "pid": 1, "port": port, "log": str(tmp_path / f"{name}.log"), "started_at": "now"
        },
    )

    parser = cli.build_parser()
    args = parser.parse_args(["start", "--dev"])
    code = cli.cmd_start(args)

    assert code == 0
    assert spawned == ["backend", "frontend"]


# ---------------------------------------------------------------------------
# cmd_build
# ---------------------------------------------------------------------------

def test_cmd_build_installs_then_runs_npm_run_build(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    npm_path = "/usr/bin/npm"
    monkeypatch.setattr(cli, "RealProbe", lambda: FakeProbe(which={"npm": npm_path}))

    calls = []

    def fake_run_streamed(cmd, cwd, env, label):
        calls.append((label, cmd, cwd))
        return True

    monkeypatch.setattr(cli, "run_streamed", fake_run_streamed)

    parser = cli.build_parser()
    args = parser.parse_args(["build"])
    code = cli.cmd_build(args)

    assert code == 0
    labels = [c[0] for c in calls]
    assert labels == ["npm install", "npm run build"]
    assert calls[0][1] == [npm_path, "install"]
    assert calls[1][1] == [npm_path, "run", "build"]
    assert calls[1][2] == tmp_path / "frontend"


def test_cmd_build_skips_install_when_vite_already_present(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    npm_path = "/usr/bin/npm"
    monkeypatch.setattr(cli, "RealProbe", lambda: FakeProbe(which={"npm": npm_path}))
    vite_bin = tmp_path / "frontend" / "node_modules" / ".bin" / "vite"
    vite_bin.parent.mkdir(parents=True)
    vite_bin.touch()

    calls = []
    monkeypatch.setattr(
        cli, "run_streamed", lambda cmd, cwd, env, label: calls.append(label) or True
    )

    parser = cli.build_parser()
    args = parser.parse_args(["build"])
    code = cli.cmd_build(args)

    assert code == 0
    assert calls == ["npm run build"]


def test_cmd_build_fails_without_npm(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cli, "RealProbe", lambda: FakeProbe(which={}))

    parser = cli.build_parser()
    args = parser.parse_args(["build"])
    code = cli.cmd_build(args)

    assert code == 1
    assert "npm not found" in capsys.readouterr().out
