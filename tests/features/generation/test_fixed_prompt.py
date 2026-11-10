from types import SimpleNamespace
from unittest.mock import Mock

from src.features.generation.fixed_prompt import (
    FixedPromptRun,
    applied_parameters,
    apply_fixed_prompt,
    fixed_prompt_applies,
    with_applied_prompts,
)
from src.features.generation.prompt_expansion import PromptExpander
from src.features.presets.fixed_prompt import FixedPrompt
from src.pipelines.outputs import ParamGenerationOutput, RenderedPromptGenerationOutput, SeedGenerationOutput

FILM = FixedPrompt(prefix="35mm film still,", suffix="grainy, warm light", negative="cartoon")
TEMPLATE = SimpleNamespace(modes={"txt2img": object(), "edit": object(), "upscale": object()},
                           vars={"promptless_modes": ["upscale"]})
PAIRS = [{"positive": "a fox", "negative": ""}]


def _request(seed=10, variables=None):
    return SimpleNamespace(form_data={"seed": seed}, variables=variables)


def _run():
    return apply_fixed_prompt(
        [{"positive": "a red fox", "negative": "blurry"}, {"positive": "a grey wolf", "negative": "blurry"}],
        [("PREFIX one", "SUFFIX one", "NEG one"), ("PREFIX two", "SUFFIX two", "NEG two")],
    )


class TestJoining:
    def test_prefix_prompt_and_suffix_are_joined_by_one_space(self):
        run = apply_fixed_prompt([{"positive": "a fox", "negative": ""}], [("film still,", "warm light", "")])

        assert run.applied == [{"positive": "film still, a fox warm light", "negative": ""}]

    def test_the_fixed_negative_follows_the_users_negative(self):
        run = apply_fixed_prompt([{"positive": "a fox", "negative": "blurry"}], [("", "", "cartoon")])

        assert run.applied[0]["negative"] == "blurry, cartoon"

    def test_empty_parts_leave_no_stray_separators(self):
        run = apply_fixed_prompt([{"positive": "", "negative": ""}], [("film still", "  ", "cartoon")])

        assert run.applied == [{"positive": "film still", "negative": "cartoon"}]

    def test_the_visible_pairs_are_the_users_own(self):
        run = _run()

        assert run.visible == [
            {"positive": "a red fox", "negative": "blurry"},
            {"positive": "a grey wolf", "negative": "blurry"},
        ]


class TestExpansion:
    def test_each_image_gets_its_own_roll_of_the_fixed_text(self):
        expander = PromptExpander()
        fixed = FixedPrompt(prefix="{a|b|c|d|e|f|g|h}")

        parts = expander.expand_fixed("g", _request(seed=10), fixed, 6)

        assert len(parts) == 6
        assert all(prefix in "abcdefgh" for prefix, _, _ in parts)
        assert len({prefix for prefix, _, _ in parts}) > 1

    def test_image_i_uses_the_same_seed_as_a_single_image_run_at_base_plus_i(self):
        expander = PromptExpander()
        fixed = FixedPrompt(prefix="{a|b|c|d|e|f|g|h}", suffix="{1|2|3|4|5|6|7|8}", negative="{x|y|z|w}")

        batch = expander.expand_fixed("g", _request(seed=40), fixed, 4)
        singles = [expander.expand_fixed("g", _request(seed=40 + i), fixed, 1)[0] for i in range(4)]

        assert batch == singles

    def test_the_same_seed_reproduces_the_same_fixed_text(self):
        expander = PromptExpander()
        fixed = FixedPrompt(prefix="{a|b|c|d}", negative="{x|y|z}")

        assert expander.expand_fixed("g", _request(seed=7), fixed, 3) == expander.expand_fixed("h", _request(seed=7), fixed, 3)

    def test_request_variables_reach_the_fixed_text(self):
        expander = PromptExpander()
        fixed = FixedPrompt(prefix="a ${mood} scene", suffix="by ${artist}")

        parts = expander.expand_fixed("g", _request(variables={"mood": "moody", "artist": "nobody"}), fixed, 1)

        assert parts == [("a moody scene", "by nobody", "")]

    def test_the_fixed_parts_go_through_the_prompt_transform_hook(self):
        registry = Mock()

        def execute(name, context):
            context.data["positive"] = context.data["positive"].replace("FOX", "fox")
            return context, True

        registry.execute_hook.side_effect = execute
        expander = PromptExpander(plugin_registry=registry)

        parts = expander.expand_fixed("g", _request(), FixedPrompt(prefix="FOX"), 1)

        assert parts[0][0] == "fox"


class TestWhenItApplies:
    def test_it_applies_to_a_prompted_mode(self):
        assert fixed_prompt_applies(FILM, TEMPLATE, "txt2img", {}, PAIRS)

    def test_an_empty_fixed_prompt_is_skipped(self):
        assert not fixed_prompt_applies(FixedPrompt(prefix="  "), TEMPLATE, "txt2img", {}, PAIRS)
        assert not fixed_prompt_applies(None, TEMPLATE, "txt2img", {}, PAIRS)

    def test_a_mode_outside_the_allow_list_is_skipped(self):
        fixed = FixedPrompt(prefix="film", modes=["txt2img"])

        assert fixed_prompt_applies(fixed, TEMPLATE, "txt2img", {}, PAIRS)
        assert not fixed_prompt_applies(fixed, TEMPLATE, "edit", {}, PAIRS)

    def test_a_promptless_mode_is_skipped(self):
        assert not fixed_prompt_applies(FILM, TEMPLATE, "upscale", {}, PAIRS)

    def test_a_request_without_prompts_is_skipped(self):
        assert not fixed_prompt_applies(FILM, TEMPLATE, "txt2img", {}, [])
        assert not fixed_prompt_applies(FILM, TEMPLATE, "txt2img", {}, None)

    def test_a_video_director_document_is_skipped(self):
        assert not fixed_prompt_applies(FILM, TEMPLATE, "txt2img", {"video_director": {"segments": []}}, PAIRS)

    def test_a_music_director_document_is_skipped(self):
        assert not fixed_prompt_applies(FILM, TEMPLATE, "txt2img", {"music_director": {"sections": []}}, PAIRS)

    def test_a_template_without_vars_still_applies(self):
        assert fixed_prompt_applies(FILM, SimpleNamespace(vars=None), "txt2img", {}, PAIRS)


