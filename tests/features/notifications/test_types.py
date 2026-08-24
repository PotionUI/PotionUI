"""Tests for the NotificationTypeRegistry."""
import pytest

from src.features.notifications.types import (
    NotificationTypeRegistry,
    NotificationTypeSpec,
    DuplicateNotificationTypeError,
    notification_type_registry,
)


class TestNotificationTypeRegistry:

    @pytest.fixture
    def registry(self):
        return NotificationTypeRegistry()

    def test_register_and_get(self, registry):
        spec = NotificationTypeSpec(key="foo.bar", label="Foo Bar")
        registry.register(spec)

        assert registry.get("foo.bar") is spec
        assert registry.has("foo.bar") is True

    def test_get_unregistered_returns_none(self, registry):
        assert registry.get("nonexistent") is None
        assert registry.has("nonexistent") is False

    def test_register_duplicate_key_raises(self, registry):
        registry.register(NotificationTypeSpec(key="foo.bar", label="First"))

        with pytest.raises(DuplicateNotificationTypeError):
            registry.register(NotificationTypeSpec(key="foo.bar", label="Second"))

    def test_all_returns_registered_specs(self, registry):
        spec1 = NotificationTypeSpec(key="a", label="A")
        spec2 = NotificationTypeSpec(key="b", label="B")
        registry.register(spec1)
        registry.register(spec2)

        result = registry.all()

        assert len(result) == 2
        assert spec1 in result
        assert spec2 in result

    def test_default_enabled_defaults_true(self, registry):
        spec = NotificationTypeSpec(key="foo", label="Foo")
        assert spec.default_enabled is True

    def test_admin_only_defaults_false(self, registry):
        spec = NotificationTypeSpec(key="foo", label="Foo")
        assert spec.admin_only is False

    # ========== Core module-level singleton ==========

    def test_core_types_are_registered_on_singleton(self):
        assert notification_type_registry.has("generation.completed")
        assert notification_type_registry.has("generation.failed")
        assert notification_type_registry.has("system.plugins")

    def test_generation_completed_spec(self):
        spec = notification_type_registry.get("generation.completed")
        assert spec.category == "generation"
        assert spec.label == "Generation completed"

    def test_generation_failed_spec(self):
        spec = notification_type_registry.get("generation.failed")
        assert spec.category == "generation"
        assert spec.label == "Generation failed"

    def test_system_plugins_spec(self):
        spec = notification_type_registry.get("system.plugins")
        assert spec.category == "system"
        assert spec.label == "Plugin lifecycle events"
        assert spec.admin_only is True
