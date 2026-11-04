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

**Prompts per input** (`prompts_per_input`, 1 to 20, default 1) sets how many prompts the writer
writes for each input. Every prompt is its own write: a separate language model call through the
same path, with its own roll and seed, its own retry and its own pin. The node lists them per
input as "Prompt k of N"; each written entry carries `for_key` (the input it was written for),
`slot_key`, `prompt_number` and `prompt_count`, and a pin is kept under its `slot_key` (the
input key for prompt 1, `<input key>#k` for prompt k), so one prompt can be pinned without the
others. With 1 the entries look as they always did. The cap is the batch prompt limit of 20
images ([Prompts](prompts.md#batch-prompts)), so the prompts of one input always fit one batch
at quantity 1; the brew's `elixirs_max_invocations` still bounds the total.

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

When a preset step gets several written prompts for the same input, either straight from a
writer or paired with the file they were written for, and the preset supports batch prompts
(`supports_prompt_batch`: every enabled backend of its engine can take them) and its form has
no Video or Music Director document, the brew sends them as one generation with
`batch_prompts: true` and `prompts` set to those prompts, the step's quantity per prompt.
Prompts written for different inputs are never merged, since a batch shares its input files.
When `prompts x quantity` would pass the 20-image cap the prompts are split over several
batches. Otherwise each prompt is its own generation, as before. A batch is stored by
Generate as one history entry per prompt; the brew waits for that split and records each file
under its own entry with `prompt_index` (its place in the batch) and `prompt_key` (the written
prompt it came from), so the step's outputs stay grouped per prompt and picks and Continue
work per file.

Brews survive a restart. Starting the app resumes every brew that was still running: work
that was claimed but not sent is sent again, and a generation or written prompt cut off by
the restart is marked interrupted so it can be retried. While a brew still needs files from
a generation, that generation cannot be deleted.

## Chat assistant

While an Elixir tab is open, a new chat conversation is the Elixirs assistant (PotionAI) for
that elixir: the tab sends the elixir's id with every message, and the mode has no route of
its own. The assistant edits the elixir and never runs it. A brew spends GPU time, so it tells
the user to press Brew, or Continue after a choice.

| Tool | Approval | What it does |
| --- | --- | --- |
| `get_elixir` | no | Steps left to right (id, kind, preset and mode, session mode and session name, a writer's instruction, writes-for and prompts per input), connections, problems, the last brew and the steps waiting for a choice. `include_presets` lists the preset modes an elixir can use. |
| `get_step_settings` | no | One step's effective prompt, negative prompt and form fields, bounded. A following step is read from its session. |
| `get_choice_outputs` | no | The outputs of a step waiting for a choice, numbered, with their ids. When the chat's model has vision it also gets one image with the outputs numbered; without vision it gets the details only and asks the user. |
| `edit_elixir` | yes | One batch of operations against the revision the assistant read. |
| `choose_outputs` | yes | Sets the pick at a waiting step, the same pick the tiles set. It does not continue the brew. |

`edit_elixir` operations: `add_step` (after a step, or a new lane), `add_start` (before a
lane's first step), `insert_between`, `add_writer`, `remove` (files are rewired past the
removed step), `connect`, `set_settings`, `set_writer` (instruction text, the writer's own
`variables` merged by name with `null` removing one, writes-for and `prompts_per_input`
from 1 to 20; phrasebook chips stay the
user's: sending `instruction_chips` is refused, and a new instruction drops chips it no longer
contains) and `set_session` (follow, copy or new). `set_settings` checks field names
against the step's preset form and refuses fields filled by a connection. A following step's
change is saved into its session through the sessions API with the version the elixir last
saw, so every step and Generate tab using that session updates; a copy or new step keeps the
change on the step.

The batch is all or nothing. When the elixir changed since the assistant read it, the edit is
refused and the assistant reads it again. Every refusal names the fix, e.g. `Operation 1
(add_step): Krea-2 · Text to image takes no images, so it cannot follow Sharpen. Pick a preset
mode whose inputs take images (get_elixir with include_presets=true lists 'takes'). No change
was made.`

After an approved change the server pushes `elixir_changed` on the user's notification socket,
and an open Elixir tab with no unsaved edits reloads the elixir, or the brew whose pick
changed. With unsaved edits the tab shows its usual conflict prompt. These tools are not
offered over MCP.
