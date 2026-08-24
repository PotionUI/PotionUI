"""`artifacts.fetch` against fake download-manager / model repository
surfaces - no real network or filesystem I/O."""

from src.features.setup.executors.artifacts_fetch import ArtifactsFetchExecutor
from src.features.setup.executors.base import StepContext
from src.features.setup.recipe_schema import Recipe, RecipeArtifact, RecipeChecksum, RecipeStep
from src.features.setup.records import SetupRun, SetupRunStatus


class FakeModelRepository:
    def __init__(self, present=()):
        self.present = set(present)

    def get_by_identity(self, model_type, filename, include_providers=True):
        return object() if (model_type, filename) in self.present else None


class FakeDownload:
    def __init__(self, id, status="completed", error_message=None, downloaded_bytes=None, total_bytes=None):
        self.id = id
        self.status = status
        self.error_message = error_message
        self.downloaded_bytes = downloaded_bytes
        self.total_bytes = total_bytes


class FakeDownloadService:
    def __init__(self, terminal_status="completed"):
        self.terminal_status = terminal_status
        self.queued = []

    async def queue_model_download(self, **kwargs):
        self.queued.append(kwargs)
        return FakeDownload(id="dl-1")

    def get_download(self, download_id):
        return FakeDownload(id=download_id, status=self.terminal_status)


class ProgressingDownloadService(FakeDownloadService):
    """Reports a growing `downloaded_bytes` for a couple of polls before
    landing on `terminal_status` - exercises the `report_progress` seam
    (`_wait_for_completion`) without a real download or sleep."""

    def __init__(self, terminal_status="completed", ticks=(2_000, 6_000)):
        super().__init__(terminal_status=terminal_status)
        self._ticks = list(ticks)
        self._calls = 0

    def get_download(self, download_id):
        self._calls += 1
        if self._ticks:
            downloaded = self._ticks.pop(0)
            return FakeDownload(id=download_id, status="downloading", downloaded_bytes=downloaded, total_bytes=10_000)
        return FakeDownload(id=download_id, status=self.terminal_status, downloaded_bytes=10_000, total_bytes=10_000)


def _artifact(aid="ckpt", download_url="https://example.test/model.safetensors"):
    return RecipeArtifact(
        id=aid,
        kind="checkpoint",
        model_type="checkpoint",
        filename="model.safetensors",
        display_name="Model",
        checksum=RecipeChecksum(algorithm="sha256", value=None),
        provider_hint={"download_url": download_url} if download_url else {},
    )


def _context(recipe, artifact_ids):
    run = SetupRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=SetupRunStatus.RUNNING, created_by="owner-1")
    step = RecipeStep(key="artifacts.fetch", kind="artifacts.fetch", title="Fetch", params={"artifact_ids": artifact_ids})
    return StepContext(run=run, recipe=recipe, step=step)


def test_nothing_to_fetch_when_all_present():
    artifact = _artifact()
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native", artifacts=[artifact])
    model_repo = FakeModelRepository(present={("checkpoint", "model.safetensors")})
    executor = ArtifactsFetchExecutor(FakeDownloadService(), model_repo)

    result = executor.execute(_context(recipe, ["ckpt"]))

    assert result.success is True
    assert result.safe_output["fetched"] == []


def test_missing_download_manager_fails_plainly():
    artifact = _artifact()
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native", artifacts=[artifact])
    executor = ArtifactsFetchExecutor(None, FakeModelRepository())

    result = executor.execute(_context(recipe, ["ckpt"]))

    assert result.success is False
    assert result.error_code == "NO_DOWNLOAD_MANAGER"


def test_fetches_missing_artifact_via_explicit_download_url():
    artifact = _artifact()
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native", artifacts=[artifact])
    service = FakeDownloadService(terminal_status="completed")
    executor = ArtifactsFetchExecutor(service, FakeModelRepository())
    executor._models_dir = lambda: "models"  # avoid a real settings-repo/DB round trip

    result = executor.execute(_context(recipe, ["ckpt"]))

    assert result.success is True
    assert result.safe_output["fetched"][0]["id"] == "ckpt"
    assert service.queued[0]["url"] == "https://example.test/model.safetensors"
    assert service.queued[0]["filename"] == "model.safetensors"


