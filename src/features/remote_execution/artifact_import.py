"""Turning a worker-produced `ArtifactRefV1` into a local file plus the
`GenerationOutput` the normal output-handling pipeline expects.

Mirrors `WorkerPipelineExecutor._materialize_artifact`
(`src.features.remote_execution.worker.executor`) in reverse: that module maps
a pipe's final `GenerationOutput` onto `kind`/`media_type` on the way out; this
one maps `kind` back onto the matching `GenerationOutput` subclass on the way
in, so a remote-executed pipeline's artifacts reach the same handlers
(gallery/video/audio/mesh) a local one's do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.pipelines.outputs import (
    AudioGenerationOutput,
    GenerationOutput,
    ImageGenerationOutput,
    MeshGenerationOutput,
    VideoGenerationOutput,
)
from src.platform.worker_protocol import ArtifactRefV1


class ArtifactImportError(Exception):
    """The destination path for an artifact could not be resolved safely."""


def resolve_import_destination(imports_dir: Path, artifact: ArtifactRefV1) -> Path:
    """The contained, collision-free path an artifact's bytes are written to.

    Named by `artifact_id` (already validated as a bare 32-hex-char id by the
    protocol layer - see `ArtifactRefV1`/the worker's own `_ARTIFACT_ID_RE`
    check) plus the suggested filename's suffix, so two artifacts can never
    collide and the destination can never escape ``imports_dir`` even if a
    future protocol version widens what a filename may contain.
    """
    imports_root = imports_dir.resolve()
    imports_root.mkdir(parents=True, exist_ok=True)

    suffix = Path(artifact.filename).suffix if artifact.filename else ""
    candidate = (imports_root / f"{artifact.artifact_id}{suffix}").resolve()
    try:
        candidate.relative_to(imports_root)
    except ValueError as exc:
        raise ArtifactImportError(
            f"artifact {artifact.artifact_id!r} destination escapes {imports_root}"
        ) from exc
    return candidate


def output_for_artifact(
    artifact: ArtifactRefV1,
    local_path: Path,
    *,
    pipe_index: Optional[int],
    pipe_type: Optional[str],
) -> Optional[GenerationOutput]:
    """The `GenerationOutput` a local pipe would have emitted for this artifact,
    or `None` for a `kind` this build doesn't know how to import (logged by the
    caller, never fatal - an unknown artifact kind must not fail the whole run).

    `role == "preview"` is the only case that comes back temporary - a bare
    leaf artifact (`role is None`, what a pre-role worker always sends) and a
    `role == "gallery"` member are both final output, exactly as
    `WorkerPipelineExecutor` only ever materialized non-temporary artifacts
    before previews existed. An unrecognized role degrades the same way `None`
    does: a plain, non-temporary leaf output.
    """
    common = {"pipe_id": pipe_index, "pipe_name": pipe_type}
    temporary = artifact.role == "preview"
    derived = bool(artifact.derived) if artifact.derived is not None else False

    if artifact.kind == "image":
        from PIL import Image

        image = Image.open(local_path)
        image.load()
        return ImageGenerationOutput(
            image=image, temporary=temporary, seed=artifact.seed, derived=derived, **common,
        )

    if artifact.kind == "video":
        return VideoGenerationOutput(
            video_path=local_path, temporary=temporary, seed=artifact.seed, derived=derived, **common,
        )

    if artifact.kind == "audio":
        return AudioGenerationOutput(audio_path=local_path, temporary=temporary, seed=artifact.seed, **common)

    if artifact.kind == "mesh":
        return MeshGenerationOutput(
            mesh_path=local_path, temporary=temporary, seed=artifact.seed, derived=derived, **common,
        )

    return None
