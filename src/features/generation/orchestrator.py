"""
Generation Orchestrator

Orchestrates the complete generation lifecycle from request to completion.

This is the main application layer component that coordinates between:
- API layer (controllers, DTOs)
- Core layer (backends, generation outputs)
- Infrastructure layer (database, WebSocket)

Responsibilities:
- Coordinating generation start/stop/cancel operations
- Selecting the backend that provides the engine the preset declares
- Building pipelines using PipelineBuilder
- Processing outputs through OutputProcessor
- Managing generation status via GenerationStatusTracker (the single owner
  of generation state - backends are stateless executors)
- Bridging thread-produced outputs to the event loop via OutputBridge
- Sending WebSocket notifications

All backends receive the same pipeline_data format with processed pipes.
Backend selection is based on the engine declared by the preset.

Example:
    orchestrator = GenerationOrchestrator(...)
    result = await orchestrator.start_generation(request, user_id)
    # Returns: {'generation_id': '...', 'status': {...}, 'backend': {...}}

    status = await orchestrator.get_generation_status(generation_id)
    success = await orchestrator.cancel_generation(generation_id)
"""

import asyncio
import logging
import time
from functools import partial
from typing import Dict, Any, List, Optional, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from src.features.notifications.manager import NotificationManager
    from src.features.presets.repository import DatabasePresetRepository
    from src.features.models.access_policy import ModelAccessPolicy
    from src.features.users.repository import UserRepository
    from src.features.stats.generation_stats_manager import GenerationStatsManager
    from src.features.media_index.manager import MediaIndexManager
    from src.platform.runtime.gpu import GpuManager

from src.platform.util.ids import generate_ulid
from src.features.generation.pipeline_builder import PipelineBuilder
from src.features.generation.output_processor import OutputProcessor
from src.features.generation.output_bridge import OutputBridge
from src.features.generation.queue import QueuedGeneration
from src.features.generation.queue_dispatcher import QueueDispatcher
from src.features.generation.prompt_expansion import PromptExpander
from src.features.generation.notifier import GenerationNotifier
from src.features.generation.status_tracker import (
    GenerationState,
    GenerationStatusTracker,
    TERMINAL_STATES,
)
from src.features.backends.backend_registry import BackendRegistry
from src.features.models.form_refs import collect_model_ids, resolve_form_model_refs
from src.features.models.exceptions import ModelAccessDeniedException
from src.pipelines.outputs import ErrorGenerationOutput, GenerationOutput, ProgressGenerationOutput
from src.features.generation.repository import generation_repo
from src.features.generation.records import Generation
from src.features.generation.exceptions import InvalidGenerationSourceException
from src.features.generation.temp_source_tracker import temp_source_tracker
from src.platform.websocket.connection_manager import ConnectionManager
from src.platform.settings.settings import SettingsManager
from src.platform.plugins.hooks import HookContext, await_hook_blocking_waits
from src.features.generation.hooks import GENERATION_HOOKS
from src.features.presets import PresetTemplateLoader
from src.features.video_director import apply_preset_mode_overlay, normalize_video_director
from src.features.music_director import (
    apply_preset_mode_overlay as apply_music_director_mode_overlay,
    compile_sections_to_lyrics,
    normalize_music_director,
)
from src.features.forms.binding import bind_form, FormBindingError

logger = logging.getLogger(__name__)

# Fires once per unindexed engine rather than on every generation submission.
_warned_unindexed_engine: set = set()

# Loaded weights run a little over their on-disk size (allocator slack, dtype
# staging). The resolution-scaled activation spike is priced separately below,
# so this stays a small weight-only overhead rather than the old flat 1.3.
_WEIGHT_LOAD_MARGIN = 1.1

# Sampling-phase activation spike over resident weights, per VAE-latent pixel.
# Mirrors `_SAMPLING_MB_PER_LATENT_PX` in
# src/platform/runtime/native/memory/tiering.py (0.1 MB/latent-px, calibrated
# as the total peak-over-weights). Reproduced here rather than imported: that
# module pulls in vendor.gpl/torch, too heavy for the submission path. At 1024²
# this is ~1.6 GB, the right order of magnitude for the DiT forward spike.
_SAMPLING_ACT_MB_PER_LATENT_PX = 0.1
_VAE_SPATIAL_DOWNSCALE = 8
# Video latents compress in time too (Wan 4x, LTX 8x); 4 is a nominal middle so
# the term is monotone in frame count without pretending to per-model accuracy.
_NOMINAL_TEMPORAL_DOWNSCALE = 4

# `form.upscale`'s two "on" values (see content/presets/marketplace/LTX-2/modes/video/
# pipeline.yml) to the rational scale `latent_upscaler/ltx`'s resampler
# actually runs -- "off" (or anything else) means no two-stage recipe, so
# `_check_ltx_two_stage_geometry` below has nothing to preflight.
_LTX_UPSCALE_SCALES = {"1.5x": 1.5, "2.0x": 2.0}


def _check_ltx_two_stage_geometry(preset_template, mode: str, form_data: Dict[str, Any]) -> None:
    """Preflight: fail BEFORE stage 1 ever runs when the LTX two-stage
    upscale's stage-2 resolution won't agree with what `latent_upscaler/ltx`
    will actually hand it -- see `src/pipelines/pipes/latent_upscaler/ltx/
    geometry.py`'s module docstring for why the two can disagree (a real gap
    in the 1.5x rational resampler's rounding, not a bug in either stage on
    its own). Previously this only surfaced as a 400 AFTER the expensive
    stage-1 render, as generator/video_ltx's "initial_latent token count"
    guard.

    Scoped narrowly: only the LTX family's Director `video` mode has an
    independent, separately-configured stage-2 resolution to disagree with
    (the standalone `upscale` mode derives its refine geometry entirely from
    the upsampled latent -- see that pipeline's header comment -- so it has
    nothing to preflight). Gated on the preset's own `tags` (the same
    family-tagging convention `@config:*_tags` model filters use), not a
    hardcoded preset id, so any current/future LTX-tagged preset sharing this
    `upscale`/`resolution` form shape is covered.
    """
    if mode != "video" or "ltx" not in (getattr(preset_template, "tags", None) or []):
        return
    scale = _LTX_UPSCALE_SCALES.get(form_data.get("upscale"))
    if scale is None:
        return
    wh = _parse_resolution(form_data)
    if wh is None:
        return
    width, height = wh

    from src.pipelines.pipes.latent_upscaler.ltx.geometry import (
        compute_two_stage_geometry,
        nearest_achievable_resolution,
        required_axis_divisor,
    )

    geometry = compute_two_stage_geometry(width, height, scale)
    if geometry.ok:
        return

    suggested_w, suggested_h = nearest_achievable_resolution(width, height, scale)
    upscale_label = form_data.get("upscale")
    divisor = required_axis_divisor(scale)
    raise ValueError(
        f"LTX {upscale_label} upscale of {width}x{height} is not achievable: the upsampled "
        f"stage-1 latent lands on a {geometry.actual_width_lat}x{geometry.actual_height_lat} grid, "
        f"but the refine stage is configured for {geometry.expected_width_lat}x{geometry.expected_height_lat} "
        f"(target resolution {geometry.stage2_width}x{geometry.stage2_height}) -- {upscale_label} only lands "
        f"on a clean grid when both the width and height are multiples of {divisor}px. "
        f"Nearest achievable resolution: {suggested_w}x{suggested_h}. You can also switch Upscale to 2.0x, "
        f"which is always achievable at any resolution."
    )


