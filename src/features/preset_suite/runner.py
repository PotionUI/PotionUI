"""Preset E2E test-suite runner.

Two layers, deliberately split so the orchestration is unit-testable without a
GPU:

  * :class:`PresetSuiteRunner` — PURE orchestration: discover ``tests.yml`` cases,
    resolve each case's models to file paths, inject them into the form, ask a
    ``GenerationClient`` to run the case, evaluate the sanity checks, and produce a
    :class:`~src.features.preset_suite.models.CaseResult`. It talks only to injected
    collaborators (a resolver + a client), so tests drive it with fakes and no real
    generation ever runs.
  * :class:`HeadlessGenerationClient` — the real client: stands up the same
    injector composition root ``api.py`` builds (minus uvicorn/websockets), submits
    each case through :class:`GenerationOrchestrator` exactly as a UI request does,
    with a collector standing in for the WebSocket callback, and polls the status
    tracker to completion. This layer is exercised for the first time by the user's
    real run (per the task's no-GPU-in-dev constraint); everything above it is
    covered by unit tests.

**Prompt convention (tests.yml #45):** a case's ``form.prompt`` /
``form.negative_prompt`` are NOT preset form fields — they map to the
``GenerationRequest``'s prompt input, exactly as the UI submits it. The runner
therefore POPS ``prompt``/``negative_prompt`` out of the case form before
submission and hands them to the client separately; the real client builds
``request.prompts = [PromptPair(positive, negative)]`` (the modern array the
orchestrator's prompt expansion actually reads — ``prompts[0]`` == ``p_prompt``
per docs/prompts.md; the legacy scalar ``prompt`` field is deprecated and NOT read
by that path). Everything else in the form is submitted untouched.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, Protocol, Tuple

from .models import CaseOutcome, CaseResult, FAIL, PASS, SKIP
from .resolver import ModelResolver

logger = logging.getLogger(__name__)


class GenerationClient(Protocol):
    """What the runner needs from a generation backend (real or fake)."""

    def can_run(self, preset_id: str, engine: str) -> Tuple[bool, str]:
        """Whether this host can execute ``preset_id`` (``engine``); ``(ok, reason)``."""
        ...

    def run_case(
        self, preset_id: str, mode: str, form_data: dict,
        *, prompt: Optional[str] = None, negative_prompt: Optional[str] = None,
        max_seconds: Optional[float] = None,
    ) -> CaseOutcome:
        """Run ONE generation and return its outcome (images/status/error/timing).

        ``prompt``/``negative_prompt`` were lifted out of the case form by the
        runner (they belong on the request, not in form_data). ``max_seconds`` is
        the case's ``checks.max_seconds`` (a per-case timeout ceiling); ``None``
        lets the client apply its own default."""
        ...


def _read_preset_meta(preset_dir: Path) -> Tuple[str, Optional[str]]:
    """``(engine, preset_id)`` from a preset's ``preset.yml``.

    ``engine`` defaults to ``"native"`` when absent; ``preset_id`` is the preset's
    ``id:`` (a ULID) — the value :class:`GenerationRequest` identifies the preset by
    (NOT its directory path). ``None`` when the file is missing/unreadable or has no
    ``id`` (the caller then FAILs the case with a clear reason). A light one-field
    yaml read — no preset processor boot needed.
    """
    try:
        import yaml

        with open(preset_dir / "preset.yml", "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        engine = str(data.get("engine", "native"))
        pid = data.get("id")
        return engine, (str(pid) if pid else None)
    except Exception:  # noqa: BLE001 - a missing/broken preset.yml -> handled by the caller
        return "native", None


def _preset_id_for(presets_root: Path, preset_dir: Path) -> str:
    """Preset id = the tests.yml dir path relative to ``presets/`` (e.g.
    ``native/SDXL/realistic``)."""
    return preset_dir.relative_to(presets_root).as_posix()


class PresetSuiteRunner:
    """Runs discovered ``tests.yml`` cases through a :class:`GenerationClient`."""

    def __init__(
        self,
        client: GenerationClient,
        resolver: ModelResolver,
        *,
        presets_root: str | Path = "presets",
        loader: Optional[Callable[[Path], Any]] = None,
    ) -> None:
        self.client = client
        self.resolver = resolver
        self.presets_root = Path(presets_root)
        # Injected so the runner is testable before the schema module (tier1c's
        # tests_schema.load_tests_yml) lands; defaults to the real loader.
        self._loader = loader or _default_loader

    # -- discovery ---------------------------------------------------------

    def discover(self, preset_filter: Optional[str] = None) -> List[Tuple[Path, Any]]:
        """Return ``(preset_dir, PresetTests)`` for every preset with a tests.yml,
        optionally narrowed to a single ``preset_filter`` id."""
        found: List[Tuple[Path, Any]] = []
        if not self.presets_root.is_dir():
            return found
        for tests_yml in sorted(self.presets_root.rglob("tests.yml")):
            preset_dir = tests_yml.parent
            preset_id = _preset_id_for(self.presets_root, preset_dir)
            if preset_filter and preset_id != preset_filter:
                continue
            try:
                tests = self._loader(preset_dir)
            except Exception as e:  # noqa: BLE001 - a broken tests.yml shouldn't abort the whole run
                logger.warning("could not load %s: %s", tests_yml, e)
                continue
            if tests is not None and getattr(tests, "cases", None):
                found.append((preset_dir, tests))
        return found

    def iter_cases(
        self, preset_filter: Optional[str] = None, tag: Optional[str] = None
    ) -> Iterable[Tuple[Path, str, Any]]:
        """Yield ``(preset_dir, preset_id, case)`` for every case, tag-filtered."""
        for preset_dir, tests in self.discover(preset_filter):
            preset_id = _preset_id_for(self.presets_root, preset_dir)
            for case in tests.cases:
                if tag and tag not in (getattr(case, "tags", None) or []):
                    continue
                yield preset_dir, preset_id, case

    # -- execution ---------------------------------------------------------

    def run(self, preset_filter: Optional[str] = None, tag: Optional[str] = None) -> List[CaseResult]:
        """Run every (filtered) case serially and return their results."""
        results: List[CaseResult] = []
        for preset_dir, preset_id, case in self.iter_cases(preset_filter, tag):
            results.append(self.run_case(preset_dir, preset_id, case))
        return results

    def run_case(self, preset_dir: Path, preset_id: str, case: Any) -> CaseResult:
        """Run one case end to end: engine-gate, resolve models, submit, check.

        ``preset_id`` here is the DISPLAY id (the directory-relative path, e.g.
        ``native/SDXL/realistic``) used in reports/gallery; the generation request
        is submitted with the preset's real ``id:`` (a ULID) read from preset.yml.
        """
        name = getattr(case, "name", "unnamed")
        mode = getattr(case, "mode", "txt2img")
        seed = getattr(case, "seed", None)
        tags = list(getattr(case, "tags", None) or [])
        checks = getattr(case, "checks", None)

        def _skip(reason: str) -> CaseResult:
            return CaseResult(
                preset_id=preset_id, case_name=name, verdict=SKIP,
                outcome=CaseOutcome(status="skipped", skip_reason=reason),
                reason=reason, tags=tags, seed=seed, mode=mode,
            )

        def _fail(reason: str, outcome: CaseOutcome, check_results=None) -> CaseResult:
            return CaseResult(
                preset_id=preset_id, case_name=name, verdict=FAIL, outcome=outcome,
                checks=check_results or [], reason=reason, tags=tags, seed=seed, mode=mode,
            )

        # The preset is identified to the generator by its preset.yml ``id:`` (a
        # ULID), NOT its directory path. A missing/malformed preset.yml (or one
        # without an id) FAILs the case with a clear reason rather than submitting a
        # path the orchestrator can't resolve.
        engine, request_preset_id = _read_preset_meta(preset_dir)
        if not request_preset_id:
            return _fail(
                f"preset.yml missing/unreadable or has no 'id' (at {preset_dir})",
                CaseOutcome(status="failed", error="no preset id"),
            )

        # Engine gating: a non-native preset needs a configured+reachable backend.
        ok, reason = self.client.can_run(request_preset_id, engine)
        if not ok:
            return _skip(reason or f"no runnable backend for engine '{engine}'")

        # Resolve models -> inject into the form. Any unresolved model SKIPs the case.
        form = dict(getattr(case, "form", None) or {})
        for field_name, ref in (getattr(case, "models", None) or {}).items():
            res = self.resolver.resolve(ref, model_type=_model_type_hint(field_name))
            if not res.resolved:
                return _skip(f"model '{field_name}': {res.reason}")
            form[field_name] = res.file_path

        # Lift the prompt onto the request — form.prompt / form.negative_prompt are
        # GenerationRequest inputs, not preset form fields (see module docstring).
        prompt = form.pop("prompt", None)
        negative_prompt = form.pop("negative_prompt", None)

        # Run the generation (real or fake). The REQUEST carries the ULID preset id.
        # A per-case timeout ceiling comes from checks.max_seconds when declared.
        max_seconds = getattr(checks, "max_seconds", None)
        try:
            outcome = self.client.run_case(
                request_preset_id, mode, form, prompt=prompt, negative_prompt=negative_prompt,
                max_seconds=max_seconds,
            )
        except Exception as e:  # noqa: BLE001 - a client crash is a case FAILURE, not a suite crash
            logger.exception("case %s/%s raised", preset_id, name)
            return _fail(f"runner error: {e}", CaseOutcome(status="failed", error=str(e), submitted_form=form))

        outcome.submitted_form = form
        outcome.prompt = prompt
        outcome.negative_prompt = negative_prompt
        if outcome.status == "skipped":
            return _skip(outcome.skip_reason or "skipped by client")
        if outcome.status == "failed":
            # Covers generation errors AND the NaN/Inf watchdog abort, which surface
            # as a failed generation with the watchdog's message.
            return _fail(outcome.error or "generation failed", outcome)

        # Completed: evaluate the declared sanity checks.
        from .checks import checks_passed, run_checks

        check_results = run_checks(outcome, checks)
        if checks_passed(check_results):
            return CaseResult(
                preset_id=preset_id, case_name=name, verdict=PASS, outcome=outcome,
                checks=check_results, reason="", tags=tags, seed=seed, mode=mode,
            )
        failed = [c for c in check_results if not c.passed]
        summary = "; ".join(f"{c.name}: {c.detail}" for c in failed) or "checks failed"
        return _fail(summary, outcome, check_results)


def _model_type_hint(field_name: str) -> Optional[str]:
    """Best-effort models-subdir hint for a download from the form field name
    (e.g. a ``*lora*`` field -> ``loras``). ``None`` -> the resolver uses its
    ``tests-downloads`` fallback. Deliberately conservative — a wrong guess only
    changes the download DIRECTORY, never correctness."""
    f = field_name.lower()
    for needle, subdir in (("lora", "loras"), ("vae", "vae"), ("controlnet", "controlnet"),
                           ("embedding", "embeddings"), ("upscal", "upscalers"), ("checkpoint", "checkpoints")):
        if needle in f:
            return subdir
    return None


def _default_loader(preset_dir: Path):
    """Load a preset's tests.yml via the frozen schema module (tier1c's #45).
    Imported lazily so this package imports even before that module lands."""
    from src.features.presets.tests_schema import load_tests_yml

    return load_tests_yml(preset_dir)


# ---------------------------------------------------------------------------
# Real headless client — the composition root. NOT unit-tested (no GPU in dev);
# first exercised by the user's real run. Mirrors api.py's startup.
# ---------------------------------------------------------------------------


class HeadlessGenerationClient:
    """Submits cases through the real :class:`GenerationOrchestrator`, headless.

    Runs the same startup ``api.py`` runs — migrations, then the injector
    container — but skips uvicorn/websockets; submits each case exactly where
    the generation controller does
    (``orchestrator.start_generation(request, user_id, output_callback)``) with a
    collector standing in for the WebSocket callback, and polls the status tracker
    to a terminal state. Serial — one case at a time — and relies on the
    preset-scoped RAM cache to release the previous preset's models on a
    preset switch.

    ``container_factory`` is what builds that container, and is passed in rather
    than imported: this is a feature, and the composition root is not something a
    feature may reach for. It is a factory and not a container because the
    ephemeral database has to be in place before anything is constructed against
    it — see ``_boot``.
    """

    def __init__(
        self,
        container_factory: Callable[[], Any],
        user_id: str = "preset-suite",
        run_dir: Optional[Path] = None,
    ) -> None:
        self._container_factory = container_factory
        self.user_id = user_id
        # When set, the client runs in EPHEMERAL mode: a throwaway DB + isolated
        # file storage inside run_dir, so a real run never touches the user's live
        # database or gallery. None keeps the legacy behaviour (used only by the
        # mock-level tests, which bypass _boot()).
        self.run_dir = Path(run_dir) if run_dir is not None else None
        self._container = None
        self._orchestrator = None
        self._backend_registry = None

    def _boot(self) -> None:
        if self._orchestrator is not None:
            return

        # Migrations before the container, exactly as create_app()'s
        # run_migrations_sync() does — replicated here rather than imported,
        # because importing api.py executes its module-level app boot.
        from src.platform.database import migration_manager

        # EPHEMERAL DB — re-point the shared Database singleton at a throwaway sqlite
        # file inside run_dir BEFORE migrations/injector, so the ENTIRE run
        # (migrations, generation + file records, history) targets it and never the
        # user's live storage/db.sqlite. The singleton reads self.db_path fresh on
        # every get_connection(), so re-pointing it switches every repository at
        # once — no import-ordering constraint. ORDERING NOTE: this must run before
        # has_pending_migrations() so migrations build the ephemeral schema, and
        # before the container is built so every constructed repository sees it.
        if self.run_dir is not None:
            import os

            from src.platform.database.database import db as _db_singleton

            suite_db = self.run_dir / "suite.db"
            self.run_dir.mkdir(parents=True, exist_ok=True)
            os.environ["POTIONUI_DB_PATH"] = str(suite_db)  # also for any child process
            _db_singleton.db_path = suite_db
            logger.info("preset-suite: using ephemeral DB at %s", suite_db)

        if migration_manager.has_pending_migrations():
            migration_manager.run_migrations()

        # With a fresh ephemeral schema, seed the one user row the generation FK
        # needs and redirect file storage into the run dir (see _prepare_ephemeral_db).
        # Pass the singleton resolved above explicitly rather than letting
        # _prepare_ephemeral_db re-import it: real run and tests then use the
        # same "caller hands over its db" contract.
        if self.run_dir is not None:
            self._prepare_ephemeral_db(db=_db_singleton)

        self._container = self._container_factory()

        # Register the builtin output-type handlers. They register as import-time
        # side effects (module-level output_type_registry.register(...) in each
        # handler module); nothing on the orchestrator's own import path pulls the
        # package in, so without this ImageGenerationOutput et al. would have no
        # handler and never get saved/thumbnailed the way the real app does.
        # (ErrorGenerationOutput deliberately has no side-effect handler — only a
        # serializer — so its "No handler registered" log line is a benign warning
        # that appears in the live app too, not a defect.)
        import src.features.generation.handlers  # noqa: F401 - registration side effects

        self._orchestrator = self._container.generation_orchestrator

        # Pipe discovery is lazy (the first get_pipe() triggers it), but force it
        # at boot so a pipe that fails to IMPORT — e.g. detailer/sdxl when cv2's
        # system libs (libglib/libgthread) are missing — is logged here instead of
        # surfacing later as a confusing "Pipe 'detailer/sdxl' not found in pipe
        # registry" mid-generation. Best-effort: a discovery hiccup must not abort
        # the whole run.
        try:
            pipes = self._container.pipe_catalog.get_available_pipes()
            logger.info("preset-suite boot: %d pipes discovered", len(pipes))
        except Exception as e:  # pragma: no cover - discovery is best-effort here
            logger.warning("preset-suite boot: pipe discovery raised: %s", e)

        try:
            self._backend_registry = self._container.backend_registry
        except Exception:  # pragma: no cover - registry optional for gating
            self._backend_registry = None

    def _prepare_ephemeral_db(self, *, db=None) -> None:
        """Seed the freshly-migrated ephemeral DB so a real generation runs fully
        isolated from the user's data:

          * create the suite user row (id == ``self.user_id``) — the generation /
            file-record inserts carry a ``user_id`` FK into ``users(id)``; without
            this row the image handler fails with ``FOREIGN KEY constraint failed``;
          * redirect ``file_storage_directory`` to ``<run_dir>/storage`` so saved
            images + thumbnails land in the run dir, not the user's gallery (the
            image handler reads this setting fresh on every save).

        Seeding itself is raw SQL down in `PresetSuiteRepository`: the
        settings/user rows may not exist yet in a fresh schema, and
        set_setting() only UPDATES existing rows.

        ``db``, when given, is written to explicitly instead of falling back to
        the process-default `Database` singleton — callers that bypass `_boot()`
        (the unit tests) MUST pass their own db handle, so the write can never
        land on whatever the default singleton happens to be at call time.
        """
        from src.features.preset_suite.repository import preset_suite_repo

        storage_dir = self.run_dir / "storage"
        # Mark the storage dir so cleanup is authorised to remove it later.
        from src.features.preset_suite import ephemeral
        ephemeral.mark(storage_dir)

        preset_suite_repo.seed_ephemeral(self.user_id, str(storage_dir), db=db)
        logger.info(
            "preset-suite: seeded ephemeral DB (user %r, file_storage_directory=%s)",
            self.user_id, storage_dir,
        )

    def can_run(self, preset_id: str, engine: str) -> Tuple[bool, str]:
        self._boot()
        if engine == "native":
            return True, ""
        # Non-native (e.g. comfyui): only runnable if a backend of that engine is
        # configured AND reachable/indexed. Ask the registry; be conservative.
        reg = self._backend_registry
        if reg is None:
            return False, f"engine '{engine}' needs a backend registry (unavailable)"
        try:
            has = bool(reg.get_backends_for_engine(engine))
        except Exception as e:  # pragma: no cover
            return False, f"could not query backends for engine '{engine}': {e}"
        if not has:
            return False, f"no configured backend for engine '{engine}'"
        return True, ""

    # Wall-clock ceiling for a single case when the case declares no
    # ``checks.max_seconds``; a case must FAIL with a clear reason rather than
    # hang the whole suite (a stuck generation would otherwise never return).
    DEFAULT_TIMEOUT_S: float = 600.0

    def run_case(
        self, preset_id: str, mode: str, form_data: dict,
        *, prompt: Optional[str] = None, negative_prompt: Optional[str] = None,
        max_seconds: Optional[float] = None,
    ) -> CaseOutcome:
        import asyncio

        self._boot()
        return asyncio.run(
            self._run_async(preset_id, mode, form_data, prompt, negative_prompt, max_seconds)
        )

    async def _run_async(
        self, preset_id: str, mode: str, form_data: dict,
        prompt: Optional[str], negative_prompt: Optional[str],
        max_seconds: Optional[float] = None,
    ) -> CaseOutcome:
        from src.features.generation.dto import GenerationRequest, PromptPair
        from src.pipelines.outputs import (
            GalleryGenerationOutput,
            ImageGenerationOutput,
            ErrorGenerationOutput,
        )

        images: list = []
        error_box: dict = {}
        done_event = asyncio.Event()

        async def _collect(generation_id: str, output) -> None:
            # Collector standing in for the WebSocket callback. The orchestrator
            # AWAITS this callback (orchestrator.py _handle_generation_output /
            # _finish_generation), so it MUST be a coroutine — a sync callable
            # returns None and `await None` raises TypeError, which then masks the
            # real generation error.
            #
            # The final (generation_id, None) call is the orchestrator's
            # authoritative completion signal — it fires for BOTH success and
            # failure (orchestrator.py:694 emits it after the backend's error
            # output too) — so it is the reliable "done" edge to wait on rather
            # than polling a tracker record that may be pruned.
            if output is None:
                done_event.set()
                return
            if isinstance(output, ImageGenerationOutput) and not output.temporary:
                images.append(output.image)
            elif isinstance(output, GalleryGenerationOutput):
                images.extend(img.image for img in output.images)
            elif isinstance(output, ErrorGenerationOutput):
                error_box["error"] = getattr(output, "error", "generation error")

        # Prompt goes on the request as the modern prompts[] pair the orchestrator's
        # expansion reads (prompts[0] == p_prompt); omit when the case set none.
        prompts = None
        if prompt is not None or negative_prompt is not None:
            prompts = [PromptPair(positive=prompt or "", negative=negative_prompt or "")]
        request = GenerationRequest(preset_id=preset_id, mode=mode, form_data=form_data, prompts=prompts)
        start = time.perf_counter()
        try:
            result = await self._orchestrator.start_generation(request, self.user_id, _collect)
        except Exception as e:  # noqa: BLE001
            return CaseOutcome(status="failed", error=str(e), seconds=time.perf_counter() - start)

        generation_id = result.get("generation_id")
        deadline = max_seconds if (max_seconds and max_seconds > 0) else self.DEFAULT_TIMEOUT_S
        try:
            await asyncio.wait_for(
                self._wait_for_completion(done_event, generation_id), timeout=deadline
            )
        except asyncio.TimeoutError:
            return CaseOutcome(
                status="failed", images=images,
                error=f"timed out waiting for completion after {deadline:.0f}s",
                seconds=time.perf_counter() - start,
            )

        seconds = time.perf_counter() - start
        state = await self._terminal_state(generation_id)
        if state == "completed":
            return CaseOutcome(status="completed", images=images, seconds=seconds)
        err = error_box.get("error") or f"generation {state or 'ended'}"
        return CaseOutcome(status="failed", images=images, error=err, seconds=seconds)

    async def _wait_for_completion(self, done_event, generation_id: str) -> None:
        """Return once the generation is done. Primary signal: the collector's
        ``(generation_id, None)`` completion callback sets ``done_event``.
        Fallback: poll the status tracker for a terminal state, in case that
        signal is ever missed (e.g. the record was pruned)."""
        terminal = {"completed", "failed", "cancelled"}
        while True:
            if done_event.is_set():
                return
            if await self._terminal_state(generation_id) in terminal:
                return
            try:
                await asyncio.wait_for(done_event.wait(), timeout=0.5)
                return
            except asyncio.TimeoutError:
                continue

    async def _terminal_state(self, generation_id: str) -> Optional[str]:
        """The generation's status string, or ``None`` if unknown. Handles a
        ``GenerationRecord`` (whose status lives at ``model_dump()['status']`` ==
        ``state.value``, NOT a ``.status`` attribute), a plain dict, or ``None``."""
        status = await self._orchestrator.get_generation_status(generation_id)
        if status is None:
            return None
        if isinstance(status, dict):
            return status.get("status")
        if hasattr(status, "model_dump"):
            return status.model_dump().get("status")
        state = getattr(status, "state", None)
        return getattr(state, "value", None)
