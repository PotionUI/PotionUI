from __future__ import annotations

from types import SimpleNamespace

import pytest
import torch

from src.pipelines.pipes.model_loader.qwen.edit_layout import (
    EDIT_PLUS_LAYOUT,
    proj_out_bias_digest,
    qwen_edit_vision_layout,
)

PROJ_OUT_BIAS_HEX = {
    "2508": "19be10be11be0abe22be27be2fbe2ebeaebc04bd34bdcfbc983c673c8a3c883c503d353d633d493dc33dba3dd83db03d853d7f3d8d3d8d3d"
            "473e563e583e3d3ed13df23dee3de03d1f3d023ddb3c1a3d33b9d7bb0e3cff3b28be3bbe38be28be08bdb0bc0abdd3bccc3d973dc83dc33d"
            "c8bdd8bdd0bdb4bdb6bdf4bdf6bdd7bd",
    "2509": "1bbe11be12be09be20be23be2bbe2cbeadbc06bd36bdd8bc893c513c893c7b3c543d363d643d483dc33dbc3ddc3db13d883d823d913d8d3d"
            "423e543e543e3a3ece3df63df13de13d123ddf3cbb3c0a3d8dbb35bc923b8e3b24be3abe37be26be0dbdb9bc0ebddcbcd03d993dcc3dc83d"
            "cdbddbbdcebdb6bdb7bdf7bdf6bdd6bd",
    "2511": "1ebe12be15be0abe21be25be2fbe2fbecabc1ebd44bd01bd843c443c863c773c563d373d633d4b3dc23dba3ddb3db13d8f3d8a3d943d923d"
            "433e563e553e3b3edb3dff3dfd3dec3d1c3dfa3cd73c183dcdbb43bc243b0a3b24be3abe3abe27be04bdbabc0bbdd1bcce3d963dca3dc63d"
            "d6bde3bdd8bdbdbdb8bdfbbdf7bdd7bd",
}


def _bias(release: str, dtype: torch.dtype = torch.bfloat16) -> torch.Tensor:
    raw = torch.frombuffer(bytearray(bytes.fromhex(PROJ_OUT_BIAS_HEX[release])), dtype=torch.bfloat16)
    return raw.float().to(dtype)


def _dit(release: str, dtype: torch.dtype = torch.bfloat16, ref_method: str = "index"):
    return SimpleNamespace(
        config=SimpleNamespace(default_ref_method=ref_method),
        proj_out=SimpleNamespace(bias=_bias(release, dtype)),
    )


@pytest.mark.parametrize("dtype", [torch.bfloat16, torch.float8_e4m3fn, torch.float32, torch.float16])
def test_the_original_edit_release_keeps_the_single_image_layout(dtype):
    assert qwen_edit_vision_layout(_dit("2508", dtype)) == {}


@pytest.mark.parametrize("dtype", [torch.bfloat16, torch.float8_e4m3fn])
def test_the_2509_release_reads_the_picture_in_the_plus_layout(dtype):
    assert qwen_edit_vision_layout(_dit("2509", dtype)) == EDIT_PLUS_LAYOUT


def test_the_timestep_zero_marker_of_2511_means_the_plus_layout():
    assert qwen_edit_vision_layout(_dit("2511", ref_method="index_timestep_zero")) == EDIT_PLUS_LAYOUT
    assert qwen_edit_vision_layout(_dit("2508", ref_method="index_timestep_zero")) == EDIT_PLUS_LAYOUT


def test_the_plus_layout_is_a_picture_label_and_a_384_square_area():
    assert EDIT_PLUS_LAYOUT == {"picture_labels": True, "grounding_area": 147456}


def test_a_checkpoint_it_cannot_read_falls_back_to_the_plus_layout():
    assert proj_out_bias_digest(None) is None
    assert qwen_edit_vision_layout(object()) == EDIT_PLUS_LAYOUT
    meta = SimpleNamespace(config=None, proj_out=SimpleNamespace(bias=torch.empty(64, device="meta")))
    assert qwen_edit_vision_layout(meta) == EDIT_PLUS_LAYOUT


def test_the_layout_is_a_fresh_dict_each_time():
    layout = qwen_edit_vision_layout(_dit("2509"))
    layout["picture_labels"] = False
    assert qwen_edit_vision_layout(_dit("2509"))["picture_labels"] is True
