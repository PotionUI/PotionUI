"""Tests for seeded, per-image prompt expansion."""

import pytest

from src.platform.plugins.hooks import HookContext
from src.features.prompt.expander import ExpandedPrompt, expand_prompts
from src.features.prompt.hooks import PROMPT_HOOKS


class FakePluginRegistry:
    """Minimal stand-in for PluginRegistry.execute_hook."""

    def __init__(self, handler=None, raises=False):
        self.handler = handler
        self.raises = raises
        self.calls = []

    def execute_hook(self, hook_name, context: HookContext):
        self.calls.append(dict(context.data))
        if self.raises:
            raise RuntimeError("plugin exploded")
        if self.handler:
            context = self.handler(context)
        return context, True


def positives(expanded):
    return [e.positive for e in expanded]


class TestDeterminism:
    def test_same_seed_reproduces_the_same_batch(self):
        a = expand_prompts("a {red|blue|green} dress", "", count=4, base_seed=1234)
        b = expand_prompts("a {red|blue|green} dress", "", count=4, base_seed=1234)
        assert positives(a) == positives(b)

    def test_different_seed_gives_a_different_batch(self):
        a = expand_prompts("a {red|blue|green} dress", "", count=6, base_seed=1)
        b = expand_prompts("a {red|blue|green} dress", "", count=6, base_seed=999)
        assert positives(a) != positives(b)

    def test_images_within_a_batch_vary(self):
        # Enough options and images that identical output would be a real signal,
        # not chance. Seeds are base+i, so each image samples independently.
        expanded = expand_prompts(
            "{a|b|c|d|e|f|g|h}", "", count=8, base_seed=7
        )
        assert len(set(positives(expanded))) > 1

    def test_seed_of_image_i_is_base_plus_i(self):
        expanded = expand_prompts("x", "", count=3, base_seed=500)
        assert [e.seed for e in expanded] == [500, 501, 502]

    def test_image_i_matches_a_single_expansion_at_that_seed(self):
        """Per-image expansion must be a pure function of base_seed + i."""
        batch = expand_prompts("{a|b|c|d}", "", count=4, base_seed=42)
        for i, item in enumerate(batch):
            single = expand_prompts("{a|b|c|d}", "", count=1, base_seed=42 + i)
            assert single[0].positive == item.positive


class TestVariables:
    def test_variable_is_substituted(self):
        expanded = expand_prompts(
            "a ${mood} shot", "", count=1, base_seed=1, variables={"mood": "cinematic"}
        )
        assert expanded[0].positive == "a cinematic shot"

    def test_variable_value_may_itself_be_a_template(self):
        expanded = expand_prompts(
            "a ${m} shot", "", count=1, base_seed=1, variables={"m": "{x|y}"}
        )
        assert expanded[0].positive in ("a x shot", "a y shot")

    def test_undefined_variable_expands_to_nothing_rather_than_raising(self):
        expanded = expand_prompts("a ${nope} shot", "", count=1, base_seed=1)
        assert "${nope}" not in expanded[0].positive
        assert "shot" in expanded[0].positive

    def test_unparseable_variable_is_skipped_not_fatal(self):
        expanded = expand_prompts(
            "hello", "", count=1, base_seed=1, variables={"bad": "{unclosed"}
        )
        assert expanded[0].positive == "hello"

    def test_variables_apply_to_the_negative_channel_too(self):
        expanded = expand_prompts(
            "x", "${junk}", count=1, base_seed=1, variables={"junk": "blurry"}
        )
        assert expanded[0].negative == "blurry"


class TestVariantSyntax:
    def test_weighted_variant_samples_only_declared_options(self):
        expanded = expand_prompts("{0.5::a|0.3::b|c}", "", count=10, base_seed=3)
        assert set(positives(expanded)) <= {"a", "b", "c"}

    def test_count_and_separator_variant(self):
        expanded = expand_prompts("{2$$ and $$a|b|c}", "", count=5, base_seed=3)
        for text in positives(expanded):
            assert " and " in text
            left, right = text.split(" and ")
            # Sampling is without replacement.
            assert left != right
            assert {left, right} <= {"a", "b", "c"}

    def test_wildcards_are_not_supported_and_survive_as_literal_text(self):
        # `#phrasebook` supersedes `__wildcard__`; a bare WildcardManager
        # resolves nothing, so the token must pass through, not crash.
        expanded = expand_prompts("__season__ vibes", "", count=1, base_seed=1)
        assert "__season__" in expanded[0].positive

    def test_malformed_template_falls_back_to_literal_text(self):
        expanded = expand_prompts("a {unclosed dress", "", count=1, base_seed=1)
        assert expanded[0].positive == "a {unclosed dress"


