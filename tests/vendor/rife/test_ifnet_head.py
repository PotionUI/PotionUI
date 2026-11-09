import pytest

torch = pytest.importorskip("torch")

from tests.vendor.rife.layouts import published_head_layout, state_dict_for
from vendor.rife import ifnet_head
from vendor.rife.ifnet import IFNet
from vendor.rife.ifnet_head import HeadIFNet
from vendor.rife.inference import interpolate, interpolate_prepared, pad_dims, prepare_frame
from vendor.rife.loader import SUPPORTED_FAMILY, load_ifnet

EXPECTED = {
    "rife4.22": {"blocks": [(23, 256), (36, 192), (36, 96), (36, 48)], "encode": (32, 8),
                 "scales": (8.0, 4.0, 2.0, 1.0)},
    "rife4.22.lite": {"blocks": [(15, 192), (28, 128), (28, 64), (28, 32)], "encode": (16, 4),
                      "scales": (8.0, 4.0, 2.0, 1.0)},
    "rife4.25": {"blocks": [(15, 192), (28, 128), (28, 96), (28, 64), (28, 32)], "encode": (16, 4),
                 "scales": (16.0, 8.0, 4.0, 2.0, 1.0)},
    "rife4.26": {"blocks": [(15, 192), (28, 128), (28, 96), (28, 64), (28, 32)], "encode": (16, 4),
                 "scales": (16.0, 8.0, 4.0, 2.0, 1.0)},
}


def _published(name, seed=0):
    return state_dict_for(published_head_layout(name), seed=seed)


def _pair(h=72, w=104):
    torch.manual_seed(0)
    return torch.rand(1, 3, h, w), torch.rand(1, 3, h, w)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_published_layout_loads_strictly(name):
    model = load_ifnet(_published(name), "cpu")

    assert isinstance(model, HeadIFNet)
    assert model.num_blocks == len(EXPECTED[name]["blocks"])
    assert model.scale_base == EXPECTED[name]["scales"]
    assert model.pad_modulo == 64
    assert model.mask_is_residual is False
    for idx, (in_planes, c) in enumerate(EXPECTED[name]["blocks"]):
        conv = getattr(model, f"block{idx}").conv0[0][0]
        assert (conv.in_channels, conv.out_channels * 2) == (in_planes, c)
    mid, out = EXPECTED[name]["encode"]
    assert (model.encode.cnn0.out_channels, model.encode.cnn3.out_channels) == (mid, out)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_the_published_files_carry_training_only_tensors_that_are_dropped(name):
    layout = published_head_layout(name)
    training = [key for key in layout if key.startswith(("module.teacher.", "module.caltime."))]

    model = load_ifnet(_published(name), "cpu")

    assert len(training) == 40
    assert not any(key.startswith(("teacher.", "caltime.")) for key in model.state_dict())
    assert len(model.state_dict()) == len(layout) - 40


def test_weights_land_on_the_published_keys():
    state = _published("rife4.25", seed=3)

    model = load_ifnet(state, "cpu")

    assert torch.equal(model.block4.lastconv[0].weight, state["module.block4.lastconv.0.weight"])
    assert torch.equal(model.encode.cnn3.bias, state["module.encode.cnn3.bias"])


def test_an_unknown_extra_tensor_is_still_refused():
    state = _published("rife4.25")
    state["module.refine.weight"] = torch.zeros(1)

    with pytest.raises(ValueError, match="loadable"):
        load_ifnet(state, "cpu")


def test_a_missing_tensor_is_refused():
    state = _published("rife4.26")
    del state["module.block2.convblock.3.beta"]

    with pytest.raises(ValueError, match="loadable"):
        load_ifnet(state, "cpu")


def test_rife425_lite_is_refused_by_name():
    with pytest.raises(ValueError, match="4.25.lite"):
        load_ifnet(_published("rife4.25.lite"), "cpu")


def test_a_head_checkpoint_of_another_depth_is_refused():
    state = _published("rife4.25")
    for key in [key for key in state if ".block4." in key]:
        state[key.replace(".block4.", ".block5.")] = state[key].clone()

    with pytest.raises(ValueError, match="6 blocks"):
        load_ifnet(state, "cpu")


def test_an_incomplete_head_encoder_is_refused():
    state = _published("rife4.25")
    del state["module.encode.cnn2.weight"]

    with pytest.raises(ValueError, match="Head"):
        load_ifnet(state, "cpu")


def test_the_supported_family_names_the_head_line():
    assert "4.21" in SUPPORTED_FAMILY and "4.26" in SUPPORTED_FAMILY and "rife49" in SUPPORTED_FAMILY


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_forward_crops_back_to_the_input_size(name):
    model = load_ifnet(_published(name), "cpu")
    img0, img1 = _pair()

    out = interpolate(model, img0, img1, 0.4)

    assert out.shape == (1, 3, 72, 104)
    assert torch.isfinite(out).all()


def test_head_frames_pad_to_64_and_older_frames_to_128():
    head = load_ifnet(_published("rife4.25"), "cpu")
    older = IFNet([(7, 16), (12, 16), (12, 16), (12, 16)], None).eval()
    img = torch.rand(1, 3, 130, 200)

    assert prepare_frame(head, img).padded.shape[-2:] == (192, 256)
    assert prepare_frame(older, img).padded.shape[-2:] == (256, 256)
    assert pad_dims(130, 200, 0.5, 64) == (256, 256)
    assert pad_dims(130, 200, 1.0) == (256, 256)


def test_prepared_head_frames_give_the_same_frame_as_a_fresh_pass():
    model = load_ifnet(_published("rife4.26", seed=5), "cpu")
    img0, img1 = _pair()

    f0, f1 = prepare_frame(model, img0), prepare_frame(model, img1)

    assert f0.features is not None and f0.features.shape[1] == 4
    assert torch.equal(interpolate_prepared(model, f0, f1, 0.3), interpolate(model, img0, img1, 0.3))


def test_the_forward_follows_the_model_scale_ladder(monkeypatch):
    model = load_ifnet(_published("rife4.25"), "cpu")
    seen = []
    original = model.forward

    def spy(x, timestep=0.5, scale_list=None, f0=None, f1=None):
        seen.append(list(scale_list))
        return original(x, timestep, scale_list, f0=f0, f1=f1)

    monkeypatch.setattr(model, "forward", spy)
    img0, img1 = _pair(64, 64)

    interpolate(model, img0, img1, 0.5, flow_scale=0.5)

    assert seen == [[32.0, 16.0, 8.0, 4.0, 2.0]]


def test_the_head_mask_is_a_blend_weight_between_0_and_1():
    model = load_ifnet(_published("rife4.25", seed=1), "cpu")
    img0, img1 = _pair(64, 64)

    with torch.no_grad():
        _, mask, _ = model(torch.cat((img0, img1), 1), 0.5, list(model.scale_base))

    assert mask.min() >= 0 and mask.max() <= 1


def test_each_head_block_after_the_first_warps_both_feature_maps(monkeypatch):
    model = load_ifnet(_published("rife4.25"), "cpu")
    warped = []
    original = ifnet_head.warp

    def spy(tensor, flow):
        warped.append(tensor.shape[1])
        return original(tensor, flow)

    monkeypatch.setattr(ifnet_head, "warp", spy)
    img0, img1 = _pair(64, 64)

    with torch.no_grad():
        model(torch.cat((img0, img1), 1), 0.5, list(model.scale_base))

    assert warped.count(4) == 2 * (model.num_blocks - 1)
    assert warped.count(3) == 2 * model.num_blocks
