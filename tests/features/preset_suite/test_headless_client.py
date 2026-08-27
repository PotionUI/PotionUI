"""Mock-level tests for :class:`HeadlessGenerationClient`.

The real client boots the whole injector and runs on a GPU (first exercised by
the user's real run), so these tests do NOT boot it. They inject a fake
orchestrator to pin the two contracts a real run depends on and that regressed
in the maiden run:

  * the output callback the client hands to ``orchestrator.start_generation``
    MUST be a coroutine, because the orchestrator ``await``s it (including a
    final ``(generation_id, None)`` completion call) — a sync callback made the
    real generation error surface as a masking ``TypeError``;
  * a FAILED generation surfaces as a ``CaseOutcome(status="failed")`` carrying
    the backend's error text.
"""

from __future__ import annotations

import inspect

import pytest

from src.features.preset_suite.runner import HeadlessGenerationClient


def _never_called():
    """The container factory the client is handed.

    Every test here bypasses ``_boot()`` -- no injector, no database, no GPU --
    so nothing should ever build a container. Raising says so out loud, instead
    of a stub quietly returning a Mock that a later assertion mistakes for real.
    """
    raise AssertionError("the container factory should not be called")


class _FakeOrchestrator:
    """Stands in for GenerationOrchestrator: drives the callback exactly as the
    real one does (awaits it for each output AND once with ``None`` to signal
    completion), then reports a terminal status."""

    def __init__(self, outputs, final_state="completed"):
        self._outputs = outputs
        self._final_state = final_state
        self.callback = None
        self.request = None

    async def start_generation(self, request, user_id, output_callback):
        self.request = request
        self.callback = output_callback
        # The real orchestrator awaits the callback per output...
        for out in self._outputs:
            await output_callback("gen-1", out)
        # ...and once more with None to signal completion.
        await output_callback("gen-1", None)
        return {"generation_id": "gen-1"}

    async def get_generation_status(self, generation_id):
        return {"status": self._final_state}


def _client_with(orch) -> HeadlessGenerationClient:
    c = HeadlessGenerationClient(_never_called)
    c._orchestrator = orch  # bypass _boot(): no injector, no GPU
    return c


def test_collector_callback_is_a_coroutine():
    # A sync callback returns None and `await None` raises TypeError, masking the
    # real generation error. Capture the callback the client hands the
    # orchestrator and assert it is awaitable.
    orch = _FakeOrchestrator(outputs=[])
    client = _client_with(orch)
    client.run_case("PID", "txt2img", {}, prompt="hi", negative_prompt=None)
    assert orch.callback is not None
    assert inspect.iscoroutinefunction(orch.callback), "callback must be async"


def test_completed_generation_collects_images():
    from src.pipelines.outputs import GalleryGenerationOutput, ImageGenerationOutput

    img = object()
    outputs = [
        ImageGenerationOutput(image=object(), temporary=True),   # ignored (temp)
        ImageGenerationOutput(image=img, temporary=False),       # kept
    ]
    orch = _FakeOrchestrator(outputs=outputs, final_state="completed")
    outcome = _client_with(orch).run_case("PID", "txt2img", {})
    assert outcome.status == "completed"
    assert img in outcome.images


def test_failed_generation_surfaces_error_text():
    from src.pipelines.outputs import ErrorGenerationOutput

    outputs = [ErrorGenerationOutput(error="Pipe 'detailer/sdxl' not found in pipe registry")]
    orch = _FakeOrchestrator(outputs=outputs, final_state="failed")
    outcome = _client_with(orch).run_case("PID", "txt2img", {})
    assert outcome.status == "failed"
    assert "detailer/sdxl" in outcome.error


def test_none_completion_call_is_handled_without_error():
    # The orchestrator's final (generation_id, None) call must be a no-op, not a
    # crash. _FakeOrchestrator always issues it; a completed run proves it.
    orch = _FakeOrchestrator(outputs=[], final_state="completed")
    outcome = _client_with(orch).run_case("PID", "txt2img", {})
    assert outcome.status == "completed"


def test_completion_uses_done_signal_not_status_attribute():
    # Regression: GenerationRecord exposes .state (an enum), NOT .status — the
    # old poll did getattr(status, "status", "pending") and looped forever. Prove
    # completion is driven by the (gen_id, None) done-signal + model_dump()['status'].
    class _RecordOrch(_FakeOrchestrator):
        async def get_generation_status(self, generation_id):
            # A record-like object with .state and model_dump(), and NO .status.
            class _Rec:
                def model_dump(self_inner):
                    return {"status": "completed"}
            return _Rec()

    orch = _RecordOrch(outputs=[], final_state="completed")
    outcome = _client_with(orch).run_case("PID", "txt2img", {})
    assert outcome.status == "completed"


def test_case_times_out_instead_of_hanging():
    # A generation that never signals completion must FAIL with a timeout, not
    # hang the suite. Orchestrator that never awaits the callback with None and
    # always reports non-terminal status.
    class _HangOrch:
        async def start_generation(self, request, user_id, output_callback):
            return {"generation_id": "gen-1"}

        async def get_generation_status(self, generation_id):
            return {"status": "running"}

    client = _client_with(_HangOrch())
    outcome = client.run_case("PID", "txt2img", {}, max_seconds=0.5)
    assert outcome.status == "failed" and "timed out" in outcome.error


