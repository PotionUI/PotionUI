"""RequirementCheckerRegistry: register/unregister/get/all, duplicate guard."""

import pytest

from src.platform.plugins.requirement_checkers import (
    DuplicateRequirementCheckerError,
    RequirementCheckerRegistration,
    RequirementCheckerRegistry,
)


class _FakeChecker:
    type = "fake"
    schema = None

    async def check(self, spec, ctx):
        raise NotImplementedError


class TestRequirementCheckerRegistry:
    def test_register_and_get(self):
        registry = RequirementCheckerRegistry()
        checker = _FakeChecker()
        registry.register(RequirementCheckerRegistration(type_name="fake", checker=checker, source="core"))

        registration = registry.get("fake")
        assert registration is not None
        assert registration.checker is checker
        assert registration.source == "core"

    def test_get_unknown_type_returns_none(self):
        registry = RequirementCheckerRegistry()
        assert registry.get("does-not-exist") is None

    def test_duplicate_type_raises(self):
        registry = RequirementCheckerRegistry()
        registry.register(RequirementCheckerRegistration(type_name="fake", checker=_FakeChecker(), source="core"))

        with pytest.raises(DuplicateRequirementCheckerError):
            registry.register(RequirementCheckerRegistration(type_name="fake", checker=_FakeChecker(), source="plugin-a"))

    def test_unregister_source_removes_only_that_sources_types(self):
        registry = RequirementCheckerRegistry()
        registry.register(RequirementCheckerRegistration(type_name="core-type", checker=_FakeChecker(), source="core"))
        registry.register(RequirementCheckerRegistration(type_name="plugin-type", checker=_FakeChecker(), source="plugin-a"))

        registry.unregister_source("plugin-a")

        assert registry.get("core-type") is not None
        assert registry.get("plugin-type") is None

    def test_all_returns_every_registration(self):
        registry = RequirementCheckerRegistry()
        registry.register(RequirementCheckerRegistration(type_name="a", checker=_FakeChecker(), source="core"))
        registry.register(RequirementCheckerRegistration(type_name="b", checker=_FakeChecker(), source="core"))

        assert {r.type_name for r in registry.all()} == {"a", "b"}
