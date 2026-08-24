"""
Seeded, per-image prompt expansion.

The prompt that reaches this module is a *template*: the chip editor serializes
its variable chips to `${name}` and its choice chips to `{a|b}` (dynamicprompts
syntax), so pasted A1111/Civitai prompts round-trip through the same grammar.
Here we sample that template once per image, seeded off the generation seed, so
a batch varies but re-running the same seed reproduces the batch exactly.

Wildcards (`__name__`) are deliberately unsupported: the `#phrasebook` chip
system supersedes them. A bare `WildcardManager()` resolves nothing, so a stray
`__foo__` survives as literal text (dynamicprompts logs a warning) rather than
blowing up.
"""

import dataclasses
import logging
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from dynamicprompts.enums import SamplingMethod
from dynamicprompts.parser.config import default_parser_config
from dynamicprompts.parser.parse import parse
from dynamicprompts.sampling_context import SamplingContext
from dynamicprompts.wildcards import WildcardManager

from src.platform.plugins.hooks import HookContext
from src.features.prompt.hooks import PROMPT_HOOKS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExpandedPrompt:
    """One image's fully-expanded prompt pair."""
    positive: str
    negative: str
    seed: int


def _base_context(variables: Optional[Dict[str, str]] = None) -> SamplingContext:
    """
    One context per generation, carrying the variable bindings.

    `unknown_variable_value=""` matters: without it an undefined `${var}` raises
    rather than expanding to nothing, which would fail the whole generation on a
    typo in the prompt.
    """
    ctx = SamplingContext(
        default_sampling_method=SamplingMethod.RANDOM,
        wildcard_manager=WildcardManager(),
        parser_config=default_parser_config,
        unknown_variable_value="",
    )
    if variables:
        # A variable's value is itself a template, so `{a|b}` as a variable value
        # samples per use. parse() turns it into the Command that expects.
        bindings = {}
        for name, value in variables.items():
            try:
                bindings[name] = parse(str(value), parser_config=default_parser_config)
            except Exception as e:
                logger.warning(f"Ignoring unparseable prompt variable '{name}': {e}")
        if bindings:
            ctx = ctx.with_variables(bindings)
    return ctx


def _sample_one(ctx: SamplingContext, template: str) -> str:
    """Sample a single realization. Empty templates yield [] from dynamicprompts."""
    if not template or not template.strip():
        return ""
    try:
        result = next(iter(ctx.sample_prompts(template, 1)), None)
    except Exception as e:
        # A malformed template (unbalanced brace, bad weight) must not kill the
        # generation - fall back to the literal text the user typed.
        logger.warning(f"Prompt expansion failed, using template verbatim: {e}")
        return template
    return result.text if result is not None else ""


def _run_transform(
    plugin_registry: Any,
    generation_id: Optional[str],
    image_index: int,
    seed: int,
    phase: str,
    positive: str,
    negative: str,
) -> Tuple[str, str]:
    """Fire `prompt.transform`, letting plugins rewrite either channel."""
    if not plugin_registry:
        return positive, negative

    context = HookContext(
        hook_name=PROMPT_HOOKS.transform,
        plugin_id="system",
        data={
            "generation_id": generation_id,
            "image_index": image_index,
            "phase": phase,
            "seed": seed,
            "positive": positive,
            "negative": negative,
        },
    )
    try:
        context, success = plugin_registry.execute_hook(PROMPT_HOOKS.transform, context)
        if not success:
            logger.warning(f"Some plugins failed during {PROMPT_HOOKS.transform} ({phase})")
    except Exception as e:
        logger.warning(f"Failed to execute {PROMPT_HOOKS.transform} ({phase}): {e}")
        return positive, negative

    return (
        context.data.get("positive", positive),
        context.data.get("negative", negative),
    )


def expand_prompts(
    positive: str,
    negative: str,
    *,
    count: int,
    base_seed: int,
    variables: Optional[Dict[str, str]] = None,
    plugin_registry: Any = None,
    generation_id: Optional[str] = None,
) -> List[ExpandedPrompt]:
    """
    Expand an authored prompt template into one realization per image.

    Image `i` is sampled with `base_seed + i`, mirroring how `seed_generator`
    derives its per-image seeds, so a generation's prompts and its latents move
    together and both reproduce from the same seed.
    """
    count = max(1, int(count))
    ctx = _base_context(variables)

    expanded: List[ExpandedPrompt] = []
    for i in range(count):
        seed = base_seed + i

        p_template, n_template = _run_transform(
            plugin_registry, generation_id, i, seed, "pre", positive, negative
        )

        # SamplingContext is a frozen dataclass, so per-image seeding is a replace.
        seeded = dataclasses.replace(ctx, rand=random.Random(seed))
        p_text = _sample_one(seeded, p_template).strip()
        n_text = _sample_one(seeded, n_template).strip()

        p_text, n_text = _run_transform(
            plugin_registry, generation_id, i, seed, "post", p_text, n_text
        )

        expanded.append(ExpandedPrompt(positive=p_text, negative=n_text, seed=seed))

    return expanded
