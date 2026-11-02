---
category: Presets / Models
category_order: 70
order: 46
---

# Recipe Smoke Generation

A recipe ends by proving that its setup works: a `generation.smoke` step runs one real generation
through the normal generation path, the same one the Generate page uses. The step's `params` name
the preset and mode, and the recipe's `smoke:` block says how to run it. How a recipe is written,
shipped by a plugin and extended with new step kinds is described in
[The Plugin API](plugin-api.md#contributing-a-recipe-and-recipe-step-kinds).

## The `smoke:` block

| Key | Meaning |
|---|---|
| `preset_id`, `mode` | The preset and mode to run. The step's `params` repeat them. |
| `prompt`, `negative_prompt` | The prompt of the test generation. |
| `seed` | A fixed seed, so that two runs give the same result. |
| `form` | Field values laid over the mode's default form: a small resolution, few steps. |
| `timeout_seconds` | How long the step waits for the generation, in seconds. |

## How long the step waits

`timeout_seconds` is a whole number from 1 to 3600, and 300 when it is omitted or `null`. Any
other value (a float, a string, `true`, 0, more than 3600) is a lint error, reported by
`validate_recipe_dict` and therefore by `scripts/recipe_lint.py`. When the generation has not
finished in time, the step fails with "timed out waiting for completion after Ns", where N is the
recipe's value. `GET /api/recipes/{id}` reports the value in the recipe's `smoke` detail.

Raise it when the model needs more than five minutes for its first generation. A large video model
on a 12 GB card spends most of that time loading its weights:

```yaml
smoke:
  preset_id: "01KX47WANVIDEO0000000000TV"
  mode: "video"
  timeout_seconds: 600
```

## The form of the test generation

The `generation.smoke` and `pipeline.render` steps build the same form. It starts from the
defaults of the mode's default form, the recipe's `form` values are laid over them, and the result
goes through `bind_form` like a form sent from the Generate page.

- **Reactions run as they do in the UI.** A field whose value is set by another field's reactions,
  such as the steps and CFG a speed profile chooses, is left to `bind_form` unless the recipe sets
  it. A smoke form with `speed_profile: "fast"` alone therefore runs the fast profile's steps and
  CFG, not the static defaults of the form. A value the recipe sets always wins over a reaction.
- **Video Director presets get a Video Director document.** When the mode shows the Video Director
  with a text-to-video sub-mode (Wan, LTX-2, LTX-2.5, MiniMax-H3, MiniMax-H3-Fast), the form gets a
  one-segment `t2v` document: the shortest duration the preset allows (1 s for the shipped
  presets), the form's resolution and seed, no media, and the recipe's smoke `prompt` as the segment
  prompt (a neutral prompt for `pipeline.render`). `pipeline.render` normalizes that document the
  way a real generation does. Without it, the Wan, LTX-2 and LTX-2.5 pipelines fail on
  `form.video_director`, and the MiniMax-H3 ones fall back to the form's `frames`. A recipe can
  still pass a document of its own as `form.video_director`; it is kept as given.
- **The clip length comes from that document.** These pipelines read the duration of the Video
  Director document before any `frames` field, so a `frames`, `duration` or `num_frames` value in
  the smoke `form` of such a recipe is never read.

## When the test generation runs too long

The step starts its generation on the application's event loop, the one that serves the API, so
the generation's output bridge lives as long as the application and not as long as the step. When
the timeout elapses, the step cancels the generation it gave up on through the orchestrator's
normal cancel path, and the backend is free for the next generation. A failed cancel is logged and
the step still reports its timeout. Outside the application (a script, a test), where no
application loop runs, the step starts the generation on a loop of its own.

Cancelling a running generation that its backend no longer knows and whose output bridge is gone
(its loop has closed) marks it cancelled and releases its queue slot, instead of leaving it running
forever with the backend held. A generation whose bridge is still alive is left to finish.
