import asyncio
import threading
import time
from pathlib import Path
from typing import Dict, Any, Optional


from src.pipelines.outputs import (
    ErrorGenerationOutput,
    GenerationOutput,
    ProgressGenerationOutput,
    TimerGenerationOutput,
)
from src.features.generation.error_classification import classify_generation_error
from src.platform.assets import AssetFetcher
from src.platform.runtime.gpu import GpuMonitor
from src.platform.observability.system_probe import SystemMonitor
from src.platform.runtime.memory_advisor import MemoryAdvisor
from src.platform.observability.logger import logger
from src.platform.security.redaction import mask_secret_value, redact_mapping
from src.features.models.directory import ModelDirectories
from src.pipelines.catalog import PipeCatalog
from src.pipelines.contracts import BasePipe
from src.platform.settings.settings import Settings
from src.pipelines.contracts import PipeInput, IOType, resolve_display_title
from src.pipelines.outputs import Icon
from src.platform.plugins.hooks import HookContext
from src.features.generation.hooks import PIPE_HOOKS
from src.platform.observability.profiling import get_profiler

def deep_update(original: Dict[Any, Any], updates: Dict[Any, Any]) -> Dict[Any, Any]:
    """
    Recursively update 'original' dict with 'updates'.
    """
    for k, v in updates.items():
        # If both original[k] and v are dicts, recurse.
        if (
                k in original
                and isinstance(original[k], dict)
                and isinstance(v, dict)
        ):
            deep_update(original[k], v)
        else:
            # Otherwise just overwrite/assign.
            original[k] = v
    return original