def _parse_resolution(form_data: Dict[str, Any]) -> Optional[tuple]:
    """(width, height) from the form's `resolution` "WxH" string or explicit
    width/height ints, or None when neither is present."""
    res = form_data.get("resolution")
    if isinstance(res, str) and "x" in res.lower():
        try:
            w_str, h_str = res.lower().split("x")
            return int(w_str.strip()), int(h_str.strip())
        except (ValueError, TypeError):
            pass
    w, h = form_data.get("width"), form_data.get("height")
    if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
        return w, h
    return None


def _frame_count(form_data: Dict[str, Any]) -> int:
    """Requested frame count from whichever key a preset uses, or 1 (still image).
    Video presets that don't expose a frame field fall through to 1 - their
    (large) model weights dominate the estimate regardless."""
    for key in ("num_frames", "frames", "video_length", "length", "frame_count"):
        value = form_data.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return 1


def _activation_headroom_gb(form_data: Dict[str, Any]) -> float:
    """Resolution/frames-scaled sampling activation spike, in GB. 0 when the form
    carries no resolution."""
    wh = _parse_resolution(form_data)
    if wh is None:
        return 0.0
    width, height = wh
    latent_px = (width / _VAE_SPATIAL_DOWNSCALE) * (height / _VAE_SPATIAL_DOWNSCALE)
    latent_frames = max(1, round(_frame_count(form_data) / _NOMINAL_TEMPORAL_DOWNSCALE))
    return latent_px * latent_frames * _SAMPLING_ACT_MB_PER_LATENT_PX / 1024.0


def _estimate_generation_vram_gb(form_data: Any) -> Optional[float]:
    """Best-effort VRAM need for a request, in GB - a documented LOWER BOUND.

    = (summed on-disk size of resolvable `model:<id>` references x weight-load
    margin) + a resolution/frames-scaled sampling activation term.

    Coverage: every model the form stores as a `model:<id>` reference. For the
    native presets (Krea-2/Qwen/Wan/LTX/Z-Image) the diffusion model, text
    encoder, VAE and LoRAs are all model-picker fields, so all are counted; a
    component a preset PINS in its config (not exposed as a picker) is outside
    form refs and is NOT counted. Partial coverage is intentional: an
    unresolvable size is skipped, so the sum is a lower bound - which, with the
    automatic "evict when estimate is null or exceeds free" rule, still errs
    toward evicting.

    Returns None only when NOTHING is resolvable: the request carries no model
    references, or none of the referenced models' sizes are indexed (weights are
    the anchor - an activation term alone would be a meaningless underestimate,
    and null routes the automatic flow to its safe "clear the room" default).
    Cheap: a couple of indexed-model lookups by id, no GPU/CUDA touched.
    """
    form_data = form_data or {}
    model_ids = collect_model_ids(form_data)
    if not model_ids:
        return None

    from src.features.models.repository import model_repo

    total_bytes = 0
    known = False
    for model_id in model_ids:
        try:
            model = model_repo.get_by_id(model_id, include_providers=False, include_tags=False)
        except Exception:
            model = None
        size = getattr(model, "file_size", None) if model is not None else None
        if size:
            total_bytes += int(size)
            known = True

    if not known:
        return None

    weights_gb = (total_bytes / (1024 ** 3)) * _WEIGHT_LOAD_MARGIN
    return round(weights_gb + _activation_headroom_gb(form_data), 2)


# The sibling-key suffix `bind_form` (src/features/forms/binding.py) passes
# through unstripped next to any declared media field. Kept as one constant
# so the wire shape only needs to change in one place.
_ORIGIN_SUFFIX = "__origin"


