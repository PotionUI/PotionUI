# OpenRouter Images

Adds an OpenRouter cloud backend (driver `cloud.openrouter`), discovers OpenRouter's image models
and ships the "OpenRouter Images" preset with a text-to-image and an edit mode.

## Setting it up (needs a real OpenRouter key to try out)

1. Admin, Plugins: enable "OpenRouter Images" and restart the server once, so the new backend type is
   registered.
2. Admin, Backends: add a backend and choose "OpenRouter". Paste the API key (created at
   openrouter.ai under Keys) and save. Optionally set the app name and address OpenRouter shows in its
   usage statistics, the upstream providers requests may be routed to, and the anonymous user id.
3. Open the backend's Catalog tab and refresh it. The models OpenRouter lists for images appear,
   none enabled.
4. Enable the models you want people to use. Only enabled models show up in the preset's model picker.
5. Generate from the "OpenRouter Images" preset. Every generation is billed to your OpenRouter account.

## What the plugin assumes about the OpenRouter API

The image endpoints are documented only partly, and the plugin reads them defensively: anything that
does not map is skipped instead of failing the refresh.

- `GET /images/models` answers `{"data": [...]}` (or a bare list). Each model has `id`, optional `name`,
  `description`, `expiration_date`, `architecture.input_modalities` and `architecture.output_modalities`,
  and `supported_parameters`. A model is kept only when it outputs `image`. It accepts reference pictures
  when `image` is among its input modalities.
- `supported_parameters` entries are either a bare name or an object with `name` (or `parameter`),
  `type` (`enum`, `range`, `boolean`, `string`), `values` (or `options`), `min`/`max`, `step`, `default`.
- `GET /images/models/{id}/endpoints` answers `{"data": {"endpoints": [...]}}` (or a bare list). Each
  endpoint has `supported_parameters`, `allowed_passthrough_parameters` (strings, offered as `x.` text
  options) and `pricing` (an object or list of `{billable, unit, cost_usd}`). Values of one parameter are
  merged across endpoints, and the cheapest price per unit is kept. A failing lookup only drops that
  model's details.
- A bare parameter name with no values is given documented defaults for `resolution`
  (512, 1K, 2K, 4K), `output_format` (png, jpeg, webp), `background` (yes/no) and `output_compression`
  (0 to 100). `aspect_ratio` and `quality` without values are skipped, because no value list is documented.
- `n` (range) sets how many pictures one request may return. `input_references` (range) sets how many
  reference pictures are accepted, else 4.
- `POST /images` answers synchronously with `data[]` items carrying `b64_json` or `url`, optionally
  `media_type`, plus `usage.cost`. `seed` is sent only for models that list it. Options the plugin does
  not know are sent top level under their own name.
- `background` is offered as a yes/no choice and sent in words: yes becomes `"transparent"`, no is left out, and a
  text value such as `"opaque"` is passed through. The real-key run should confirm OpenRouter accepts `"transparent"`.
- The anonymous user id is made by core (an opaque per-install id derived from the server's secret key, different for
  each backend) and sent as `user` only when the backend's toggle is on.
- A 403 with `metadata.reasons` is a moderation refusal. Its reasons, provider and model are kept in the
  admin-only detail; the user sees a plain sentence without them.
- The key is checked with a listing request. It is sent only to the API address, never to the download
  addresses of finished pictures.

`cloud_models.yml` lists the model ids the catalog marks as suggested. Ids OpenRouter does not list are
ignored.

## Trying it with a real key

This run needs an OpenRouter account with credits and a key, so it is done by hand and is not part of the tests.

1. Enable the plugin (Admin, Plugins) and restart once.
2. Add an OpenRouter backend (Admin, Backends) with your key, and save.
3. Open the backend's Catalog tab and refresh. Note any models listed as skipped: they show where the assumptions above do not hold.
4. Enable two image models, preferably one that accepts reference pictures.
5. Generate from "OpenRouter Images" in text-to-image mode. Try a transparent background and a count above one.
6. Generate in edit mode with one picture, then with two.
7. Check the pictures arrive in history, and that a prompt the model refuses is shown as blocked.
8. With the anonymous user id switched on, check in the OpenRouter activity log that requests carry an id that is not a user name.
