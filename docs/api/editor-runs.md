---
title: "Generative edits in the editor"
order: 15
category: API
category_order: 90
---

# Generative edits in the editor

A **run** is one press of Generate in the Image editor. You mark an area of a picture, the server crops it with some of its surroundings, runs a preset mode on the crop as a normal generation, and blends each result back as a **patch**: a PNG the size of the crop with a feathered edge. Nothing is pasted for you; the editor places the patch, and Save places it again in the server's edit chain with the `place_patch` tool, so the preview and the saved picture agree.

You need a key with **Can generate** to start or cancel a run and **Can read** to follow one. A run is yours alone: someone else's run id answers 404.

## What an offer looks like

`GET /api/editor/tasks?editor=image` lists each task with the `offers` the caller may use, one per editor mode:

```json
{
  "preset_id": "...", "preset_name": "Krea-2", "mode": "editor:edit", "from_mode": "edit",
  "mode_label": "Edit", "label": "Edit", "short_description": null, "order": 0,
  "region_strategy": "crop", "via": null, "qualifies": true,
  "owned": ["quantity", "source_image", "match_source", "fit_mode"], "variant": null
}
```

`mode` is the editor key to send back. `from_mode` is the generation mode it derives from (`null` for a standalone
editor mode). `via` names the task an offer reaches through the fallback, per preset: a preset that ships its own
mode for a task is offered with `via: null` and its modes of the fallback task are not offered for it. `owned` lists the fields the
editor fills or hides, and `variant` is always `null`; both stay for clients that still read them, and the served
form (`GET /api/presets/{id}/form?mode=editor:<name>`) already leaves those fields out.

## Start a run

`POST /api/editor/tasks/{task_id}/runs` answers `202` with the run. Task ids come from `GET /api/editor/tasks`: `edit_region` and `remove_object` ship with the app. `remove_object` runs the mode with a built-in prompt.

```json
{
  "edit": { "id": "ed_...", "step_id": "s7" },
  "input": { "item_id": "..." },
  "steps": [],
  "region": { "item_id": "..." },
  "prompt": "a red wool scarf",
  "setup": { "preset_id": "...", "mode": "editor:edit", "form_data": {} },
  "candidates": 2,
  "seed": null
}
```

- `edit` is a saved edit you own. `input` is the picture and `steps` are the visible edit steps beneath, the same list Save sends; the server renders them to get the picture the model must see (at most 8192 px on the long side).
- `region` is an image whose pixels at 128 or above mark the area. Leave it out to edit the whole picture, when the task allows it.
- `setup` names the editor mode, written `editor:<name>` as the offers of `GET /api/editor/tasks` list it, and its form values. The picture, the mask, the output size, the number of results, the seed and the `fixed` values of the editor mode's `editor_mode.yml` are filled by the server and replace whatever you send for them; its `hide` fields keep their own defaults. For one more release a generation mode name without the prefix (`"edit"`) is accepted and maps to the one editor mode derived from it for that task; no match or several answers `422 task_not_offered`. `variant` is ignored.
- `candidates` is 1 to 4. `seed` pins the seed; leave it out to use the form's.

The generation carries the editor key as its `mode`, and `POST /api/generations/start` itself refuses an editor key with `422 mode_is_editor`; only a run may use one. The run goes through the same submit path as that route, so the plan limits, quotas, content policy and preset access that apply there apply here, with the same answers. A refused start leaves no run behind.

## Follow a run

`GET /api/editor/runs/{run_id}` answers the run:

```json
{
  "run": {
    "id": "er_...", "task": "edit_region", "status": "done",
    "edit_id": "ed_...", "step_id": "s7", "generation_id": "...",
    "progress": 1.0, "error": null, "soft": false,
    "rect": { "x": 0.31, "y": 0.22, "width": 0.28, "height": 0.35 },
    "candidates": [
      { "patch": { "item_id": "..." }, "url": "/api/media/uploads/...", "seed": 12345, "generation_file_id": "..." }
    ]
  }
}
```

`status` is `queued`, `running`, `done`, `failed` or `cancelled`. `rect` is where the patch goes, as fractions of the picture. `soft` is true when the crop is much larger than the size the model works at, so the result comes back softer. A run the server lost in a restart answers `failed`.

The generation behind the run is an ordinary generation: it appears in History with its `origin` (`{"kind": "editor", "edit_id", "step_id", "task", "run_id"}`), and the live preview arrives as `workbench_update` on `/ws/generation` once you subscribe to its `generation_id`. When the run changes state the same socket sends:

```json
{ "type": "editor_run_update", "generation_id": "...", "run": { } }
```

Only the clients subscribed to that generation, which means its owner, receive it.

## Cancel a run

`DELETE /api/editor/runs/{run_id}` cancels the run's generation and settles the run as `cancelled`. If the generation cannot be cancelled and is still going, the run stays as it is and the answer is `400 cancel_failed` ("The run could not be cancelled, try again"). If the generation has in fact already finished, the run is settled from its real result instead. A run that is already finished is returned as it is.

The route only cancels. It never removes patches: a finished run keeps its patches, so a saved edit that still points at one is not broken.

