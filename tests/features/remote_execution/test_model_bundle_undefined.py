from unittest.mock import Mock

import pytest

from src.features.models.records import Model
from src.features.remote_execution.model_bundle_builder import ModelBundleResolutionError, resolve_bundle_entry


def test_a_model_that_needs_a_type_cannot_be_bundled():
    model = Model(id="m1", filename="mystery.safetensors", model_type="undefined", sha256="ab" * 32, file_size=4)

    with pytest.raises(ModelBundleResolutionError, match="needs a type"):
        resolve_bundle_entry(model, Mock(), Mock())


def test_a_typed_model_still_bundles():
    model = Model(id="m1", filename="x.safetensors", model_type="lora", sha256="ab" * 32, file_size=4)

    entry = resolve_bundle_entry(model, Mock(), Mock())

    assert entry.role == "lora"
