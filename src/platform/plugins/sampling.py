"""Registries for step algorithms (samplers) and sigma schedules.

Lives here, next to ``field_types.py``/``prompt_importers.py``/the other
plugin extension-point registries, rather than under
``src.platform.runtime.native``: it is a plain name->spec table with no
``torch`` dependency, and importing it must not pull the native engine (and
therefore torch) into processes that only need the catalog -- form field
types, the preset linter, the sampling catalog route, application boot.
``src/platform/runtime/native/sampling/registry.py`` re-exports these names
for the engine-side callers that already depend on torch.

Core registers its own entries at import time (``denoise_loop`` for samplers,
``flow_schedule`` for schedules); plugins register through the ``samplers:`` /
``schedules:`` manifest roots (``src/platform/plugins/registry.py``) and are
removed again by ``source`` on disable. Every dispatch site -- ``denoise()``,
``build_sigmas()``, the pipe config resolvers, the form field types, the
catalog endpoint -- reads these registries; there is no other list of
sampler or schedule names anywhere.

A sampler is a callable with the uniform signature every entry in
``sampling/algorithms/`` shares::

    sample(model_fn, x, sigmas, guidance, cond, uncond, hooks, is_cancelled,
           sampler_options) -> Tensor

A schedule is a callable ``build(ctx: ScheduleContext) -> Tensor`` that returns
``ctx.steps + 1`` descending sigmas from ``1.0`` to ``0.0`` (float32, CPU).
``build_sigmas`` owns everything around that call: the denoise truncation
(``ctx.steps`` is already the expanded step count), the detail-daemon warp and
the head/tail pinning. A schedule that carries its own step count (the manual
sigma list) sets ``owns_steps=True`` and receives the raw ``steps``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generic, Iterable, List, Optional, Tuple, TypeVar

ANY_FAMILY = "*"


class DuplicateSamplingEntryError(ValueError):
    """Raised when registering a sampler or schedule key that already exists."""


@dataclass(frozen=True)
class OptionSpec:
    """One entry of a sampler's ``sampler_options`` / a schedule's ``schedule_options``."""

    name: str
    type: str = "float"
    default: Any = None
    description: str = ""
    min_value: Optional[float] = None
    max_value: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "default": self.default,
            "description": self.description,
            "min_value": self.min_value,
            "max_value": self.max_value,
        }


@dataclass(frozen=True)
class SamplerDefinition:
    key: str
    sample: Callable[..., Any]
    label: str
    stochastic: bool = False
    options: Tuple[OptionSpec, ...] = ()
    families: Tuple[str, ...] = (ANY_FAMILY,)
    description: str = ""
    source: str = "core"

    def applies_to(self, family: Optional[str]) -> bool:
        return ANY_FAMILY in self.families or (family is not None and family in self.families)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "stochastic": self.stochastic,
            "options": [o.to_dict() for o in self.options],
            "families": list(self.families),
            "description": self.description,
            "source": self.source,
        }


@dataclass(frozen=True)
class ScheduleContext:
    """Everything a schedule builder may read. ``steps`` is the expanded step
    count after denoise truncation (``build_sigmas`` keeps the tail); the
    shift-family fields mirror ``build_sigmas``' keyword arguments and are
    ``None`` when the caller did not supply them."""

    steps: int
    options: Dict[str, Any] = field(default_factory=dict)
    shift: Optional[float] = None
    base_shift: Optional[float] = None
    max_shift: Optional[float] = None
    dynamic_shift: Optional[dict] = None
    fixed_mu: Optional[float] = None
    image_seq_len: Optional[int] = None


@dataclass(frozen=True)
class ScheduleDefinition:
    key: str
    build: Callable[[ScheduleContext], Any]
    label: str
    options: Tuple[OptionSpec, ...] = ()
    families: Tuple[str, ...] = (ANY_FAMILY,)
    description: str = ""
    source: str = "core"
    owns_steps: bool = False
    requires_image_seq_len: bool = False

    def applies_to(self, family: Optional[str]) -> bool:
        return ANY_FAMILY in self.families or (family is not None and family in self.families)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "options": [o.to_dict() for o in self.options],
            "families": list(self.families),
            "description": self.description,
            "source": self.source,
            "owns_steps": self.owns_steps,
            "requires_image_seq_len": self.requires_image_seq_len,
        }


T = TypeVar("T", SamplerDefinition, ScheduleDefinition)


class SamplingRegistry(Generic[T]):
    """Ordered registry keyed by ``definition.key``; insertion order is the
    catalog order (core first, then plugins in enable order)."""

    def __init__(self, kind: str):
        self.kind = kind
        self._by_key: Dict[str, T] = {}

    def register(self, definition: T) -> None:
        if definition.key in self._by_key:
            existing = self._by_key[definition.key]
            raise DuplicateSamplingEntryError(
                f"{self.kind} {definition.key!r} is already registered by {existing.source!r}"
            )
        self._by_key[definition.key] = definition

    def unregister(self, key: str) -> None:
        """Remove one entry by key, if present. Registration is otherwise
        append-only -- this is the seam a test uses to stand a spy in front of
        a core algorithm; a plugin's entries come off through
        :meth:`unregister_source` instead."""
        self._by_key.pop(key, None)

    def unregister_source(self, source: str) -> None:
        for key in [k for k, d in self._by_key.items() if d.source == source]:
            del self._by_key[key]

    def has(self, key: str) -> bool:
        return key in self._by_key

    def get(self, key: str) -> T:
        try:
            return self._by_key[key]
        except KeyError:
            raise KeyError(f"unknown {self.kind} {key!r}; available: {sorted(self._by_key)}") from None

    def keys(self) -> List[str]:
        return list(self._by_key)

    def definitions(self) -> List[T]:
        return list(self._by_key.values())

    def for_family(self, family: Optional[str]) -> List[T]:
        return [d for d in self._by_key.values() if d.applies_to(family)]

    def select(
        self,
        family: Optional[str] = None,
        include: Optional[Iterable[str]] = None,
        exclude: Optional[Iterable[str]] = None,
    ) -> List[T]:
        """Catalog subset a form field shows: the family's entries, narrowed by
        an explicit ``include`` list (kept in include order) and an ``exclude``
        list. Unknown keys in either list are ignored here; the preset linter
        reports them."""
        entries = self.for_family(family)
        if include is not None:
            wanted = list(include)
            by_key = {d.key: d for d in entries}
            entries = [by_key[k] for k in wanted if k in by_key]
        if exclude:
            dropped = set(exclude)
            entries = [d for d in entries if d.key not in dropped]
        return entries


sampler_registry: SamplingRegistry[SamplerDefinition] = SamplingRegistry("sampler")
schedule_registry: SamplingRegistry[ScheduleDefinition] = SamplingRegistry("schedule")
