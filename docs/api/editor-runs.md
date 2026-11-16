---
title: "Generative edits in the editor"
order: 15
category: API
category_order: 90
---

# Generative edits in the editor

A **run** is one press of Generate in the Image editor. You mark an area of a picture, the server crops it with some of its surroundings, runs a preset mode on the crop as a normal generation, and blends each result back as a **patch**: a PNG the size of the crop with a feathered edge. Nothing is pasted for you; the editor places the patch, and Save places it again in the server's edit chain with the `place_patch` tool, so the preview and the saved picture agree.

You need a key with **Can generate** to start or cancel a run and **Can read** to follow one. A run is yours alone: someone else's run id answers 404.

## Start a run

`POST /api/editor/tasks/{task_id}/runs` answers `202` with the run. Task ids come from `GET /api/editor/tasks`: `edit_region` and `remove_object` ship with the app. `remove_object` runs the mode with a built-in prompt.

```json
{
  "edit": { "id": "ed_...", "step_id": "s7" },
  "input": { "item_id": "..." },
  "steps": [],
  "region": { "item_id": "..." },
  "prompt": "a red wool scarf",
  "setup": { "preset_id": "...", "mode": "edit", "variant": null, "form_data": {} },
  "candidates": 2,
  "seed": null
}
```

- `edit` is a saved edit you own. `input` is the picture and `steps` are the visible edit steps beneath, the same list Save sends; the server renders them to get the picture the model must see (at most 8192 px on the long side).
- `region` is an image whose pixels at 128 or above mark the area. Leave it out to edit the whole picture, when the task allows it.
- `setup` names the preset mode and its form values. The picture, the mask, the output size, the number of results, the seed and the fixed values the mode declares in `editor.yml` are filled by the server and replace whatever you send for them.
- `candidates` is 1 to 4. `seed` pins the seed; leave it out to use the form's.

The run goes through the same submit path as `POST /api/generations/start`, so the plan limits, quotas, content policy and preset access that apply there apply here, with the same answers. A refused start leaves no run behind.

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

## Errors

| Status | `error` | Meaning |
| --- | --- | --- |
| 404 | `edit_not_found`, `run_not_found`, `preset_not_found`, `task_not_found` | Not yours, deleted, or not available to you. |
| 422 | `task_not_offered` | The mode does not offer the task. |
| 422 | `prompt_required`, `region_required`, `region_empty` | The task needs a prompt or a marked area. |
| 403 | `limit_exceeded` and the other generation refusals | The same refusals as starting a generation. |
| 400 | `cancel_failed` | The generation could not be cancelled; try again. |
| 500 | `run_start_failed` | The run could not be linked to its generation; nothing was started. |
