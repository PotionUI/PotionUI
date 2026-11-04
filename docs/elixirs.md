---
category: Presets / Models
category_order: 70
order: 47
---

# Elixirs

Elixirs chain presets and media as cards that flow left to right. A preset step runs a copy
of a saved session; a files card holds files picked from History or the Library; a prompt
writer writes one prompt per file for the step after it. Each card sits to the right of the
step it takes its files from, its input slots pick that source from a menu, and a brew runs
every step in order.

The code lives in `src/features/elixirs/`; its API is served under `/api/elixirs`.

## Which presets can be used

A preset mode can be used in an elixir once its inputs and outputs are declared. The
shipped declarations live in `content/elixirs/marketplace/contracts.yml`, keyed by preset id
and mode.

To use your own or local presets, create `content/elixirs/local/contracts.yml` with the same
shape. It is read on top of the shipped file: a mode you declare there replaces the shipped
declaration for that preset and mode, and everything else stays as shipped. The file is
checked with the same rules as the shipped one and picked up again as soon as it changes,
with no restart. If it cannot be read, the shipped declarations are used and the preset list
reports why. The `content/elixirs/local/` folder is ignored by git.

```yaml
schema_version: 1
presets:
  "YOUR-PRESET-ID":
    name: "My preset"
    modes:
      img2img:
        label: "Restyle"
        prompt: true
        quantity_field: "quantity"
        inputs:
          image:
            label: "Image"
            media_type: "image"
            target: {kind: "form_field", field: "image"}
        outputs:
          images:
            label: "Final images"
            media_type: "image"
            selector: {is_final: true, is_derived: false}
```

An input target is either a form field of the mode (`kind: form_field`) or the first or
last frame of a single-shot Video Director document (`kind: video_director_role`,
`role: first` or `last`).

## Prompt writer

A prompt writer node asks a language model to write a prompt from its instruction, once for
each image or video connected to it, or once when nothing is connected. Connect its Text
output to a preset's Prompt or Negative prompt input; the written text replaces that prompt
for the generation. A preset that gets both files and a written prompt pairs them by file,
so both must come from the same node. The prompt is written for the preset its text goes to,
or the one chosen in the writer's settings, using that preset's prompting guide and the model
in its session. A written prompt can be edited or pinned on the node; a pinned prompt is used
again in later brews for the same file instead of asking the language model.

The writer uses a language model the user can use: the server default when one is set,
otherwise the user's assigned models, and one with vision support when files are connected.

The instruction is a prompt template typed in the same chip editor as Generate's prompt:
`{a|b}` choices and weights, `${name}` variables, conditions and `#phrasebook` chips (see
[Prompts](prompts.md)). The writer has its own variables, kept on the node and edited with
Generate's variables manager from the Variables button next to the instruction: text, choice
and number variables with shuffle, pin, per image and conditions. `$` offers those variables;
the step it writes for contributes none. The server rolls the template once per write, before
the language model call, with a fresh seed recorded on the invocation (`effective_seed`): a
pinned variable uses its pin, a shuffled or per-image one rolls from that seed, and a shuffled
phrasebook chip picks one of the owner's active values in that category. Each written prompt
keeps the instruction it was written from and its seed (`rolled_instruction`,
`instruction_seed`), shown as "Rolled instruction" on the node. Running the writer or the brew
again rolls again; retrying a failed write reuses its roll. An instruction without template
syntax or chips is sent exactly as typed.

## Limits

Admins set how large an elixir and a brew may grow through these system settings:

| Setting | Default | What it bounds |
| --- | --- | --- |
| `elixirs_max_nodes` | 24 | Preset and media nodes in one elixir |
| `elixirs_max_edges` | 48 | Connections in one elixir |
| `elixirs_max_branches` | 8 | Outgoing connections from one node |
| `elixirs_max_media_per_source` | 50 | Files in one media source node |
| `elixirs_max_outputs_per_connection` | 50 | Files one connection passes on in a single brew |
| `elixirs_max_invocations` | 200 | Generations one brew may start across all branches |

A value that is empty, not a number or below 1 falls back to the default, and a value above
what an elixir can store is capped.

## Brewing

Brews survive a restart. Starting the app resumes every brew that was still running: work
that was claimed but not sent is sent again, and a generation or written prompt cut off by
the restart is marked interrupted so it can be retried. While a brew still needs files from
a generation, that generation cannot be deleted.
