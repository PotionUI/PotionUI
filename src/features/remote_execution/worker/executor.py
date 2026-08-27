"""Reconstructing and running a ``ProcessedPipelineV1`` pipe by pipe.

**The instantiation algorithm is not invented here.** It is the same one
``PipelineExecutor`` uses locally
(``src/features/generation/generation.py:601-624``): pipe-class defaults
deep-merged under the shipped config via ``deep_update``, then
``validate_pipe_configuration`` fills any spec default still missing. A
package's config already carries the class defaults
(``src.features.generation.effective_config.merge_pipe_defaults`` ran on the
dispatching side), so re-running that merge here is a no-op except for the
worker-decided ``device``/``dtype``/``vram_limit_gb`` this module
``setdefault``s in first - see ``device_injection.py``, which mirrors
``NativeBackend.prepare_pipes``'s injection order (backend/worker injection
sits between the pipe's own defaults and the preset's explicit config).

**What is deliberately not reconstructed.** ``GenerationEngine`` (the local
executor) also injects SERVICE-typed pipe inputs (GPU/SYSTEM/MEMORY/LLM/
MODELS/ASSETS/SETTINGS) and runs plugin before/after-execute hooks. A worker
process has no PotionUI database, no settings table, and no model-lifecycle
cache to back MEMORY/MODELS/LLM/ASSETS/SETTINGS with, so only GPU and SYSTEM -
the two that need no such state - are wired; a pipe requesting anything else
gets ``None`` with a logged warning. Plugin hooks are out of scope entirely: a
worker's plugin surface is its pipe catalog, not the hook chain.
"""

from __future__ import annotations

import hashlib
import inspect
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.features.generation.engine import deep_update, validate_pipe_configuration
from src.features.remote_execution.worker.device_injection import inject_worker_device
from src.pipelines.catalog import PipeCatalog
from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.outputs import (
    AudioGenerationOutput,
    ErrorGenerationOutput,
    ImageGenerationOutput,
    MeshGenerationOutput,
    ProgressGenerationOutput,
    TimerGenerationOutput,
    VideoGenerationOutput,
)
from src.platform.worker_protocol import ArtifactRefV1, ContentDigest, ProcessedPipelineV1

logger = logging.getLogger(__name__)


class PipeExecutionError(Exception):
    """A pipe could not be resolved, configured, or ran and raised.

    ``retryable`` is the worker's own honest guess: an unresolvable pipe type
    or an invalid configuration will fail again on retry (``False``), whereas
    a pipe raising mid-``process`` is treated as possibly environmental
    (``True``) absent a way to distinguish "this preset is broken" from "this
    host ran out of VRAM" from inside a generic exception.
    """

    def __init__(self, code: str, message: str, *, retryable: bool, pipe_id: Optional[str] = None):
        self.code = code
        self.message = message
        self.retryable = retryable
        self.pipe_id = pipe_id
        super().__init__(message)


@dataclass
class WorkerEvent:
    """One thing the executor wants journaled. Cursor-agnostic - the
    coordinator stamps ``execution_id``/``worker_id``/``cursor``/``emitted_at``."""

    kind: str
    pipe_id: Optional[str] = None
    progress: Optional[float] = None
    detail: Optional[str] = None
    artifacts: Tuple[ArtifactRefV1, ...] = ()
    payload: Dict[str, Any] = field(default_factory=dict)


def _resolve_asset_tokens(value: Any, resolve: Callable[[str], Path]) -> Any:
    if isinstance(value, str) and value.startswith("asset://"):
        return str(resolve(value[len("asset://"):]))
    if isinstance(value, dict):
        return {key: _resolve_asset_tokens(val, resolve) for key, val in value.items()}
    if isinstance(value, list):
        return [_resolve_asset_tokens(val, resolve) for val in value]
    return value


