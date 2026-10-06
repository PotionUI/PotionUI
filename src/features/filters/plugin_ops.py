from __future__ import annotations

import hashlib
import importlib.util
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from src.platform.imaging.filters import ColourOp, OpSpec, ParamSpec, SpatialOp
from src.platform.plugins.manifest_roots import PluginManifestRoot, plugin_manifest_roots

logger = logging.getLogger(__name__)


def plugin_filter_roots(manifests) -> List[PluginManifestRoot]:
    return plugin_manifest_roots(manifests, "filters")


def op_spec_from_entry(entry: Mapping[str, Any], plugin_id: str) -> OpSpec:
    params = tuple(
        ParamSpec(
            id=param["id"],
            label=param.get("label") or param["id"],
            type=param.get("type", "int"),
            default=param.get("default", 0),
            min=param.get("min"),
            max=param.get("max"),
            unit=param.get("unit"),
        )
        for param in entry.get("params") or []
    )
    return OpSpec(
        id=entry["id"],
        label=entry.get("label") or entry["id"],
        kind=entry["kind"],
        params=params,
        source="plugin",
        plugin_id=plugin_id,
        python=entry.get("python"),
    )


def plugin_filter_ops(manifests: Iterable[Any]) -> Dict[str, OpSpec]:
    ops: Dict[str, OpSpec] = {}
    for manifest in manifests:
        plugin_id = getattr(manifest, "id", "") or ""
        for entry in getattr(manifest, "filter_ops", None) or []:
            if not isinstance(entry, Mapping) or "id" not in entry or "kind" not in entry:
                continue
            ops.setdefault(entry["id"], op_spec_from_entry(entry, plugin_id))
    return ops


def load_op_impl(plugin_dir: Path, reference: str) -> Optional[Any]:
    file_part, _, class_name = reference.partition(":")
    base = Path(plugin_dir).resolve()
    target = (base / file_part).resolve()
    if base not in target.parents or not target.is_file():
        logger.warning("Filter op module '%s' is outside or missing from %s", reference, base)
        return None
    module_name = "potionui_filter_op_" + hashlib.sha1(str(target).encode("utf-8")).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(module_name, target)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        candidate = getattr(module, class_name)
        instance = candidate() if isinstance(candidate, type) else candidate
    except Exception:
        logger.exception("Could not load filter op '%s' from %s", class_name, target)
        return None
    if not isinstance(instance, (ColourOp, SpatialOp)):
        logger.warning("Filter op '%s' must subclass ColourOp or SpatialOp", reference)
        return None
    return instance


def load_op_impls(manifests: Iterable[Any], needed: Iterable[str]) -> Dict[str, Any]:
    wanted = set(needed)
    impls: Dict[str, Any] = {}
    for manifest in manifests:
        plugin_dir = getattr(manifest, "plugin_dir", None)
        if not plugin_dir:
            continue
        for entry in getattr(manifest, "filter_ops", None) or []:
            if entry.get("id") in wanted and entry.get("python"):
                impl = load_op_impl(Path(plugin_dir), entry["python"])
                if impl is not None:
                    impls[entry["id"]] = impl
    return impls
