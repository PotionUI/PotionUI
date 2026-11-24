import pytest
import torch
from PIL import Image
from safetensors.torch import save_file

from src.pipelines.pipes.generator.sdxl import ip_adapter as ipa


@pytest.fixture(autouse=True)
def mock_cuda_for_tests():
    yield


CROSS = 32
CLIP_DIM = 16


def _tiny_unet():
    from diffusers import UNet2DConditionModel

    torch.manual_seed(0)
    return UNet2DConditionModel(
        sample_size=8,
        in_channels=4,
        out_channels=4,
        block_out_channels=(32, 64),
        layers_per_block=1,
        down_block_types=("DownBlock2D", "CrossAttnDownBlock2D"),
        up_block_types=("CrossAttnUpBlock2D", "UpBlock2D"),
        cross_attention_dim=CROSS,
        attention_head_dim=(4, 8),
        norm_num_groups=8,
        addition_embed_type="text_time",
        addition_time_embed_dim=8,
        projection_class_embeddings_input_dim=16 + 6 * 8,
        transformer_layers_per_block=1,
        use_linear_projection=True,
    ).eval()


def _cross_attention_widths(unet):
    blocks = unet.config.block_out_channels
    widths = []
    for name in unet.attn_processors:
        if not name.endswith("attn2.processor"):
            continue
        if name.startswith("mid_block"):
            widths.append(blocks[-1])
        elif name.startswith("down_blocks"):
            widths.append(blocks[int(name.split(".")[1])])
        else:
            widths.append(list(reversed(blocks))[int(name.split(".")[1])])
    return widths


def _plain_state(unet, clip_dim=CLIP_DIM):
    torch.manual_seed(1)
    image_proj = {
        "proj.weight": torch.randn(4 * CROSS, clip_dim) * 0.1,
        "proj.bias": torch.zeros(4 * CROSS),
        "norm.weight": torch.ones(CROSS),
        "norm.bias": torch.zeros(CROSS),
    }
    adapter = {}
    for index, width in enumerate(_cross_attention_widths(unet)):
        adapter[f"{2 * index + 1}.to_k_ip.weight"] = torch.randn(width, CROSS) * 0.5
        adapter[f"{2 * index + 1}.to_v_ip.weight"] = torch.randn(width, CROSS) * 0.5
    return {"image_proj": image_proj, "ip_adapter": adapter}


def _forward(unet, image_embeds=None):
    torch.manual_seed(2)
    added = {"text_embeds": torch.randn(2, 16), "time_ids": torch.randn(2, 6)}
    if image_embeds is not None:
        added["image_embeds"] = image_embeds
    with torch.no_grad():
        return unet(
            torch.randn(2, 4, 8, 8), torch.tensor([5.0, 5.0]),
            encoder_hidden_states=torch.randn(2, 3, CROSS), added_cond_kwargs=added,
        ).sample


def _embeds(clip_dim=CLIP_DIM):
    torch.manual_seed(3)
    return [torch.randn(2, 1, clip_dim)]


def test_injection_swaps_cross_attention_processors_and_adds_the_projection():
    unet = _tiny_unet()
    before = {name: type(p).__name__ for name, p in unet.attn_processors.items()}
    handle = ipa.inject_ip_adapter(unet, _plain_state(unet), 0.6)
    after = {name: type(p).__name__ for name, p in unet.attn_processors.items()}
    kinds = ipa.ip_processor_types()
    swapped = {name for name, p in unet.attn_processors.items() if isinstance(p, kinds)}
    assert swapped == {name for name in after if name.endswith("attn2.processor")}
    assert all(not name.endswith("attn1.processor") for name in swapped)
    assert unet.encoder_hid_proj is not None
    assert unet.config.encoder_hid_dim_type == "ip_image_proj"
    assert before != after
    ipa.remove_ip_adapter(unet, handle)


