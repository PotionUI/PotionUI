"""
Tests for PromptEncoderPipe's per-image prompt handling.

The preset renders `p_prompt.output` as the authored prompt plus a suffix (the
enabled embedding tokens). Per-image expansion has to swap the prompt body while
keeping that suffix, which is what `_substitute_pair` does.
"""

import pytest

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.prompt_encoder.main import PromptEncoderPipe
from src.platform.runtime.primitives.clip import ConditioningModel


sub = PromptEncoderPipe._substitute_pair


class TestSubstitutePair:
    def test_swaps_the_body_and_keeps_the_embedding_suffix(self):
        template = "a red dress\n\n positive_embedding_1"
        assert sub(template, "a red dress", "a blue dress") == (
            "a blue dress\n\n positive_embedding_1"
        )

    def test_image_zero_reconstructs_the_template_byte_for_byte(self):
        # For image 0 the body and the replacement are the same string, so the
        # rendered template must survive untouched.
        template = "a red dress\n\n positive_embedding_1"
        assert sub(template, "a red dress", "a red dress") == template

    def test_empty_body_treats_the_whole_template_as_suffix(self):
        # An empty negative prompt still carries negative embedding tokens.
        template = "\n\n negative_embedding_1"
        assert sub(template, "", "") == template
        assert sub(template, "", "blurry") == "blurry\n\n negative_embedding_1"

    def test_body_absent_from_template_falls_back_to_the_template(self):
        assert sub("something else", "a red dress", "a blue dress") == "something else"

    def test_only_the_first_occurrence_is_replaced(self):
        assert sub("cat cat", "cat", "dog") == "dog cat"

    def test_no_suffix_is_fine(self):
        assert sub("a red dress", "a red dress", "a blue dress") == "a blue dress"


class TestPairsWiring:
    def test_pairs_is_a_declared_config_key(self):
        keys = {spec.name for spec in PromptEncoderPipe.configuration()}
        assert "pairs" in keys

    def test_wildcards_config_is_gone(self):
        # Expansion moved to src/core/prompt/expander.py; the pipe no longer
        # owns an unseeded RandomPromptGenerator.
        keys = {spec.name for spec in PromptEncoderPipe.configuration()}
        assert "wildcards" not in keys

    def test_module_no_longer_imports_dynamicprompts(self):
        import src.pipelines.pipes.prompt_encoder.main as module
        assert not hasattr(module, "RandomPromptGenerator")
        assert not hasattr(module, "WildcardManager")


# --- optional image input (Qwen-Image-Edit) ---------------


class _FakeClip:
    """Records the requests handed to encode_prompts(); no real encoding."""

    _model_fingerprint = "fake-fp"

    def __init__(self):
        self.requests = None

    def encode_prompts(self, requests):
        self.requests = requests
        return [
            ConditioningModel(p_prompt=r["prompt"], n_prompt=r["negative_prompt"], embeds={}, n_embeds={})
            for r in requests
        ]


def _config(**over):
    cfg = PromptEncoderPipe.get_default_config()
    cfg.update({"p_prompt": {"input": "a cat", "output": "a cat"},
                "n_prompt": {"input": "", "output": ""}})
    cfg.update(over)
    return cfg


class TestImageInputDeclared:
    def test_image_is_a_declared_input(self):
        specs = {s.name: s for s in PromptEncoderPipe.inputs()}
        assert "image" in specs
        assert specs["image"].io_type == IOType.IMAGE
        assert specs["image"].required is False
        assert specs["image"].is_array is True