class TestEdgeCases:
    def test_empty_negative_prompt(self):
        expanded = expand_prompts("cat", "", count=2, base_seed=1)
        assert [e.negative for e in expanded] == ["", ""]

    def test_both_prompts_empty(self):
        expanded = expand_prompts("", "", count=1, base_seed=1)
        assert expanded[0] == ExpandedPrompt(positive="", negative="", seed=1)

    def test_promptless_batch_yields_count_empty_pairs(self):
        # A promptless mode (upscale, slow-motion) submits no prompt: the
        # orchestrator still expands per image, so every image must get a
        # well-formed empty pair rather than the batch collapsing.
        expanded = expand_prompts("", "", count=3, base_seed=7)
        assert expanded == [
            ExpandedPrompt(positive="", negative="", seed=7),
            ExpandedPrompt(positive="", negative="", seed=8),
            ExpandedPrompt(positive="", negative="", seed=9),
        ]

    def test_count_is_clamped_to_at_least_one(self):
        assert len(expand_prompts("x", "", count=0, base_seed=1)) == 1

    def test_literal_prompt_is_unchanged(self):
        expanded = expand_prompts("a photo of a cat", "blurry", count=2, base_seed=1)
        assert positives(expanded) == ["a photo of a cat"] * 2
        assert [e.negative for e in expanded] == ["blurry"] * 2


class TestTransformHook:
    def test_pre_phase_rewrite_changes_what_gets_expanded(self):
        def handler(ctx):
            if ctx.data["phase"] == "pre":
                ctx.data["positive"] = "{only}"
            return ctx

        registry = FakePluginRegistry(handler)
        expanded = expand_prompts(
            "{a|b}", "", count=1, base_seed=1, plugin_registry=registry
        )
        assert expanded[0].positive == "only"

    def test_post_phase_rewrite_changes_the_final_text(self):
        def handler(ctx):
            if ctx.data["phase"] == "post":
                ctx.data["positive"] = ctx.data["positive"] + ", masterpiece"
            return ctx

        registry = FakePluginRegistry(handler)
        expanded = expand_prompts(
            "cat", "", count=1, base_seed=1, plugin_registry=registry
        )
        assert expanded[0].positive == "cat, masterpiece"

    def test_hook_fires_twice_per_image_with_correct_payload(self):
        registry = FakePluginRegistry()
        expand_prompts(
            "cat", "blurry", count=2, base_seed=10,
            plugin_registry=registry, generation_id="gen-1",
        )

        assert len(registry.calls) == 4  # 2 images x (pre, post)
        assert [c["phase"] for c in registry.calls] == ["pre", "post", "pre", "post"]
        assert [c["image_index"] for c in registry.calls] == [0, 0, 1, 1]
        assert [c["seed"] for c in registry.calls] == [10, 10, 11, 11]
        assert all(c["generation_id"] == "gen-1" for c in registry.calls)

    def test_post_phase_sees_the_expanded_text_not_the_template(self):
        registry = FakePluginRegistry()
        expand_prompts("{a|a}", "", count=1, base_seed=1, plugin_registry=registry)
        pre, post = registry.calls
        assert pre["positive"] == "{a|a}"
        assert post["positive"] == "a"

    def test_a_throwing_hook_does_not_kill_the_generation(self):
        registry = FakePluginRegistry(raises=True)
        expanded = expand_prompts(
            "cat", "", count=1, base_seed=1, plugin_registry=registry
        )
        assert expanded[0].positive == "cat"

    def test_no_registry_is_a_no_op(self):
        expanded = expand_prompts("cat", "", count=1, base_seed=1, plugin_registry=None)
        assert expanded[0].positive == "cat"

    def test_hook_name_is_the_declared_one(self):
        registry = FakePluginRegistry()
        expand_prompts("cat", "", count=1, base_seed=1, plugin_registry=registry)
        assert PROMPT_HOOKS.transform == "prompt.transform"