def test_scale_reaches_every_ip_processor():
    unet = _tiny_unet()
    ipa.inject_ip_adapter(unet, _plain_state(unet), 0.35)
    scales = [p.scale for p in unet.attn_processors.values() if isinstance(p, ipa.ip_processor_types())]
    assert scales and all(s == [0.35] for s in scales)
    ipa.set_ip_adapter_scale(unet, 1.2)
    assert all(p.scale == [1.2] for p in unet.attn_processors.values() if isinstance(p, ipa.ip_processor_types()))


def test_reference_changes_the_unet_output_and_scale_zero_does_not():
    unet = _tiny_unet()
    base = _forward(unet)
    handle = ipa.inject_ip_adapter(unet, _plain_state(unet), 1.0)
    with_ref = _forward(unet, _embeds())
    ipa.set_ip_adapter_scale(unet, 0.0)
    zero = _forward(unet, _embeds())
    ipa.remove_ip_adapter(unet, handle)
    assert not torch.allclose(base, with_ref, atol=1e-4)
    assert torch.allclose(base, zero, atol=1e-5)


def test_removal_restores_the_unet_exactly():
    unet = _tiny_unet()
    base = _forward(unet)
    saved = dict(unet.attn_processors)
    handle = ipa.inject_ip_adapter(unet, _plain_state(unet), 1.0)
    ipa.remove_ip_adapter(unet, handle)
    assert unet.encoder_hid_proj is None
    assert unet.config.encoder_hid_dim_type is None
    assert all(unet.attn_processors[name] is proc for name, proc in saved.items())
    assert torch.equal(base, _forward(unet))


def test_flat_state_splits_into_projection_and_adapter_groups():
    state = ipa.split_ip_adapter_state({
        "image_proj.proj.weight": 1, "image_proj.norm.bias": 2, "ip_adapter.1.to_k_ip.weight": 3,
    })
    assert state == {"image_proj": {"proj.weight": 1, "norm.bias": 2}, "ip_adapter": {"1.to_k_ip.weight": 3}}


def test_a_file_without_adapter_weights_is_rejected():
    with pytest.raises(ValueError, match="not an IP-Adapter"):
        ipa.split_ip_adapter_state({"model.weight": 1})


def test_plain_adapters_pick_their_encoder_from_the_projection_width():
    vit_h = ipa.classify_ip_adapter({"image_proj": {"proj.weight": torch.zeros(8, 1024)}})
    big_g = ipa.classify_ip_adapter({"image_proj": {"proj.weight": torch.zeros(8, 1280)}})
    assert (vit_h.variant, vit_h.encoder, vit_h.uses_hidden_states) == ("plain", "vit_h", False)
    assert (big_g.variant, big_g.encoder, big_g.uses_hidden_states) == ("plain", "big_g", False)


def test_plus_adapters_use_the_vit_h_hidden_states():
    info = ipa.classify_ip_adapter({"image_proj": {"latents": torch.zeros(1, 16, 8), "proj_in.weight": torch.zeros(8, 1280)}})
    assert (info.variant, info.encoder, info.uses_hidden_states) == ("plus", "vit_h", True)


def test_plus_adapter_on_a_wider_encoder_is_rejected():
    with pytest.raises(ValueError, match="1664"):
        ipa.classify_ip_adapter({"image_proj": {"latents": torch.zeros(1, 16, 8), "proj_in.weight": torch.zeros(8, 1664)}})


def test_unknown_projection_width_is_rejected():
    with pytest.raises(ValueError, match="no known image encoder"):
        ipa.classify_ip_adapter({"image_proj": {"proj.weight": torch.zeros(8, 777)}})


def _tiny_encoder():
    from transformers import CLIPVisionConfig, CLIPVisionModelWithProjection

    torch.manual_seed(4)
    config = CLIPVisionConfig(
        hidden_size=64, intermediate_size=128, projection_dim=CLIP_DIM, num_hidden_layers=3,
        num_attention_heads=1, image_size=224, patch_size=56, hidden_act="gelu", layer_norm_eps=1e-5,
    )
    return CLIPVisionModelWithProjection(config).eval()