class TestImagesThreadedToClip:
    """Locks today's default (single-image-per-request, positional-with-
    clamp) forwarding -- `_FakeClip` declares no `forwards_full_image_batch`,
    so `getattr(clip, "forwards_full_image_batch", False)` must resolve to
    False and reproduce this exact shape, unchanged, for every encoder that
    doesn't opt in (Qwen-Image-Edit, Krea-2, every other existing family)."""

    def test_no_image_input_never_adds_images_key(self):
        clip = _FakeClip()
        pipe = PromptEncoderPipe(config=_config())
        pipe.process(PipeInput(input={"clip": clip}), lambda o: None)
        assert all("images" not in r for r in clip.requests)

    def test_image_input_added_to_every_request(self):
        clip = _FakeClip()
        pipe = PromptEncoderPipe(config=_config(quantity=2))
        pipe.process(PipeInput(input={"clip": clip, "image": ["IMG"]}), lambda o: None)
        assert all(r["images"] == ["IMG"] for r in clip.requests)

    def test_image_input_clamps_to_last_when_batch_outruns_the_list(self):
        clip = _FakeClip()
        pipe = PromptEncoderPipe(config=_config(quantity=3))
        pipe.process(PipeInput(input={"clip": clip, "image": ["A", "B"]}), lambda o: None)
        assert [r["images"] for r in clip.requests] == [["A"], ["B"], ["B"]]

    def test_default_encoder_never_declares_the_full_batch_marker(self):
        # A plain ClipTextEncoder subclass (every existing family) inherits
        # False from the ABC unless it explicitly opts in.
        assert getattr(_FakeClip(), "forwards_full_image_batch", False) is False


class _FakeClipFullImageBatch(_FakeClip):
    """An encoder that opts into the full, unindexed image list (H3 fl2va's
    shape: a fixed keyframe pair shared by every output of a generation)."""

    forwards_full_image_batch = True


class TestFullImageBatchForwarding:
    """The opt-in path: `forwards_full_image_batch = True` forwards the
    WHOLE `image` input list to every request, unindexed -- the shape H3's
    fl2va needs (first/last keyframes shared across every `quantity`
    variation), never selected/clamped by output index."""

    def test_every_request_gets_the_whole_image_list(self):
        clip = _FakeClipFullImageBatch()
        pipe = PromptEncoderPipe(config=_config(quantity=3))
        pipe.process(PipeInput(input={"clip": clip, "image": ["FIRST", "LAST"]}), lambda o: None)
        assert [r["images"] for r in clip.requests] == [["FIRST", "LAST"]] * 3

    def test_single_image_still_forwarded_as_a_one_item_list(self):
        clip = _FakeClipFullImageBatch()
        pipe = PromptEncoderPipe(config=_config())
        pipe.process(PipeInput(input={"clip": clip, "image": ["ONLY"]}), lambda o: None)
        assert clip.requests[0]["images"] == ["ONLY"]

    def test_no_image_input_still_never_adds_images_key(self):
        # The marker only changes HOW images are selected once there are
        # some; it must not fabricate an images key out of nothing.
        clip = _FakeClipFullImageBatch()
        pipe = PromptEncoderPipe(config=_config())
        pipe.process(PipeInput(input={"clip": clip}), lambda o: None)
        assert all("images" not in r for r in clip.requests)

    def test_forwarded_list_is_a_copy_not_the_same_object(self):
        # request["images"] must not alias pipe_input's own list -- a
        # downstream mutation (e.g. a per-request pop) must not corrupt the
        # NEXT request's images.
        clip = _FakeClipFullImageBatch()
        images_in = ["FIRST", "LAST"]
        pipe = PromptEncoderPipe(config=_config(quantity=2))
        pipe.process(PipeInput(input={"clip": clip, "image": images_in}), lambda o: None)
        for request in clip.requests:
            assert request["images"] is not images_in


