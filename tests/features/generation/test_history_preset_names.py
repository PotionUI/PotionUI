"""Preset ids in history payloads resolve to the name declared in preset.yml."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.features.generation.history_query import GenerationHistoryQuery
from src.features.generation.records import Generation
from src.features.presets import PresetTemplateLoader
from src.features.presets.name_resolver import PresetNameResolver
from src.platform.util.ids import generate_ulid


@pytest.fixture(scope="module")
def real_preset():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    if not loader.presets:
        pytest.skip("no presets on disk")
    return loader, loader.presets[0]


@pytest.fixture
def resolver(real_preset):
    loader, _ = real_preset
    return PresetNameResolver(loader)


def _query(resolver, repo=None):
    return GenerationHistoryQuery(
        generation_repo=repo or Mock(),
        preset_name_resolver=resolver,
    )


def _gen(preset_id):
    return Generation(
        id=generate_ulid(),
        preset_id=preset_id,
        form_data={"prompt": "x"},
        user_id="u1",
        status="completed",
        mode="txt2img",
    )


def test_resolver_maps_id_to_yaml_name(real_preset, resolver):
    _, preset = real_preset
    assert resolver.name_map()[preset.id] == preset.name
    assert resolver.resolve(preset.id) == preset.name


def test_known_preset_id_serializes_to_its_name(real_preset, resolver):
    _, preset = real_preset
    rows = _query(resolver)._serialize_generations([_gen(preset.id)], include_tags=False)
    assert rows[0]["preset_name"] == preset.name
    assert rows[0]["preset_name"] != preset.id


def test_deleted_preset_id_falls_back_to_the_id(resolver):
    missing = generate_ulid()
    rows = _query(resolver)._serialize_generations([_gen(missing)], include_tags=False)
    assert rows[0]["preset_name"] == missing


def test_null_preset_id_is_uploaded(resolver):
    rows = _query(resolver)._serialize_generations([_gen(None)], include_tags=False)
    assert rows[0]["preset_name"] == "Uploaded"


def test_absent_resolver_leaves_ids_intact():
    preset_id = generate_ulid()
    query = GenerationHistoryQuery(generation_repo=Mock())
    rows = query._serialize_generations([_gen(preset_id)], include_tags=False)
    assert rows[0]["preset_name"] == preset_id


def test_facets_resolve_names_and_fall_back(real_preset, resolver):
    _, preset = real_preset
    missing = generate_ulid()
    repo = Mock()
    repo.get_facets.return_value = {
        "modes": [],
        "models": [],
        "presets": [
            {"id": preset.id, "count": 3},
            {"id": missing, "count": 1},
        ],
    }
    facets = _query(resolver, repo).get_facets("u1")
    by_id = {p["id"]: p["name"] for p in facets["presets"]}
    assert by_id[preset.id] == preset.name
    assert by_id[missing] == missing


def test_preset_list_is_read_once_per_serialization_pass(real_preset):
    loader, preset = real_preset
    counting = PresetNameResolver(loader)
    calls = []
    original = counting.name_map

    def counted():
        calls.append(1)
        return original()

    counting.name_map = counted
    generations = [_gen(preset.id) for _ in range(25)]
    _query(counting)._serialize_generations(generations, include_tags=False)
    assert len(calls) == 1
