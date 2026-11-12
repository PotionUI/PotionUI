from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
import threading
from types import ModuleType
from typing import Any

logger = logging.getLogger(__name__)

GUARDED_ROOTS: frozenset[str] = frozenset({"huggingface_hub", "transformers", "tokenizers", "diffusers"})

WARM_UP_ORDER: tuple[str, ...] = ("huggingface_hub", "tokenizers", "transformers", "diffusers")

_LOCK = threading.RLock()


def _root(name: str) -> str:
    return name.partition(".")[0]


def _forget(names: list[str]) -> None:
    for name in names:
        sys.modules.pop(name, None)
        parent_name, _, child = name.rpartition(".")
        parent = sys.modules.get(parent_name) if parent_name else None
        if parent is not None and child in getattr(parent, "__dict__", {}):
            try:
                delattr(parent, child)
            except AttributeError:
                pass


def _guarded_names() -> list[str]:
    return [name for name in list(sys.modules) if _root(name) in GUARDED_ROOTS]


def _purge_new(before: set[str]) -> None:
    _forget([name for name in _guarded_names() if name not in before])


def _purge_family() -> None:
    _forget(_guarded_names())


def _import_once(name: str) -> ModuleType:
    before = set(sys.modules)
    try:
        return importlib.import_module(name)
    except BaseException:
        _purge_new(before)
        raise


def guarded_import(name: str) -> ModuleType:
    with _LOCK:
        try:
            return _import_once(name)
        except ModuleNotFoundError:
            raise
        except ImportError as first:
            if _root(name) not in GUARDED_ROOTS:
                raise
            logger.warning("Import of %s failed (%s); clearing its package state and retrying once", name, first)
            _purge_family()
            try:
                return _import_once(name)
            except BaseException as second:
                _purge_family()
                raise second from first


def guarded_from(name: str, *attrs: str) -> Any:
    with _LOCK:
        module = guarded_import(name)
        try:
            values = tuple(getattr(module, attr) for attr in attrs)
        except AttributeError as exc:
            raise ImportError(f"cannot import name from {name!r}: {exc}", name=name) from exc
    return values[0] if len(values) == 1 else values


def warm_up_heavy_imports(names: tuple[str, ...] = WARM_UP_ORDER) -> list[str]:
    failed: list[str] = []
    for name in names:
        if importlib.util.find_spec(name) is None:
            continue
        try:
            guarded_import(name)
        except Exception:
            logger.exception("Warming up the %s import failed; it will be retried on first use", name)
            failed.append(name)
    return failed