def _parse_generation_origins(form_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract and shape-validate every `<field>__origin` sibling key
    put on the wire, so a standalone run (e.g. Krea-2's Whole-Frame Enhance)
    can record which prior generation's output seeded its source field.

    Each recognized key must map to `{"generation_id": <non-empty str>,
    "file_index": <non-negative int>}`, and the field it names (`<field>`)
    must itself carry a truthy value in `form_data` - an origin link with
    nothing to be the origin OF is a malformed submission, not a link to
    silently drop.

    Returns one `{field_name, source_generation_id, source_file_index}` dict
    per origin key found (order follows `form_data` iteration; empty when
    the form carries none). Raises ValueError - mapped by the controller to
    the same 400 as any other submission validation failure - on a
    malformed shape.
    """
    form_data = form_data or {}
    origins: List[Dict[str, Any]] = []

    for key, value in form_data.items():
        if not key.endswith(_ORIGIN_SUFFIX):
            continue
        field_name = key[: -len(_ORIGIN_SUFFIX)]

        if not isinstance(value, dict):
            raise ValueError(f"'{key}' must be an object with generation_id and file_index")

        source_generation_id = value.get("generation_id")
        if not isinstance(source_generation_id, str) or not source_generation_id:
            raise ValueError(f"'{key}.generation_id' must be a non-empty string")

        source_file_index = value.get("file_index")
        if not isinstance(source_file_index, int) or isinstance(source_file_index, bool) or source_file_index < 0:
            raise ValueError(f"'{key}.file_index' must be a non-negative integer")

        if not form_data.get(field_name):
            raise ValueError(f"'{key}' has no corresponding value in field '{field_name}'")

        origins.append({
            "field_name": field_name,
            "source_generation_id": source_generation_id,
            "source_file_index": source_file_index,
        })

    return origins


def _validate_generation_origins(origins: List[Dict[str, Any]], user_id: str) -> None:
    """Confirm every `source_generation_id` an origin link names exists and
    belongs to `user_id`, BEFORE the submitting generation is persisted.

    Raises InvalidGenerationSourceException - never a plain ValueError - for
    a missing or foreign generation: the controller maps it to a 404, the
    same existence-concealing status `ModelNotFoundException` gets (a 403,
    or a message distinguishing "not found" from "not yours", would confirm
    the id exists to someone probing it).
    """
    for origin in origins:
        source = generation_repo.get_by_id(origin["source_generation_id"], user_id=user_id)
        if source is None:
            raise InvalidGenerationSourceException(
                "One or more referenced source generations are not available"
            )


class GenerationOrchestrator:
    """
    Orchestrates the complete generation lifecycle.

    This class is responsible for:
    - Starting new generations with backend selection
    - Building pipelines from form data
    - Handling generation outputs and status updates
    - Cancelling running generations
    - Broadcasting WebSocket updates
    - Owning generation state via GenerationStatusTracker
    """

    def __init__(
        self,
        pipeline_builder: PipelineBuilder,
        backend_registry: BackendRegistry,
        connection_manager: ConnectionManager,
        settings_manager: SettingsManager,
        output_processor: 'OutputProcessor',
        preset_template_loader: PresetTemplateLoader,
        status_tracker: Optional[GenerationStatusTracker] = None,
        plugin_registry: Optional['PluginRegistry'] = None,
        notification_manager: Optional['NotificationManager'] = None,
        database_preset_repository: Optional['DatabasePresetRepository'] = None,
        model_access_policy: Optional['ModelAccessPolicy'] = None,
        user_repository: Optional['UserRepository'] = None,
        generation_stats_manager: Optional['GenerationStatsManager'] = None,
        media_index_manager: Optional['MediaIndexManager'] = None,
        gpu_manager: Optional['GpuManager'] = None,
    ):
        """
        Initialize the generation orchestrator.

        Args:
            pipeline_builder: Builds pipeline configurations from form data
            backend_registry: Registry for selecting and accessing backends
            connection_manager: WebSocket connection manager for real-time updates
            settings_manager: Application settings manager
            output_processor: Processes generation outputs through handlers
            preset_template_loader: Loader for preset templates
            status_tracker: Single owner of generation state/progress
            plugin_registry: Optional plugin registry for hook execution (backward compatible)
            notification_manager: Optional notification manager - emits generation
                completed/failed notifications for the owning user (best-effort;
                never breaks completion handling)
            database_preset_repository: Reads a preset's stored admin per-field
                form overrides so `bind_form` can enforce them. `None`
                (e.g. in tests that don't need it) skips override lookup - the
                submission binds against the preset's own declared defaults only.
            model_access_policy: Verifies every `model:<id>` reference the bound
                form carries is one the requesting user may reach, except
                references living under a field `bind_form` pinned
                via an admin override (`BoundForm.admin_pinned`) - an
                admin-pinned hidden/locked model default bypasses the user's
                own model-access scope by design. `None` skips enforcement.
            user_repository: Resolves `user_id` to a full `User` (for
                `account_type`) for `model_access_policy`. `None` skips
                enforcement (paired with `model_access_policy` above).
            generation_stats_manager: Writes the durable per-generation
                resource/timing row at completion. `None` (e.g. tests
                that don't need it) skips the write entirely.
            media_index_manager: Queues a completed generation's final files
                for system tagging (best-effort; never breaks completion
                handling). `None` skips the enqueue entirely.
        """
        self.pipeline_builder = pipeline_builder
        self.preset_template_loader = preset_template_loader
        self.backend_registry = backend_registry
        self.generation_stats_manager = generation_stats_manager
        self.media_index_manager = media_index_manager
        self.connection_manager = connection_manager
        self.settings_manager = settings_manager
        self.output_processor = output_processor
        self.plugin_registry = plugin_registry
        self.notification_manager = notification_manager
        self.database_preset_repository = database_preset_repository
        self.model_access_policy = model_access_policy
        self.user_repository = user_repository
        self.gpu_manager = gpu_manager

        self.status_tracker = status_tracker or GenerationStatusTracker()

        # Nothing executes directly any more: work is enqueued, and the
        # dispatcher runs it when the target backend's single slot frees up.
        # Dispatch loops back into `_start_generation` because only the
        # orchestrator knows how to build and start a pipeline.
        self._queue_dispatcher = QueueDispatcher(
            status_tracker=self.status_tracker,
            dispatch=self._start_generation,
        )
        self._prompt_expander = PromptExpander(plugin_registry=plugin_registry)
        self._notifier = GenerationNotifier(notification_manager=notification_manager)

        # Keeps OutputBridge.run() consumer tasks alive (fire-and-forget
        # tasks would otherwise be eligible for GC mid-flight).
        self._bridge_tasks: Dict[str, asyncio.Task] = {}

        logger.debug("GenerationOrchestrator initialized")

    @property
    def queue(self):
        """The single generation queue, owned by the QueueDispatcher."""
        return self._queue_dispatcher.queue

    def _read_vram_gb(self) -> tuple:
        """(free_gb, total_gb) from the host GPU monitor, or (None, None).

        Reads NVML through `GpuManager` (the same source the resource trigger
        polls) - it does not initialize CUDA. None when no GpuManager was wired
        (e.g. tests) or the read fails, so the payload stays well-formed.
        """
        if self.gpu_manager is None:
            return None, None
        try:
            free_gb = round(self.gpu_manager.get_free_vram() / 1024, 2)
            total_gb = round(self.gpu_manager.get_total_vram() / 1024, 2)
            return free_gb, total_gb
        except Exception:
            logger.debug("before_start: VRAM read failed", exc_info=True)
            return None, None

    def _narrow_backends_by_availability(self, engine: str, form_data: Dict[str, Any]):
        """Restrict candidate backends to those holding every selected model.

        Returns None (meaning "do not narrow") when the form carries no model references,
        which is the case for legacy form data and for presets whose model fields still
        store plain paths.

        Narrowing is skipped entirely when no backend of this engine has been indexed.
        A configured-but-unindexed backend genuinely holds models; it has simply never
        been asked. Enforcing availability against an empty index would fail every
        generation on that engine rather than degrade to the previous behaviour.
        """
        from src.features.models.availability import candidate_backends
        from src.features.models.availability_repository import (
            model_availability_repo,
        )

        model_ids = collect_model_ids(form_data)
        if not model_ids:
            return None

        engine_backend_ids = [
            b.backend_id for b in self.backend_registry.get_backends_for_engine(engine)
        ]
        if not model_availability_repo.any_indexed(engine_backend_ids):
            if engine not in _warned_unindexed_engine:
                logger.warning(
                    f"No backend for engine '{engine}' has been indexed; skipping "
                    f"availability narrowing. Index the backend to enable model-aware routing."
                )
                _warned_unindexed_engine.add(engine)
            return None

        allowed = candidate_backends(engine, model_ids, self.backend_registry)
        logger.debug(
            f"Availability narrowed '{engine}' backends to {allowed or 'none'} "
            f"for {len(model_ids)} selected model(s)"
        )
        return allowed

    def _enforce_model_access(self, bound, user_id: str) -> None:
        """Verify every `model:<id>` reference in `bound.values` is one
        `user_id` may reach, skipping references that live only under a
        field name in `bound.admin_pinned`.

        A no-op when `model_access_policy`/`user_repository` weren't wired
        (e.g. most existing tests).

        Raises:
            ModelNotFoundException / ModelAccessDeniedException: propagated
                from ModelAccessPolicy.verify_model_access, mapped by the
                controller to the house 404-not-403 pattern.
        """
        if self.model_access_policy is None or self.user_repository is None:
            return

        pinned = set(bound.admin_pinned)
        model_ids: set = set()
        for field_name, value in bound.values.items():
            if field_name in pinned:
                continue
            model_ids.update(collect_model_ids(value))

        if not model_ids:
            return

        user = self.user_repository.get_by_id(user_id)
        if user is None:
            raise ModelAccessDeniedException(f"User '{user_id}' not found")

        for model_id in model_ids:
            self.model_access_policy.verify_model_access(model_id, user)

    async def start_generation(
        self,
        request,  # GenerationRequest type from API layer
        user_id: str,
        output_callback: Optional[Callable[[str, GenerationOutput], Any]] = None
    ) -> Dict[str, Any]:
        """
        Start a new generation with the given request.

        This method orchestrates the complete generation startup process:
        1. Selects appropriate backend
        2. Generates unique generation ID
        3. Creates database record
        4. Builds pipeline configuration (for local) or serializes request (for remote)
        5. Starts generation on backend with output callback
        6. Sets up status tracking and WebSocket subscriptions

        Args:
            request: Generation request with preset_id, form_data, etc.
            user_id: ID of the user making the request
            output_callback: Optional callback for generation outputs (used by controller)

        Returns:
            Dictionary containing:
            - generation_id: Unique ULID for this generation
            - status: GenerationStatus model dump
            - backend: Backend info (id, name, engine)

        Raises:
            ValueError: If no backend available or invalid preset

        Example:
            >>> result = await orchestrator.start_generation(request, 'user123')
            >>> print(result['generation_id'])
            '01ARZ3NDEKTSV4RRFFQ69G5FAV'
            >>> print(result['backend']['name'])
            'Local Generation'
        """
        try:
            logger.info(f"Starting generation for user={user_id}, preset={request.preset_id}")

            # The preset declares which engine its pipes speak; that engine
            # determines which backends can execute this generation.
            preset_template = self.preset_template_loader.load_preset_by_id(request.preset_id)
            if not preset_template:
                raise ValueError(f"Preset '{request.preset_id}' not found")

            engine = preset_template.engine
            logger.info(f"Preset '{request.preset_id}' requires engine: {engine}")

            # Bind the submission against the preset's form schema BEFORE
            # anything is persisted, normalized, or queued (spec §3): resolves
            # `form_name`, strips unknown keys, applies server-owned typed
            # defaults, validates required/range/option constraints, and
            # rejects any image/video/audio/media/file value that doesn't
            # resolve inside the user's storage root. Raises FormBindingError
            # (a ValueError) / FormNotFoundException, mapped by the controller.
            mode = getattr(request, 'mode', 'txt2img')
            storage_dir = self.settings_manager.get_file_storage_directory(user_id)
            field_overrides = None
            if self.database_preset_repository is not None:
                field_overrides = self.database_preset_repository.get_preset_form_overrides(
                    request.preset_id
                ).get(mode, {})
            bound = bind_form(
                preset_template,
                mode,
                getattr(request, 'form_name', None),
                request.form_data,
                user_id,
                storage_dir=storage_dir,
                field_overrides=field_overrides,
            )
            request.form_data = bound.values

            # Every `model:<id>` reference the bound form carries must be one
            # the requesting user may reach - except refs living under a field
            # bind_form pinned via an admin override (an admin-pinned
            # hidden/locked model default bypasses the user's own model-access
            # scope by design). Raises ModelNotFoundException/
            # ModelAccessDeniedException, mapped by the controller to the house
            # 404-not-403 pattern.
            self._enforce_model_access(bound, user_id)

            # A Video Director document, if present, is untrusted client input:
            # validate + canonicalize it against what this preset actually
            # supports before anything is persisted or queued. Raises
            # VideoDirectorValidationError (a ValueError) which the controller
            # maps to a 400 - nothing downstream ever sees a malformed document.
            if isinstance(request.form_data, dict) and isinstance(request.form_data.get('video_director'), dict):
                capabilities = (preset_template.vars or {}).get('video_director') or {}
                # A preset's Director capabilities can differ per preset mode
                # (e.g. MiniMax-H3's `video` vs `refs`) via
                # `preset_mode_overrides` -- `mode` here is that preset mode,
                # already resolved above. See apply_preset_mode_overlay().
                capabilities = apply_preset_mode_overlay(capabilities, mode)
                storage_dir = self.settings_manager.get_file_storage_directory(user_id)
                raw_doc = request.form_data['video_director']

                # The frontend's plain "seed" field (shared with every other
                # generation mode) still hardcodes -1 into the director
                # document's own settings.seed, so it never reaches the
                # normalizer. Let an explicit form seed override the
                # document's -1 here, before normalization resolves it
                # randomly, so the field a user actually sets is the seed
                # every mode (including chain, base+index per segment)
                # ends up using. A -1/absent form seed changes nothing:
                # normalize_video_director() still rolls its own.
                form_seed = request.form_data.get('seed')
                if isinstance(form_seed, int) and form_seed != -1:
                    raw_doc = {**raw_doc, 'settings': {**(raw_doc.get('settings') or {}), 'seed': form_seed}}

                request.form_data['video_director'] = normalize_video_director(
                    raw_doc, capabilities, storage_dir, request.form_data
                )

            # A Music Director document, same discipline as Video Director
            # above: untrusted client input, validated + canonicalized
            # against what this preset actually supports before anything is
            # persisted or queued. Raises MusicDirectorValidationError (a
            # ValueError) which the controller maps to a 400.
            if isinstance(request.form_data, dict) and isinstance(request.form_data.get('music_director'), dict):
                music_capabilities = (preset_template.vars or {}).get('music_director') or {}
                music_capabilities = apply_music_director_mode_overlay(music_capabilities, mode)
                storage_dir = self.settings_manager.get_file_storage_directory(user_id)
                raw_music_doc = request.form_data['music_director']

                # Same discipline as the Video Director form-seed override
                # above: the frontend's plain "seed" field (shared with every
                # other generation mode) still hardcodes -1 into the document's
                # own settings.seed, so it never reaches the normalizer. Let an
                # explicit form seed override the document's -1 here, before
                # normalization resolves it randomly, so the field a user
                # actually sets is the seed every reader of the normalized
                # document (including this preset's own pipeline) sees. A
                # -1/absent form seed changes nothing: normalize_music_director()
                # still rolls its own.
                music_form_seed = request.form_data.get('seed')
                if isinstance(music_form_seed, int) and music_form_seed != -1:
                    raw_music_doc = {**raw_music_doc, 'settings': {**(raw_music_doc.get('settings') or {}), 'seed': music_form_seed}}

                music_document = normalize_music_director(
                    raw_music_doc, music_capabilities, storage_dir, request.form_data
                )
                # `compile_sections_to_lyrics` (docs/music-director.md's
                # `compile: "single_shot"`) is not wired into the normalizer
                # itself -- it's a pure function a preset's pipeline calls
                # once its document is on hand. This is that call site: every
                # normalized document gets a `compiled_lyrics` key, always
                # present (empty string when `sections` is empty, e.g. a
                # `t2m` document) so a preset's pipeline.yml can read
                # `music_director.compiled_lyrics` unconditionally -- Jinja's
                # StrictUndefined would raise on a missing dict key, not just
                # a falsy one. A pipeline prefers this over its own plain
                # lyrics field whenever it's non-empty; see
                # content/presets/marketplace/MiniMax-Music3/modes/song/pipeline.yml.
                music_document['compiled_lyrics'] = compile_sections_to_lyrics(music_document.get('sections') or [])
                request.form_data['music_director'] = music_document

            # LTX two-stage upscale geometry: fail fast, before the (expensive)
            # stage-1 render, when the picked resolution/upscale combination
            # cannot possibly succeed. Raises ValueError, mapped by
            # the controller to the same 400 as any other validation_error.
            _check_ltx_two_stage_geometry(preset_template, mode, request.form_data or {})

            # `<field>__origin` sibling keys, if present, mark a media
            # field as seeded from a prior generation's output rather than a
            # bare upload - parse + validate them before anything is
            # persisted or queued, so a malformed or foreign reference never
            # reaches a `generation_sources` row. Raises ValueError (shape) /
            # InvalidGenerationSourceException (foreign/missing source,
            # 404-semantics) - both mapped by the controller.
            generation_origins = _parse_generation_origins(request.form_data or {})
            if generation_origins:
                _validate_generation_origins(generation_origins, user_id)

            # Select backend for this generation. When the form carries `model:<id>`
            # references, only backends holding every one of them can run it.
            backend_id = getattr(request, 'backend_id', None)
            allowed_backend_ids = self._narrow_backends_by_availability(
                engine, request.form_data or {}
            )
            backend = self.backend_registry.select_backend_for_generation(
                engine=engine,
                backend_id=backend_id,
                allowed_backend_ids=allowed_backend_ids,
            )

            logger.debug(f"Selected backend: {backend.name} (engine={backend.engine})")

            # Now that the executing backend is known, rewrite each model reference into
            # the engine-native string that backend expects. Values that are not model
            # references (legacy paths, preset defaults) pass through untouched.
            request.form_data = resolve_form_model_refs(
                request.form_data or {}, backend.backend_id
            )

            # Generate unique ID for this generation
            generation_id = generate_ulid()
            logger.debug(f"Generated generation_id: {generation_id}")

            # Execute generation.before_start hook
            form_data = request.form_data or {}
            if self.plugin_registry:
                pre_hook_form_data = form_data
                logger.debug(f"Executing {GENERATION_HOOKS.before_start} hook")
                vram_free_gb, vram_total_gb = self._read_vram_gb()
                context = HookContext(
                    hook_name=GENERATION_HOOKS.before_start,
                    plugin_id="system",
                    data={
                        "generation_id": generation_id,
                        "preset_id": request.preset_id,
                        "form_data": form_data,
                        "backend_id": backend.backend_id,
                        "user_id": user_id,
                        "vram_free_gb": vram_free_gb,
                        "vram_total_gb": vram_total_gb,
                        "vram_estimate_gb": _estimate_generation_vram_gb(form_data),
                    }
                )
                context, success = self.plugin_registry.execute_hook(
                    GENERATION_HOOKS.before_start,
                    context
                )
                # A before_start subscriber (e.g. an automation set to "wait for
                # this run to finish") may have deferred work whose completion the
                # generation should wait on before proceeding.
                await await_hook_blocking_waits(context)
                if not success:
                    logger.warning(
                        f"Some plugins failed during {GENERATION_HOOKS.before_start} hook"
                    )
                # Update form_data if modified by plugins
                form_data = context.data.get("form_data", form_data)
                hook_changed_form_data = form_data != pre_hook_form_data
                logger.debug(f"Hook execution complete (modified: {hook_changed_form_data})")

                if hook_changed_form_data:
                    # A hook can replace form_data wholesale with no guarantee it
                    # still satisfies the preset's form schema, so re-run the same
                    # bind_form boundary used at submission time before it reaches
                    # pipeline building/persistence. Raises FormBindingError /
                    # FormNotFoundException, caught by the same handlers as the
                    # original bind (routes.py maps them to 422/404).
                    logger.debug(
                        f"before_start hook modified form_data for {generation_id}; re-binding"
                    )
                    bound = bind_form(
                        preset_template,
                        mode,
                        bound.form_name,
                        form_data,
                        user_id,
                        storage_dir=storage_dir,
                        field_overrides=field_overrides,
                    )
                    form_data = bound.values

                # Update request with potentially modified (and re-validated) form_data
                request.form_data = form_data

            # Create database record (`mode` was already resolved above, for bind_form)
            prompt_state = getattr(request, 'prompt_state', None)

            db_generation = Generation(
                id=generation_id,
                preset_id=request.preset_id,
                preset_version=None,  # Will be set later if native backend
                form_data=request.form_data or {},
                user_id=user_id,
                status='pending',
                backend_id=backend.backend_id,
                tab_id=getattr(request, 'tab_id', None),
                mode=mode,
                prompt_state=prompt_state,
                # The resolved form variant (`docs/presets.md` "Variants"), from the
                # bind_form boundary above - never the raw, possibly-`None` client
                # request.form_name - so reuse can restore the exact variant that
                # actually ran even when the client submitted no explicit form_name
                # and the mode's default was used.
                form_name=bound.form_name,
                source_prompt_id=getattr(request, 'source_prompt_id', None)
            )
            generation_repo.create(db_generation)
            logger.debug(f"Created database record for generation {generation_id}")

            # Persist the provenance links validated above, now that
            # generation_id exists for them to point at.
            if generation_origins:
                from src.features.generation.source_repository import generation_source_repo
                try:
                    generation_source_repo.create_for_generation(generation_id, generation_origins)
                    logger.debug(
                        f"Persisted {len(generation_origins)} source link(s) for generation {generation_id}"
                    )
                except Exception as e:
                    logger.warning(f"Failed to persist source links for generation {generation_id}: {e}")

            # Apply auto-tags if provided
            if request.tag_ids:
                from src.features.tags.repository import TagRepository
                tag_repo = TagRepository()
                for tag_id in request.tag_ids:
                    try:
                        tag_repo.add_tag_to_generation(generation_id, tag_id)
                    except Exception as e:
                        logger.warning(f"Failed to apply auto-tag {tag_id} to generation {generation_id}: {e}")
                logger.debug(f"Applied {len(request.tag_ids)} auto-tag(s) to generation {generation_id}")

            # Add the new generation to any auto-collections selected by the
            # originating tab. operations.add_members verifies that each
            # target is owned by this user before creating the membership.
            collection_ids = getattr(request, 'collection_ids', None)
            if isinstance(collection_ids, (list, tuple)) and collection_ids:
                from src.features.collections import operations as collection_operations
                from src.features.collections.repository import CollectionRepository
                collection_repository = CollectionRepository()
                for collection_id in collection_ids:
                    try:
                        collection_operations.add_members(collection_repository, collection_id, [generation_id], user_id, "history")
                    except Exception as e:
                        logger.warning(
                            f"Failed to apply auto-collection {collection_id} "
                            f"to generation {generation_id}: {e}"
                        )
                logger.debug(
                    f"Applied {len(collection_ids)} auto-collection(s) "
                    f"to generation {generation_id}"
                )

            # Persist the detached rich segment metadata and resolved phrasebook values.
            if request.segments:
                from src.features.generation.segment_repository import generation_segment_repo
                try:
                    generation_segment_repo.create_for_generation(generation_id, request.segments)
                    logger.debug(f"Persisted {len(request.segments)} segment(s) for generation {generation_id}")
                except Exception as e:
                    logger.warning(f"Failed to persist segments for generation {generation_id}: {e}")

            # Create status tracking record (creation itself is already
            # reflected in the DB row created above with status='pending';
            # transition() is reserved for state changes after this point)
            record = self.status_tracker.create(
                id=generation_id,
                preset_id=request.preset_id,
                backend_id=backend.backend_id,
                user_id=user_id,
                tab_id=getattr(request, 'tab_id', None),
            )
            logger.debug(f"Created status tracking for {generation_id}")

            # Enqueue rather than execute. If the backend is idle the queue
            # dispatches synchronously here, so the common single-generation
            # case behaves exactly as it did before the queue existed.
            logger.info(f"Queueing generation on {backend.name} (engine={backend.engine})")
            await self._queue_dispatcher.enqueue(QueuedGeneration(
                generation_id=generation_id,
                backend_id=backend.backend_id,
                user_id=user_id,
                tab_id=getattr(request, 'tab_id', None),
                payload={
                    'request': request,
                    'backend': backend,
                    'db_generation': db_generation,
                    'output_callback': output_callback,
                },
            ))

            await self._queue_dispatcher.publish_positions()

            queue_position = self._queue_dispatcher.position(generation_id)
            logger.info(
                f"Generation {generation_id} "
                + (f"queued at position {queue_position}" if queue_position is not None
                   else f"started on {backend.name}")
            )

            return {
                'generation_id': generation_id,
                'status': record.model_dump(),
                'queue_position': queue_position,
                'backend': {
                    'id': backend.backend_id,
                    'name': backend.name,
                    'engine': backend.engine
                }
            }

        except Exception as e:
            logger.error(f"Failed to start generation: {str(e)}", exc_info=True)
            raise

    def set_queue_listener(self, listener: Callable[[str, Dict[str, Any]], Any]) -> None:
        """Register the callback that broadcasts `queue_update` messages."""
        self._queue_dispatcher.set_queue_listener(listener)

    def _expand_prompts_per_image(
        self,
        generation_id: str,
        request,  # GenerationRequest type
        prompts: Optional[List[Dict[str, str]]],
    ) -> Optional[List[Dict[str, str]]]:
        """Expand the authored prompt template into one realization per image.

        Delegates to PromptExpander; see prompt_expansion.py."""
        return self._prompt_expander.expand_per_image(generation_id, request, prompts)

    async def _start_generation(
        self,
        generation_id: str,
        request,  # GenerationRequest type
        backend,
        db_generation: Generation,
        output_callback: Optional[Callable]
    ):
        """
        Start generation on the selected backend.

        Uses PipelineBuilder to transform form data into executable pipeline
        configuration. All backends receive the same pipeline_data format,
        and a fresh OutputBridge funnels their outputs back to this
        orchestrator in order, without blocking the backend's execution
        thread.

        Args:
            generation_id: Generated ULID for this generation
            request: Original generation request
            backend: Selected backend instance
            db_generation: Database generation record
            output_callback: Callback for generation outputs
        """
        logger.debug(f"Building pipeline for generation {generation_id}")

        # Build pipeline configuration using PipelineBuilder
        # Extract request parameters - prompts is already normalized by DTO validator
        prompts = getattr(request, 'prompts', None)
        # Convert PromptPair objects to dicts if needed
        if prompts is not None:
            prompts = [{'positive': p.positive, 'negative': p.negative} if hasattr(p, 'positive') else p for p in prompts]

        prompts = self._expand_prompts_per_image(generation_id, request, prompts)

        mode = db_generation.mode

        logger.debug(f"Building pipeline: mode={mode}, prompts_count={len(prompts) if prompts else 0}")

        try:
            built_pipeline = self.pipeline_builder.build_pipeline(
                preset_id=request.preset_id,
                form_data=request.form_data or {},
                mode=mode,
                generation_id=generation_id,
                prompts=prompts,
                form_name=getattr(request, 'form_name', None),
                user_id=db_generation.user_id,
            )
            logger.info(f"Pipeline built with {len(built_pipeline.pipes)} pipes")
        except Exception as e:
            logger.error(f"Failed to build pipeline: {str(e)}", exc_info=True)
            await self.status_tracker.transition_async(generation_id, GenerationState.FAILED, error=str(e))
            self._notify_generation_failure(generation_id, str(e))
            self._queue_dispatcher.prune_finished()
            raise

        # Update preset version in database if available
        if built_pipeline.preset_template:
            preset_version = getattr(built_pipeline.preset_template, 'version', None)
            if preset_version:
                logger.debug(f"Updating preset version to {preset_version}")
                generation_repo.update_preset_version(generation_id, preset_version)

        bridge = OutputBridge(
            on_output=partial(
                self._handle_generation_output,
                generation_id,
                engine=backend.engine,
                output_callback=output_callback,
            )
        )
        bridge_task = asyncio.create_task(bridge.run())
        self._bridge_tasks[generation_id] = bridge_task
        bridge_task.add_done_callback(lambda _t, gid=generation_id: self._bridge_tasks.pop(gid, None))

        # Start generation with processed pipeline data. Backends receive a
        # plain sync callable (`bridge.emit`) they can call from any thread
        # (including a background pipe-execution thread) without blocking.
        await backend.start_generation(built_pipeline.to_backend_payload(), bridge.emit)
        logger.info(f"Generation {generation_id} started successfully on {backend.name}")

    def _notify_generation_failure(
        self,
        generation_id: str,
        error: str,
        detail: Optional[str] = None,
    ) -> None:
        """Raise a persistent, toast-surfaced notification for a failed
        generation. Delegates to GenerationNotifier; see notifier.py."""
        self._notifier.notify_failure(generation_id, error, detail)

    async def _handle_generation_output(
        self,
        generation_id: str,
        output: Optional[GenerationOutput],
        engine: str,
        output_callback: Optional[Callable]
    ):
        """
        Handle generation output from any backend (invoked by OutputBridge's
        consumer, in order).

        This method processes outputs:
        1. Checks if generation still exists
        2. Detects completion signal (output=None)
        3. Drops stale progress updates once the generation has reached a
           terminal state (e.g. progress arriving after cancellation)
        4. Updates tracked status/progress
        5. Processes output through handlers (via OutputProcessor)
        6. Notifies controller callback for WebSocket broadcast

        Args:
            generation_id: ID of the generation
            output: Generation output object (None signals completion)
            engine: Engine of the executing backend, for logging context
            output_callback: Controller callback for WebSocket broadcast
        """
        record = self.status_tracker.get(generation_id)
        if record is None:
            logger.warning(f"Received output for unknown generation {generation_id}")
            return

        # Check for completion signal
        if output is None:
            logger.info(f"Generation {generation_id} completed")
            await self._handle_generation_completion(generation_id, output_callback)
            return

        # Drop stale progress updates once the generation has already
        # reached a terminal state (e.g. progress emitted after cancel).
        if record.state.value in TERMINAL_STATES and isinstance(output, ProgressGenerationOutput):
            logger.debug(f"Dropping stale progress output for terminal generation {generation_id}")
            return

        # A cancel already transitioned the record and broadcast
        # generation_cancelled; an error surfacing afterward (e.g. a pipe's
        # SamplingCancelled unwinding through a wrapper that reports it as an
        # error) must not relabel that as a failure.
        if record.state == GenerationState.CANCELLED and isinstance(output, ErrorGenerationOutput):
            logger.debug(f"Dropping error output for already-cancelled generation {generation_id}")
            return

        logger.debug(f"Processing output for {generation_id}: {type(output).__name__}")

        if isinstance(output, ErrorGenerationOutput):
            await self.status_tracker.transition_async(generation_id, GenerationState.FAILED, error=output.error)
            self._notify_generation_failure(generation_id, output.error, output.detail)
        else:
            self.status_tracker.update_from_output(generation_id, output)

        # Process output through OutputProcessor (handles file saving, etc.)
        generation = generation_repo.get_by_id(generation_id)
        user_id = generation.user_id if generation else None

        handler_metadata: Optional[Dict[str, Any]] = None
        try:
            handler_metadata = await self.output_processor.process_output(
                generation_id, output, user_id
            )
            if handler_metadata.get('processed'):
                logger.debug(
                    f"Processed {type(output).__name__} with {handler_metadata.get('handler', 'Unknown')}"
                )
        except Exception as e:
            logger.error(f"Error processing output through OutputProcessor: {str(e)}", exc_info=True)

        # A final (non-temporary) image/video/audio save failing (disk full,
        # permission denied, ...) is caught and swallowed inside the handler -
        # it comes back as metadata, not an exception - so it would otherwise
        # sail through to COMPLETED. Treat it exactly like a pipe-raised
        # ErrorGenerationOutput: fail the generation and replace the output
        # that reaches the client with the error, instead of the workbench
        # update for a file that was never actually written.
        save_error = self._final_save_error(output, handler_metadata)
        if save_error:
            await self.status_tracker.transition_async(generation_id, GenerationState.FAILED, error=save_error)
            self._notify_generation_failure(generation_id, save_error)
            output = ErrorGenerationOutput(
                error=save_error,
                pipe_id=getattr(output, 'pipe_id', None),
                pipe_name=getattr(output, 'pipe_name', None),
            )

        # Notify callback if provided (this triggers WebSocket broadcast in controller)
        if output_callback:
            await output_callback(generation_id, output)

    @staticmethod
    def _final_save_error(
        output: GenerationOutput,
        handler_metadata: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        """Detect a failed disk save for a non-temporary (final) output.

        Handlers (image/video/audio) catch their own save exceptions and
        report failure via `handler_metadata['processed'] = False` and/or a
        `save_error` message rather than raising - this is the one place
        that turns that metadata back into a generation failure.
        """
        if handler_metadata is None or getattr(output, 'temporary', True) is not False:
            return None
        if handler_metadata.get('processed') is False:
            return (
                handler_metadata.get('error')
                or handler_metadata.get('save_error')
                or "Failed to save generation output"
            )
        return handler_metadata.get('save_error')

    async def _handle_generation_completion(
        self,
        generation_id: str,
        output_callback: Optional[Callable]
    ):
        """
        Handle generation completion (the ``None`` sentinel), then free the
        backend so the queue can dispatch whatever is waiting on it.

        The release is in a ``finally``: an after_complete hook or a broadcast
        that raises must not strand a backend slot forever, which would wedge
        every generation queued behind it.
        """
        logger.info(f"Completing generation {generation_id}")

        record = self.status_tracker.get(generation_id)
        if record is None:
            return

        try:
            await self._finish_generation(generation_id, record, output_callback)
        finally:
            if record.backend_id:
                await self._queue_dispatcher.release(record.backend_id, generation_id)
            self._queue_dispatcher.prune_finished()

    async def _finish_generation(
        self,
        generation_id: str,
        record,
        output_callback: Optional[Callable]
    ):
        """
        Transition status (unless the generation already reached a terminal
        state such as FAILED/CANCELLED, in which case that state wins), fire
        hooks, and notify the callback.
        """
        # From dispatch, not from enqueue: a generation that waited an hour in
        # the queue took however long it actually ran, not an hour.
        duration = time.time() - (record.started_at or record.created_at)

        if record.state not in (GenerationState.FAILED, GenerationState.CANCELLED):
            record = await self.status_tracker.transition_async(generation_id, GenerationState.COMPLETED)
            logger.debug(f"Updated status to completed for {generation_id}")

        # Terminal for every outcome (completed/failed/cancelled all reach
        # here) -- the one place safe to unlink this generation's tracked temp
        # video sources, since every handler that needed their bytes already
        # ran.
        try:
            removed = temp_source_tracker.cleanup(generation_id)
            if removed:
                logger.debug(f"Removed {removed} temp video source(s) for {generation_id}")
        except Exception:
            logger.exception(f"failed cleaning up temp video sources for {generation_id}")

        generation = generation_repo.get_by_id(generation_id)

        # Write the durable generation_stats row. Only for a run that
        # actually reached COMPLETED -- a failed/cancelled run's partial
        # duration/resources are noise, not a data point a preset's cold/warm
        # averages should absorb. Every step here is best-effort: a stats
        # write must never be able to break generation completion.
        if self.generation_stats_manager is not None and record.state == GenerationState.COMPLETED:
            try:
                engine = None
                resource_stats: Optional[Dict[str, Any]] = None
                backend = self.backend_registry.get_backend(record.backend_id) if record.backend_id else None
                if backend is not None:
                    engine = getattr(backend, "engine", None)
                    gen_manager = getattr(backend, "generation_manager", None)
                    pop = getattr(gen_manager, "pop_resource_stats", None)
                    if callable(pop):
                        resource_stats = pop(generation_id)
                resource_stats = resource_stats or {}

                self.generation_stats_manager.record_completion(
                    generation_id=generation_id,
                    preset_id=record.preset_id,
                    engine=engine,
                    backend_id=record.backend_id,
                    duration_ms=int(duration * 1000),
                    cold_start=resource_stats.get("cold_start"),
                    model_load_ms=resource_stats.get("model_load_ms"),
                    peak_vram_mb=resource_stats.get("peak_vram_mb"),
                    peak_ram_mb=resource_stats.get("peak_ram_mb"),
                    cpu_percent=resource_stats.get("cpu_percent"),
                )
            except Exception:
                logger.exception(f"failed recording generation_stats for {generation_id}")

        # Queue the run's final files for system tagging (best-effort, like the
        # stats write above: indexing must never break generation completion).
        if self.media_index_manager is not None:
            try:
                self.media_index_manager.on_generation_complete(
                    generation_id, record.state.value
                )
            except Exception:
                logger.exception(f"failed queueing media index for {generation_id}")

        # Execute generation.after_complete hook
        if self.plugin_registry:
            logger.debug(f"Executing {GENERATION_HOOKS.after_complete} hook")

            context = HookContext(
                hook_name=GENERATION_HOOKS.after_complete,
                plugin_id="system",
                data={
                    "generation_id": generation_id,
                    "status": record.state.value,
                    "duration": duration,
                    "outputs": [],  # Could be populated from database if needed
                    "preset_id": record.preset_id,
                    "user_id": generation.user_id if generation else None
                }
            )
            context, success = self.plugin_registry.execute_hook(
                GENERATION_HOOKS.after_complete,
                context
            )
            if not success:
                logger.warning(
                    f"Some plugins failed during {GENERATION_HOOKS.after_complete} hook"
                )
            logger.debug("Hook execution complete")

        # Notify the owning user of completion/failure (best-effort). CANCELLED
        # is user-initiated and intentionally not notified.
        self._notifier.notify_completion(generation_id, record, generation, duration)

        # Notify callback about completion (with None to signal completion)
        if output_callback:
            await output_callback(generation_id, None)

    async def get_generation_status(self, generation_id: str) -> Optional[Any]:
        """
        Get the current status of a generation.

        Args:
            generation_id: ID of the generation

        Returns:
            GenerationRecord (duck-types GenerationStatus via .model_dump()) or None if not found

        Example:
            >>> status = await orchestrator.get_generation_status('01ARZ...')
            >>> print(status.status)
            'running'
        """
        return self.status_tracker.get(generation_id)

    async def cancel_generation(self, generation_id: str) -> bool:
        """
        Cancel a running generation.

        This method:
        1. Checks if generation exists and is cancellable
        2. Attempts to cancel on backend
        3. Transitions status to cancelled (writes to DB)

        Args:
            generation_id: ID of the generation to cancel

        Returns:
            True if cancelled successfully, False otherwise

        Example:
            >>> success = await orchestrator.cancel_generation('01ARZ...')
            >>> if success:
            ...     print("Generation cancelled")
        """
        record = self.status_tracker.get(generation_id)
        if record is None:
            logger.warning(f"Cannot cancel unknown generation {generation_id}")
            return False

        # Check if already completed
        if record.state.value in TERMINAL_STATES:
            logger.debug(
                f"Cannot cancel generation {generation_id} with status {record.state.value}"
            )
            return False

        logger.info(f"Cancelling generation {generation_id}")

        # A generation still waiting in the queue has never reached a backend:
        # dropping it from the queue is the whole cancellation. Going to the
        # backend here would be wrong - it would find nothing running under this
        # id, and (before the id check landed) could have aborted someone else's.
        if await self._queue_dispatcher.cancel(generation_id):
            await self.status_tracker.transition_async(generation_id, GenerationState.CANCELLED)
            self._queue_dispatcher.prune_finished()
            await self._queue_dispatcher.publish_positions()
            logger.info(f"Cancelled queued generation {generation_id} before it started")
            return True

        # Get backend and attempt cancellation
        backend_id = record.backend_id
        if backend_id:
            try:
                backend = self.backend_registry.get_backend(backend_id)
                if backend:
                    success = await backend.cancel_generation(generation_id)
                    if success:
                        logger.info(
                            f"Cancelled generation {generation_id} on backend {backend.name}"
                        )
                    else:
                        logger.warning(
                            f"Backend {backend.name} could not cancel {generation_id}"
                        )
            except Exception as e:
                logger.error(
                    f"Error cancelling {generation_id} on backend {backend_id}: {str(e)}",
                    exc_info=True
                )

        await self.status_tracker.transition_async(generation_id, GenerationState.CANCELLED)
        self._queue_dispatcher.prune_finished()
        logger.debug(f"Updated status to cancelled for {generation_id}")

        return True

    async def clear_tab_queue(self, user_id: str, tab_id: str) -> List[str]:
        """Drop every *pending* generation belonging to a tab.

        Delegates to QueueDispatcher; see queue_dispatcher.py."""
        return await self._queue_dispatcher.clear_tab_queue(user_id, tab_id)

    def get_queue_snapshot(
        self,
        user_id: str,
        tab_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """The caller's view of the queue: their pending and running work.

        Delegates to QueueDispatcher; see queue_dispatcher.py."""
        return self._queue_dispatcher.get_queue_snapshot(user_id, tab_id)

    def list_active_generations(self) -> list:
        """
        List all currently active generations.

        Returns:
            List of generation status dictionaries

        Example:
            >>> generations = orchestrator.list_active_generations()
            >>> print(len(generations))
            3
        """
        return [record.model_dump() for record in self.status_tracker.list_active()]
