"""Deterministic fingerprints for the Remote Native handshake.

Before a remote worker executes a processed pipeline, core and worker must
agree they mean the same thing by "pipe X" and "plugin Y's code is present" -
this module computes the values that agreement is checked against. It computes
fingerprints only; the handshake exchange, transport, and the DTOs that carry
these values as opaque strings (see ``src/platform/worker_protocol/``) live
elsewhere and are someone else's file.

Every function here is pure with respect to process state: given the same
catalog/manifest contents, the same string comes out, in this process or a
freshly started one with a different hash seed - no dict/set iteration order,
``id()``, timestamp, or absolute path is allowed to leak into a fingerprint.
`tests/pipelines/test_remote_fingerprint.py` enforces that, including across a
real subprocess with ``PYTHONHASHSEED`` forced to a different value.

`compute_remote_plugin_bundle_fingerprint` covers plugins that contribute a
registered pipe *and* plugins that declare a ``remote: true`` backend hook
(e.g. a ``prompt.transform`` handler that must also run on the worker) - see
that function's docstring for the exact membership rule. A backend hook
without the ``remote:`` flag is deliberately excluded: it runs core-side
before dispatch, so its code is core's concern, not the worker's.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Optional

from src.pipelines.catalog import PipeCatalog
from src.platform.plugins.loader import PluginManifest
from src.platform.worker_protocol.version import WORKER_PROTOCOL_VERSION


def _canonical_json(value: Any) -> str:
    """`json.dumps` with every knob that could vary between two otherwise-
    identical runs pinned down: sorted keys, fixed separators, and a
    `default` that turns anything JSON can't natively encode into its type
    name rather than a `str()`/`repr()` that could carry a memory address or
    other per-run noise."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda o: f"<{type(o).__name__}>",
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _pipe_contract(pipe_class: type) -> dict:
    """The part of a pipe class that changes the wire contract: its
    input/output/config shape.

    Deliberately excludes ``description`` (prose, not contract) and
    ``get_requirements()`` (an install-time/worker-capability question, not a
    "does the pipeline still mean the same thing" question - a pipe whose pip
    requirement gets a version bump with no behavioural change should not
    force every worker to re-handshake).
    """
    return {
        "inputs": sorted(
            (spec.name, spec.io_type.value, spec.required, spec.is_array)
            for spec in pipe_class.inputs()
        ),
        "outputs": sorted(
            (spec.name, spec.io_type.value, spec.is_array)
            for spec in pipe_class.outputs()
        ),
        "configuration": sorted(
            (
                spec.name,
                spec.param_type.__name__,
                spec.required,
                list(spec.choices) if spec.choices is not None else None,
                spec.min_value,
                spec.max_value,
            )
            for spec in pipe_class.configuration()
        ),
        "default_config": pipe_class.get_default_config(),
    }


def compute_pipe_catalog_fingerprint(catalog: PipeCatalog) -> str:
    """Fingerprint of every registered pipe's I/O + config contract.

    Moves when a pipe is added, removed, or its ``inputs()``/``outputs()``/
    ``configuration()``/``get_default_config()`` shape changes. Does NOT move
    for a change to ``description``, requirements, docstrings, or which of
    core/custom/a plugin the pipe came from (a pipe moving from core into a
    plugin with an identical contract is not a wire-protocol change - that
    plugin's presence is `compute_remote_plugin_bundle_fingerprint`'s concern,
    not this one's).

    Forces `PipeCatalog`'s EAGER discovery tier (``get_available_pipes()``),
    not the light-scan tier ``get_pipe()`` normally uses: the contract lives on
    the imported class, not the filesystem location the light scan learns
    without exec'ing anything. The first call after a fresh catalog therefore
    pays the same "import every pipe, including heavy diffusers/torch
    dependencies" cost the light-scan tier exists specifically to avoid (see
    ``catalog.py``'s module docstring) - compute this once at startup and cache
    it rather than calling it per handshake; every call after the first is
    cheap (reads already-imported classes off ``catalog.pipes``).
    """
    catalog.get_available_pipes()  # force eager discovery; see docstring above
    # `catalog.pipes` values are already concrete BasePipe subclasses only -
    # `_load_pipe_module`'s `attr.__module__ == module_name` check (the fix for
    # the "abstract base class registered as the first subclass found" trap)
    # keeps an imported base class out of this dict before it ever gets here.
    contracts = {
        name: _pipe_contract(pipe_class)
        for name, pipe_class in catalog.pipes.items()
    }
    return _digest(contracts)


