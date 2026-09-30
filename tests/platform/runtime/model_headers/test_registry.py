from __future__ import annotations

import logging

import pytest

from src.platform.runtime.model_headers import (
    FamilyMatch,
    ModelClassifierDefinition,
    ModelClassifierRegistry,
    model_classifier_registry,
)
from tests.fixtures.model_header_fixtures import make_view


def definition(key, classify, *, priority=0, version=1, formats=("safetensors", "gguf"), source="test"):
    return ModelClassifierDefinition(key, classify, key, version, formats, source, priority)


def constant(family):
    return lambda view: FamilyMatch(family)


VIEW = make_view({"a": (1,)})


def test_builtins_are_registered():
    keys = {d.key for d in model_classifier_registry.definitions()}
    assert {"core.native_dit", "core.sd_unet", "core.gguf_arch"} <= keys


def test_higher_priority_wins():
    registry = ModelClassifierRegistry()
    registry.register(definition("low", constant("low"), priority=1))
    registry.register(definition("high", constant("high"), priority=9))
    found = registry.classify(VIEW)
    assert (found.key, found.match.family) == ("high", "high")


def test_registration_order_breaks_priority_ties():
    registry = ModelClassifierRegistry()
    registry.register(definition("first", constant("first")))
    registry.register(definition("second", constant("second")))
    assert registry.classify(VIEW).key == "first"


def test_first_non_none_match_wins():
    registry = ModelClassifierRegistry()
    registry.register(definition("skip", lambda view: None, priority=5))
    registry.register(definition("hit", constant("hit")))
    assert registry.classify(VIEW).key == "hit"


def test_no_match_returns_none():
    registry = ModelClassifierRegistry()
    registry.register(definition("skip", lambda view: None))
    assert registry.classify(VIEW) is None


def test_duplicate_key_raises():
    registry = ModelClassifierRegistry()
    registry.register(definition("dup", constant("a")))
    with pytest.raises(ValueError):
        registry.register(definition("dup", constant("b")))


def test_format_filter_skips_other_formats():
    registry = ModelClassifierRegistry()
    registry.register(definition("gguf-only", constant("g"), formats=("gguf",)))
    assert registry.classify(VIEW) is None
    assert registry.classify(make_view({"a": (1,)}, fmt="gguf")).key == "gguf-only"


def test_plugin_exception_is_swallowed_and_logged_once(caplog):
    registry = ModelClassifierRegistry()

    def boom(view):
        raise RuntimeError("bad plugin")

    registry.register(definition("boom", boom, priority=9))
    registry.register(definition("ok", constant("ok")))
    with caplog.at_level(logging.WARNING):
        assert registry.classify(VIEW).key == "ok"
        assert registry.classify(VIEW).key == "ok"
    assert sum("boom" in r.getMessage() for r in caplog.records) == 1


def test_unregister_and_source_removal():
    registry = ModelClassifierRegistry()
    registry.register(definition("a", constant("a"), source="plug"))
    registry.register(definition("b", constant("b"), source="plug"))
    registry.register(definition("c", constant("c"), source="core"))
    registry.unregister("a")
    assert registry.get("a") is None
    registry.unregister_source("plug")
    assert [d.key for d in registry.definitions()] == ["c"]


def test_fingerprint_changes_when_a_classifier_is_added_or_versioned():
    registry = ModelClassifierRegistry()
    registry.register(definition("a", constant("a")))
    before = registry.fingerprint()
    registry.register(definition("b", constant("b")))
    added = registry.fingerprint()
    assert added != before
    registry.unregister("b")
    assert registry.fingerprint() == before
    registry.unregister("a")
    registry.register(definition("a", constant("a"), version=2))
    assert registry.fingerprint() != before


def test_fingerprint_ignores_registration_order():
    one = ModelClassifierRegistry()
    two = ModelClassifierRegistry()
    one.register(definition("a", constant("a")))
    one.register(definition("b", constant("b")))
    two.register(definition("b", constant("b")))
    two.register(definition("a", constant("a")))
    assert one.fingerprint() == two.fingerprint()