class TestReferenceInputs:
    """`reference_image`/`reference_video`/`reference_audio` (ref2va): mutually
    exclusive with `image` (fl2va), forwarded whole (H3's ref2va references are
    a fixed set shared by every output, same shape as fl2va's keyframes), and
    collapsed into ONE packed `references` list -- whose presence is what tells
    the H3 adapter to route to `encode_reference_request` instead of
    `encode_request`, and whose ORDER is the packed order."""

    def test_every_reference_modality_is_a_declared_input(self):
        specs = {s.name: s for s in PromptEncoderPipe.inputs()}
        for name, io_type in (
            ("reference_image", IOType.IMAGE),
            ("reference_video", IOType.VIDEO),
            ("reference_audio", IOType.AUDIO),
        ):
            assert specs[name].io_type == io_type
            assert specs[name].required is False
            assert specs[name].is_array is True

    def test_reference_image_and_image_together_raise(self):
        clip = _FakeClip()
        pipe = PromptEncoderPipe(config=_config())
        with pytest.raises(ValueError, match="mutually exclusive"):
            pipe.process(
                PipeInput(input={"clip": clip, "image": ["KEYFRAME"], "reference_image": ["REF"]}),
                lambda o: None,
            )

    @pytest.mark.parametrize("modality", ["reference_video", "reference_audio"])
    def test_any_reference_modality_and_image_together_raise(self, modality):
        clip = _FakeClip()
        pipe = PromptEncoderPipe(config=_config())
        with pytest.raises(ValueError, match="mutually exclusive"):
            pipe.process(
                PipeInput(input={"clip": clip, "image": ["KEYFRAME"], modality: ["REF"]}),
                lambda o: None,
            )

    def test_reference_image_forwarded_as_typed_references(self):
        clip = _FakeClipFullImageBatch()
        pipe = PromptEncoderPipe(config=_config(quantity=2))
        pipe.process(
            PipeInput(input={"clip": clip, "reference_image": ["REF1", "REF2"]}), lambda o: None,
        )
        assert all(
            r["references"] == [{"kind": "image", "media": "REF1"}, {"kind": "image", "media": "REF2"}]
            for r in clip.requests
        )
        # The reference list is the whole of the ref2va request: `images` is
        # fl2va's input and must not be populated alongside it.
        assert all("images" not in r for r in clip.requests)

    def test_the_three_modalities_pack_images_then_videos_then_audio(self):
        clip = _FakeClipFullImageBatch()
        pipe = PromptEncoderPipe(config=_config())
        pipe.process(PipeInput(input={
            "clip": clip,
            "reference_audio": ["A1"], "reference_video": ["V1", "V2"], "reference_image": ["I1"],
        }), lambda o: None)
        # Declared audio-first above on purpose: the packed order is the
        # contract's, not the input dict's.
        assert [entry["kind"] for entry in clip.requests[0]["references"]] == [
            "image", "video", "video", "audio",
        ]
        assert [entry["media"] for entry in clip.requests[0]["references"]] == ["I1", "V1", "V2", "A1"]

    def test_reference_video_frames_is_forwarded_only_with_references(self):
        clip = _FakeClipFullImageBatch()
        pipe = PromptEncoderPipe(config=_config(reference_video_frames=124))
        pipe.process(PipeInput(input={"clip": clip, "reference_video": ["V1"]}), lambda o: None)
        assert clip.requests[0]["reference_video_frames"] == 124

        plain = _FakeClipFullImageBatch()
        PromptEncoderPipe(config=_config(reference_video_frames=124)).process(
            PipeInput(input={"plain": None, "clip": plain, "image": ["KEYFRAME"]}), lambda o: None,
        )
        assert all("reference_video_frames" not in r for r in plain.requests)

    def test_plain_image_input_never_carries_references(self):
        clip = _FakeClipFullImageBatch()
        pipe = PromptEncoderPipe(config=_config())
        pipe.process(PipeInput(input={"clip": clip, "image": ["KEYFRAME"]}), lambda o: None)
        assert all("references" not in r for r in clip.requests)

    def test_no_reference_input_never_adds_references(self):
        clip = _FakeClipFullImageBatch()
        pipe = PromptEncoderPipe(config=_config())
        pipe.process(PipeInput(input={"clip": clip}), lambda o: None)
        assert all("references" not in r for r in clip.requests)