## Files a run keeps

While a run works, the server stores the picture it sends the model, the mask, and the base crop and region it stitches against. These are stored under an internal purpose (`editor_run_working`) that no other upload can share, so removing them can never touch a file a saved edit uses. They are removed once the run settles in any state (a refused start removes them at once), unless another running run still reads the same file. A `done` run keeps only its patches. A `failed` or `cancelled` run keeps no working files. Patches are never removed by the server, even when a cancel wins after they were stored, because the store cannot tell a new patch from one it shared with an earlier result.

## Your default setup for a task

A **setup** is what a task runs with: an editor mode that offers the task, plus where its settings start from: one of your editor sessions (a session of kind `editor`), one of your formulas, or the mode's own defaults. Your **default** is the setup you chose for a task. Only you choose it; the server never picks one for you and never swaps one for another. Defaults are yours alone: nobody else can read, set or clear them.

`PUT /api/editor/tasks/{task_id}/default` sets it (needs a key with **Can change content**) and replaces any earlier default of yours for that task:

```json
{
  "preset_id": "...",
  "mode": "editor:edit",
  "setup": { "kind": "session", "id": "..." }
}
```

`setup.kind` is `session` (an editor session of yours for that preset that holds that editor mode), `formula` (a formula of yours for that preset and that editor mode or the generation mode it derives from) or `defaults` (the mode's own defaults; no `id`). `DELETE /api/editor/tasks/{task_id}/default` clears it, and clearing a default that is not set is not an error. Both answer `{"task": {...}}`, the task entry as `GET /api/editor/tasks` lists it, so `default` reads back as set or as `null`. Nothing is written when a request is refused.

`GET /api/editor/tasks` returns each task's `default`, or `null` when you have none:

```json
{
  "preset_id": "...", "preset_name": "Qwen-Image 2.1", "mode": "editor:edit", "mode_label": "Edit", "variant": null,
  "setup": { "kind": "session", "id": "...", "name": "Soft portrait", "version": 4, "updated_at": "2026-11-16T10:00:00+00:00" },
  "set_at": "2026-11-16T09:30:00+00:00",
  "health": { "ok": true }
}
```

The default points at the session or formula by id and copies nothing, so a later save to that session is what the next run starts from: `setup.version` and `setup.updated_at` are the session's current ones, and `setup.name` is its current name. A `defaults` setup is `{"kind": "defaults", "id": null, "name": null}`.

`health` is worked out on every read. A default that stops working stays stored exactly as you set it and reads back broken; the server never repairs it and never substitutes another setup, so it works again if what went away comes back. Pick another or clear it to move on.

```json
{
  "ok": false, "reason": "session_deleted", "missing": { "kind": "session", "id": "..." },
  "name": "Soft portrait", "preset_name": "Qwen-Image 2.1", "mode_label": "Edit"
}
```

`name`, `preset_name` and `mode_label` are the last known names, so you can say what went away even when it is gone.

| `reason` | `missing.kind` | What went away |
| --- | --- | --- |
| `preset_missing` | `preset` | The preset is not installed any more (or its plugin is off). |
| `preset_unassigned` | `preset` | The preset is no longer available to you. |
| `mode_missing` | `mode` | The preset no longer has that editor mode (a default stored with a plain generation mode name reads this too). |
| `offer_withdrawn` | `task` | The editor mode no longer offers the task, directly or through the task's fallback. A preset that ships its own mode for a task is not offered through the fallback. |
| `requirements_failed` | `backend` | No backend is set up for the engine of the preset. |
| `session_deleted` | `session` | The editor session was deleted. |
| `session_mismatch` | `session` | The session is no longer an editor session of that preset holding that mode. |
| `formula_deleted` | `formula` | The formula was deleted. |
| `formula_mismatch` | `formula` | The formula no longer applies to that preset and mode. |

When several apply, the first one in this table is reported. Deleting a session or formula never clears a default pointing at it; deleting a user removes their defaults.

## Errors

| Status | `error` | Meaning |
| --- | --- | --- |
| 404 | `edit_not_found`, `run_not_found`, `preset_not_found`, `task_not_found` | Not yours, deleted, or not available to you. |
| 404 | `default_session_not_found`, `default_formula_not_found` | The session or formula does not exist, or is not yours (the answer is the same). |
| 422 | `offer_unavailable` | No backend is set up for the preset's engine. |
| 422 | `default_session_mismatch`, `default_formula_mismatch` | The session is not an editor session of that preset and mode, or the formula does not apply to it. |
| 422 | `task_not_offered` | The editor mode does not offer the task, or a mode name without the prefix matches none or several. |
| 422 | `mode_is_editor` | Returned by `POST /api/generations/start`: an editor key is for editor runs only. |
| 422 | `prompt_required`, `region_required`, `region_empty` | The task needs a prompt or a marked area. |
| 403 | `limit_exceeded` and the other generation refusals | The same refusals as starting a generation. |
| 400 | `cancel_failed` | The generation could not be cancelled; try again. |
| 500 | `run_start_failed` | The run could not be linked to its generation; nothing was started. |