class WorkerPipelineExecutor:
    def __init__(
        self,
        pipe_catalog: PipeCatalog,
        *,
        device: str,
        dtype: str,
        vram_limit_gb: Optional[float],
        artifacts_dir: Path,
        gpu_monitor: Any = None,
        system_monitor: Any = None,
        resolve_asset: Optional[Callable[[str], Path]] = None,
    ):
        self._catalog = pipe_catalog
        self._device = device
        self._dtype = dtype
        self._vram_limit_gb = vram_limit_gb
        self._artifacts_dir = artifacts_dir
        self._gpu_monitor = gpu_monitor
        self._system_monitor = system_monitor
        self._resolve_asset = resolve_asset

    def run(
        self,
        pipeline: ProcessedPipelineV1,
        *,
        emit: Callable[[WorkerEvent], None],
        is_cancelled: Callable[[], bool],
    ) -> None:
        pipe_outputs: Dict[str, Dict[str, Any]] = {}

        for processed in pipeline.pipes:
            if not processed.enabled:
                continue

            if is_cancelled():
                return

            pipe_class = self._catalog.get_pipe(processed.pipe_type)
            if pipe_class is None:
                raise PipeExecutionError(
                    "unknown_pipe",
                    f"pipe type '{processed.pipe_type}' is not on this worker's catalog",
                    retryable=False,
                    pipe_id=processed.pipe_id,
                )

            emit(WorkerEvent(kind="pipe_started", pipe_id=processed.pipe_id))

            try:
                config = self._resolve_config(pipe_class, dict(processed.config))
            except ValueError as exc:
                raise PipeExecutionError(
                    "invalid_config", str(exc), retryable=False, pipe_id=processed.pipe_id,
                ) from exc

            pipe = pipe_class(config)
            pipe_input = self._resolve_inputs(pipe_class, processed, pipe_outputs)
            pipe_input = self._inject_services(pipe_class, pipe_input)

            def _on_output(output, *, _pipe_id=processed.pipe_id):
                self._handle_output(output, pipe_id=_pipe_id, emit=emit)

            kwargs: Dict[str, Any] = {}
            if "is_cancelled" in inspect.signature(pipe.process).parameters:
                kwargs["is_cancelled"] = is_cancelled

            try:
                result = pipe.process(
                    pipe_input=PipeInput(input=pipe_input),
                    generation_outputs=_on_output,
                    **kwargs,
                )
            except Exception as exc:
                raise PipeExecutionError(
                    "pipe_failed", str(exc), retryable=True, pipe_id=processed.pipe_id,
                ) from exc

            pipe_outputs[processed.pipe_id] = dict(result.output or {}) if result else {}

    def _resolve_config(self, pipe_class: type, shipped_config: Dict[str, Any]) -> Dict[str, Any]:
        injected = inject_worker_device(
            shipped_config,
            device=self._device,
            dtype=self._dtype,
            vram_limit_gb=self._vram_limit_gb,
        )
        merged = deep_update(dict(pipe_class.get_default_config() or {}), injected)
        resolved = validate_pipe_configuration(pipe_class, merged)
        if self._resolve_asset is not None:
            resolved = _resolve_asset_tokens(resolved, self._resolve_asset)
        return resolved

    def _resolve_inputs(
        self,
        pipe_class: type,
        processed,
        pipe_outputs: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        input_specs = {spec.name: spec for spec in (pipe_class.inputs() or [])}
        resolved: Dict[str, Any] = {}

        for param_name, providers in processed.inputs.items():
            values: List[Any] = []
            for entry in providers:
                if not entry.get("enabled", True):
                    continue
                provider_id = entry["provider"]
                output_var = entry["output_var"]
                provider_outputs = pipe_outputs.get(provider_id, {})
                if output_var not in provider_outputs:
                    logger.warning(
                        "[WORKER] pipe '%s' input '%s' references %s.%s, which produced "
                        "no such output",
                        processed.pipe_id, param_name, provider_id, output_var,
                    )
                    continue
                values.append(provider_outputs[output_var])

            if not values:
                continue

            spec = input_specs.get(param_name)
            if (spec is not None and spec.is_array) or len(values) > 1:
                resolved[param_name] = values
            else:
                resolved[param_name] = values[0]

        return resolved

    def _inject_services(self, pipe_class: type, pipe_input: Dict[str, Any]) -> Dict[str, Any]:
        for spec in pipe_class.inputs() or []:
            if spec.io_type != IOType.SERVICE:
                continue
            service_name = spec.name.upper()
            if service_name == "GPU":
                pipe_input[spec.name] = self._gpu_monitor
            elif service_name == "SYSTEM":
                pipe_input[spec.name] = self._system_monitor
            else:
                logger.warning(
                    "[WORKER] pipe '%s' requests service '%s', which this worker does not "
                    "provide (no PotionUI database/settings reachable here) - injecting None",
                    pipe_class.name, service_name,
                )
                pipe_input[spec.name] = None
        return pipe_input

    def _handle_output(
        self, output: Any, *, pipe_id: str, emit: Callable[[WorkerEvent], None]
    ) -> None:
        if isinstance(output, ProgressGenerationOutput):
            progress = None
            if output.progress is not None and output.progress.max:
                progress = min(1.0, max(0.0, output.progress.current / output.progress.max))
            emit(WorkerEvent(kind="pipe_progress", pipe_id=pipe_id, progress=progress, detail=output.state))
            return

        if isinstance(output, TimerGenerationOutput):
            emit(WorkerEvent(
                kind="log", pipe_id=pipe_id,
                detail=f"{output.name}={output.value}{output.unit}",
            ))
            return

        if isinstance(output, ErrorGenerationOutput):
            emit(WorkerEvent(kind="log", pipe_id=pipe_id, detail=str(getattr(output, "message", output))))
            return

        artifact = self._materialize_artifact(output, pipe_id=pipe_id)
        if artifact is not None:
            emit(WorkerEvent(kind="artifact", pipe_id=pipe_id, artifacts=(artifact,)))

    def _materialize_artifact(self, output: Any, *, pipe_id: str) -> Optional[ArtifactRefV1]:
        if isinstance(output, ImageGenerationOutput):
            if output.temporary:
                return None
            artifact_id = uuid.uuid4().hex
            self._artifacts_dir.mkdir(parents=True, exist_ok=True)
            dest = self._artifacts_dir / f"{artifact_id}.png"
            output.image.save(dest, format="PNG")
            return self._ref_for_file(dest, artifact_id, kind="image", media_type="image/png", pipe_id=pipe_id)

        source_path: Optional[Path] = None
        kind = media_type = None
        if isinstance(output, VideoGenerationOutput) and not output.temporary:
            source_path, kind, media_type = Path(output.video_path), "video", "video/mp4"
        elif isinstance(output, AudioGenerationOutput) and not output.temporary:
            source_path, kind, media_type = Path(output.audio_path), "audio", "audio/wav"
        elif isinstance(output, MeshGenerationOutput) and not output.temporary:
            source_path, kind, media_type = Path(output.mesh_path), "mesh", "model/gltf-binary"

        if source_path is None or not source_path.exists():
            return None

        artifact_id = uuid.uuid4().hex
        self._artifacts_dir.mkdir(parents=True, exist_ok=True)
        dest = self._artifacts_dir / f"{artifact_id}{source_path.suffix}"
        dest.write_bytes(source_path.read_bytes())
        return self._ref_for_file(dest, artifact_id, kind=kind, media_type=media_type, pipe_id=pipe_id)

    @staticmethod
    def _ref_for_file(path: Path, artifact_id: str, *, kind: str, media_type: str, pipe_id: str) -> ArtifactRefV1:
        data = path.read_bytes()
        return ArtifactRefV1(
            artifact_id=artifact_id,
            kind=kind,
            media_type=media_type,
            size_bytes=len(data),
            digest=ContentDigest(algorithm="sha256", hex=hashlib.sha256(data).hexdigest()),
            uri=f"/v1/artifacts/{artifact_id}",
            filename=path.name,
            pipe_id=pipe_id,
        )
