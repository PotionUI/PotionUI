from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace

from .reader import HeaderView
from .signatures import (
    FamilyMatch,
    family_from_gguf_architecture,
    family_from_keys,
    is_transformer_extractable,
    sd_family_from_keys,
)

logger = logging.getLogger(__name__)

CORE_SOURCE = "core"


@dataclass(frozen=True)
class ModelClassifierDefinition:
    key: str
    classify: Callable[[HeaderView], FamilyMatch | None]
    label: str
    version: int
    formats: tuple[str, ...]
    source: str
    priority: int = 0


@dataclass(frozen=True)
class ClassifierMatch:
    key: str
    match: FamilyMatch


class ModelClassifierRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ModelClassifierDefinition] = {}
        self._sequence: dict[str, int] = {}
        self._counter = 0
        self._warned: set[str] = set()

    def register(self, definition: ModelClassifierDefinition) -> None:
        if definition.key in self._definitions:
            raise ValueError(f"model classifier '{definition.key}' is already registered")
        self._definitions[definition.key] = definition
        self._sequence[definition.key] = self._counter
        self._counter += 1

    def unregister(self, key: str) -> None:
        self._definitions.pop(key, None)
        self._sequence.pop(key, None)
        self._warned.discard(key)

    def unregister_source(self, source: str) -> None:
        for key in [k for k, d in self._definitions.items() if d.source == source]:
            self.unregister(key)

    def get(self, key: str) -> ModelClassifierDefinition | None:
        return self._definitions.get(key)

    def definitions(self) -> tuple[ModelClassifierDefinition, ...]:
        return tuple(
            sorted(
                self._definitions.values(),
                key=lambda d: (-d.priority, self._sequence[d.key]),
            )
        )

    def fingerprint(self) -> str:
        entries = sorted(f"{d.key}@{d.version}" for d in self._definitions.values())
        return hashlib.sha1("\n".join(entries).encode("utf-8"), usedforsecurity=False).hexdigest()

    def classify(self, view: HeaderView) -> ClassifierMatch | None:
        for definition in self.definitions():
            if view.format not in definition.formats:
                continue
            try:
                match = definition.classify(view)
            except Exception:
                if definition.key not in self._warned:
                    self._warned.add(definition.key)
                    logger.warning("model classifier '%s' failed", definition.key, exc_info=True)
                continue
            if match is not None:
                return ClassifierMatch(definition.key, match)
        return None


def _native_dit(view: HeaderView) -> FamilyMatch | None:
    match = family_from_keys(view.denoiser_keys, view.denoiser_shape)
    if match is None:
        return None
    return replace(match, transformer_extractable=is_transformer_extractable(match, view.keys, view.format))


def _sd_unet(view: HeaderView) -> FamilyMatch | None:
    return sd_family_from_keys(view.denoiser_keys, view.denoiser_shape)


def _gguf_arch(view: HeaderView) -> FamilyMatch | None:
    return family_from_gguf_architecture(view.metadata.get("general.architecture"))


def _build_default() -> ModelClassifierRegistry:
    registry = ModelClassifierRegistry()
    both = ("safetensors", "gguf")
    registry.register(
        ModelClassifierDefinition("core.native_dit", _native_dit, "Native diffusion transformers", 2, both, CORE_SOURCE, 100)
    )
    registry.register(
        ModelClassifierDefinition("core.sd_unet", _sd_unet, "Stable Diffusion UNet families", 1, both, CORE_SOURCE, 50)
    )
    registry.register(
        ModelClassifierDefinition("core.gguf_arch", _gguf_arch, "GGUF architecture tag", 1, ("gguf",), CORE_SOURCE, 0)
    )
    return registry


model_classifier_registry = _build_default()