class TestReferenceSelections:
    """`reference_selections` (per-shot ref2va subsets, index-aligned with
    `pairs`): a Director segment's own subset of the packed reference order,
    presented to the text encoder in the subset's OWN relative order so the
    adapter's `<Picture i>`/`<Video k>`/`<Audio j>` numbering re-labels from
    1 rather than preserving the packed set's global positions."""

    def test_reference_selections_is_a_declared_config_key(self):
        keys = {spec.name for spec in PromptEncoderPipe.configuration()}
        assert "reference_selections" in keys

    def test_no_selection_presents_every_reference_same_as_the_single_window_path(self):
        clip = _FakeClipFullImageBatch()
        pipe = PromptEncoderPipe(config=_config(quantity=2))
        pipe.process(
            PipeInput(input={"clip": clip, "reference_image": ["REF1", "REF2"]}), lambda o: None,
        )
        assert all(
            r["references"] == [{"kind": "image", "media": "REF1"}, {"kind": "image", "media": "REF2"}]
            for r in clip.requests
        )

    def test_a_per_output_selection_narrows_to_just_that_subset_in_order(self):
        clip = _FakeClipFullImageBatch()
        pipe = PromptEncoderPipe(config=_config(quantity=2, reference_selections=[[1], []]))
        pipe.process(
            PipeInput(input={"clip": clip, "reference_image": ["REF1", "REF2"]}), lambda o: None,
        )
        # Output 0: subset [1] -> only REF2, re-labeled to its own list.
        assert clip.requests[0]["references"] == [{"kind": "image", "media": "REF2"}]
        # Output 1: empty selection -> every packed reference, same as no selection at all.
        assert clip.requests[1]["references"] == [
            {"kind": "image", "media": "REF1"}, {"kind": "image", "media": "REF2"},
        ]

    def test_a_selection_reorders_the_subset_to_the_order_given(self):
        clip = _FakeClipFullImageBatch()
        pipe = PromptEncoderPipe(config=_config(reference_selections=[[1, 0]]))
        pipe.process(
            PipeInput(input={"clip": clip, "reference_image": ["REF1", "REF2"]}), lambda o: None,
        )
        assert clip.requests[0]["references"] == [
            {"kind": "image", "media": "REF2"}, {"kind": "image", "media": "REF1"},
        ]

    def test_an_out_of_range_selection_index_is_refused(self):
        clip = _FakeClipFullImageBatch()
        pipe = PromptEncoderPipe(config=_config(reference_selections=[[5]]))
        with pytest.raises(ValueError, match="reference_selections"):
            pipe.process(
                PipeInput(input={"clip": clip, "reference_image": ["REF1"]}), lambda o: None,
            )

    def test_bite_check_a_missing_selection_entry_would_also_pass_a_vacuous_subset_assertion(self):
        # BITE CHECK for the narrowing test above: quantity 2 with only ONE
        # selection entry supplied must fall back to "every reference" for
        # the output with no entry -- confirms that assertion is checking a
        # real narrowing on output 0, not "any list shorter than the full
        # set counts as narrowed".
        clip = _FakeClipFullImageBatch()
        pipe = PromptEncoderPipe(config=_config(quantity=2, reference_selections=[[1]]))
        pipe.process(
            PipeInput(input={"clip": clip, "reference_image": ["REF1", "REF2"]}), lambda o: None,
        )
        assert clip.requests[0]["references"] == [{"kind": "image", "media": "REF2"}]
        assert clip.requests[1]["references"] == [
            {"kind": "image", "media": "REF1"}, {"kind": "image", "media": "REF2"},
        ]


class _FakeModels:
    """Records every fingerprint handed to `acquire()`; always runs the loader."""

    def __init__(self):
        self.fingerprints = []

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.fingerprints.append(fingerprint)
        return loader()


class TestFingerprintTracksFinalPrompt:
    def test_output_only_change_still_changes_the_fingerprint(self):
        # `p_prompt.input` (authored text) stays put; only `p_prompt.output`
        # (the rendered template, e.g. LTX Upscale's `apply_quality_prompt`
        # suffix) changes. The cache must not reuse the other setting's
        # conditioning.
        models = _FakeModels()
        cfg = _config()
        cfg["p_prompt"] = {"input": "a cat", "output": "a cat"}
        PromptEncoderPipe(config=cfg).process(
            PipeInput(input={"clip": _FakeClip(), "MODELS": models}), lambda o: None
        )

        cfg2 = _config()
        cfg2["p_prompt"] = {"input": "a cat", "output": "a cat, best quality, sharp focus"}
        PromptEncoderPipe(config=cfg2).process(
            PipeInput(input={"clip": _FakeClip(), "MODELS": models}), lambda o: None
        )

        assert models.fingerprints[0] != models.fingerprints[1]

    def test_identical_requests_still_reuse_the_cache(self):
        models = _FakeModels()
        cfg = _config()
        PromptEncoderPipe(config=cfg).process(
            PipeInput(input={"clip": _FakeClip(), "MODELS": models}), lambda o: None
        )
        PromptEncoderPipe(config=_config()).process(
            PipeInput(input={"clip": _FakeClip(), "MODELS": models}), lambda o: None
        )
        assert models.fingerprints[0] == models.fingerprints[1]


