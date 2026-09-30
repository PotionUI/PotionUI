from typing import ClassVar

import pytest
from pydantic import Field

from src.features.backends.backend_config import config_class_engine
from src.features.cloud.contracts import CloudBackendConfig
from src.features.cloud.registration import driver_for, register_cloud_provider
from src.features.cloud.testing.fake import FakeCloudConfig, FakeCloudProvider
from src.platform.plugins.hooks import HookContext


def fresh_context() -> HookContext:
    return HookContext(hook_name="backend.register", plugin_id="t", data={"backend_types": {}, "config_types": {}})


def test_registers_the_config_under_the_driver_key():
    context = fresh_context()
    driver = register_cloud_provider(context, FakeCloudProvider)
    assert driver == "cloud.fake" == driver_for(FakeCloudProvider)
    assert context.data["config_types"] == {"cloud.fake": FakeCloudConfig}
    assert context.data["cloud_providers"] == {"cloud.fake": FakeCloudProvider}
    assert context.data["backend_types"] == {}


def test_registration_creates_missing_payload_maps():
    context = HookContext(hook_name="backend.register", plugin_id="t", data={})
    register_cloud_provider(context, FakeCloudProvider)
    assert "cloud.fake" in context.data["config_types"]


def test_registering_the_same_class_twice_is_idempotent():
    context = fresh_context()
    register_cloud_provider(context, FakeCloudProvider)
    register_cloud_provider(context, FakeCloudProvider)
    assert list(context.data["config_types"]) == ["cloud.fake"]


def test_a_second_class_for_the_same_key_is_refused():
    class Other(FakeCloudProvider):
        pass

    context = fresh_context()
    register_cloud_provider(context, FakeCloudProvider)
    with pytest.raises(ValueError, match="already registered"):
        register_cloud_provider(context, Other)


def test_config_must_default_to_the_provider_driver():
    class WrongDriver(CloudBackendConfig):
        driver: str = Field(default="cloud.other")

    class Provider(FakeCloudProvider):
        config_class: ClassVar = WrongDriver

    with pytest.raises(ValueError, match="cloud.fake"):
        register_cloud_provider(fresh_context(), Provider)


def test_config_must_subclass_cloud_backend_config():
    class Provider(FakeCloudProvider):
        config_class: ClassVar = dict

    with pytest.raises(ValueError, match="CloudBackendConfig"):
        register_cloud_provider(fresh_context(), Provider)


@pytest.mark.parametrize("key", ["", "Has Caps", "1abc", "a.b"])
def test_invalid_keys_are_refused(key):
    class Provider(FakeCloudProvider):
        pass

    Provider.key = key
    with pytest.raises(ValueError, match="key"):
        register_cloud_provider(fresh_context(), Provider)


def test_the_written_config_type_resolves_to_the_cloud_engine():
    context = fresh_context()
    register_cloud_provider(context, FakeCloudProvider)
    assert config_class_engine(context.data["config_types"]["cloud.fake"]) == "cloud"