def test_vision_config_is_inferred_from_state_dict_shapes():
    model = _tiny_encoder()
    kwargs = ipa.clip_vision_config_kwargs({k: v.shape for k, v in model.state_dict().items()})
    assert kwargs["hidden_size"] == 64
    assert kwargs["intermediate_size"] == 128
    assert kwargs["projection_dim"] == CLIP_DIM
    assert kwargs["num_hidden_layers"] == 3
    assert kwargs["image_size"] == 224
    assert kwargs["patch_size"] == 56


def test_loaded_encoder_matches_the_source_model(tmp_path):
    model = _tiny_encoder()
    path = tmp_path / "enc.safetensors"
    save_file({k: v.contiguous() for k, v in model.state_dict().items()}, str(path))
    loaded = ipa.load_image_encoder(str(path), torch.float32, "cpu")
    pixels = torch.randn(1, 3, 224, 224)
    with torch.no_grad():
        assert torch.allclose(model(pixels).image_embeds, loaded(pixels).image_embeds, atol=1e-6)


def test_encoder_mismatch_is_named_in_the_error(tmp_path):
    unet = _tiny_unet()
    adapter_path = tmp_path / "ad.safetensors"
    state = _plain_state(unet, clip_dim=1024)
    flat = {f"image_proj.{k}": v for k, v in state["image_proj"].items()}
    flat.update({f"ip_adapter.{k}": v for k, v in state["ip_adapter"].items()})
    save_file(flat, str(adapter_path))
    encoder_path = tmp_path / "enc.safetensors"
    save_file({k: v.contiguous() for k, v in _tiny_encoder().state_dict().items()}, str(encoder_path))
    with pytest.raises(ValueError, match="CLIP ViT-H"):
        with ipa.reference_session(unet, str(adapter_path), str(encoder_path), Image.new("RGB", (40, 40)), 0.6, "cpu", torch.float32):
            pass
    assert unet.encoder_hid_proj is None


def test_plain_encoding_stacks_a_zero_negative_under_the_positive():
    encoder = _tiny_encoder()
    info = ipa.IPAdapterInfo("plain", "vit_h", False)
    embeds = ipa.encode_reference(Image.new("RGB", (40, 40), (200, 30, 30)), encoder, info, "cpu", torch.float32)
    assert embeds.shape == (2, 1, CLIP_DIM)
    assert torch.count_nonzero(embeds[0]) == 0
    assert torch.count_nonzero(embeds[1]) > 0


def test_plus_encoding_uses_the_penultimate_hidden_state():
    encoder = _tiny_encoder()
    info = ipa.IPAdapterInfo("plus", "vit_h", True)
    embeds = ipa.encode_reference(Image.new("RGB", (40, 40), (10, 200, 30)), encoder, info, "cpu", torch.float32)
    assert embeds.shape == (2, 1, 17, 64)
    assert not torch.allclose(embeds[0], embeds[1])


def test_session_injects_then_restores_on_exit_and_on_error(tmp_path, monkeypatch):
    unet = _tiny_unet()
    state = _plain_state(unet, clip_dim=CLIP_DIM)
    flat = {f"image_proj.{k}": v for k, v in state["image_proj"].items()}
    flat.update({f"ip_adapter.{k}": v for k, v in state["ip_adapter"].items()})
    adapter_path = tmp_path / "ad.safetensors"
    save_file(flat, str(adapter_path))
    encoder_path = tmp_path / "enc.safetensors"
    save_file({k: v.contiguous() for k, v in _tiny_encoder().state_dict().items()}, str(encoder_path))
    monkeypatch.setitem(ipa.ENCODER_WIDTHS, "vit_h", (64, CLIP_DIM))
    monkeypatch.setattr(ipa, "classify_ip_adapter", lambda s: ipa.IPAdapterInfo("plain", "vit_h", False))
    with pytest.raises(RuntimeError, match="boom"):
        with ipa.reference_session(unet, str(adapter_path), str(encoder_path), Image.new("RGB", (40, 40)), 0.6, "cpu", torch.float32) as embeds:
            assert unet.encoder_hid_proj is not None
            assert embeds[0].shape == (2, 1, CLIP_DIM)
            raise RuntimeError("boom")
    assert unet.encoder_hid_proj is None
    assert unet.config.encoder_hid_dim_type is None
