from types import SimpleNamespace

from src.features.recipes.executors.artifacts_fetch import ArtifactsFetchExecutor
from src.features.recipes.executors.base import StepContext
from src.features.recipes.records import RecipeRun, RecipeRunStatus
from src.features.recipes.schema import Recipe, RecipeStep
from src.features.recipes.targets import DiskVolume, TargetOption, TargetProfile
from src.platform.runtime.gpu_profile import GpuProfile


class Repo:
    def __init__(self, present=()):
        self.present = set(present)

    def get_by_identity(self, model_type, filename, include_providers=True):
        return object() if (model_type, filename) in self.present else None


class Queue:
    def __init__(self, script=None, active=(), fail_on=None, resolver=None):
        self.queued = []
        self.cancelled = []
        self.polls = 0
        self.scans = 0
        self.script = script or {}
        self.fail_on = fail_on
        self.resolver = resolver

        def get_all(status=None):
            self.scans += 1
            return [r for r in active if r.status == status.value]

        self.repo = SimpleNamespace(get_all=get_all)

    async def queue_model_download(self, **kwargs):
        if self.fail_on is not None and len(self.queued) + 1 == self.fail_on:
            raise RuntimeError("queue refused")
        self.queued.append(kwargs)
        return SimpleNamespace(id=f"dl-{len(self.queued)}")

    def get_download(self, download_id):
        self.polls += 1
        if download_id in self.cancelled:
            return SimpleNamespace(id=download_id, status="cancelled", downloaded_bytes=0, total_bytes=None, speed_bytes_per_sec=None, error_message=None)
        steps = self.script.get(download_id) or [("completed", None)]
        status, done = steps[min(self.polls // max(len(self.queued), 1), len(steps) - 1)]
        return SimpleNamespace(id=download_id, status=status, downloaded_bytes=done, total_bytes=None, speed_bytes_per_sec=None, error_message="HTTP 403" if status == "failed" else None)

    async def cancel_download(self, download_id):
        self.cancelled.append(download_id)


class Targets:
    def __init__(self, free):
        self.free = free

    def resolve(self, engine, target):
        return TargetOption(backend_id="native", label="This computer", kind="local"), []

    def profile(self, option, model_types, probe_remote=True):
        disk = DiskVolume(id="d0", label="Models folder", free_bytes=self.free, reserve_bytes=10, model_types=tuple(model_types))
        return TargetProfile(option=option, gpu=GpuProfile(), disks=(disk,))


def _file(slot, name, size, model_type="lora"):
    return {
        "slot_id": slot, "variant_id": slot, "filename": name, "model_type": model_type, "size_bytes": size,
        "sha256": None, "provider_hint": {"download_url": f"https://example.test/{name}"},
        "display_name": slot.title(), "gated": False, "license_url": None,
    }


PLAN = {
    "preset_id": "VID", "target": "native", "engine": "native", "destination_backend_id": None,
    "modes": ["cinemagraph"], "files": [_file("a", "a.safetensors", 100), _file("b", "b.safetensors", 300)], "chosen": {},
}


def _context(plan=PLAN, cancelled=lambda: False, reports=None):
    run = RecipeRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=RecipeRunStatus.RUNNING, created_by="owner")
    step = RecipeStep(key="artifacts.fetch", kind="artifacts.fetch", title="Fetch", params={"plan": "modes.plan"})
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native", steps=[step])

    def report(**kwargs):
        if reports is not None:
            reports.append(kwargs)

    return StepContext(run=run, recipe=recipe, step=step, step_outputs={"modes.plan": {"plan": plan}}, is_cancelled=cancelled, report_progress=report)


def _executor(queue, repo=None, free=10_000, sleeps=None, clock=None):
    return ArtifactsFetchExecutor(
        queue, repo or Repo(), targets=Targets(free),
        sleep=(lambda s: sleeps.append(s)) if sleeps is not None else (lambda s: None),
        **({"clock": clock} if clock is not None else {}),
    )


def test_queues_every_file_before_waiting_and_reports_aggregate_bytes():
    queue = Queue(script={"dl-1": [("downloading", 50), ("completed", 100)], "dl-2": [("pending", 0), ("downloading", 120), ("completed", 300)]})
    reports, sleeps = [], []
    result = _executor(queue, sleeps=sleeps).execute(_context(reports=reports))

    assert result.success is True
    assert [q["filename"] for q in queue.queued] == ["a.safetensors", "b.safetensors"]
    assert all(q["destination_backend_id"] is None for q in queue.queued)
    assert reports[0]["progress_total"] == 400
    assert reports[-1]["progress_current"] == 400
    assert reports[-1]["safe_output"]["files_done"] == 2
    assert {f["status"] for f in reports[0]["safe_output"]["files"]} <= {"downloading", "pending", "completed"}
    assert len(sleeps) == len(reports) - 1
    assert [f["id"] for f in result.safe_output["fetched"]] == ["a", "b"]


def test_present_files_are_skipped_and_nothing_left_is_ok():
    queue = Queue()
    result = _executor(queue, repo=Repo(present={("lora", "a.safetensors"), ("lora", "b.safetensors")})).execute(_context())

    assert result.success is True
    assert queue.queued == []
    assert result.safe_output["message"] == "Nothing left to download."


def test_remote_targets_download_on_the_worker():
    queue = Queue()
    plan = dict(PLAN, destination_backend_id="rtx-pod", target="rtx-pod")
    _executor(queue).execute(_context(plan=plan))

    assert {q["destination_backend_id"] for q in queue.queued} == {"rtx-pod"}


def test_not_enough_disk_fails_before_queueing():
    queue = Queue()
    result = _executor(queue, free=200).execute(_context())

    assert result.error_code == "NOT_ENOUGH_DISK"
    assert "only 190 B can be used" in result.safe_error_detail
    assert queue.queued == []


def test_a_failed_file_fails_the_step_and_cancels_the_rest():
    queue = Queue(script={"dl-1": [("failed", 0)], "dl-2": [("downloading", 10)]})
    result = _executor(queue).execute(_context())

    assert result.error_code == "ARTIFACT_DOWNLOAD_FAILED"
    assert "HTTP 403" not in result.safe_error_detail
    assert result.technical_detail == "HTTP 403"
    assert result.suggested_repair is not None
    assert queue.cancelled == ["dl-2"]


def test_a_stall_fails_the_step_and_cancels_unfinished_downloads():
    queue = Queue(script={"dl-1": [("completed", 100)], "dl-2": [("downloading", 10)]})
    ticks = iter(range(0, 100_000, 400))
    result = _executor(queue, clock=lambda: next(ticks)).execute(_context())

    assert result.error_code == "ARTIFACT_DOWNLOAD_FAILED"
    assert "no progress for 10 minutes" in result.safe_error_detail
    assert queue.cancelled == ["dl-2"]


def test_queueing_that_breaks_partway_cancels_what_was_already_queued():
    queue = Queue(fail_on=2, script={"dl-1": [("downloading", 10)]})
    result = _executor(queue).execute(_context())

    assert result.error_code == "ARTIFACT_DOWNLOAD_FAILED"
    assert "queue refused" not in result.safe_error_detail
    assert "queue refused" in result.technical_detail
    assert queue.cancelled == ["dl-1"]


def test_an_unresolvable_source_partway_cancels_what_was_already_queued():
    queue = Queue(script={"dl-1": [("downloading", 10)]})
    plan = dict(PLAN, files=[PLAN["files"][0], dict(PLAN["files"][1], provider_hint={})])
    result = _executor(queue).execute(_context(plan=plan))

    assert result.error_code == "ARTIFACT_SOURCE_UNRESOLVED"
    assert queue.cancelled == ["dl-1"]


def test_cancel_stops_the_loop_and_cancels_unfinished_downloads():
    queue = Queue(script={"dl-1": [("completed", 100)], "dl-2": [("downloading", 10)]})
    state = {"polls": 0}

    def cancelled():
        state["polls"] += 1
        return state["polls"] > 3

    result = _executor(queue).execute(_context(cancelled=cancelled))

    assert result.error_code == "RUN_CANCELLED"
    assert queue.cancelled == ["dl-2"]


def _row(row_id, filename, url=None, sha=None, path="/models/loras/x", destination=None, status="downloading"):
    return SimpleNamespace(
        id=row_id, filename=filename, url=url, checksum_sha256=sha, destination_path=path,
        destination_backend_id=destination, status=status,
    )


def test_an_active_download_of_the_same_source_is_adopted_with_one_lookup_per_run():
    active = [_row("old-1", "A.safetensors", url="https://example.test/a.safetensors")]
    queue = Queue(active=active)
    result = _executor(queue).execute(_context())

    assert [q["filename"] for q in queue.queued] == ["b.safetensors"]
    assert result.safe_output["fetched"][0]["download_id"] == "old-1"
    assert queue.scans == 3


def test_adoption_needs_the_same_url_or_hash_target_and_model_folder(tmp_path):
    loras, vaes = tmp_path / "loras", tmp_path / "vae"
    loras.mkdir()
    vaes.mkdir()
    resolver = SimpleNamespace(write_dir=lambda t: SimpleNamespace(path=loras if t == "lora" else vaes))
    sha = "c" * 64
    plan = dict(PLAN, files=[dict(PLAN["files"][0], sha256=sha), PLAN["files"][1]])
    active = [
        _row("other-url", "a.safetensors", url="https://elsewhere.test/a.safetensors", path=str(loras / "a.safetensors")),
        _row("other-folder", "b.safetensors", url="https://example.test/b.safetensors", path=str(vaes / "b.safetensors")),
        _row("same-hash", "a.safetensors", url="https://mirror.test/a.safetensors", sha=sha, path=str(loras / "a.safetensors")),
        _row("on-worker", "b.safetensors", url="https://example.test/b.safetensors", path="/w/b.safetensors", destination="pod"),
    ]
    queue = Queue(active=active, resolver=resolver)
    result = _executor(queue).execute(_context(plan=plan))

    assert [q["filename"] for q in queue.queued] == ["b.safetensors"]
    assert result.safe_output["fetched"][0]["download_id"] == "same-hash"


def test_missing_plan_fails_plainly():
    context = _context()
    context.step_outputs.clear()
    assert _executor(Queue()).execute(context).error_code == "ARTIFACTS_FETCH_NO_PLAN"


def test_a_later_failure_never_cancels_a_download_another_attempt_started():
    active = [_row("old-1", "a.safetensors", url="https://example.test/a.safetensors")]
    queue = Queue(active=active, fail_on=1, script={"old-1": [("downloading", 5)]})
    result = _executor(queue).execute(_context())

    assert result.error_code == "ARTIFACT_DOWNLOAD_FAILED"
    assert queue.cancelled == []


def test_user_cancel_spares_adopted_downloads_and_cancels_its_own():
    active = [_row("old-1", "a.safetensors", url="https://example.test/a.safetensors")]
    queue = Queue(active=active, script={"old-1": [("downloading", 5)], "dl-1": [("downloading", 10)]})
    state = {"polls": 0}

    def cancelled():
        state["polls"] += 1
        return state["polls"] > 3

    result = _executor(queue).execute(_context(cancelled=cancelled))

    assert result.error_code == "RUN_CANCELLED"
    assert queue.cancelled == ["dl-1"]