class TestNewlineNormalization:
    def test_newline_becomes_a_space_not_nothing(self):
        clip = _FakeClip()
        cfg = _config()
        cfg["p_prompt"] = {"input": "red\ncar", "output": "red\ncar"}
        PromptEncoderPipe(config=cfg).process(PipeInput(input={"clip": clip}), lambda o: None)
        assert clip.requests[0]["prompt"] == "red car"

    def test_no_double_spaces_from_multiple_newlines(self):
        clip = _FakeClip()
        cfg = _config()
        cfg["p_prompt"] = {"input": "red\n\ncar", "output": "red\n\ncar"}
        PromptEncoderPipe(config=cfg).process(PipeInput(input={"clip": clip}), lambda o: None)
        assert clip.requests[0]["prompt"] == "red car"

    def test_no_double_commas_from_a_newline_next_to_a_comma(self):
        clip = _FakeClip()
        cfg = _config()
        cfg["p_prompt"] = {"input": "red,\ncar", "output": "red,\ncar"}
        PromptEncoderPipe(config=cfg).process(PipeInput(input={"clip": clip}), lambda o: None)
        assert clip.requests[0]["prompt"] == "red, car"


class TestNagForcesNegativeEncode:
    """NAG (Normalized Attention Guidance) enforces the negative prompt at
    guidance_scale=1.0 (true CFG off) by injecting it into cross-attention --
    but that only works if the negative prompt was actually ENCODED. Without
    this, `nag_scale > 1.0` at `guidance_scale <= 1.0` silently produces
    `n_embeds == {}`, which every NAG-consuming generator's
    `uncond = ... if cond_model.n_embeds else None` treats as "no negative",
    defeating NAG entirely in the one regime it exists for."""

    def test_nag_scale_is_a_declared_config_key(self):
        keys = {spec.name for spec in PromptEncoderPipe.configuration()}
        assert "nag_scale" in keys

    def test_cfg_off_and_nag_off_does_not_request_the_negative_pass(self):
        clip = _FakeClip()
        cfg = _config(guidance_scale=1.0, nag_scale=1.0)
        PromptEncoderPipe(config=cfg).process(PipeInput(input={"clip": clip}), lambda o: None)
        assert clip.requests[0]["do_classifier_free_guidance"] is False

    def test_cfg_off_but_nag_on_still_requests_the_negative_pass(self):
        clip = _FakeClip()
        cfg = _config(guidance_scale=1.0, nag_scale=1.5)
        PromptEncoderPipe(config=cfg).process(PipeInput(input={"clip": clip}), lambda o: None)
        assert clip.requests[0]["do_classifier_free_guidance"] is True

    def test_cfg_on_regardless_of_nag_still_requests_the_negative_pass(self):
        clip = _FakeClip()
        cfg = _config(guidance_scale=5.0, nag_scale=1.0)
        PromptEncoderPipe(config=cfg).process(PipeInput(input={"clip": clip}), lambda o: None)
        assert clip.requests[0]["do_classifier_free_guidance"] is True

    def test_nag_scale_change_busts_the_fingerprint_cache(self):
        # Two requests that differ ONLY in nag_scale must not alias to the same
        # cached conditioning: one needs n_embeds populated, the other doesn't.
        models = _FakeModels()
        PromptEncoderPipe(config=_config(guidance_scale=1.0, nag_scale=1.0)).process(
            PipeInput(input={"clip": _FakeClip(), "MODELS": models}), lambda o: None
        )
        PromptEncoderPipe(config=_config(guidance_scale=1.0, nag_scale=1.5)).process(
            PipeInput(input={"clip": _FakeClip(), "MODELS": models}), lambda o: None
        )
        assert models.fingerprints[0] != models.fingerprints[1]


