from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src.platform.imaging.filters import CORE_OPS, OpSpec
from src.platform.media_tools import InvalidToolParams, ToolContext, ToolFailed, ToolMedia, validate_params
from src.platform.media_tools.effects.looks import LIVE_SLOTS, Look, empty_env, look_env, look_table
from src.platform.media_tools.effects.runtime import EffectRuntime
from src.platform.media_tools.effects.tool import ASSETS_KEY, LOOKS_KEY
from src.platform.media_tools.timeline import Clock, StepContext
from tests.platform.media_tools.effects.support import core_effect, effect_spec

MEDIA = ToolMedia(path=Path("clip.mp4"), kind="video", width=64, height=36, duration=1.0, fps=24.0)
TONED = Look(id="toned", name="Toned", group="Film", intensity=100, steps=({"op": "tone", "contrast": 30},), revision="t")
REEL = Look(
    id="mine:reel", name="Reel", group="Mine", intensity=50, revision="r",
    steps=({"op": "tone", "saturation": -20}, {"op": "vignette", "amount": 40}, {"op": "grain", "amount": 30, "size": 2, "seed": 9}),
)


class Library:
    def __init__(self, *looks, owner="owner"):
        self.looks = {look.id: look for look in looks}
        self.owner = owner

    def resolve(self, ref, user_id):
        look = self.looks.get(ref)
        if look is not None and ref.startswith("mine:") and user_id != self.owner:
            return None
        return look

    def list(self, user_id):
        return list(self.looks.values())


def runtime_with(*looks, ffmpeg=True):
    runtime = EffectRuntime(filters=(lambda: frozenset({"lut3d"})) if ffmpeg else frozenset, torch_available=lambda: False)
    runtime.use_looks(Library(*looks))
    return runtime


def step_for(media, user_id, tmp_path):
    return StepContext(source=media, clock=Clock(length=media.duration), ctx=ToolContext(tmp_path, user_id=user_id))


def test_the_look_effect_takes_a_look_and_a_strength_and_previews_through_a_look_table():
    effect = core_effect("look")
    spec = effect_spec("look")

    assert spec.accepts == ("video", "image") and effect.group == "colour"
    assert [(p["name"], p["type"]) for p in spec.params] == [("look", "look"), ("strength", "float")]
    assert [(t.uniform, t.source, t.kind, t.mix) for t in effect.textures] == [("table", "look", "lut3d", "amount")]
    assert effect.live.source == "look.live == 1"
    assert effect.render[0].kind == "ffmpeg" and effect.render[0].when.source == "plain"
    assert {f"u_s{slot}_{key}" for slot in range(LIVE_SLOTS) for key in ("kind", "a", "b", "c")} <= {u["name"] for u in effect.uniforms()}


@pytest.mark.parametrize("ref", ["bad ref", "x" * 200, "/etc/passwd", "-dash", 7])
def test_a_look_ref_is_a_plain_id(ref):
    with pytest.raises(InvalidToolParams):
        validate_params(effect_spec("look"), {"look": ref})


def test_the_env_carries_the_core_spatial_steps_in_order_for_the_shader():
    env = look_env(REEL)

    assert (env["has"], env["intensity"], env["spatial"], env["live"]) == (1, 50.0, 2, 1)
    assert [env["s0_kind"], env["s0_a"], env["s0_b"], env["s0_c"]] == [1, 40.0, 50.0, 60.0]
    assert [env["s1_kind"], env["s1_a"], env["s1_b"], env["s1_c"]] == [2, 30.0, 2.0, 9.0]
    assert env["s2_kind"] == 0 and look_env(None) == empty_env()


def test_a_look_the_shader_cannot_draw_is_not_live():
    crowded = Look(id="many", name="Many", group="", intensity=100, revision="m", steps=tuple({"op": "grain", "amount": 5} for _ in range(LIVE_SLOTS + 1)))
    painterly = OpSpec("paint.smear", "Smear", "spatial", (), source="plugin", plugin_id="paint", python="ops.py:Smear")
    plugin = Look(id="smear", name="Smear", group="", intensity=100, revision="s", steps=({"op": "paint.smear"},), ops={**CORE_OPS, "paint.smear": painterly})

    assert look_env(crowded)["live"] == 0 and look_env(crowded)["spatial"] == LIVE_SLOTS + 1
    assert look_env(plugin)["live"] == 0 and look_env(plugin)["s0_kind"] == 0


