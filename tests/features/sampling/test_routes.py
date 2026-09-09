"""Tests for SamplingController (`GET /api/sampling/catalog`)."""

import pytest

from src.features.sampling.routes import SamplingController
from src.platform.runtime.native.sampling.registry import (
    SamplingRegistry,
    SamplerDefinition,
    ScheduleDefinition,
)


def _sampler(key, families=("*",), source="test"):
    return SamplerDefinition(key=key, sample=lambda *a, **kw: None, label=key.title(), families=families, source=source)


def _schedule(key, families=("*",), source="test"):
    return ScheduleDefinition(key=key, build=lambda ctx: None, label=key.title(), families=families, source=source)


@pytest.fixture
def registries():
    samplers = SamplingRegistry("sampler")
    schedules = SamplingRegistry("schedule")

    samplers.register(_sampler("euler", families=("*",)))
    samplers.register(_sampler("krea2_only", families=("krea2",)))
    schedules.register(_schedule("simple", families=("*",)))

    return samplers, schedules


@pytest.fixture
def controller(registries):
    samplers, schedules = registries
    return SamplingController(samplers=samplers, schedules=schedules)


class TestSamplingController:
    @pytest.mark.asyncio
    async def test_full_catalog_no_family(self, controller):
        result = await controller.get_catalog(family=None)

        assert result.success is True
        sampler_keys = {s["key"] for s in result.data["samplers"]}
        schedule_keys = {s["key"] for s in result.data["schedules"]}
        assert sampler_keys == {"euler", "krea2_only"}
        assert schedule_keys == {"simple"}

    @pytest.mark.asyncio
    async def test_family_filter_narrows_both_lists(self, controller):
        result = await controller.get_catalog(family="krea2")

        sampler_keys = {s["key"] for s in result.data["samplers"]}
        schedule_keys = {s["key"] for s in result.data["schedules"]}
        # "*"-family entries always apply; the krea2-only sampler applies too.
        assert sampler_keys == {"euler", "krea2_only"}
        assert schedule_keys == {"simple"}

    @pytest.mark.asyncio
    async def test_unrelated_family_excludes_family_scoped_entries(self, controller):
        result = await controller.get_catalog(family="flux")

        sampler_keys = {s["key"] for s in result.data["samplers"]}
        # krea2_only does not apply to 'flux'; the "*"-family entries still do.
        assert sampler_keys == {"euler"}

    @pytest.mark.asyncio
    async def test_unknown_family_returns_only_wildcard_entries_not_an_error(self, controller):
        result = await controller.get_catalog(family="totally_unknown_family")

        assert result.success is True
        sampler_keys = {s["key"] for s in result.data["samplers"]}
        assert sampler_keys == {"euler"}

    @pytest.mark.asyncio
    async def test_unknown_family_with_only_family_scoped_entries_is_empty_not_error(self):
        samplers = SamplingRegistry("sampler")
        schedules = SamplingRegistry("schedule")
        samplers.register(_sampler("krea2_only", families=("krea2",)))
        controller = SamplingController(samplers=samplers, schedules=schedules)

        result = await controller.get_catalog(family="totally_unknown_family")

        assert result.success is True
        assert result.data["samplers"] == []
        assert result.data["schedules"] == []

    @pytest.mark.asyncio
    async def test_definition_shape_round_trips(self, controller):
        result = await controller.get_catalog(family=None)

        by_key = {s["key"]: s for s in result.data["samplers"]}
        assert by_key["euler"]["label"] == "Euler"
        assert by_key["euler"]["families"] == ["*"]
        assert by_key["euler"]["source"] == "test"