def validate_pipe_configuration(pipe_class: type, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate pipe configuration against the pipe's configuration specifications.

    Args:
        pipe_class: The pipe class to validate against
        config: Configuration dictionary to validate

    Returns:
        Validated configuration dictionary

    Raises:
        ValueError: If configuration is invalid
    """
    try:
        config_specs = pipe_class.configuration()
    except Exception as e:
        logger.warning(f"[VALIDATION] Could not get configuration specs for {pipe_class.name}: {e}")
        return config

    # Create a mapping of parameter names to their specifications
    spec_map = {spec.name: spec for spec in config_specs}

    validated_config = {}

    # Preserve unknown parameters (like backend_config injected by backends)
    # These are not in the pipe's spec but may be needed for runtime functionality
    for param_name, value in config.items():
        if param_name not in spec_map:
            logger.debug(f"[VALIDATION] Preserving injected parameter '{param_name}' for pipe '{pipe_class.name}'")
            validated_config[param_name] = value

    # Validate each specification
    for spec in config_specs:
        param_name = spec.name
        provided_value = config.get(param_name)

        # Handle required parameters
        if spec.required and provided_value is None:
            if spec.default is not None:
                validated_config[param_name] = spec.default
                logger.debug(f"[VALIDATION] Using default value for required parameter '{param_name}' in pipe '{pipe_class.name}': {mask_secret_value(param_name, spec.default)}")
            else:
                raise ValueError(f"Required parameter '{param_name}' is missing for pipe '{pipe_class.name}'")
            continue

        # Use default value if no value provided
        if provided_value is None:
            validated_config[param_name] = spec.default
            continue

        # A blank optional numeric/bool is "not provided": pipeline.yml cannot
        # omit a mapping key, so a templated field left empty renders as "".
        if provided_value == "" and not spec.required and spec.param_type not in (str, None):
            validated_config[param_name] = spec.default
            continue

        # Type validation
        if spec.param_type and not isinstance(provided_value, spec.param_type):
            # Try to convert the value
            try:
                if spec.param_type == bool and isinstance(provided_value, str):
                    # Handle string boolean values
                    validated_value = provided_value.lower() in ('true', '1', 'yes', 'on')
                elif spec.param_type in (int, float) and isinstance(provided_value, (int, float, str)):
                    # Handle numeric conversions
                    validated_value = spec.param_type(provided_value)
                elif spec.param_type == str:
                    # Convert to string
                    validated_value = str(provided_value)
                else:
                    # Try direct conversion
                    validated_value = spec.param_type(provided_value)
            except (ValueError, TypeError):
                raise ValueError(
                    f"Parameter '{param_name}' for pipe '{pipe_class.name}' must be of type {spec.param_type.__name__}, "
                    f"but got {type(provided_value).__name__}: {provided_value}"
                )
        else:
            validated_value = provided_value

        # Choices validation
        if spec.choices:
            if isinstance(validated_value, list):
                # For list parameters, check that all items are valid choices
                for item in validated_value:
                    if item not in spec.choices:
                        raise ValueError(
                            f"Parameter '{param_name}' for pipe '{pipe_class.name}' list item '{item}' must be one of {spec.choices}"
                        )
            else:
                # For single values, check against choices
                if validated_value not in spec.choices:
                    raise ValueError(
                        f"Parameter '{param_name}' for pipe '{pipe_class.name}' must be one of {spec.choices}, "
                        f"but got: {validated_value}"
                    )

        # Range validation for numeric types
        if spec.param_type in (int, float) and isinstance(validated_value, (int, float)):
            if spec.min_value is not None and validated_value < spec.min_value:
                raise ValueError(
                    f"Parameter '{param_name}' for pipe '{pipe_class.name}' must be >= {spec.min_value}, "
                    f"but got: {validated_value}"
                )
            if spec.max_value is not None and validated_value > spec.max_value:
                raise ValueError(
                    f"Parameter '{param_name}' for pipe '{pipe_class.name}' must be <= {spec.max_value}, "
                    f"but got: {validated_value}"
                )

        validated_config[param_name] = validated_value

    # Optional cross-field guard (BasePipe.validate_config, default no-op): the
    # per-parameter checks above can't express "these two valid fields don't make
    # sense together". A ValueError gets the same structured-validation treatment;
    # anything else is a bug in the hook, logged and swallowed.
    try:
        pipe_class.validate_config(validated_config)
    except ValueError:
        raise
    except Exception as e:
        logger.warning(f"[VALIDATION] validate_config hook raised unexpectedly for pipe '{pipe_class.name}': {e}")

    logger.debug(f"[VALIDATION] Configuration validated for pipe '{pipe_class.name}': {len(validated_config)} parameters")
    return validated_config

class GenerationEngine:

    def __init__(
            self,
            gpu: GpuMonitor,
            model_directories: ModelDirectories,
            pipe_catalog: PipeCatalog,
            settings: Settings,
            system_monitor: SystemMonitor,
            memory_advisor: MemoryAdvisor,
            llm_service,  # Import will be added separately to avoid circular dependency
            models=None,  # ModelLifecycle - central model/artifact cache
            plugin_registry=None,  # Optional PluginRegistry for backward compatibility
            assets: Optional[AssetFetcher] = None,
    ):
        self.gpu_monitor = gpu
        self.model_directories = model_directories
        self.pipe_catalog = pipe_catalog
        self.settings = settings
        self.system_monitor = system_monitor
        self.memory_advisor = memory_advisor
        self.llm_service = llm_service
        self.models = models
        self.plugin_registry = plugin_registry
        self.assets = assets

        self._cancelled = False
        self._running_generation_id: Optional[str] = None
        self._run_lock = threading.Lock()

        # Persistent psutil.Process() so cpu_percent() deltas are meaningful
        # across calls; the per-generation snapshot `generate()` writes is read
        # once by the orchestrator via `pop_resource_stats`.
        import psutil
        self._proc = psutil.Process()
        self._resource_stats: Dict[str, Dict[str, Any]] = {}

    def pop_resource_stats(self, generation_id: str) -> Optional[Dict[str, Any]]:
        """Read-once accessor for the resource snapshot `generate()` captured
        for `generation_id` (cold_start, model_load_ms, peak_vram_mb,
        peak_ram_mb, cpu_percent). Pops so this dict never grows unbounded;
        returns ``None`` if nothing was captured (generation still running, id
        unknown, or capture itself failed)."""
        return self._resource_stats.pop(generation_id, None)

    def cancel(self, generation_id: str) -> bool:
        """
        Cancel `generation_id`, if it is the run this manager is executing.

        The id check is what keeps one tab's cancel from killing another's:
        `_cancelled` is a single flag, so it may only be flipped by the owner
        of the in-flight run. Returns False when `generation_id` is not running
        here (already finished, queued, or executing on a different backend).
        """
        with self._run_lock:
            if self._running_generation_id != generation_id:
                return False
            self._cancelled = True
            return True

    @property
    def running_generation_id(self) -> Optional[str]:
        """The id of the in-flight run, or None when idle."""
        with self._run_lock:
            return self._running_generation_id

    def _live_vram_note(self) -> Optional[str]:
        """Free/total VRAM (GB), read live via NVML at the moment a CUDA OOM
        was caught, or None if the read itself fails. The orchestrator's
        pre-flight `vram_estimate_gb` (computed from form_data before backend
        selection, in orchestrator.py) is not threaded down here - it would
        mean widening the generate() call across every backend implementation
        for a number that is stale anyway by the time a failure actually
        lands, minutes into model loading/sampling."""
        try:
            free_gb = round(self.gpu_monitor.get_free_vram() / 1024, 2)
            total_gb = round(self.gpu_monitor.get_total_vram() / 1024, 2)
        except Exception:
            return None
        return f"({free_gb}GB free of {total_gb}GB total VRAM)"

    def _log_memory_summary(self, generation_id: Optional[str]) -> None:
        """One INFO line at generation end: RSS, model-cache entries, pinned bytes.

        Deliberately cheap (one psutil call + one dict read, no torch/CUDA
        query) and called from a bare ``except Exception: logger.debug(...)``
        guard at the call site, so a logging hiccup can never surface as a
        generation failure. This is the native engine's primary memory
        subsystem - the point is that a future RAM regression is diagnosable
        straight from production logs, without needing to reproduce it first.
        """
        import psutil

        rss_gb = psutil.Process().memory_info().rss / (1024 ** 3)

        entries = keys = None
        if self.models is not None and hasattr(self.models, "stats"):
            stats = self.models.stats()
            entries = stats.get("entries")
            keys = stats.get("keys")

        pinned_note = "n/a"
        try:
            from src.platform.observability.profiling import pinned_cum_gb

            pinned_note = f"{pinned_cum_gb():.3f}"
        except Exception:
            pass  # pinned-bytes gauge is profiler-only instrumentation, never load-bearing

        logger.info(
            f"[GENERATION] memory summary generation_id={generation_id} rss_gb={rss_gb:.2f} "
            f"cache_entries={entries} cache_keys={keys} pinned_cum_gb={pinned_note}"
        )

    def _inject_built_in_services(self, pipe_class: type, pipe_input: Dict[str, Any]) -> Dict[str, Any]:
        """
        Inject built-in services (GPU, SYSTEM, MEMORY, LLM, MODELS, ASSETS)
        into pipe inputs.

        Built-in services are identified by SERVICE IOType and uppercase names.
        They are automatically injected by GenerationEngine and don't need
        to be provided by previous pipes.

        Args:
            pipe_class: The pipe class to check for service inputs
            pipe_input: Current pipe input dictionary

        Returns:
            dict: Pipe input with injected services
        """
        try:
            input_specs = pipe_class.inputs()
        except Exception as e:
            logger.warning(f"[GENERATION] Could not get input specs for service injection: {e}")
            return pipe_input

        for input_spec in input_specs:
            # Check if this is a service input (SERVICE IOType)
            if input_spec.io_type == IOType.SERVICE:
                service_name = input_spec.name.upper()  # Services use uppercase names

                # Inject appropriate service
                if service_name == "GPU":
                    pipe_input[input_spec.name] = self.gpu_monitor
                    logger.debug(f"[GENERATION] Injected GPU service into {pipe_class.name}")
                elif service_name == "SYSTEM":
                    pipe_input[input_spec.name] = self.system_monitor
                    logger.debug(f"[GENERATION] Injected SYSTEM service into {pipe_class.name}")
                elif service_name == "MEMORY":
                    pipe_input[input_spec.name] = self.memory_advisor
                    logger.debug(f"[GENERATION] Injected MEMORY service into {pipe_class.name}")
                elif service_name == "LLM":
                    pipe_input[input_spec.name] = self.llm_service
                    logger.debug(f"[GENERATION] Injected LLM service into {pipe_class.name}")
                elif service_name == "MODELS":
                    pipe_input[input_spec.name] = self.models
                    logger.debug(f"[GENERATION] Injected MODELS service into {pipe_class.name}")
                elif service_name == "ASSETS":
                    pipe_input[input_spec.name] = self.assets
                    logger.debug(f"[GENERATION] Injected ASSETS service into {pipe_class.name}")
                elif service_name == "SETTINGS":
                    pipe_input[input_spec.name] = self.settings
                    logger.debug(f"[GENERATION] Injected SETTINGS service into {pipe_class.name}")
                else:
                    logger.warning(
                        f"[GENERATION] Unknown service '{service_name}' requested by {pipe_class.name}. "
                        f"Available services: GPU, SYSTEM, MEMORY, LLM, MODELS, ASSETS, SETTINGS"
                    )

        return pipe_input

    def validate_pipeline(self, pipes: list) -> None:
        """Validate that all pipe inputs are satisfied by pipeline flow"""
        available_outputs = {}  # pipe_identifier -> {output_name -> output_spec}

        # Build a mapping of pipe names/IDs to their actual identifiers for validation
        # This allows input configs to reference pipes by either name or ID
        pipe_identifier_map = {}

        # First pass: count how many times each name appears
        name_counts = {}
        for p in [p for p in pipes if p['enabled']]:
            pipe_name = p['name']
            name_counts[pipe_name] = name_counts.get(pipe_name, 0) + 1

        # Second pass: build the mapping
        for p in [p for p in pipes if p['enabled']]:
            # If 'id' exists but is None, use name instead
            pipe_identifier = p.get('id') or p['name']
            pipe_name = p['name']

            # Only map the name if it's unique (appears once)
            # If a name appears multiple times, only the IDs should be used for lookup
            if name_counts[pipe_name] == 1:
                pipe_identifier_map[pipe_name] = pipe_identifier

            # Always map the ID (if different from name)
            if 'id' in p and p['id'] and p['id'] != pipe_name:
                pipe_identifier_map[p['id']] = pipe_identifier

        for pipe_config in [p for p in pipes if p['enabled']]:
            pipe_name = pipe_config['name']
            pipe_class = self.pipe_catalog.get_pipe(pipe_name)

            if not pipe_class:
                raise ValueError(f"Pipe '{pipe_name}' not found in pipe registry")

            # Use pipe ID if available, otherwise fall back to name
            pipe_identifier = pipe_config.get('id', None)

            if pipe_identifier is None:
                pipe_identifier = pipe_name

            # Get pipe input and output specifications.
            # BasePipe.inputs()/outputs() default to None (pipes that declare
            # no specs) — treat that as "nothing to validate", not a crash.
            required_inputs = pipe_class.inputs() or []
            produced_outputs = pipe_class.outputs() or []
            if pipe_class.inputs() is None:
                logger.warning(f"[VALIDATION] Pipe '{pipe_identifier}' ({pipe_class.__module__}) declares no input specs")

            # Check if all required inputs are available
            for input_spec in required_inputs:
                if not input_spec.required:
                    continue

                # Skip validation for SERVICE inputs (auto-injected)
                if input_spec.io_type == IOType.SERVICE:
                    logger.debug(f"[VALIDATION] Skipping validation for SERVICE input '{input_spec.name}' (auto-injected)")
                    continue

                input_satisfied = False
                for input_config in pipe_config.get('input', []):
                    if isinstance(input_config, dict):
                        param_name = input_config['name']
                        provider_pipe = input_config['provider']
                        provider_output = input_config['output_var']
                    else:
                        param_name, provider_pipe, provider_output = input_config

                    # Check if this input config satisfies the required input
                    if param_name == input_spec.name:
                        # Normalize provider_pipe using the mapping (handles both name and ID references)
                        actual_provider_identifier = pipe_identifier_map.get(provider_pipe, provider_pipe)

                        # Check if provider pipe exists and produces the required output
                        if actual_provider_identifier in available_outputs:
                            provider_outputs = available_outputs[actual_provider_identifier]
                            if provider_output in provider_outputs:
                                provider_spec = provider_outputs[provider_output]
                                if provider_spec.io_type == input_spec.io_type:
                                    # Check array compatibility
                                    if input_spec.is_array and not provider_spec.is_array:
                                        # Array input can accept single values (will be converted to array)
                                        input_satisfied = True
                                        break
                                    elif not input_spec.is_array and provider_spec.is_array:
                                        # Single input CAN accept array output - first element will be extracted automatically
                                        logger.debug(
                                            f"[VALIDATION] Pipe '{pipe_identifier}' input '{input_spec.name}' expects single {input_spec.io_type.value}, "
                                            f"but provider '{provider_pipe}' output '{provider_output}' produces array. First element will be extracted automatically."
                                        )
                                        input_satisfied = True
                                        break
                                    else:
                                        # Both single or both array - compatible
                                        input_satisfied = True
                                        break
                                else:
                                    raise ValueError(
                                        f"Pipe '{pipe_identifier}' input '{input_spec.name}' expects {input_spec.io_type.value}, "
                                        f"but provider '{provider_pipe}' output '{provider_output}' produces {provider_spec.io_type.value}"
                                    )
                            else:
                                raise ValueError(
                                    f"Pipe '{pipe_identifier}' input '{input_spec.name}' references non-existent output "
                                    f"'{provider_output}' from pipe '{provider_pipe}'"
                                )
                        else:
                            raise ValueError(
                                f"Pipe '{pipe_identifier}' input '{input_spec.name}' references non-existent provider pipe '{provider_pipe}'"
                            )

                if not input_satisfied:
                    raise ValueError(
                        f"Pipe '{pipe_identifier}' requires input '{input_spec.name}' ({input_spec.io_type.value}) but it's not provided in the pipeline"
                    )

            # Add this pipe's outputs to available outputs (keyed by ID, not name)
            available_outputs[pipe_identifier] = {
                output_spec.name: output_spec for output_spec in produced_outputs
            }

        logger.info(f"[VALIDATION] Pipeline validation successful for {len(pipes)} pipes")

    def hijack_pipe_generation_output(self, generation_outputs: callable, output: GenerationOutput, pipe: BasePipe, generation_id: str, pipe_id: int = None):
        """
        Process generation output through the handler system and then pass to the output callback.

        Args:
            generation_outputs: Callback function to send outputs to
            output: GenerationOutput to process
            pipe: The pipe that generated the output
            generation_id: Current generation ID
            pipe_id: Index of the pipe in the pipeline (for tracking)
        """
        # Set pipe tracking information
        output.pipe_id = pipe_id
        output.pipe_name = pipe.name

        # Add pipe title for progress outputs. The human title is the marker
        # value shown in the status line; `pipe.name`/`pipe_id` above stay
        # available on the message for debugging.
        if isinstance(output, ProgressGenerationOutput):
            title = resolve_display_title(pipe.name, pipe.config.get('display_title') or pipe.display_title)
            output.title = f"<<PIPE:{title}>>"

        # Pass the output to the callback (application layer will handle processing)
        generation_outputs(output)

    def generate(
            self,
            pipes: list,
            generation_outputs: callable,
            generation_id: str = None,
            cache_owner: str = None,
    ):
        # Deferred: importing the `native` package (even just its lightweight,
        # dependency-free errors module) executes its `__init__`, which pulls
        # in torch and the rest of the engine tree - boot must not pay for
        # that just to define an except clause. See
        # tests/architecture/test_boot_imports.py.
        from src.platform.runtime.native.errors import SamplingCancelled

        # One run at a time per manager: the GPU, the cancellation flag and the
        # model cache are all single-occupancy. Backends get their own manager
        # so they still execute in parallel; re-entering the *same* one is a bug
        # in the caller (the scheduler is supposed to hold a slot), so say so
        # loudly rather than silently corrupting the run in flight.
        with self._run_lock:
            if self._running_generation_id is not None:
                raise RuntimeError(
                    f"GenerationEngine is already running {self._running_generation_id}; "
                    f"refusing to start {generation_id} concurrently"
                )
            self._running_generation_id = generation_id
            self._cancelled = False

        generation_time_start = time.perf_counter()

        profiler = get_profiler()
        if generation_id:
            try:
                profile_dir = Path(self.settings.get_file_storage_directory()) / "profiles" / generation_id
                profiler.start(generation_id, profile_dir)
            except Exception:
                logger.debug("[GENERATION] Could not resolve profile directory; profiler not started", exc_info=True)

        # Always-on resource capture for the admin stats table, independent of
        # the opt-in GenerationProfiler above. Two cheap reads:
        # - CPU%: prime `cpu_percent(interval=None)` here and read it again in
        #   `finally` for an average over this generation's window (generate() is
        #   single-occupancy per the `_run_lock` check above).
        # - Peak VRAM: reset_peak_memory_stats() per device, read back via
        #   max_memory_allocated() at the end -- a real high-water mark.
        try:
            self._proc.cpu_percent(interval=None)
        except Exception:
            logger.debug("[GENERATION] cpu_percent priming failed", exc_info=True)
        try:
            import torch
            if torch.cuda.is_available():
                for i in range(torch.cuda.device_count()):
                    torch.cuda.reset_peak_memory_stats(i)
        except Exception:
            logger.debug("[GENERATION] reset_peak_memory_stats failed", exc_info=True)

        # Generation lease: hold models acquired during this pipeline unevictable
        # until it completes, so RAM-pressure eviction can't drop the DiT between
        # the model_loader and generator pipes (weakref would clear -> None).
        # Must wrap the entire pipeline, from before the first pipe to cleanup.
        #
        # `lease_stats` is the mutable dict `generation_lease()` yields;
        # `end_lease()` fills it with {"hits", "misses", "load_ms"} at
        # `__exit__` time, so it's readable after the `finally` block below.
        lease_context = None
        lease_stats: Optional[Dict[str, float]] = None
        if self.models is not None and hasattr(self.models, "generation_lease") and generation_id:
            try:
                lease_context = self.models.generation_lease(generation_id)
                lease_stats = lease_context.__enter__()
            except Exception:
                logger.debug("[GENERATION] generation_lease failed; continuing without lease protection", exc_info=True)
                lease_context = None
                lease_stats = None

        try:
            # Preset-scoped RAM cache: tag this generation's model-cache entries
            # with its preset and, on a preset switch, evict the previous preset's
            # models before any loader in this pipeline runs. Runs on this worker
            # thread so the ContextVar tag is visible to acquire(). ``cache_owner``
            # is None for comfyui/non-native runs -> a no-op tag, no eviction.
            if self.models is not None and hasattr(self.models, "begin_generation"):
                try:
                    self.models.begin_generation(cache_owner)
                except Exception:
                    logger.debug("[GENERATION] begin_generation failed; continuing", exc_info=True)

            # Validate pipeline before execution
            self.validate_pipeline(pipes)
            current_pipe_result = None
            pipe_outputs = []

            # Build a mapping of pipe names/IDs to their actual identifiers for output lookup
            # This allows input configs to reference pipes by either name or ID
            pipe_identifier_map = {}  # Maps both name and ID to the actual identifier used in pipe_outputs

            # First pass: count how many times each name appears
            name_counts = {}
            for p in [p for p in pipes if p['enabled']]:
                pipe_name = p['name']
                name_counts[pipe_name] = name_counts.get(pipe_name, 0) + 1

            # Second pass: build the mapping
            for p in [p for p in pipes if p['enabled']]:
                pipe_identifier = p.get('id', p['name'])
                pipe_name = p['name']

                # Only map the name if it's unique (appears once)
                # If a name appears multiple times, only the IDs should be used for lookup
                if name_counts[pipe_name] == 1:
                    pipe_identifier_map[pipe_name] = pipe_identifier

                # Always map the ID (if different from name)
                if 'id' in p and p['id'] != pipe_name:
                    pipe_identifier_map[p['id']] = pipe_identifier

                logger.debug(f"[GENERATION] Pipe mapping: name='{pipe_name}' (count={name_counts[pipe_name]}), id='{p.get('id')}', identifier='{pipe_identifier}'")

            for pipe_id, pipe_config in enumerate([p for p in pipes if p['enabled']]):
                logger.debug(f"[GENERATION] Processing pipe: name='{pipe_config['name']}', id='{pipe_config.get('id')}', enabled={pipe_config.get('enabled')}")

                if self._cancelled:  # Check cancellation before each pipe
                    logger.info("[GENERATION] Generation cancelled before pipe execution")
                    generation_outputs(
                        ProgressGenerationOutput(
                            icon=Icon("x", "beat"),
                            state="Generation cancelled",
                        )
                    )
                    return

                generation_time_pipe_start = time.perf_counter()

                pipe_class = self.pipe_catalog.get_pipe(pipe_config['name'])
                pipe_class_configuration = pipe_class.get_default_config() or {}
                custom_config = pipe_config.get('config', {})  # from your YAML

                pipe_class_configuration = deep_update(pipe_class_configuration, custom_config)

                # Validate the configuration against the pipe's specifications
                try:
                    pipe_class_configuration = validate_pipe_configuration(pipe_class, pipe_class_configuration)
                except ValueError as e:
                    logger.error(f"[VALIDATION] Configuration validation failed for pipe '{pipe_config['name']}': {e}")
                    raise

                pipe = self.pipe_catalog.get_pipe(pipe_config['name'])(pipe_class_configuration)

                logger.debug(
                    f"[GENERATION] Running pipe: {pipe.name}, "
                    f"with configuration: {redact_mapping(pipe_class_configuration)}"
                )

                pipe_input = {}
                for i in pipe_config['input']:
                    # Handle both old and new formats
                    if isinstance(i, dict):
                        # New format: {name: "param", provider: "pipe", output_var: "var", enabled: true}
                        if not i.get('enabled', True):
                            continue
                        param_name = i['name']
                        provider_pipe_name = i['provider']
                        provider_output_var = i['output_var']
                    else:
                        # New array format: ["param_name", "provider_pipe", "provider_output_var"]
                        param_name, provider_pipe_name, provider_output_var = i

                    # Find the provider pipe output
                    # Normalize provider_pipe_name using the mapping (handles both name and ID references)
                    actual_provider_identifier = pipe_identifier_map.get(provider_pipe_name, provider_pipe_name)
                    found = False
                    for o in pipe_outputs:
                        if o[0] == actual_provider_identifier and o[1] == provider_output_var:
                            output_value = o[2]

                            # Check if we need to convert array to single value
                            # Get the pipe's input spec to see if it expects an array
                            try:
                                input_specs = pipe_class.inputs()
                                for input_spec in input_specs:
                                    if input_spec.name == param_name:
                                        # If the input expects a single value but we have a list, extract first element
                                        if not input_spec.is_array and isinstance(output_value, list):
                                            if len(output_value) > 0:
                                                output_value = output_value[0]
                                                logger.debug(f"[GENERATION] Converted array output to single value for input '{param_name}' (pipe expects single value)")
                                            else:
                                                logger.warning(f"[GENERATION] Empty list provided for single-value input '{param_name}'")
                                                output_value = None
                                        break
                            except Exception as e:
                                logger.warning(f"[GENERATION] Could not check input specs for array conversion: {e}")

                            """ If there is already an input defined with this param name we will create a list of inputs """
                            if param_name in pipe_input:
                                if not isinstance(pipe_input[param_name], list):
                                    pipe_input[param_name] = [pipe_input[param_name]]
                                pipe_input[param_name].append(output_value)
                            else:
                                pipe_input[param_name] = output_value
                            found = True
                            logger.debug(f"[GENERATION] Found and set {param_name} from {provider_pipe_name}.{provider_output_var}")
                            break

                    if not found:
                        if actual_provider_identifier != provider_pipe_name:
                            logger.warning(f"[GENERATION] Could not find output {provider_pipe_name} (resolved to {actual_provider_identifier}).{provider_output_var} for input {param_name}")
                        else:
                            logger.warning(f"[GENERATION] Could not find output {provider_pipe_name}.{provider_output_var} for input {param_name}")

                # Inject built-in services (GPU, SYSTEM, MEMORY) if pipe requests them
                pipe_input = self._inject_built_in_services(pipe_class, pipe_input)

                # Execute pipe.before_execute hook
                if self.plugin_registry:
                    try:
                        logger.debug(f"[GENERATION] Executing before_execute hook for pipe {pipe.name} (id={pipe_id})")
                        context = HookContext(
                            hook_name=PIPE_HOOKS.before_execute,
                            plugin_id="system",
                            data={
                                "pipe_id": pipe_id,
                                "pipe_name": pipe.name,
                                "pipe_config": pipe_class_configuration,
                                "inputs": pipe_input,
                                "generation_id": generation_id,
                            }
                        )
                        context, success = self.plugin_registry.execute_hook(
                            PIPE_HOOKS.before_execute,
                            context
                        )
                        # Update inputs if modified
                        pipe_input = context.data.get("inputs", pipe_input)
                        # Propagate pipe_config modifications back to the instantiated pipe
                        # so hooks (e.g. plugin credential injection) take effect.
                        modified_pipe_config = context.data.get("pipe_config")
                        if isinstance(modified_pipe_config, dict) and modified_pipe_config is not pipe_class_configuration:
                            pipe_class_configuration = modified_pipe_config
                            pipe.config = modified_pipe_config
                        if success:
                            logger.debug(f"[GENERATION] before_execute hook completed successfully for pipe {pipe.name}")
                        else:
                            logger.warning(f"[GENERATION] before_execute hook had failures for pipe {pipe.name}")
                    except Exception as e:
                        logger.error(f"[GENERATION] Error executing before_execute hook for pipe {pipe.name}: {e}")

                # Create cancellation check function for the pipe
                def is_cancelled():
                    return self._cancelled

                profiler.mark("pipe.start", pipe_id=pipe_id, pipe_name=pipe.name, pipe_identifier=pipe_config.get('id'))

                # Check if pipe supports cancellation by inspecting process method signature
                import inspect
                sig = inspect.signature(pipe.process)

                if 'is_cancelled' in sig.parameters:
                    # Pipe supports cancellation
                    current_pipe_result = pipe.process(
                        pipe_input=PipeInput(input=pipe_input),
                        generation_outputs=lambda _output: self.hijack_pipe_generation_output(generation_outputs, _output, pipe, generation_id, pipe_id),
                        is_cancelled=is_cancelled,
                    )
                else:
                    # Legacy pipe without cancellation support
                    current_pipe_result = pipe.process(
                        pipe_input=PipeInput(input=pipe_input),
                        generation_outputs=lambda _output: self.hijack_pipe_generation_output(generation_outputs, _output, pipe, generation_id, pipe_id),
                    )

                profiler.mark("pipe.end", pipe_id=pipe_id, pipe_name=pipe.name, pipe_identifier=pipe_config.get('id'))

                # Execute pipe.after_execute hook
                if self.plugin_registry and current_pipe_result is not None:
                    try:
                        logger.debug(f"[GENERATION] Executing after_execute hook for pipe {pipe.name} (id={pipe_id})")
                        pipe_duration = time.perf_counter() - generation_time_pipe_start
                        context = HookContext(
                            hook_name=PIPE_HOOKS.after_execute,
                            plugin_id="system",
                            data={
                                "pipe_id": pipe_id,
                                "pipe_name": pipe.name,
                                "outputs": current_pipe_result.output,
                                "duration": pipe_duration
                            }
                        )
                        context, success = self.plugin_registry.execute_hook(
                            PIPE_HOOKS.after_execute,
                            context
                        )
                        # Update outputs if modified
                        current_pipe_result.output = context.data.get("outputs", current_pipe_result.output)
                        if success:
                            logger.debug(f"[GENERATION] after_execute hook completed successfully for pipe {pipe.name}")
                        else:
                            logger.warning(f"[GENERATION] after_execute hook had failures for pipe {pipe.name}")
                    except Exception as e:
                        logger.error(f"[GENERATION] Error executing after_execute hook for pipe {pipe.name}: {e}")

                # Check cancellation after pipe execution
                if self._cancelled:
                    logger.info("[GENERATION] Generation cancelled after pipe execution")
                    generation_outputs(
                        ProgressGenerationOutput(
                            icon=Icon("x", "beat"),
                            state="Generation cancelled",
                        )
                    )
                    return

                if current_pipe_result is None:
                    raise ValueError(f"Pipe {pipe.name} returned None")

                # Use pipe ID if available, otherwise fall back to name
                pipe_identifier = pipe_config.get('id', pipe_config['name'])
                for var_name, value in current_pipe_result.output.items():
                    pipe_outputs.append([pipe_identifier, var_name, value])

                generation_outputs(
                    TimerGenerationOutput(
                        pipe_name=pipe.name,
                        pipe_id=pipe_id,
                        name=f"wb.timers.pipes.{pipe.name}",
                        value=time.perf_counter() - generation_time_pipe_start,
                    )
                )

            if current_pipe_result is not None:
                generation_outputs(
                    TimerGenerationOutput(
                        name="wb.timers.generation.total",
                        value=time.perf_counter() - generation_time_start,
                    )
                )
                generation_outputs(
                    ProgressGenerationOutput(
                        icon=Icon("check", "beat"),
                        state="Generation completed in <<TIME:{:.2f}s>>".format(time.perf_counter() - generation_time_start),
                    )
                )
        except SamplingCancelled as exc:
            logger.info(f"[GENERATION] Generation cancelled at step {exc.step_index}")
            generation_outputs(
                ProgressGenerationOutput(
                    icon=Icon("x", "beat"),
                    state="Generation cancelled",
                )
            )
            return
        except Exception as e:
            logger.error(f"Generation error: {str(e)}")
            import traceback
            # A failed generation is a hard error — its traceback must be
            # visible at default log level, not hidden behind DEBUG.
            logger.error(traceback.format_exc())
            # Let the caller (backend wrapper -> orchestrator) know the
            # generation failed instead of silently "completing"; the
            # exception is then re-raised so the backend can react. Prefer a
            # detail body the raising pipe attached (e.g. ComfyUI node errors);
            # otherwise fall back to the Python traceback.
            attached_detail = getattr(e, 'detail', None)
            # The attached body is the only place a remote engine's real cause
            # lives (ComfyUI node errors, a backend's stderr) — the local
            # traceback above only shows where we noticed. Logged as well as
            # sent to the frontend, or an operator reading the logs sees the
            # one-line summary and nothing else.
            if attached_detail:
                logger.error(f"Generation error detail: {attached_detail}")
            detail = attached_detail or traceback.format_exc()

            # Memory-exhaustion failures (CUDA OOM, refused host-RAM streaming)
            # otherwise reach the user as a raw PyTorch/native stack trace with
            # no guidance. Classify and prepend actionable remediation; the raw
            # exception text stays in `detail` either way.
            error_message = str(e)
            classification = classify_generation_error(e)
            if classification is not None:
                error_message = classification.summary
                if classification.category == "cuda_oom":
                    vram_note = self._live_vram_note()
                    if vram_note:
                        error_message = f"{error_message} {vram_note}"
                suggestions = "\n".join(f"- {s}" for s in classification.suggestions)
                detail = f"Try:\n{suggestions}\n\n{detail}"

            generation_outputs(ErrorGenerationOutput(error=error_message, detail=detail))
            raise
        finally:
            # Release the generation lease (if it was acquired) BEFORE cleanup,
            # so the models become evictable again before any aggressive cleanup
            # that might want to reclaim them.
            if lease_context is not None:
                try:
                    lease_context.__exit__(None, None, None)
                except Exception:
                    logger.debug("[GENERATION] generation_lease __exit__ failed; continuing", exc_info=True)
            with self._run_lock:
                self._cancelled = False
                self._running_generation_id = None
            # A generation's CPU-side churn (TE dequant copies, activation
            # buffers, VAE decode tiles) is real glibc heap growth that Python
            # frees but the allocator won't return to the OS without an explicit
            # trim. Without this, a generation keeping the SAME preset resident
            # (no eviction fires) never gets trimmed, so RSS ratchets up every
            # generation. Runs BEFORE profiler.stop so the generation.end mark
            # reflects post-trim RSS, not allocator churn that self-heals.
            if self.models is not None and hasattr(self.models, "cleanup"):
                try:
                    self.models.cleanup(aggressive=True)
                    cache_expected_gb = None
                    if hasattr(self.models, "expected_ram_gb"):
                        try:
                            cache_expected_gb = self.models.expected_ram_gb()
                        except Exception:
                            logger.debug("[GENERATION] expected_ram_gb read failed", exc_info=True)
                    profiler.mark("models.cleanup.post", aggressive=True, cache_expected_gb=cache_expected_gb)
                except Exception:
                    logger.debug("[GENERATION] post-generation cleanup failed; continuing", exc_info=True)
            if generation_id:
                profiler.stop(generation_id)
            # One-line memory summary at every generation's end: RSS regressions
            # have repeatedly only been diagnosable after the fact from a live
            # server. Never raises - a logging failure must not turn a completed
            # generation into an error.
            try:
                self._log_memory_summary(generation_id)
            except Exception:
                logger.debug("[GENERATION] memory summary logging failed; continuing", exc_info=True)

            # Finish the always-on resource capture and stash it for the
            # orchestrator to pick up via `pop_resource_stats`. `cold_start`/
            # `model_load_ms` are None (unknown) rather than a guessed value
            # whenever no lease ran (e.g. a non-native backend with no `self.models`).
            if generation_id:
                try:
                    cpu_percent = None
                    try:
                        cpu_percent = self._proc.cpu_percent(interval=None)
                    except Exception:
                        logger.debug("[GENERATION] cpu_percent read failed", exc_info=True)

                    ram_mb = None
                    try:
                        ram_mb = self._proc.memory_info().rss / (1024 ** 2)
                    except Exception:
                        logger.debug("[GENERATION] rss read failed", exc_info=True)

                    peak_vram_mb = None
                    try:
                        import torch
                        if torch.cuda.is_available():
                            peak_bytes = sum(
                                torch.cuda.max_memory_allocated(i)
                                for i in range(torch.cuda.device_count())
                            )
                            peak_vram_mb = peak_bytes / (1024 ** 2)
                    except Exception:
                        logger.debug("[GENERATION] peak vram read failed", exc_info=True)

                    cold_start = None
                    model_load_ms = None
                    if lease_stats is not None and (lease_stats.get("hits", 0) or lease_stats.get("misses", 0)):
                        cold_start = bool(lease_stats.get("misses", 0) > 0)
                        model_load_ms = lease_stats.get("load_ms")

                    self._resource_stats[generation_id] = {
                        "cold_start": cold_start,
                        "model_load_ms": model_load_ms,
                        "peak_vram_mb": peak_vram_mb,
                        "peak_ram_mb": ram_mb,
                        "cpu_percent": cpu_percent,
                    }
                except Exception:
                    logger.debug("[GENERATION] resource-stats capture failed; continuing", exc_info=True)