def test_the_table_is_the_full_strength_look_and_is_cached_by_revision():
    table = look_table(TONED)

    assert table.size == 33 and look_table(TONED) is table
    assert look_table(Look(**{**TONED.__dict__, "revision": "t2"})) is not table
    assert np.abs(table.data[-1] - 1).max() < 1e-6 and table.data[0].max() < 1e-6


def test_prepare_resolves_your_own_look_with_your_user_and_stages_it_for_ffmpeg(tmp_path):
    spec = effect_spec("look", runtime_with(TONED, REEL))
    params = validate_params(spec, {"look": "mine:reel"})

    prepared = spec.impl.prepare(params, step_for(MEDIA, "owner", tmp_path))

    assert prepared[LOOKS_KEY] == {"look": REEL}
    assert list(prepared[ASSETS_KEY]) == ["look"]
    with pytest.raises(ToolFailed, match="not available"):
        spec.impl.prepare(params, step_for(MEDIA, "stranger", tmp_path))
    with pytest.raises(ToolFailed, match="not available"):
        spec.impl.video_filter(MEDIA, params)


def test_a_colour_only_look_renders_with_ffmpeg_and_a_spatial_one_with_numpy(tmp_path):
    spec = effect_spec("look", runtime_with(TONED, REEL))
    plain = spec.impl.prepare(validate_params(spec, {"look": "toned", "strength": 0.5}), step_for(MEDIA, None, tmp_path))
    spatial = spec.impl.prepare(validate_params(spec, {"look": "mine:reel"}), step_for(MEDIA, "owner", tmp_path))

    ffmpeg = spec.impl.video_filter(MEDIA, plain)
    staged = spec.impl.video_filter(MEDIA, spatial)

    assert ffmpeg.stage is None and ffmpeg.vf[0].startswith("lut3d=file=") and "interp='trilinear'" in ffmpeg.vf[0]
    assert staged.stage is not None and staged.stage.kind == "numpy"


def test_no_look_or_zero_strength_adds_no_filter_and_keeps_pixels(tmp_path):
    spec = effect_spec("look", runtime_with(TONED))
    for raw in ({}, {"look": "toned", "strength": 0}):
        params = spec.impl.prepare(validate_params(spec, raw), step_for(MEDIA, None, tmp_path))
        assert spec.impl.video_filter(MEDIA, params).vf == []
    image = Image.new("RGBA", (10, 6), (120, 80, 200, 90))
    assert spec.impl.image(image, validate_params(spec, {"look": "toned", "strength": 0})).tobytes() == image.tobytes()


def test_an_image_keeps_its_alpha_and_matches_the_numpy_render_of_the_video_path():
    spec = effect_spec("look", runtime_with(REEL))
    image = Image.new("RGBA", (40, 24), (200, 150, 100, 77))
    params = {**validate_params(spec, {"look": "mine:reel", "strength": 1}), LOOKS_KEY: {"look": REEL}}

    out = spec.impl.image(image, params)

    rgb = np.asarray(out.convert("RGB"), dtype=np.int16)
    assert out.mode == "RGBA" and out.getchannel("A").getextrema() == (77, 77)
    assert rgb[12, 20].tolist() != [200, 150, 100]
    assert rgb[0, 0].sum() < rgb[12, 20].sum()


def test_without_a_user_only_shared_looks_resolve():
    spec = effect_spec("look", runtime_with(TONED, REEL))

    assert spec.impl.video_filter(MEDIA, validate_params(spec, {"look": "toned"})) is not None
    with pytest.raises(ToolFailed, match="not available"):
        spec.impl.image(Image.new("RGB", (4, 4)), validate_params(spec, {"look": "mine:reel"}))