class TestNegativeAppliedMarker:
    """A negative prompt that the model never sees (do_cfg False) is
    recorded as inert rather than pretending it applied. The marker is emitted
    from `process()` (config-only, cache-independent) and stamped onto the
    per-image "Negative Prompt" diff artifact."""

    @staticmethod
    def _collect(cfg):
        from src.pipelines.outputs import ParamGenerationOutput, DiffTextGenerationOutput
        outputs = []
        PromptEncoderPipe(config=cfg).process(
            PipeInput(input={"clip": _FakeClip()}), outputs.append
        )
        params = {o.name: o.values for o in outputs if isinstance(o, ParamGenerationOutput)}
        diffs = {o.name: o for o in outputs if isinstance(o, DiffTextGenerationOutput)}
        return params, diffs

    def test_cfg_off_and_nag_off_marks_negative_inert(self):
        params, diffs = self._collect(_config(guidance_scale=1.0, nag_scale=1.0))
        assert params["negative_applied"] == [False]
        assert diffs["Negative Prompt"].negative_applied is False
        assert diffs["Positive Prompt"].negative_applied is True

    def test_cfg_on_marks_negative_applied(self):
        params, diffs = self._collect(_config(guidance_scale=5.0, nag_scale=1.0))
        assert params["negative_applied"] == [True]
        assert diffs["Negative Prompt"].negative_applied is True

    def test_nag_on_at_cfg_off_marks_negative_applied(self):
        params, diffs = self._collect(_config(guidance_scale=1.0, nag_scale=1.5))
        assert params["negative_applied"] == [True]
        assert diffs["Negative Prompt"].negative_applied is True

    def test_marker_is_emitted_per_image(self):
        params, _ = self._collect(_config(guidance_scale=1.0, nag_scale=1.0, quantity=3))
        assert params["negative_applied"] == [False, False, False]

    def test_marker_emitted_even_when_conditioning_cache_hits(self):
        # The record must be honest for every generation, not only cache misses:
        # `_encode` (and its diff outputs) is skipped on a cache hit, so the
        # negative_applied param has to come from process(), not the encode loop.
        from src.pipelines.outputs import ParamGenerationOutput

        class _CachedModels:
            def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
                return [ConditioningModel(p_prompt="a cat", n_prompt="", embeds={}, n_embeds={})]

        outputs = []
        PromptEncoderPipe(config=_config(guidance_scale=1.0, nag_scale=1.0)).process(
            PipeInput(input={"clip": _FakeClip(), "MODELS": _CachedModels()}), outputs.append
        )
        params = {o.name: o.values for o in outputs if isinstance(o, ParamGenerationOutput)}
        assert params["negative_applied"] == [False]


class TestImageFingerprint:
    def test_different_images_get_different_models_fingerprints(self):
        class _FakeModels:
            def __init__(self):
                self.fingerprints = []

            def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
                self.fingerprints.append(fingerprint)
                return loader()

        models = _FakeModels()
        pipe = PromptEncoderPipe(config=_config())
        pipe.process(PipeInput(input={"clip": _FakeClip(), "MODELS": models, "image": [b"\x00\x01"]}), lambda o: None)
        pipe.process(PipeInput(input={"clip": _FakeClip(), "MODELS": models, "image": [b"\xff\xfe"]}), lambda o: None)
        assert models.fingerprints[0] != models.fingerprints[1]

    def test_no_image_and_absent_image_key_fingerprint_the_same(self):
        class _FakeModels:
            def __init__(self):
                self.fingerprints = []

            def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
                self.fingerprints.append(fingerprint)
                return loader()

        models = _FakeModels()
        pipe = PromptEncoderPipe(config=_config())
        pipe.process(PipeInput(input={"clip": _FakeClip(), "MODELS": models}), lambda o: None)
        pipe.process(PipeInput(input={"clip": _FakeClip(), "MODELS": models, "image": []}), lambda o: None)
        assert models.fingerprints[0] == models.fingerprints[1]


