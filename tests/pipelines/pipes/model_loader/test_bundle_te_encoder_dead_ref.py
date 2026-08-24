"""A bundle whose TE weakref has died must report te_encoder=None, not crash.

Regression for the inline-enhance crash: the base generator's
_release_idle_te evicts the TE from the MODELS cache, the shared bundle's
WeakModelRef goes dead, and a second generator instance in the same pipeline
reads bundle.te_encoder while constructing its NativeGenerator. SeedVR2's
bundle already returns None by contract, so downstream handles it; every other
family's property must too.
"""

import gc

import pytest

from src.pipelines.pipes.model_loader.anima.bundle import AnimaModelBundle
from src.pipelines.pipes.model_loader.flux.bundle import FluxModelBundle
from src.pipelines.pipes.model_loader.krea2.bundle import Krea2ModelBundle
from src.pipelines.pipes.model_loader.ltx.bundle import LTXModelBundle
from src.pipelines.pipes.model_loader.qwen.bundle import QwenModelBundle
from src.pipelines.pipes.model_loader.wan22.bundle import WanModelBundle
from src.pipelines.pipes.model_loader.z_image.bundle import ZImageModelBundle

BUNDLES = [
    AnimaModelBundle,
    FluxModelBundle,
    Krea2ModelBundle,
    LTXModelBundle,
    QwenModelBundle,
    WanModelBundle,
    ZImageModelBundle,
]


class _Component:
    module = object()

    def unload(self):
        pass


@pytest.mark.parametrize("bundle_cls", BUNDLES, ids=lambda c: c.__name__)
def test_te_encoder_none_when_ref_dead(bundle_cls):
    te = _Component()
    dit_field = "high_dit" if bundle_cls is WanModelBundle else "dit"
    bundle = bundle_cls(**{dit_field: _Component(), "te": te, "vae": _Component()})
    assert bundle.te_encoder is te.module

    del te
    gc.collect()
    assert bundle.te is None
    assert bundle.te_encoder is None