def test_failed_download_reports_plain_error():
    artifact = _artifact()
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native", artifacts=[artifact])
    service = FakeDownloadService(terminal_status="failed")
    executor = ArtifactsFetchExecutor(service, FakeModelRepository())
    executor._models_dir = lambda: "models"

    result = executor.execute(_context(recipe, ["ckpt"]))

    assert result.success is False
    assert result.error_code == "ARTIFACT_DOWNLOAD_FAILED"


def test_unresolvable_source_fails_with_plain_message():
    artifact = _artifact(download_url=None)
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native", artifacts=[artifact])
    service = FakeDownloadService()
    executor = ArtifactsFetchExecutor(
        service, FakeModelRepository(), provider_registry_factory=None
    )
    executor._models_dir = lambda: "models"
    executor._get_provider_registry = lambda: None  # no provider plugin available either

    result = executor.execute(_context(recipe, ["ckpt"]))

    assert result.success is False
    assert result.error_code == "ARTIFACT_SOURCE_UNRESOLVED"


def test_reports_bytes_progress_per_poll_tick(monkeypatch):
    """`_wait_for_completion` reports `downloaded_bytes`/`total_bytes` off
    each `get_download` poll through `context.report_progress`, unit
    'bytes' - the setup-run T3.7 progress-report seam (see
    `executors/base.py`'s `StepContext.report_progress` and
    `executors/registry.py`, which wires the real callback; here we just
    assert the executor calls it correctly)."""
    from src.features.setup.executors import artifacts_fetch as artifacts_fetch_module

    monkeypatch.setattr(artifacts_fetch_module.time, "sleep", lambda _seconds: None)

    artifact = _artifact()
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native", artifacts=[artifact])
    service = ProgressingDownloadService(terminal_status="completed", ticks=(2_000, 6_000))
    executor = ArtifactsFetchExecutor(service, FakeModelRepository())
    executor._models_dir = lambda: "models"

    reports = []
    context = _context(recipe, ["ckpt"])
    context.report_progress = lambda progress_current=None, progress_total=None, progress_unit=None: reports.append(
        (progress_current, progress_total, progress_unit)
    )

    result = executor.execute(context)

    assert result.success is True
    assert reports == [
        (2_000, 10_000, "bytes"),
        (6_000, 10_000, "bytes"),
        (10_000, 10_000, "bytes"),
    ]


class StalledDownloadService(FakeDownloadService):
    """Always reports the same `downloaded_bytes`, never reaching a terminal
    status - a download that is stuck, not just slow."""

    def __init__(self, downloaded_bytes=2_000, total_bytes=10_000):
        super().__init__(terminal_status="downloading")
        self._downloaded_bytes = downloaded_bytes
        self._total_bytes = total_bytes

    def get_download(self, download_id):
        return FakeDownload(
            id=download_id,
            status="downloading",
            downloaded_bytes=self._downloaded_bytes,
            total_bytes=self._total_bytes,
        )


class FakeMonotonic:
    """Feeds `time.monotonic()` a fixed, pre-scripted sequence of values so a
    test can pin exactly how much (simulated) time elapses between polls
    without a real sleep."""

    def __init__(self, values):
        self._values = list(values)
        self._last = 0.0

    def __call__(self):
        if self._values:
            self._last = self._values.pop(0)
        else:
            # Keeps advancing (rather than freezing) once the scripted values
            # run out, so a revert to a different call pattern still
            # terminates - never spins forever waiting for a clock that never
            # moves.
            self._last += 10_000.0
        return self._last


def test_stalled_download_fails_after_no_progress_deadline(monkeypatch):
    """`downloaded_bytes` stuck at the same value for `_STALL_SECONDS` fails
    the step - the stall-deadline replacement for the old wall-clock
    `_MAX_WAIT_SECONDS` timeout."""
    from src.features.setup.executors import artifacts_fetch as artifacts_fetch_module

    monkeypatch.setattr(artifacts_fetch_module.time, "sleep", lambda _seconds: None)
    # initial baseline, then two poll ticks 700s apart (>= the 600s deadline).
    monkeypatch.setattr(
        artifacts_fetch_module.time, "monotonic", FakeMonotonic([0.0, 0.0, 700.0])
    )

    artifact = _artifact()
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native", artifacts=[artifact])
    service = StalledDownloadService()
    executor = ArtifactsFetchExecutor(service, FakeModelRepository())
    executor._models_dir = lambda: "models"

    result = executor.execute(_context(recipe, ["ckpt"]))

    assert result.success is False
    assert result.error_code == "ARTIFACT_DOWNLOAD_FAILED"
    assert "made no progress for 10 minutes" in result.safe_error_detail


