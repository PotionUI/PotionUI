from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = {
    "linux": REPO_ROOT / ".github" / "workflows" / "backend-tests.yml",
    "windows": REPO_ROOT / ".github" / "workflows" / "windows.yml",
}


def load_shards(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data["jobs"]["backend-tests"]["strategy"]["matrix"]["shard"]


def tokens(shard, key):
    return shard[key].split()


@pytest.mark.parametrize("os_name", sorted(WORKFLOWS))
def test_every_shard_path_exists(os_name):
    for shard in load_shards(WORKFLOWS[os_name]):
        for key in ("paths", "serial_paths"):
            for token in tokens(shard, key):
                assert (REPO_ROOT / token.removeprefix("--ignore=")).exists(), f"{shard['name']}: {token}"


def test_linux_and_windows_run_identical_shards():
    assert load_shards(WORKFLOWS["linux"]) == load_shards(WORKFLOWS["windows"])


@pytest.mark.parametrize("os_name", sorted(WORKFLOWS))
def test_catch_all_shard_ignores_every_other_shard_root(os_name):
    shards = load_shards(WORKFLOWS[os_name])
    catch_all = [s for s in shards if "tests/" in tokens(s, "paths")]
    assert len(catch_all) == 1
    ignored = {t.removeprefix("--ignore=") for t in tokens(catch_all[0], "paths") if t.startswith("--ignore=")}
    for shard in shards:
        if shard is catch_all[0]:
            continue
        roots = {t for t in tokens(shard, "paths") if not t.startswith("--ignore=")}
        assert roots <= ignored, f"{shard['name']} roots not excluded from the catch-all shard: {roots - ignored}"


@pytest.mark.parametrize("os_name", sorted(WORKFLOWS))
def test_shard_roots_do_not_overlap(os_name):
    seen = {}
    for shard in load_shards(WORKFLOWS[os_name]):
        for token in tokens(shard, "paths"):
            if token.startswith("--ignore=") or token == "tests/":
                continue
            assert token not in seen, f"{token} in both {seen[token]} and {shard['name']}"
            seen[token] = shard["name"]


def load_e2e_job(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]["e2e"]


def test_e2e_windows_matrix_matches_run_py_shard_flag():
    job = load_e2e_job(WORKFLOWS["windows"])
    shards = job["strategy"]["matrix"]["shard"]
    assert shards == list(range(1, len(shards) + 1))
    steps = {step["name"]: step for step in job["steps"] if "name" in step}
    ui = steps["Playwright UI journeys (throwaway backend + vite preview)"]["run"]
    assert f"--skip-build --shard ${{{{ matrix.shard }}}}/{len(shards)}" in ui
    assert steps["HTTP journeys (throwaway backend)"]["if"] == "matrix.shard == 1"
    upload = steps["Upload E2E logs + artifacts (Windows)"]["with"]["name"]
    assert "matrix.shard" in upload