class TestMasking:
    def test_rendered_prompts_show_the_users_text(self):
        run = _run()
        output = RenderedPromptGenerationOutput(index=1, positive=run.applied[1]["positive"], negative=run.applied[1]["negative"])

        run.mask(output)

        assert (output.positive, output.negative) == ("a grey wolf", "blurry")

    def test_prompt_parameters_show_the_users_text_per_index(self):
        run = _run()
        positive = ParamGenerationOutput(name="positive_prompt", values=[pair["positive"] for pair in run.applied])
        negative = ParamGenerationOutput(name="negative_prompt", values=[pair["negative"] for pair in run.applied])

        run.mask(positive)
        run.mask(negative)

        assert positive.values == ["a red fox", "a grey wolf"]
        assert negative.values == ["blurry", "blurry"]

    def test_a_first_prompt_broadcast_to_every_image_stays_the_first_prompt(self):
        run = _run()
        output = ParamGenerationOutput(name="positive_prompt", values=[run.applied[0]["positive"]] * 2)

        run.mask(output)

        assert output.values == ["a red fox", "a red fox"]

    def test_a_rewritten_prompt_is_replaced_by_the_users_text_at_its_index(self):
        run = _run()
        output = ParamGenerationOutput(name="positive_prompt", values=["LLM rewrite of PREFIX one", "LLM rewrite of PREFIX two"])

        run.mask(output)

        assert output.values == ["a red fox", "a grey wolf"]

    def test_values_tiled_across_passes_are_masked_on_every_pass(self):
        run = _run()
        tiled = [pair["positive"] for pair in run.applied] * 2
        output = ParamGenerationOutput(name="positive_prompt", values=tiled)

        run.mask(output)

        assert output.values == ["a red fox", "a grey wolf", "a red fox", "a grey wolf"]

    def test_other_outputs_are_untouched(self):
        run = _run()
        steps = ParamGenerationOutput(name="steps", values=[30, 30])
        seed = SeedGenerationOutput(index=0, seed=5)

        run.mask(steps)
        run.mask(seed)

        assert steps.values == [30, 30]
        assert (seed.index, seed.seed) == (0, 5)

    def test_no_masked_output_contains_fixed_text(self):
        run = _run()
        outputs = [
            RenderedPromptGenerationOutput(index=i, positive=pair["positive"], negative=pair["negative"])
            for i, pair in enumerate(run.applied)
        ]
        outputs.append(ParamGenerationOutput(name="positive_prompt", values=[p["positive"] for p in run.applied]))
        outputs.append(ParamGenerationOutput(name="negative_prompt", values=[p["negative"] for p in run.applied]))

        for output in outputs:
            run.mask(output)

        text = repr(outputs)
        for secret in ("PREFIX", "SUFFIX", "NEG"):
            assert secret not in text


class TestAdminView:
    def test_history_parameters_get_the_applied_text_per_index(self):
        run = _run()
        result = {"id": "g", "parameters": {
            "positive_prompt": ["a red fox", "a grey wolf"],
            "negative_prompt": ["blurry", "blurry"],
            "steps": [30, 30],
        }}

        shown = with_applied_prompts(result, run.records())

        assert shown["parameters"]["positive_prompt"] == [p["positive"] for p in run.applied]
        assert shown["parameters"]["negative_prompt"] == [p["negative"] for p in run.applied]
        assert shown["parameters"]["steps"] == [30, 30]
        assert shown["fixed_prompt_applied"] is True
        assert result["parameters"]["positive_prompt"] == ["a red fox", "a grey wolf"]

    def test_parameters_for_one_index_get_that_images_applied_text(self):
        run = _run()
        result = {"generation_id": "g", "index": 1, "parameters": {"positive_prompt": "a grey wolf", "negative_prompt": "blurry"}}

        shown = with_applied_prompts(result, run.records(), 1)

        assert shown["parameters"] == {"positive_prompt": run.applied[1]["positive"], "negative_prompt": run.applied[1]["negative"]}
        assert shown["fixed_prompt_applied"] is True

    def test_without_a_record_the_result_is_unchanged(self):
        result = {"parameters": {"positive_prompt": ["a fox"]}}

        assert with_applied_prompts(result, None) is result
        assert with_applied_prompts(result, []) is result

    def test_the_admin_generation_gets_the_applied_lists(self):
        run = _run()

        assert applied_parameters(run.records()) == {
            "positive_prompt": [p["positive"] for p in run.applied],
            "negative_prompt": [p["negative"] for p in run.applied],
        }
        assert applied_parameters(None) is None

    def test_an_empty_run_masks_nothing(self):
        run = FixedPromptRun(visible=[], applied=[])
        output = ParamGenerationOutput(name="positive_prompt", values=["x"])

        run.mask(output)

        assert output.values == ["x"]
