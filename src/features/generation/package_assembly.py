"""Assembling a worker execution package from a built pipeline.

The local app computes the whole pipeline; a worker executes it. This module is
where that becomes literal: it takes the canonical ``BuiltPipeline`` the one
build path produces and turns it into a validated
:class:`~src.platform.worker_protocol.ExecutionPackageV1`, with each pipe's
*effective* configuration (see ``effective_config``) rather than only the keys
its preset happened to write.

Assembly only - no transport, no worker, no side effects. What it deliberately
does not do:

- **Model paths stay verbatim.** The worker mounts the model depot at the same
  path, so a locally-computed path resolves as-is. Rewriting or logical-id'ing
  them here would break that.
- **User-media paths do not.** Unlike model paths, a pipe config can carry a
  path into the *local host's* storage directory - a worker has no access to
  that filesystem at all. When ``storage_dir`` is given, ``collect_input_assets``
  (``input_assets.py``) rewrites every such path into an ``asset://<logical_id>``
  token before the digest is computed, so the token - not the host path - is
  what a worker (and the request digest) actually sees.
- **The model bundle is an input, not something derived.** Content digests over
  model files are computed by the model layer; this function takes the finished
  manifest so there is exactly one place that decides what a digest is.
- **Compatibility is an input too.** ``required_fingerprints`` is passed in,
  because what makes two installations interchangeable is the pipe catalog's
  question, not this module's.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from src.features.generation.effective_config import merge_pipe_defaults
from src.features.generation.input_assets import collect_input_assets
from src.features.generation.pipeline_builder import BuiltPipeline
from src.features.remote_execution.policy import RemoteExecutionPolicy
from src.pipelines.catalog import PipeCatalog
from src.pipelines.remote_fingerprint import compute_pipe_contract_fingerprint
from src.platform.worker_protocol import (
    ContentDigest,
    ExecutionLimitsV1,
    ExecutionPackageV1,
    ModelBundleManifestV1,
    ProcessedPipelineV1,
    ProcessedPipeV1,
)

_DIGEST_ALGORITHM = "sha256"
_DIGEST_PLACEHOLDER = ContentDigest(algorithm=_DIGEST_ALGORITHM, hex="0" * 64)


def assemble_execution_package(
    built_pipeline: BuiltPipeline,
    *,
    pipe_catalog: PipeCatalog,
    model_bundle: ModelBundleManifestV1,
    engine: Optional[str] = None,
    execution_id: Optional[str] = None,
    issued_at: Optional[datetime] = None,
    expires_at: Optional[datetime] = None,
    required_fingerprints: Optional[Mapping[str, str]] = None,
    limits: Optional[ExecutionLimitsV1] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    policy: Optional[RemoteExecutionPolicy] = None,
    storage_dir: Optional[Path] = None,
) -> ExecutionPackageV1:
    """Turn a built pipeline into a validated execution package.

    ``idempotency_key`` is the generation id: a resubmission of the same
    generation must collapse onto the execution already running for it, whereas
    ``execution_id`` may differ per attempt.

    ``expires_at`` and ``limits``, when left as ``None``, are derived from
    *policy* (or its defaults): ``expires_at`` becomes ``issued_at`` plus
    ``policy.package_ttl_seconds``, and ``limits`` becomes
    ``policy.default_limits()``. There is no way to request an unbounded
    package through this default - pass an explicit ``ExecutionLimitsV1``/
    ``expires_at`` if a caller ever needs one.
    """
    generation_id = built_pipeline.generation_id
    resolved_engine = engine if engine is not None else built_pipeline.preset_template.engine
    resolved_policy = policy or RemoteExecutionPolicy()

    package_metadata: Dict[str, Any] = {'preset_id': built_pipeline.preset_id}
    if metadata:
        package_metadata.update(metadata)

    resolved_issued_at = issued_at or datetime.now(timezone.utc)
    resolved_expires_at = expires_at if expires_at is not None else (
        resolved_issued_at + timedelta(seconds=resolved_policy.package_ttl_seconds)
    )

    processed_pipeline = build_processed_pipeline(built_pipeline.pipes, pipe_catalog)
    input_assets = None
    if storage_dir is not None:
        rewritten_pipes, input_assets, _sources = collect_input_assets(processed_pipeline.pipes, storage_dir)
        processed_pipeline = ProcessedPipelineV1(pipes=rewritten_pipes)

    fields: Dict[str, Any] = {
        'execution_id': execution_id or generation_id,
        'idempotency_key': generation_id,
        'engine': resolved_engine,
        'issued_at': resolved_issued_at,
        'expires_at': resolved_expires_at,
        'required_fingerprints': dict(required_fingerprints or {}),
        'pipe_contracts': _pipe_contracts(processed_pipeline, pipe_catalog),
        'model_bundle': model_bundle,
        'processed_pipes': processed_pipeline,
        'input_assets': input_assets,
        'limits': limits if limits is not None else resolved_policy.default_limits(),
        'metadata': package_metadata,
    }

    draft = ExecutionPackageV1(request_digest=_DIGEST_PLACEHOLDER, **fields)
    return ExecutionPackageV1(request_digest=_body_digest(draft), **fields)


def _pipe_contracts(pipeline: ProcessedPipelineV1, pipe_catalog: PipeCatalog) -> Dict[str, str]:
    """pipe_type -> contract fingerprint, one entry per distinct type in *pipeline*."""
    contracts: Dict[str, str] = {}
    for pipe in pipeline.pipes:
        if pipe.pipe_type in contracts:
            continue
        pipe_class = pipe_catalog.get_pipe(pipe.pipe_type)
        if pipe_class is not None:
            contracts[pipe.pipe_type] = compute_pipe_contract_fingerprint(pipe_class)
    return contracts


def _body_digest(draft: ExecutionPackageV1) -> ContentDigest:
    """Digest the package's body - everything except the digest field itself.

    Over the JSON projection with sorted keys, so the value depends on what the
    package *says* rather than on field declaration order or on how a transport
    happens to encode it.
    """
    body = draft.model_dump(mode="json")
    body.pop("request_digest", None)
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return ContentDigest(
        algorithm=_DIGEST_ALGORITHM,
        hex=hashlib.sha256(canonical).hexdigest(),
    )


def build_processed_pipeline(
    pipes: List[Dict[str, Any]],
    pipe_catalog: PipeCatalog,
) -> ProcessedPipelineV1:
    """Merge pipe-class defaults into ``pipes`` and project them onto the wire shape.

    Public (not assembly-private) because it is also the canonical way to
    reproduce the exact ``ProcessedPipelineV1`` a package will carry BEFORE
    the request digest/input-asset rewrite happen - a caller that needs the
    real source path behind an ``asset://`` token (the remote-native
    transport, uploading files) calls this and ``collect_input_assets``
    itself rather than re-deriving the pipe-shape logic.
    """
    merged = merge_pipe_defaults(pipes, pipe_catalog)
    return ProcessedPipelineV1(
        pipes=tuple(
            ProcessedPipeV1(
                pipe_id=pipe.get('id') or f"{pipe['name']}#{index}",
                pipe_type=pipe['name'],
                enabled=bool(pipe.get('enabled')),
                config=pipe.get('config') or {},
                inputs=_pipe_inputs(pipe),
            )
            for index, pipe in enumerate(merged)
        )
    )


def _pipe_inputs(pipe: Dict[str, Any]) -> Dict[str, Any]:
    """Project a pipe's input wiring onto ``parameter -> [providers]``.

    A list per parameter rather than a single provider, because feeding one
    parameter from several providers is meaningful: the executor collects those
    values into a list, in wiring order.
    """
    inputs: Dict[str, Any] = {}

    for entry in pipe.get('input') or []:
        if not isinstance(entry, dict):
            raise ValueError(
                f"pipe '{pipe['name']}' has an input that is not a mapping: {entry!r}"
            )
        inputs.setdefault(entry['name'], []).append({
            'provider': entry['provider'],
            'output_var': entry['output_var'],
            'enabled': bool(entry.get('enabled', True)),
        })

    return inputs
