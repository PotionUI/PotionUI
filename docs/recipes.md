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