def test_slow_but_steadily_advancing_download_never_hits_the_deadline(monkeypatch):
    """A download that keeps advancing - however slowly in wall-clock terms -
    must never fail: each poll here is a simulated 900s (> the 600s stall
    deadline) apart, but `downloaded_bytes` always moves, so the step
    succeeds instead of timing out the way the old 1-hour wall clock would
    have."""
    from src.features.setup.executors import artifacts_fetch as artifacts_fetch_module

    monkeypatch.setattr(artifacts_fetch_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        artifacts_fetch_module.time,
        "monotonic",
        FakeMonotonic([0.0, 0.0, 900.0, 1800.0, 2700.0]),
    )

    artifact = _artifact()
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native", artifacts=[artifact])
    service = ProgressingDownloadService(terminal_status="completed", ticks=(2_000, 4_000, 6_000, 8_000))
    executor = ArtifactsFetchExecutor(service, FakeModelRepository())
    executor._models_dir = lambda: "models"

    result = executor.execute(_context(recipe, ["ckpt"]))

    assert result.success is True


def test_auth_shaped_failure_gets_a_suggested_repair(monkeypatch):
    """A download failure whose message carries the download worker's own
    auth-failure shape (HTTP 401/403, or an HTML page instead of a file) gets
    a concrete `suggested_repair` naming the provider that needs a key -
    resolved from the artifact's own `provider_hint.source` via the provider
    registry, never hardcoded."""
    artifact = _artifact()
    artifact = RecipeArtifact(
        id=artifact.id,
        kind=artifact.kind,
        model_type=artifact.model_type,
        filename=artifact.filename,
        display_name=artifact.display_name,
        checksum=artifact.checksum,
        provider_hint={"download_url": artifact.provider_hint["download_url"], "source": "civitai"},
    )
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native", artifacts=[artifact])

    class AuthFailingDownloadService(FakeDownloadService):
        def get_download(self, download_id):
            return FakeDownload(
                id=download_id,
                status="failed",
                error_message="Access denied (HTTP 401) fetching 'model.safetensors' from civitai.com",
            )

    class FakeRegistry:
        def get_provider_metadata(self, provider_id):
            assert provider_id == "civitai"
            return type("M", (), {"name": "CivitAI", "website": "https://civitai.com"})()

        def get_provider_settings_schema(self, provider_id):
            return {"properties": {"api_key": {"format": "password"}}}

        def get_provider_current_settings(self, provider_id):
            return {}

    executor = ArtifactsFetchExecutor(AuthFailingDownloadService(), FakeModelRepository())
    executor._models_dir = lambda: "models"
    executor._get_provider_registry = lambda: FakeRegistry()

    result = executor.execute(_context(recipe, ["ckpt"]))

    assert result.success is False
    assert result.suggested_repair == (
        "Add your CivitAI API key in Administration -> Plugins -> CivitAI, then retry setup."
    )


def test_non_auth_failure_gets_no_suggested_repair():
    """A plain network/file failure (no auth-shaped signal in the message)
    must not suggest an API-key fix - that would be misleading."""
    artifact = _artifact()
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native", artifacts=[artifact])
    service = FakeDownloadService(terminal_status="failed")
    executor = ArtifactsFetchExecutor(service, FakeModelRepository())
    executor._models_dir = lambda: "models"

    result = executor.execute(_context(recipe, ["ckpt"]))

    assert result.success is False
    assert result.suggested_repair is None


def test_no_progress_report_when_download_exposes_no_byte_counts():
    """A download surface that never sets `downloaded_bytes`/`total_bytes`
    (both `None`, like the plain `FakeDownload` default) must not spam
    zero/None progress writes."""
    artifact = _artifact()
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native", artifacts=[artifact])
    service = FakeDownloadService(terminal_status="completed")
    executor = ArtifactsFetchExecutor(service, FakeModelRepository())
    executor._models_dir = lambda: "models"

    reports = []
    context = _context(recipe, ["ckpt"])
    context.report_progress = lambda **kwargs: reports.append(kwargs)

    result = executor.execute(context)

    assert result.success is True
    assert reports == []
