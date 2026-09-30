from __future__ import annotations

import pytest
import torch
from safetensors.torch import save_file

from src.platform.runtime.native.arch.anima.model import Anima
from src.platform.runtime.native.arch.flux.model import Flux
from src.platform.runtime.native.engine import NativeEngineLoader
from tests.platform.runtime.native.arch.test_anima_model import TINY as ANIMA_TINY
from tests.platform.runtime.native.test_engine import _FLUX1, _finite_sd
from vendor.gpl.comfyui.ops import disable_weight_init, pick_operations


def _bundle(dit_sd, prefix):
    sd = {prefix + key: value for key, value in dit_sd.items()}
    sd["vae.decoder.conv_in.weight"] = torch.full((3, 3), float("nan"))
    sd["vae.decoder.conv_out.weight"] = torch.full((3, 3), float("nan"))
    sd["text_encoders.t5xxl.transformer.shared.weight"] = torch.full((5, 5), float("nan"))
    sd["text_encoders.clip_l.transformer.embeddings.weight"] = torch.full((5, 5), float("nan"))
    return sd


@pytest.fixture(scope="module")
def flux_sd():
    return _finite_sd(Flux.from_config(_FLUX1, disable_weight_init))


@pytest.fixture(scope="module")
def anima_sd():
    module = Anima.from_config(ANIMA_TINY, pick_operations(torch.float32, torch.float32))
    torch.manual_seed(0)
    sd = {}
    for key, value in module.state_dict().items():
        real = torch.empty(tuple(value.shape))
        if key.endswith(".weight") and ".norm" in key:
            sd[key] = real.fill_(1.0)
        elif value.is_floating_point():
            sd[key] = real.normal_(0.0, 0.02)
        else:
            sd[key] = real.zero_().to(value.dtype)
    return sd


def test_flux_all_in_one_loads_only_the_transformer(flux_sd, tmp_path):
    path = tmp_path / "flux1-dev-fp8.safetensors"
    save_file(_bundle(flux_sd, "model.diffusion_model."), str(path))

    model = NativeEngineLoader(device="cpu").load(path, "diffusion_model")

    assert (model.spec.family, model.spec.variant) == ("flux", "flux1")
    assert set(model.module.state_dict()) == set(flux_sd)


def test_flux_all_in_one_matches_the_transformer_only_file(flux_sd, tmp_path):
    bundle = tmp_path / "bundle.safetensors"
    plain = tmp_path / "plain.safetensors"
    save_file(_bundle(flux_sd, "model.diffusion_model."), str(bundle))
    save_file(flux_sd, str(plain))
    loader = NativeEngineLoader(device="cpu")

    from_bundle = loader.load(bundle, "diffusion_model").module.state_dict()
    from_plain = loader.load(plain, "diffusion_model").module.state_dict()

    assert set(from_bundle) == set(from_plain)
    for key in from_plain:
        assert torch.equal(from_bundle[key], from_plain[key])


def test_anima_with_the_net_prefix_loads(anima_sd, tmp_path):
    path = tmp_path / "anima-base.safetensors"
    save_file({"net." + key: value for key, value in anima_sd.items()}, str(path))

    model = NativeEngineLoader(device="cpu").load(path, "diffusion_model")

    assert model.spec.family == "anima"
    assert set(model.module.state_dict()) >= set(anima_sd)
