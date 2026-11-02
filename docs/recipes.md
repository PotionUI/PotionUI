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

## Cancelling a run during its test generation

Cancelling a recipe run while its test generation is running cancels that generation through the
orchestrator's normal cancel path. The step returns `GENERATION_SMOKE_CANCELLED` with the
generation's id, its attempt is closed as cancelled with a finish time, and the run's drive thread
ends without an error. Progress the generation reports after the cancel is dropped. A generation
that has already finished when the cancel is seen is not cancelled, and its outputs are kept; the
attempt is still closed as cancelled, since the run is.

Every step gets the same check: `StepContext.is_cancelled()` turns true once the run is cancelled.
A step that starts long work of its own, a plugin step kind included
(`from src.plugin_api.recipes import StepContext`), should poll it and stop that work when it
does. Whatever such a step returns after the cancel, its attempt is recorded as cancelled, and a
cancel that lands between two steps stops the run before the next one.

## Timeouts of the shipped recipes

Four core recipes declare more than the default. The times were measured on one RTX 4070 Ti
(12 GB), under Docker Desktop on WSL2, from a cold start (nothing loaded):

| Recipe | `timeout_seconds` | Why |
|---|---|---|
| `ltx25-starter` | 600 | The first run loads the 20 GB int8 DiT and the 15 GB Gemma 4 encoder. It finished in 5 min 57 s, one second after the step had given up at 300 s (20 October 2026). |
| `wan22-starter` | 600 | Loading the two 14B experts and the UMT5 encoder on a 12 GB card takes most of five minutes on its own. The value comes from another recipe that installs the same preset and runs the same 6-step clip (171 s once the experts are in memory); it was not measured on this recipe. |
| `minimax-h3-starter` | 400 | 214 s: 92 s of loading, about 140 s to the end of DiT placement, 69 s for the 4 steps and the decode, a 6,739 MB engine peak (23 October 2026). |
| `minimax-h3-fast-starter` | 400 | 268 s: 61 s of loading, 93 s of DiT placement, about 13 s per step, a 7,338 MB engine peak (23 October 2026). |

A longer timeout costs nothing on a faster machine: the step returns as soon as its generation
ends.

The two MiniMax-H3 smokes generate 1 s of video, because they run on a Video Director document (see
above). Their `smoke.form` sets no `frames`, which would not be read; the timeouts were measured on
that 1 s clip.

`tests/features/recipes/test_shipped_recipes_lint.py` runs the recipe lint over every shipped
recipe, the core tree and every plugin's `recipes:` root, so a timeout out of range fails the
backend suite and not only the release gate. It also pins the four timeouts above, and fails a
recipe whose smoke runs on a Video Director document while its `smoke.form` sets `frames`,
`duration` or `num_frames`.
