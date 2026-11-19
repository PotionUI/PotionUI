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
  "mode_label": "Edit", "mode_icon": "pencil", "label": "Edit", "short_description": null, "order": 0,
  "region_strategy": "crop", "via": null, "qualifies": true
}
```

`mode` is the editor key to send back. `from_mode` is the generation mode it derives from (`null` for a standalone
editor mode). `mode_label` and `mode_icon` are what the mode picker shows: the label, and an icon name from the app's icon set (`null` when the mode has none); a derived mode inherits both from its `from` mode unless `editor_mode.yml` sets `label` or `icon`. `via` names the task an offer reaches through the fallback, per preset: a preset that ships its own
mode for a task is offered with `via: null` and its modes of the fallback task are not offered for it. The served
form (`GET /api/presets/{id}/form?mode=editor:<name>`) leaves out the fields the editor fills or hides.

Each task entry also carries what the editor needs to treat it: `id`, `label`, `icon`, `editors`, `region`, `prompt`,
`range`, `result`, `audio`, `resizes`, `fallback` (`{task, prompt}` or `null`; the prompt may contain `{prompt}`, which a
run replaces with the user's text), `offers`, `default`, and these, all optional for older clients to ignore:

| Field | Meaning |
|---|---|
| `produces` | `patch` (stitched onto the picture), `replace`, or `matte` (a cutout, see below). |
| `internal` | `true` for a task that has no tool in the rail and is run by other tools (`find_subject`). |
| `matte` | `true` when the `region` is a soft matte kept grayscale and used as the patch alpha (`replace_background`). |
| `grow` | The fraction of the marked area's longer side the server grows the region by before the run (`remove_object`: `0.04`; `0` for the rest). |

## Start a run

`POST /api/editor/tasks/{task_id}/runs` answers `202` with the run. Task ids come from `GET /api/editor/tasks`: `edit_region`, `remove_object`, `replace_background` and `find_subject` ship with the app. `remove_object` takes no prompt and runs the mode with its `instruction` (or the task's built-in prompt through the fallback); `replace_background` needs a `prompt` describing the new background and takes a soft matte as `region`.

```json
{
  "edit": { "id": "ed_...", "step_id": "s7" },
  "input": { "item_id": "..." },
  "steps": [],
  "region": { "item_id": "..." },
  "prompt": "a red wool scarf",
  "setup": { "preset_id": "...", "mode": "editor:edit", "form_data": {} },
  "candidates": 2,
  "seed": null,
  "options": {}
}
```

- `edit` is a saved edit you own, for a picture (`tool` `image`) or a clip (`tool` `video`; its state holds the source clip's steps and an optional `clip` object with `duration_seconds`, `width` and `height`, which `GET /api/edits` returns for listings). An id that is not yours, or that does not exist, answers `404 edit_not_found`, for clips as for pictures. `input` is the picture and `steps` are the visible edit steps beneath, the same list Save sends; the server renders them to get the picture the model must see (at most 8192 px on the long side).
- `region` is an image whose pixels at 128 or above mark the area. Leave it out to edit the whole picture, when the task allows it.
- `setup` names the editor mode, written `editor:<name>` as the offers of `GET /api/editor/tasks` list it, and its form values. The picture, the mask, the output size, the number of results, the seed and the `fixed` values of the editor mode's `editor_mode.yml` are filled by the server and replace whatever you send for them; its `hide` fields keep their own defaults. A mode name without the prefix answers `422 task_not_offered`.
- `candidates` is 1 to 4. `seed` pins the seed; leave it out to use the form's.
- `options` is optional (default `{}`) and carries per-task switches. `include_shadow: true` on `remove_object` grows the removed area 2.5 times as much and also downward by a quarter of the marked height, so the object's shadow goes with it; any other value, and any other key, changes nothing.
- For a task with `grow` above 0 the server dilates the region before cropping, so the crop, the mask the mode gets and the stitch all use the grown area; the stitch feather is multiplied by the task's `feather`. For a `matte` task the region keeps its gray levels and the patch alpha is the matte itself.

The generation carries the editor key as its `mode`, and `POST /api/generations/start` itself refuses an editor key with `422 mode_is_editor`; only a run may use one. The run goes through the same submit path as that route, so the plan limits, quotas, content policy and preset access that apply there apply here, with the same answers. A refused start leaves no run behind.

## Expand a picture

`expand` is a core task with `grows: true`: its result is larger than the picture, so a run names the new canvas instead of a region.

```json
{
  "edit": { "id": "ed_...", "step_id": "s7" },
  "input": { "item_id": "..." },
  "steps": [],
  "expand": { "width": 1024, "height": 1821, "x": 0, "y": 399 },
  "prompt": null,
  "setup": { "preset_id": "...", "mode": "editor:expand", "form_data": {} }
}
```

`expand.width` and `height` are the new canvas and `x` and `y` where the picture's top-left corner sits on it (clamped so the picture stays inside). The canvas has to be at least as large as the picture (`422 expand_smaller`), differ from it (`422 expand_same`), stay within 8192 px a side and 36 megapixels (`422 expand_too_large`). A run for `expand` without `expand` answers `422 expand_required`; `expand` on another task answers `422 expand_not_supported`. An empty `prompt` runs the built-in "Continue the picture beyond its edges so the new area matches it.". One picture is made, whatever the form's quantity says.

A large expansion is made in passes; the server runs them one after another inside the same run (each pass is a generation with the run's origin, and the run's `generation_id` follows the current one), and `progress` covers all of them. The finished run has one candidate whose patch is an RGBA picture with the size of the new canvas, opaque over the new area and transparent where the picture was; its `rect` is the whole canvas (`{x: 0, y: 0, width: 1, height: 1}`). Place it with the media tool `place_patch` using `canvas_width`, `canvas_height`, `canvas_x` and `canvas_y` (all optional, 0 keeps the picture's size), which first place the picture on a transparent canvas of that size.

`GET /api/editor/expand/formats` lists the named formats the caller may use, built-in first:

```json
{ "formats": [{ "id": "story", "label": "Story", "ratio": "9:16", "source": "built_in", "group_id": null }] }
```

House formats come from the system setting `editor_expand_formats` (a list of `{id, label, ratio, group_id}`), appear with the id `house.<id>` and `source: "house"`, and a format with a `group_id` is listed only for members of that group.

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

A task with `produces: "matte"` (`find_subject`) answers candidates of the same shape, but the PNG behind `patch.item_id` is the subject as an RGBA cutout at the size of the picture (alpha from the model, nothing to stitch), and `rect` is the whole picture (`x` 0, `y` 0, `width` 1, `height` 1). `replace_background` answers an ordinary patch whose alpha is the matte; to put a colour, a picture or transparency behind the subject without a model run, use the editor tool `replace_background` of [media tools](plugin-api.md) with the cutout's matte.

`status` is `queued`, `running`, `done`, `failed` or `cancelled`. `rect` is where the patch goes, as fractions of the picture. `soft` is true when the crop is much larger than the size the model works at, so the result comes back softer. A run the server lost in a restart answers `failed`.

The generation behind the run is an ordinary generation: it appears in History with its `origin` (`{"kind": "editor", "edit_id", "step_id", "task", "run_id"}`), and the live preview arrives as `workbench_update` on `/ws/generation` once you subscribe to its `generation_id`. When the run changes state the same socket sends:

```json
{ "type": "editor_run_update", "generation_id": "...", "run": { } }
```

Only the clients subscribed to that generation, which means its owner, receive it.

## Runs on a video

The video editor uses the same routes. `GET /api/editor/tasks?editor=video` lists only the video tasks (`extend`, `refine_faces`, `remix`) that a preset on this instance offers. A task entry says what it needs:

```json
{
  "id": "extend", "label": "Extend", "editors": ["video"], "region": "none", "prompt": "optional",
  "range": "optional", "result": "append", "audio": "result", "resizes": false, "offers": []
}
```

`range` is `optional` (no range means the whole clip) or `required` (`refine_faces`: a run without one is refused with a 422 `range_required`; an offer's `max_range_s` is the longest range its mode takes, `null` without one, and a longer range is refused with a 422 `range_too_long`), `result` is `replace` (the new footage has the length of the range) or `append` (it adds time), `audio` is `keep` (the clip's sound stays) or `result` (the added part brings its own sound, silence when the model made none) and `resizes` says a whole-clip result may be larger than the clip. An offer's `region_strategy` is `range`.

`input` and `steps` name the clip as the editor currently shows it (the source plus its steps, as for a picture). `range` marks the part to work on, in seconds, and is left out for the whole clip. The server snaps it to the frames of the clip at its own frame rate, so `range` in the run and in each candidate is the snapped range.

```json
{
  "edit": { "id": "ed_...", "step_id": "s4" },
  "input": { "item_id": "..." },
  "steps": [],
  "range": { "start_s": 2.0, "end_s": 4.5 },
  "prompt": "make it snow",
  "setup": { "preset_id": "...", "mode": "editor:remix", "form_data": {} },
  "candidates": 1
}
```

The server cuts the range frame by frame, sends it to the mode as its input video, and when the generation finishes fits each result to the clip's size and frame rate and to the length of the range (frames are dropped or the last one repeated; a task with `resizes` keeps the result's own size on the whole clip), then splices it into the clip. Frames outside the range are not changed. A `replace` task keeps the clip's own sound; `extend` inserts the new footage at the end of the range (the end of the clip without one) and the part after the range follows it. Each finished candidate carries the whole spliced clip, not a patch:

```json
{
  "run": {
    "id": "er_...", "task": "remix", "status": "done", "range": { "start_s": 2.0, "end_s": 4.5, "whole": false },
    "candidates": [
      {
        "clip": { "item_id": "..." }, "url": "/api/media/uploads/...", "seed": 12345, "generation_file_id": "...",
        "range": { "start_s": 2.0, "end_s": 4.5 }, "duration_s": 9.0
      }
    ]
  }
}
```

An `extend` candidate has `"added": { "at_s": 4.5, "seconds": 5.0 }` instead of `range`. `duration_s` is the length of the new clip. The clip is a stored upload (`clip.item_id`); the run keeps its working copies of the source and the cut under the `editor_run_working` purpose until it settles, like a picture run. A run on a video has `rect: null`.

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
  "preset_id": "...", "preset_name": "Qwen-Image 2.1", "mode": "editor:edit", "mode_label": "Edit",
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
| 422 | `task_not_offered` | The editor mode does not offer the task, or the mode name has no `editor:` prefix. |
| 422 | `mode_is_editor` | Returned by `POST /api/generations/start`: an editor key is for editor runs only. |
| 422 | `prompt_required`, `region_required`, `region_empty` | The task needs a prompt or a marked area. |
| 422 | `range_invalid` | The range starts before the clip, ends no later than it starts, or is shorter than one frame. |
| 422 | `range_outside_clip` | The range starts or ends past the end of the clip. |
| 422 | `range_not_supported`, `range_required` | A range was sent for a task that takes none, or the task needs one. |
| 422 | `unsupported_media` | A picture was sent to a video task, or a video to a picture task. |
| 422 | `clip_unreadable` | The clip's size, rate or length could not be read. |
| 403 | `limit_exceeded` and the other generation refusals | The same refusals as starting a generation. |
| 400 | `cancel_failed` | The generation could not be cancelled; try again. |
| 500 | `run_start_failed` | The run could not be linked to its generation; nothing was started. |

## Fix faces: detections and strength

`fix_faces` repaints the faces and hands the user ticks. The editor first asks which are in the picture, then starts an ordinary run whose `region` is the union of the ticked boxes and any areas the user marked by hand.

`POST /api/editor/tasks/fix_faces/detections` takes the picture the way a run start does, and needs the `generate` scope:

```json
{ "input": { "item_id": "..." }, "steps": [] }
```

```json
{
  "success": true,
  "data": {
    "picture": { "width": 1024, "height": 768 },
    "detections": [
      { "id": "d1", "kind": "face", "box": { "x": 0.08, "y": 0.06, "width": 0.24, "height": 0.48 }, "score": 0.9 },
      { "id": "d2", "kind": "hand", "box": { "x": 0.04, "y": 0.57, "width": 0.12, "height": 0.36 }, "score": 0.7 }
    ]
  }
}
```

- `picture` is the size of the picture the detectors ran on, the same render a run uses (at most 8192 px on the long side). `box` is in 0 to 1 of that picture (`x`, `y` is the top left corner), widened by a tenth of its size on each side and kept inside the picture.
- `kind` is `face` or `hand`. `id` is `d1`, `d2`, ... in the order of the list: top to bottom, then left to right. The ids are only stable within one answer. `score` is the detector's confidence, 0 to 1.
- Detectors are a registry: the shipped one runs the YOLO face and hand weights the SDXL detailer uses (`detection_bbox` model folders), on the CPU, and a plugin adds its own with `register_detector` of `src.plugin_api.editor`. Boxes of the same kind that overlap by more than 60% are merged into the one with the better score.
- `503 detector_unavailable`: no detector can run (no weights are installed, or the detection runtime is missing). The message says why. `404`: a picture that is not the caller's.

An offer of a mode with a `strength` in its `editor_mode.yml` carries `"strength": {"default": 0.35, "min": 0.1, "max": 1.0}` (`null` for every other mode). A run start takes an optional `"strength": 0.5`, clamped to that range and used in place of the form value of the mode's strength field; a start without it uses `default`. A mode without a `strength` ignores the key.