def test_prepare_ephemeral_db_seeds_suite_user_and_isolated_storage(tmp_path, mock_db):
    # The ephemeral-DB seed must create the suite user row (so the generation /
    # file-record FK holds — the FK failure the user hit) and redirect
    # file_storage_directory into the run dir (so images never land in the user's
    # gallery). Runs against a fresh migrated in-memory DB (mock_db), never live.
    from src.features.preset_suite import ephemeral

    client = HeadlessGenerationClient(_never_called, run_dir=tmp_path)
    client._prepare_ephemeral_db(db=mock_db)

    with mock_db.get_cursor() as cur:
        cur.execute("SELECT id, account_type FROM users WHERE id = ?", (client.user_id,))
        user = cur.fetchone()
        assert user is not None and user["account_type"] == "ADMIN"
        cur.execute("SELECT value FROM settings WHERE key = 'file_storage_directory'")
        setting = cur.fetchone()
    assert setting is not None and setting["value"] == str(tmp_path / "storage")
    # The isolated storage dir is created AND marked so cleanup may remove it.
    assert ephemeral.is_marked(tmp_path / "storage")


def test_prepare_ephemeral_db_is_idempotent(tmp_path, mock_db):
    # A second boot (or a re-run) must not blow up on the already-present user row.
    client = HeadlessGenerationClient(_never_called, run_dir=tmp_path)
    client._prepare_ephemeral_db(db=mock_db)
    client._prepare_ephemeral_db(db=mock_db)  # INSERT OR IGNORE / OR REPLACE — no error
    with mock_db.get_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM users WHERE id = ?", (client.user_id,))
        assert cur.fetchone()["n"] == 1


def test_prepare_ephemeral_db_ignores_the_process_default_singleton(tmp_path, mock_db):
    # Regression for the 2026-08-15 incident: a full-suite run left the
    # process-default `Database` singleton pointing somewhere `mock_db` never
    # touched, and `_prepare_ephemeral_db` wrote a real setting there instead
    # of to the ephemeral db — poisoning the maintainer's live
    # storage/db.sqlite with a throwaway tmp_path. `mock_db` here stands in
    # for "whatever the default singleton currently resolves to" (it patches
    # exactly that module attribute); `explicit_db` is a second, independent
    # database the code under test is actually handed. The fix must write
    # only to `explicit_db` and never fall through to the default.
    #
    # `explicit_db` gets only the two tables `seed_ephemeral` touches, built
    # by hand rather than via MigrationRunner: migration files each do their
    # own `from ...database import db` (freshly re-executed per migration,
    # since they're importlib-loaded fresh every run), so driving them against
    # a *third* db here would need patching that module too, muddying a test
    # about `_prepare_ephemeral_db`'s own db-selection contract.
    from tests.conftest import TestDatabase

    explicit_db = TestDatabase()
    with explicit_db.get_cursor() as cur:
        cur.execute(
            "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT, email TEXT, "
            "password_hash TEXT, account_type TEXT)"
        )
        cur.execute(
            "CREATE TABLE settings (id TEXT PRIMARY KEY, key TEXT UNIQUE, value TEXT, "
            "value_type TEXT, description TEXT, type TEXT)"
        )

    client = HeadlessGenerationClient(_never_called, run_dir=tmp_path)
    client._prepare_ephemeral_db(db=explicit_db)

    with explicit_db.get_cursor() as cur:
        cur.execute("SELECT value FROM settings WHERE key = 'file_storage_directory'")
        row = cur.fetchone()
    assert row is not None and row["value"] == str(tmp_path / "storage")

    # The "default" (mock_db, standing in for the live DB) must be untouched:
    # migrations seed it with 'storage' (migration 028) - the incident
    # overwrote exactly this row with a throwaway tmp_path.
    with mock_db.get_cursor() as cur:
        cur.execute("SELECT value FROM settings WHERE key = 'file_storage_directory'")
        row = cur.fetchone()
    assert row is not None and row["value"] == "storage"



def test_boot_registers_output_handlers_via_handlers_import():
    # _boot() imports src.features.generation.handlers for its registration side
    # effects. Pin the contract at the registry level (no injector/GPU boot):
    # after that import, ImageGenerationOutput must resolve to a handler class so
    # generated images are actually saved/thumbnailed the way the live app does.
    import src.features.generation.handlers  # noqa: F401 - what _boot() does
    from src.features.generation.output_types import output_type_registry
    from src.pipelines.outputs import ImageGenerationOutput

    spec = output_type_registry.spec_for(ImageGenerationOutput(image=object()))
    assert spec is not None and spec.handler_cls is not None


def test_prompt_is_placed_on_the_request_as_a_pair():
    orch = _FakeOrchestrator(outputs=[], final_state="completed")
    client = _client_with(orch)
    client.run_case("PID", "txt2img", {"steps": 8}, prompt="a red fox", negative_prompt="blurry")
    prompts = getattr(orch.request, "prompts", None)
    assert prompts and prompts[0].positive == "a red fox" and prompts[0].negative == "blurry"
    assert orch.request.preset_id == "PID"
