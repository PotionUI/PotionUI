from __future__ import annotations

from pathlib import Path

import pytest

from src.platform.media_tools import FilterFragment, InvalidToolParams, MediaToolRegistry, ToolMedia, validate_params
from src.platform.media_tools.fastpath import build_timed_args, merge_fragments
from src.platform.media_tools.ops import paint_layer, replace_audio
from src.platform.media_tools.params import bind_media, media_params

VIDEO = ToolMedia(path=Path("clip.mp4"), kind="video", width=32, height=32, duration=2.0, has_audio=True)


class TestMediaParam:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("abc123", {"item_id": "abc123"}),
            ({"item_id": " abc-1 "}, {"item_id": "abc-1"}),
            ({"generation_file_id": "01HXYZ"}, {"generation_file_id": "01HXYZ"}),
        ],
    )
    def test_a_reference_is_normalised(self, value, expected):
        assert validate_params(replace_audio.spec(), {"audio": value})["audio"] == expected

    @pytest.mark.parametrize(
        "value",
        [
            {},
            {"item_id": "a", "generation_file_id": "b"},
            {"item_id": "../etc/passwd"},
            {"path": "/srv/file.wav"},
            {"item_id": "x" * 65},
            12,
            ["a"],
        ],
    )
    def test_anything_but_one_id_is_refused(self, value):
        with pytest.raises(InvalidToolParams) as caught:
            validate_params(replace_audio.spec(), {"audio": value})
        assert caught.value.field == "audio"

    def test_binding_swaps_references_for_staged_media_and_leaves_the_rest(self):
        spec = replace_audio.spec()
        params = validate_params(spec, {"audio": "abc", "start": 1})
        seen = []

        def lookup(param, ref):
            seen.append((param["name"], ref))
            return VIDEO

        bound = bind_media(spec, params, lookup)
        assert bound["audio"] is VIDEO and bound["start"] == 1
        assert params["audio"] == {"item_id": "abc"}
        assert seen == [("audio", {"item_id": "abc"})]
        assert [param["name"] for param in media_params(paint_layer.spec())] == ["layer"]

    def test_the_registry_accepts_a_media_param(self):
        registry = MediaToolRegistry()
        registry.register(replace_audio.spec())
        assert registry.get("replace_audio") is not None


class TestCopiedVideo:
    def test_sound_only_fragments_copy_the_video(self):
        plan = merge_fragments(VIDEO, [FilterFragment(af=["volume=0.5"], copy_video=True)])
        args = build_timed_args(VIDEO, plan, Path("out.mp4"))
        assert args[args.index("-c:v") + 1] == "copy"
        assert "-vf" not in args

    def test_a_picture_edit_in_the_same_pass_encodes_the_video(self):
        plan = merge_fragments(
            VIDEO, [FilterFragment(vf=["hflip"]), FilterFragment(drop_audio=True, copy_video=True)]
        )
        args = build_timed_args(VIDEO, plan, Path("out.mp4"))
        assert args[args.index("-c:v") + 1] == "libx264"

    def test_a_trim_in_the_same_pass_encodes_the_video(self):
        plan = merge_fragments(
            VIDEO, [FilterFragment(start=0.5), FilterFragment(drop_audio=True, copy_video=True)]
        )
        assert not plan.copy_video

    def test_a_different_container_encodes_the_video(self):
        source = VIDEO.with_changes(path=Path("clip.avi"))
        plan = merge_fragments(source, [FilterFragment(drop_audio=True, copy_video=True)])
        args = build_timed_args(source, plan, Path("out.mp4"))
        assert args[args.index("-c:v") + 1] == "libx264"