class TestImagePixelBudget:
    """The pixel-area cap on images sent to a vision-grounded text encoder,
    expressed as a multiple of the output canvas area. The encoder's own
    checkpoint bound can sit an order of magnitude above the canvas being
    generated, and the vision forward is priced by area."""

    def test_both_keys_are_declared_config(self):
        keys = {spec.name for spec in PromptEncoderPipe.configuration()}
        assert {"image_pixel_budget", "output_resolution"} <= keys

    def test_budget_times_canvas_area_reaches_every_request(self):
        clip = _FakeClipFullImageBatch()
        cfg = _config(quantity=2, image_pixel_budget=2, output_resolution="1344x768")
        PromptEncoderPipe(config=cfg).process(
            PipeInput(input={"clip": clip, "image": ["FIRST"]}), lambda o: None
        )
        assert all(r["image_max_pixels"] == 2 * 1344 * 768 for r in clip.requests)

    def test_string_valued_budget_from_a_select_field_is_coerced(self):
        # The form knob is a select, so the rendered pipeline hands this pipe
        # a string rather than a number.
        clip = _FakeClipFullImageBatch()
        cfg = _config(image_pixel_budget="1", output_resolution="640x384")
        PromptEncoderPipe(config=cfg).process(
            PipeInput(input={"clip": clip, "image": ["FIRST"]}), lambda o: None
        )
        assert clip.requests[0]["image_max_pixels"] == 640 * 384

    def test_absent_budget_never_adds_the_key(self):
        clip = _FakeClipFullImageBatch()
        PromptEncoderPipe(config=_config(output_resolution="1344x768")).process(
            PipeInput(input={"clip": clip, "image": ["FIRST"]}), lambda o: None
        )
        assert all("image_max_pixels" not in r for r in clip.requests)

    def test_budget_without_a_resolution_never_adds_the_key(self):
        clip = _FakeClipFullImageBatch()
        PromptEncoderPipe(config=_config(image_pixel_budget=2)).process(
            PipeInput(input={"clip": clip, "image": ["FIRST"]}), lambda o: None
        )
        assert all("image_max_pixels" not in r for r in clip.requests)

    def test_unparseable_resolution_never_adds_the_key(self):
        clip = _FakeClipFullImageBatch()
        cfg = _config(image_pixel_budget=2, output_resolution="auto")
        PromptEncoderPipe(config=cfg).process(
            PipeInput(input={"clip": clip, "image": ["FIRST"]}), lambda o: None
        )
        assert all("image_max_pixels" not in r for r in clip.requests)

    def test_budget_is_inert_without_an_image(self):
        clip = _FakeClipFullImageBatch()
        cfg = _config(image_pixel_budget=2, output_resolution="1344x768")
        PromptEncoderPipe(config=cfg).process(PipeInput(input={"clip": clip}), lambda o: None)
        assert all("image_max_pixels" not in r for r in clip.requests)

    def test_a_different_budget_busts_the_fingerprint(self):
        # Same prompt, same image, different budget -> a genuinely different
        # vision grid; a stale memo would serve the other budget's embeds.
        models = _FakeModels()
        for budget in (1, 4):
            PromptEncoderPipe(
                config=_config(image_pixel_budget=budget, output_resolution="1344x768")
            ).process(
                PipeInput(input={"clip": _FakeClip(), "MODELS": models, "image": [b"\x00\x01"]}),
                lambda o: None,
            )
        assert models.fingerprints[0] != models.fingerprints[1]

    def test_a_different_canvas_busts_the_fingerprint_at_the_same_budget(self):
        models = _FakeModels()
        for resolution in ("1344x768", "640x384"):
            PromptEncoderPipe(
                config=_config(image_pixel_budget=2, output_resolution=resolution)
            ).process(
                PipeInput(input={"clip": _FakeClip(), "MODELS": models, "image": [b"\x00\x01"]}),
                lambda o: None,
            )
        assert models.fingerprints[0] != models.fingerprints[1]

    def test_the_same_budget_still_reuses_the_cache(self):
        models = _FakeModels()
        for _ in range(2):
            PromptEncoderPipe(
                config=_config(image_pixel_budget=2, output_resolution="1344x768")
            ).process(
                PipeInput(input={"clip": _FakeClip(), "MODELS": models, "image": [b"\x00\x01"]}),
                lambda o: None,
            )
        assert models.fingerprints[0] == models.fingerprints[1]
