# Prompt Expansion

A prompt that reaches the backend is a **template**, not final text. Before the pipeline is built,
the orchestrator expands it into one realization **per image**, seeded off the generation seed.

This is the authoritative reference. Where a detail depends on a source file, that file is named.

## The grammar

The prompt editor never asks a user to type this syntax — its variable and choice chips *serialize*
to it. The upshot is that a prompt copied from Civitai or A1111 parses straight into chips, and a
prompt authored in PotionUI pastes back out into those tools.

| Form | Meaning |
|---|---|
| `{a\|b\|c}` | pick one at random |
| `{0.5::a\|0.3::b\|c}` | weighted pick; omitted weights default to 1 |
| `{2$$a\|b\|c}` | pick two, without replacement |
| `{2$$ and $$a\|b\|c}` | pick two, joined by `" and "` |
| `{1-2$$ and $$a\|b\|c}` | pick between one and two |
| `${name}` | substitute a prompt variable |

Expansion is done by the [`dynamicprompts`](https://github.com/adieyal/dynamicprompts) library
(`SamplingContext`, `SamplingMethod.RANDOM`).

### Deliberately unsupported

- **`__wildcard__`** — the `#phrasebook` chip system supersedes it, and is better: DB-backed,
  searchable, with per-generation shuffle. A bare `WildcardManager()` resolves nothing, so a stray
  `__foo__` survives as literal text (with a warning) rather than failing the generation.
- **`#` comments** — `#` is the phrasebook trigger in the editor. The collision is not worth it.
- **`%{wrap}`**, parameterized wildcards, and the `~` / `@` sampler sigils.

An undefined `${var}` expands to the empty string rather than raising, so a typo degrades the prompt
instead of failing the generation.

## Variables

`GenerationRequest.variables` is a `name -> value` map (`src/features/generation/dto.py`). A value is
itself a template, so `{"mood": "{noir|sunlit}"}` re-samples per use. Variables are scoped to the
generate tab; there is no variables table.

## Seeding

`src/features/generation/orchestrator.py::_expand_prompts_per_image` runs just before
`build_pipeline`. It:

1. reads `quantity` and `seed` from `form_data` (both are flat keys — `get_form('custom', ['seed'])`
   resolves to `form_data['seed']`, because `PresetProcessor` assigns the same flat dict to every
   form name);
2. resolves `seed == -1` **eagerly** via `generate_seed()` and **writes it back into `form_data`**;
3. expands image `i` with `base_seed + i`, mirroring how `seed_generator` derives its own per-image
   seeds.

Step 2 is the load-bearing one. Without it, `seed_generator` would draw an independent random seed
per image at pipe time, and the prompt expansion could never be reproduced from the recorded seed.
With it, prompts and latents move together: re-running a generation at the same seed reproduces the
same batch of prompts *and* the same images.

Only `prompts[0]` is treated as the template. Multi-prompt tabs are a separate concept; expanding
each would multiply the image count.

Expansion never fails a generation — a malformed template (unbalanced brace, bad weight) falls back
to the literal text the user typed.

## How the expanded prompts reach the pipes

`PresetProcessor` (`src/features/presets/processor.py`) exposes, under `input.generation.prompts`:

| Key | Value |
|---|---|
| `p_prompt` / `n_prompt` | `pairs[0]` — the scalar every preset already used |
| `pairs` | `[{positive, negative}, ...]`, one entry per image |
| `positives` / `negatives` | the same, split per channel |

Because `p_prompt` still means `pairs[0]`, **every existing preset keeps rendering unchanged**. The
per-image data rides alongside in `pairs`.

A preset opts into per-image prompts with `@object:` (which returns the real list, where a `{{ }}`
template would stringify it):

```yaml
- name: "prompt_encoder"
  configuration:
    pairs: "@object:input.generation.prompts.pairs"

- name: "param_emitter"
  configuration:
    parameters:
      - ["positive_prompt", "@object:input.generation.prompts.positives"]
      - ["negative_prompt", "@object:input.generation.prompts.negatives"]
```

`param_emitter` stores an array of length `quantity` per-index, so `GET /{id}/params/{index}` — the
call the history modal already makes — returns the prompt **that image actually ran with**.
`generation_segments.text` keeps the authored recipe; the two answer different questions.

A preset that saves the same batch more than once (Krea-2 txt2img saves the base batch and the
inline-enhance batch) sets `passes:` to the number of gallery saves. `param_emitter` then writes
`quantity × passes` rows and tiles the per-index arrays across the passes, so every saved index —
not just the first batch — resolves a full parameter set.

`prompt_encoder` swaps each image's prompt into the preset's wrapper template (the authored prompt
plus enabled embedding tokens) via `_substitute_pair`, so the suffix survives. For image 0 the
substitution is a no-op by construction.

An upstream `prompt_expander` (the LLM one) wins: when it supplies `p_prompt` on the pipe input,
`prompt_encoder` ignores `pairs`.

## Known limitation: ComfyUI is per-batch, not per-image

A ComfyUI preset submits **one** workflow with `batch_size = quantity`, a single prompt text node,
and `seeds[0]` (`content/plugins/marketplace/comfyui-backend/backend/pipes/comfyui/main.py`). Per-image
prompts are not expressible there.

So a ComfyUI preset consumes `pairs[0]`: **one seeded, deterministic roll shared across the batch.**
`{red|blue} hair` at quantity 4 yields four images with the same colour. This is expected, not a bug.

Making it per-image means submitting N workflows at `batch_size=1` when the expanded pairs differ —
a change to the comfyui-backend plugin's progress aggregation, output collection, and cancellation.

## Extending it: the `prompt.transform` hook

Declared in `src/features/prompt/hooks.py`. It fires **twice per image**:

- `phase="pre"` — on the authored template, before it is sampled.
- `phase="post"` — on the expanded text.

Payload: `generation_id`, `image_index`, `phase`, `seed`, `positive`, `negative`. Mutable:
`positive`, `negative`. A handler that throws is logged and skipped; it cannot fail the generation.

```python
# hooks/prompt_hooks.py
def on_transform(context: HookContext) -> HookContext:
    if context.data["phase"] == "post":
        context.data["positive"] += ", masterpiece"
    return context
```

```yaml
# manifest.yml
hooks:
  backend:
    - hook: "prompt.transform"
      handler: "hooks.prompt_hooks.on_transform"
```

## Note on `preset.yml` `wildcards:`

The `wildcards:` download list in `preset.yml` and the `path('wildcard')` resolver are now inert —
nothing reads them. They are left in place rather than removed as unrelated cleanup.