def compute_remote_plugin_bundle_fingerprint(
    catalog: PipeCatalog,
    enabled_plugins: Iterable[PluginManifest],
) -> str:
    """Fingerprint of the plugin code a remote worker must have on disk.

    "Remote-safe" is the union of two disjoint reasons a plugin's code must be
    present on the worker:

    1. It contributes at least one registered pipe - only pipe code actually
       executes inside a processed pipeline. Membership comes from
       `PipeCatalog.remote_relevant_plugin_ids()` - the catalog's own record of
       which plugin actually got a pipe registered - not from re-parsing
       manifests, so a plugin that *declares* pipes but whose ``main.py``
       failed to resolve is correctly excluded.
    2. It declares at least one backend hook with ``remote: true``
       (``BackendHookSpec.remote``, ``src/platform/plugins/manifest.py``) - a
       hook whose handler runs inside the worker-executed portion of a
       generation (e.g. a ``prompt.transform``), as opposed to a hook that only
       runs core-side before dispatch. A hook without the flag is deliberately
       invisible here even if the plugin is otherwise enabled: that is the
       author's declaration that the hook is core-only.

    A plugin that adds a sidebar widget, a chat tool, a documentation page, or
    a core-only hook has nothing a worker needs and is excluded. A plugin can
    match both reasons (a pipe plus a ``remote: true`` hook); its bundle entry
    then carries both ``pipes`` and ``remote_hooks``.

    ``enabled_plugins`` is taken as an explicit argument rather than read off
    ``catalog.plugin_registry`` so this function has no dependency on a live
    registry - a list of ``PluginManifest`` is enough, which is what makes it
    easy to unit test and easy to call from wherever the handshake code ends
    up living.
    """
    relevant_ids = catalog.remote_relevant_plugin_ids()
    pipes_by_plugin: dict[str, list[str]] = {pid: [] for pid in relevant_ids}
    for pipe_name, source in catalog.pipe_sources.items():
        if source in pipes_by_plugin:
            pipes_by_plugin[source].append(pipe_name)

    bundle = {}
    for manifest in enabled_plugins:
        remote_hooks = sorted(manifest.remote_hooks)
        contributes_pipe = manifest.id in relevant_ids
        if not contributes_pipe and not remote_hooks:
            continue
        entry = {
            "version": manifest.version,
            "dependencies_python": sorted(manifest.dependencies_python),
            "dependencies_binaries": sorted(manifest.dependencies_binaries),
            "pipes": sorted(pipes_by_plugin.get(manifest.id, [])),
        }
        if remote_hooks:
            entry["remote_hooks"] = remote_hooks
        bundle[manifest.id] = entry

    missing = relevant_ids - bundle.keys()
    if missing:
        # A pipe's source names a plugin ID that isn't in `enabled_plugins` -
        # the caller passed a stale/mismatched list against this catalog.
        # Fingerprinting a partial view would be worse than refusing: it would
        # produce a value that looks valid but silently omits a plugin whose
        # code the worker actually needs.
        raise ValueError(
            f"catalog has pipes from plugin(s) {sorted(missing)} not present "
            f"in enabled_plugins - fingerprint would omit their code"
        )

    return _digest(bundle)


def compute_build_fingerprint(build_id: Optional[str] = None) -> str:
    """Fingerprint of the protocol version + build identity.

    The protocol version is ``WORKER_PROTOCOL_VERSION``
    (``src/platform/worker_protocol/version.py``) - a single leaf constant
    shared with the envelope module that stamps/validates wire documents, so
    core cannot report a worker "compatible" here while the envelope rejects
    its documents as the wrong version elsewhere.

    No runtime-derivable build identity exists in this repo today: there is no
    VERSION file, no git tag, no ``POTIONUI_BUILD_*`` env convention, and
    ``src/bootstrap/app.py``'s FastAPI ``version=POTIONUI_VERSION`` is a
    hand-bumped release marker, not a build identity - none of that is
    fabricated here.
    ``build_id`` is therefore deliberately caller-supplied: pass ``None`` (the
    default) until a deployment adds a real source (a packaging-time stamp
    file, an env var, ...), and this degrades gracefully to a
    protocol-version-only fingerprint that still moves whenever
    ``WORKER_PROTOCOL_VERSION`` is bumped by hand.
    """
    return _digest({"protocol_version": WORKER_PROTOCOL_VERSION, "build_id": build_id})
