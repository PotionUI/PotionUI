"""Tests for the @resource provider registry."""

from typing import List, Optional

import pytest

from src.platform.plugins.chat_modes import ChatMode
from src.platform.resources import (
    BaseResourceProvider,
    DuplicateResourceNamespaceError,
    ResolvedResource,
    ResourceContext,
    ResourceRegistry,
    ResourceSuggestion,
)


class FakeProvider(BaseResourceProvider):
    """Provider resolving 'items.<name>' and suggesting a fixed child list."""

    def __init__(self, namespace: str = "items", modes: Optional[List[str]] = None):
        self._namespace = namespace
        self.modes = modes
        self.last_resolve_path = None
        self.last_suggest_args = None

    @property
    def namespace(self) -> str:
        return self._namespace

    async def resolve(self, path, ctx) -> Optional[ResolvedResource]:
        self.last_resolve_path = path
        if path == ["missing"]:
            return None
        if path == ["explode"]:
            raise RuntimeError("boom")
        return ResolvedResource(
            uri=f"{self._namespace}." + ".".join(path),
            namespace=self._namespace,
            kind="item",
            title=path[-1] if path else self._namespace,
            content=f"content for {'.'.join(path)}",
        )

    async def suggest(self, path, partial, ctx, limit=15):
        self.last_suggest_args = (path, partial, limit)
        children = ["alpha", "beta", "gamma"]
        return [
            ResourceSuggestion(
                uri=f"{self._namespace}.{c}", label=c, kind="item",
            )
            for c in children
            if c.startswith(partial)
        ]


def _ctx() -> ResourceContext:
    return ResourceContext(user_id="u1", mode_id="generation")


def _mode(**overrides) -> ChatMode:
    defaults = dict(id="generation", name="Generation", system_prompt="p")
    defaults.update(overrides)
    return ChatMode(**defaults)


class TestRegistration:
    def test_register_and_get(self):
        registry = ResourceRegistry()
        provider = FakeProvider()
        registry.register(provider)
        assert registry.get("items") is provider
        assert registry.get_all() == [provider]

    def test_duplicate_namespace_raises(self):
        registry = ResourceRegistry()
        registry.register(FakeProvider())
        with pytest.raises(DuplicateResourceNamespaceError):
            registry.register(FakeProvider())

    def test_unregister_source(self):
        registry = ResourceRegistry()
        registry.register(FakeProvider("a"), source="plugin-x")
        registry.register(FakeProvider("b"), source="plugin-x")
        registry.register(FakeProvider("c"), source="builtin")
        assert registry.unregister_source("plugin-x") == 2
        assert registry.get("a") is None
        assert registry.get("b") is None
        assert registry.get("c") is not None


class TestModeFiltering:
    def test_provider_modes_restrict_visibility(self):
        registry = ResourceRegistry()
        registry.register(FakeProvider("everywhere"))
        registry.register(FakeProvider("gen_only", modes=["generation"]))
        registry.register(FakeProvider("other_only", modes=["dataset"]))

        visible = {p.namespace for p in registry.providers_for_mode(_mode())}
        assert visible == {"everywhere", "gen_only"}

    def test_mode_namespace_allowlist(self):
        registry = ResourceRegistry()
        registry.register(FakeProvider("a"))
        registry.register(FakeProvider("b"))

        mode = _mode(resource_namespaces=["b"])
        visible = {p.namespace for p in registry.providers_for_mode(mode)}
        assert visible == {"b"}

    def test_no_mode_means_all(self):
        registry = ResourceRegistry()
        registry.register(FakeProvider("a", modes=["dataset"]))
        assert len(registry.providers_for_mode(None)) == 1


class TestResolve:
    async def test_resolve_dispatches_by_first_segment(self):
        registry = ResourceRegistry()
        provider = FakeProvider()
        registry.register(provider)

        resolved = await registry.resolve("items.alpha.beta", _ctx())
        assert provider.last_resolve_path == ["alpha", "beta"]
        assert resolved.kind == "item"
        assert resolved.content == "content for alpha.beta"

    async def test_resolve_strips_at_prefix(self):
        registry = ResourceRegistry()
        registry.register(FakeProvider())
        resolved = await registry.resolve("@items.alpha", _ctx())
        assert resolved.kind == "item"

    async def test_unknown_namespace_yields_error_resource(self):
        registry = ResourceRegistry()
        resolved = await registry.resolve("nope.thing", _ctx())
        assert resolved.kind == "error"
        assert "@nope.thing" in resolved.content

    async def test_unresolved_path_yields_error_resource(self):
        registry = ResourceRegistry()
        registry.register(FakeProvider())
        resolved = await registry.resolve("items.missing", _ctx())
        assert resolved.kind == "error"

    async def test_provider_exception_yields_error_resource(self):
        registry = ResourceRegistry()
        registry.register(FakeProvider())
        resolved = await registry.resolve("items.explode", _ctx())
        assert resolved.kind == "error"

    async def test_empty_uri_yields_error_resource(self):
        registry = ResourceRegistry()
        resolved = await registry.resolve("", _ctx())
        assert resolved.kind == "error"


class TestSuggest:
    async def test_empty_query_lists_namespaces(self):
        registry = ResourceRegistry()
        registry.register(FakeProvider("models"))
        registry.register(FakeProvider("presets"))

        suggestions = await registry.suggest("", _mode(), _ctx())
        assert [s.uri for s in suggestions] == ["models", "presets"]
        assert all(s.has_children for s in suggestions)

    async def test_dotfree_query_prefix_matches_namespaces(self):
        registry = ResourceRegistry()
        registry.register(FakeProvider("models"))
        registry.register(FakeProvider("presets"))

        suggestions = await registry.suggest("mo", _mode(), _ctx())
        assert [s.uri for s in suggestions] == ["models"]

    async def test_dotted_query_delegates_with_path_and_partial(self):
        registry = ResourceRegistry()
        provider = FakeProvider("items")
        registry.register(provider)

        suggestions = await registry.suggest("items.sub.al", _mode(), _ctx(), limit=10)
        assert provider.last_suggest_args == (["sub"], "al", 10)
        assert [s.label for s in suggestions] == ["alpha"]

    async def test_mode_filtering_applies_to_suggest(self):
        registry = ResourceRegistry()
        registry.register(FakeProvider("hidden", modes=["dataset"]))
        suggestions = await registry.suggest("hidden.x", _mode(), _ctx())
        assert suggestions == []

    async def test_suggest_provider_exception_returns_empty(self):
        class ExplodingProvider(FakeProvider):
            async def suggest(self, path, partial, ctx, limit=15):
                raise RuntimeError("boom")

        registry = ResourceRegistry()
        registry.register(ExplodingProvider("items"))
        assert await registry.suggest("items.x", _mode(), _ctx()) == []
