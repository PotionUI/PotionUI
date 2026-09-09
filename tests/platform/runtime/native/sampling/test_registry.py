"""Semantics of the sampler/schedule registries themselves.

These exercise a fresh `SamplingRegistry` rather than the process-wide
singletons, so a failure here is about the container, not about which
algorithms core happens to ship.
"""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.sampling.registry import (
    ANY_FAMILY,
    DuplicateSamplingEntryError,
    OptionSpec,
    SamplerDefinition,
    SamplingRegistry,
    ScheduleContext,
    ScheduleDefinition,
    sampler_registry,
    schedule_registry,
)


def _sampler(key, **kw):
    return SamplerDefinition(key, lambda *a, **k: None, kw.pop("label", key.title()), **kw)


def _schedule(key, **kw):
    return ScheduleDefinition(key, lambda ctx: None, kw.pop("label", key.title()), **kw)


def test_register_then_get_and_has():
    registry = SamplingRegistry("sampler")
    definition = _sampler("spin")
    registry.register(definition)

    assert registry.has("spin")
    assert registry.get("spin") is definition
    assert registry.keys() == ["spin"]
    assert registry.definitions() == [definition]


def test_get_unknown_key_names_the_registered_ones():
    registry = SamplingRegistry("schedule")
    registry.register(_schedule("ramp"))

    with pytest.raises(KeyError) as excinfo:
        registry.get("nope")
    assert "ramp" in str(excinfo.value)
    assert "schedule" in str(excinfo.value)


def test_duplicate_key_is_refused_and_names_the_incumbent():
    registry = SamplingRegistry("sampler")
    registry.register(_sampler("spin", source="core"))

    with pytest.raises(DuplicateSamplingEntryError) as excinfo:
        registry.register(_sampler("spin", source="my-plugin"))
    assert "spin" in str(excinfo.value)
    assert "core" in str(excinfo.value)
    # The incumbent survives a refused registration.
    assert registry.get("spin").source == "core"


def test_keys_keep_registration_order():
    registry = SamplingRegistry("sampler")
    for key in ("zeta", "alpha", "mid"):
        registry.register(_sampler(key))
    assert registry.keys() == ["zeta", "alpha", "mid"]


def test_unregister_source_removes_only_that_source():
    registry = SamplingRegistry("sampler")
    registry.register(_sampler("core_one", source="core"))
    registry.register(_sampler("plug_one", source="my-plugin"))
    registry.register(_sampler("plug_two", source="my-plugin"))

    registry.unregister_source("my-plugin")

    assert registry.keys() == ["core_one"]
    registry.unregister_source("my-plugin")  # idempotent
    assert registry.keys() == ["core_one"]


def test_unregister_removes_one_key_and_tolerates_a_miss():
    registry = SamplingRegistry("sampler")
    registry.register(_sampler("spin"))
    registry.unregister("spin")
    registry.unregister("spin")
    assert registry.keys() == []


def test_for_family_matches_wildcards_and_explicit_families():
    registry = SamplingRegistry("schedule")
    registry.register(_schedule("everywhere"))
    registry.register(_schedule("ltx_only", families=("ltx",)))
    registry.register(_schedule("wan_or_ltx", families=("wan", "ltx")))

    assert [d.key for d in registry.for_family("ltx")] == ["everywhere", "ltx_only", "wan_or_ltx"]
    assert [d.key for d in registry.for_family("flux")] == ["everywhere"]
    # No family at all still sees the wildcard entries.
    assert [d.key for d in registry.for_family(None)] == ["everywhere"]


def test_applies_to_reads_the_wildcard_constant():
    assert _sampler("spin").families == (ANY_FAMILY,)
    assert _sampler("spin").applies_to("flux")
    assert not _sampler("spin", families=("ltx",)).applies_to("flux")


def test_select_narrows_by_include_in_include_order():
    registry = SamplingRegistry("sampler")
    for key in ("a", "b", "c"):
        registry.register(_sampler(key))

    assert [d.key for d in registry.select(include=["c", "a"])] == ["c", "a"]
    # Unknown include keys are dropped here; the preset linter reports them.
    assert [d.key for d in registry.select(include=["c", "ghost"])] == ["c"]


def test_select_drops_excluded_keys():
    registry = SamplingRegistry("sampler")
    for key in ("a", "b", "c"):
        registry.register(_sampler(key))

    assert [d.key for d in registry.select(exclude=["b"])] == ["a", "c"]
    assert [d.key for d in registry.select(include=["a", "b"], exclude=["b"])] == ["a"]


def test_select_respects_family_before_include_and_exclude():
    registry = SamplingRegistry("sampler")
    registry.register(_sampler("everywhere"))
    registry.register(_sampler("ltx_only", families=("ltx",)))

    assert [d.key for d in registry.select("flux", include=["ltx_only", "everywhere"])] == ["everywhere"]


def test_to_dict_carries_the_catalog_payload():
    definition = _sampler(
        "spin", label="Spin", stochastic=True, description="A test sampler",
        options=(OptionSpec("eta", "float", 1.0, "Noise fraction", 0.0, 1.0),),
        families=("ltx",), source="my-plugin",
    )
    assert definition.to_dict() == {
        "key": "spin",
        "label": "Spin",
        "stochastic": True,
        "options": [{
            "name": "eta", "type": "float", "default": 1.0,
            "description": "Noise fraction", "min_value": 0.0, "max_value": 1.0,
        }],
        "families": ["ltx"],
        "description": "A test sampler",
        "source": "my-plugin",
    }


def test_schedule_to_dict_carries_the_step_ownership_flags():
    payload = _schedule("listed", owns_steps=True, requires_image_seq_len=True).to_dict()
    assert payload["owns_steps"] is True
    assert payload["requires_image_seq_len"] is True


def test_core_registers_its_algorithms_and_schedules_on_the_singletons():
    """The singletons are what every dispatch site reads; core must have filled
    them by the time the sampling package is imported."""
    assert sampler_registry.has("euler")
    assert schedule_registry.has("shift")
    assert all(d.source == "core" for d in sampler_registry.definitions())
    assert all(d.source == "core" for d in schedule_registry.definitions())


def test_schedule_context_defaults_are_empty_not_shared():
    first = ScheduleContext(steps=4)
    second = ScheduleContext(steps=4)
    assert first.options == {} and first.options is not second.options
    assert first.image_seq_len is None


def test_a_registered_schedule_builds_through_the_definition():
    definition = ScheduleDefinition(
        "linear", lambda ctx: torch.linspace(1.0, 0.0, ctx.steps + 1), "Linear",
    )
    sigmas = definition.build(ScheduleContext(steps=4))
    assert sigmas.shape == (5,)
    assert sigmas[0].item() == 1.0 and sigmas[-1].item() == 0.0
