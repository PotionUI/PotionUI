---
category: Presets / Models
category_order: 70
order: 27
---

# Cloud Models

A **cloud model** is a model that runs on a provider's servers instead of on a GPU you own. PotionUI
drives it through the same Generate page as a local model: the user picks a preset, picks a model,
fills in the form and gets an image, video or audio file back in the gallery. What differs is what
sits behind the button: an HTTPS API, a per-job price, and a model list that changes without you
touching a file.

Cloud models are built from the same parts as the rest of the platform, and core names no provider:

- a **provider plugin** teaches PotionUI one provider's API (`cloud.<key>`, for example `cloud.acme`);
- an admin creates a **backend** for it, which holds the credentials and limits;
- the backend's **catalog** lists what the provider offers; the admin **enables** the models users
  may use;
- the provider's **presets** (shipped inside the plugin) build their forms from shared blocks, so
  the controls follow whichever model the user picked.

This page is the reference for admins and for anyone writing a provider plugin. The mechanics it
relies on are documented in [Backends and Engines](backends.md), [Generation Routing](generation-routing.md),
[The Plugin API](plugin-api.md), [Preset forms](presets/forms.md) and the [preset manifest](presets/manifest.md).

## How the parts fit together

```text
provider plugin        registers driver "cloud.acme"       (src.plugin_api.cloud)
        |
backend (admin)        engine "cloud", driver "cloud.acme"  (credentials, limits)
        |
catalog (per backend)  everything the provider offers      (Refresh)
        |
enabled models         model rows of type "cloud"          (Enable / Disable)
        |
presets                engine: cloud, driver: cloud.acme   (list, accept and route only these)
```

- **One engine, many drivers.** Every provider shares the engine `cloud`. Each provider is one
  *driver* (`cloud.<key>`). A backend belongs to one driver; two accounts with the same provider are
  two backends of the same driver. `BaseBackendConfig.effective_driver` is the driver a backend uses
  (its `driver`, or its `engine` when the driver is blank).
- **A preset belongs to one provider.** A cloud preset declares `driver: cloud.acme`. The picker lists
  only that driver's models, the router only considers that driver's backends, and a model id of
  another provider posted to the API for that preset is refused. See
  [`driver:` in the manifest](presets/manifest.md) and the `PresetDriver` rule in
  [Generation Routing](generation-routing.md).
- **Cloud models have no file.** An enabled model is a row with model type `cloud`, no path, size or
  hash, and an availability row for each backend that offers it. `cloud` is a *virtual* model type:
  it is never part of the model roots, layout profiles, downloads, indexing or the type-change
  control. Changing the type of a cloud model, or deleting it from the index, is refused; disable it
  in the catalog instead.
- **The model's name is a slug.** The row's filename is `<provider key>~<provider model id>`,
  lower-cased, with `/` replaced by `~` and every character outside `a-z 0-9 . _ ~ -` dropped, for
  example `acme~studio~render-2.1`. The slug contains no `/` because history matches models by the
  text after the last `/`. The provider's real model id is kept in the catalog.
- **History, assignments and names survive.** Disabling a model removes only its availability. The
  model row, its generation history, user and group assignments and custom names stay, and come back
  when the model is enabled again.

## For admins

### Adding a provider

1. Enable the provider's plugin in **Admin → Plugins** and restart (engines and drivers are collected
   at startup).
2. In **Admin → Backends**, create a backend and choose the provider's driver. Fill in the
   credentials; secret fields are stored encrypted and never shown again.
3. Open the backend's **Catalog** tab and choose **Refresh**.
4. Enable the models users may use. Nothing is enabled by default.

Every cloud backend has these settings besides the provider's own:

| Setting | Default | Meaning |
|---|---|---|
| `max_parallel` ("Parallel jobs") | 4 (1 to 32) | How many generations run on this backend at once. The rest wait in the normal queue order. Local backends stay at one. |
| `timeout_seconds` | 1800 | The longest one generation may run. A model's own `max_seconds`, when the catalog has one, can shorten it. |
| `enabled`, `priority`, default flag | as any backend | Backend selection works as described in [Backends and Engines](backends.md). |

The Catalog tab also shows the provider's data notice: what leaves your server (the rendered prompt,
the selected input media, the parameters and the seed), where the provider routes it and how long it
keeps results. PotionUI downloads every result immediately and never links to the provider's copy.

### The Catalog tab

The tab is shown for backends of the `cloud` engine and is admin-only, because it is the only place
that shows **prices**.

- **Refresh** asks the provider for its current model list. New models appear disabled. Models that
  disappeared are marked unavailable and kept; they are not offered while unavailable and come back
  on their own if the provider lists them again. A refresh that finds **no** models changes nothing
  and says so, so an outage cannot hide every enabled model. Entries the provider described
  incorrectly are skipped and reported with their reasons.
- **Filter** by task, output kind, enabled state and free text.
- **Enable / Disable** one or many models. Enabling creates the model row and the backend's
  availability row; disabling removes the availability row only.
- **Badges** show unavailable models and models the provider has marked for retirement.

The same actions are available over HTTP (all admin-only):

| Method and path | Purpose |
|---|---|
| `GET /api/cloud/backends/{backend_id}/catalog` | List entries. Query: `task`, `output`, `enabled`, `search`, `limit` (1 to 200), `offset`. |
| `POST /api/cloud/backends/{backend_id}/catalog/refresh` | Refresh from the provider. |
| `POST /api/cloud/backends/{backend_id}/catalog/enable` | Body `{"slugs": ["..."]}`. |
| `POST /api/cloud/backends/{backend_id}/catalog/disable` | Body `{"slugs": ["..."]}`. |

A listing returns `backend_id`, `driver`, `provider` (`key`, `label`, `data_notice`, `supports_cancel`),
`state` (last refresh: `refreshed_at`, `listed`, `skipped`), `counts` (`total`, `enabled`, `missing`),
`total`, `limit`, `offset` and `items`. Each item has `slug`, `provider_model_id`, `label`, `vendor`,
`description`, `tasks`, `outputs`, `enabled`, `available`, `missing_since`, `deprecated`,
`deprecated_at`, `model_id`, `max_outputs_per_job`, `typical_seconds`, `max_seconds`, `params`,
`inputs` and `pricing` (a list of `{unit, usd, applies_to}` with `usd` as a decimal string). A refresh
returns `listed`, `accepted`, `created`, `vanished`, `skipped` (each with `problems`), `empty` and
`message`. Enabling an unknown slug is a 404, enabling a model the provider no longer offers is a 409,
and nothing changes when a request is refused.

### Who can use a model

Cloud models follow the ordinary model access rules: an admin sees every enabled model, other users
only the models assigned to them or to their groups. Restricted-content accounts get cloud models
only through explicit assignment. Assignment, custom names and collections work on the model's page
in **Admin → Models**, exactly as for a local model. Cloud models have no location or hash, so those
panels stay empty.

### Limiting a model to some presets

By default an enabled model is offered in every compatible preset. An admin can restrict one to a
list of presets (a *scope*):

- a model with **no** scope is offered everywhere it is compatible;
- a model with a scope is listed and accepted **only** in the listed presets. Posting it for another
  preset is refused on the server with "is not available in this preset";
- a scope can only name presets that exist and run on the model's own driver;
- a scope row whose preset file has since disappeared is shown as missing. It still keeps the model
  out of every other preset until the admin saves the scope again.

| Method and path | Purpose |
|---|---|
| `GET /api/cloud/models/{model_id}/scope` | Read the scope (admin-only). |
| `PUT /api/cloud/models/{model_id}/scope` | Replace it. Body `{"preset_ids": ["..."]}`; `[]` clears it. |

Both return `model_id`, `slug`, `label`, `driver`, `scoped`, `preset_ids`, `presets` (for each id:
`id`, `title`, `engine`, `driver`, `missing`, `compatible`) and `candidates` (`id`, `title` of every
preset the model could be scoped to). A `PUT` naming an unknown preset, or a preset of another
engine or driver, is a 422 (`cloud_scope_invalid`) listing every problem, and changes nothing.
Scopes apply to cloud models only. Disabling and re-enabling a model keeps its scope.

### Costs

Every cloud job's cost is recorded, and it is shown **to admins only**. No user-facing payload, websocket
message, history entry, gallery item, session or model listing carries a price or a cost.

- Each successful request writes one row with a **source**: `provider` (the amount the provider
  reported), `estimate` (the provider reported none, so the price lines in the catalog were applied)
  or `unknown` (nothing could be estimated; the row has no amount).
- Estimates use the catalog's price lines: `request` once per request, `image` per image produced,
  `megapixel` using the `size` (`WxH`) or the resolution tier (`512`, `1K`, `2K`, `4K`), and `second`
  using `duration_s` or the model's default duration. `token` and `sku` lines cannot be estimated and
  are reported as skipped. A line with `applies_to` applies only when a parameter has that value.
- A cancelled or failed request writes nothing. A request that was billed before a later one was
  cancelled keeps its row.
- Cost rows belong to no generation row, so they survive deleting history. If the full record cannot
  be written, a minimal `unknown` row with the reason is written instead; if even that fails the
  amount is logged at error level so it can be reconciled.

Where admins see it:

- **Admin → Generations**: each item of `GET /api/admin/generations` has `cost` (`null`, or
  `{amount_usd, source, entries, unpriced}` with `source` one of `provider`, `estimate`, `mixed`,
  `unknown`), and the detail at `GET /api/admin/generations/{id}` adds `cost.items` (`id`,
  `backend_id`, `model_id`, `amount_usd`, `source`, `detail`, `created_at`).
- **Spend**: `GET /api/stats/spend?from=YYYY-MM-DD&to=YYYY-MM-DD` (admin-only, dates inclusive and
  optional) returns `total_usd`, `entries`, `unpriced`, `by_backend` and `by_model`, each item with
  `amount_usd`, `source`, `entries` and `unpriced`, largest first. Amounts are decimal strings.

### When a job fails

Failures are classified, so the user sees a plain message and the admin sees the cause:

| Error code | Cause |
|---|---|
| `cloud_auth` | The provider rejected the key. Check the backend's credentials. |
| `cloud_credits` | The provider account is out of credits. |
| `cloud_refused` | The provider refused the request, for example its moderation. |
| `cloud_rate_limited` | Still rate limited after the retries. |
| `cloud_unavailable` | The provider, or the network path to it, was down. |
| `cloud_timeout` | The backend's time limit, or the provider's, passed. |
| `cloud_invalid_request` | The provider rejected the parameters. |
| `cloud_failed`, `cloud_expired` | The job failed, or its result expired, at the provider. |

Cancelling stops the job locally within about a second. If the provider can cancel, PotionUI asks it
to (waiting at most five seconds). If it cannot, or does not confirm, the run says "Stopped waiting.
The provider may still finish this job and bill it." and no result is fetched.

## Presets for cloud models

A cloud preset is an ordinary preset with `engine: cloud` and a `driver:`. It ships inside the
provider's plugin through the manifest `presets:` root, so it is scanned only while the plugin is
enabled.

```yaml
schema: 1
id: "01EXAMPLEACMEPRESET0000000"
name: "Acme Studio"
category: "image"
version: "1.0.0"
engine: "cloud"
driver: "cloud.acme"
modes:
  - txt2img
  - edit
```

`driver:` is required for `engine: cloud`, must look like `<engine>.<name>` and must start with the
preset's own engine. `preset_lint` warns when the driver is not registered on the instance it runs
against (enable the plugin and restart).

Keep one preset per output kind ("Acme Studio" for images, "Acme Motion" for video): a preset files
under a single `category`.

### Forms follow the chosen model

A model's parameters differ, so a cloud form does not hard-code controls. A field declares which
parameter or media input of the chosen model it stands for, with `capability:`:

```yaml
- name: "model"
  type: "model"
  configuration: { model_type: "cloud", tasks: ["txt2img"] }

- name: "aspect_ratio"
  type: "select"
  label: "Aspect ratio"
  capability: { model_field: "model", param: "aspect_ratio" }

- name: "references"
  type: "image"
  capability: { model_field: "model", input: "reference" }

- name: "provider_options"
  type: "cloud_options"
  capability: { model_field: "model" }
  configuration: { include_unbound: true }
```

- `model_field` names the field that holds the model. `param` is one canonical parameter (below) or
  a provider extra `x.<wire name>`; `input` is a media role. Give exactly one of them. A
  `cloud_options` field names only `model_field`.
- `tasks` on the model field keeps only models whose catalog lists one of those tasks; it is sent as
  `?tasks=` to `GET /api/presets/{id}/models` and is ignored for other model types.
- The form schema served to the frontend carries `capability` on each bound field. The frontend reads
  `GET /api/cloud/models/{model_id}/capabilities` (any signed-in user with access to the model; a
  model the user cannot use is a 404, and there are no prices) and hides, fills or constrains each
  bound field accordingly.
- **Server side**, `bind_form` uses the same capabilities, pinned to the backend the job was routed
  to. A value for a parameter or media role the model lacks is dropped (set to `null` and listed in
  `stripped`). An enum value the model does not offer, a number outside its span, a non-integer for
  an integer range, a non-boolean for a boolean, or a missing required parameter is a 422 with a
  `field_errors` entry. The accepted values are available to the pipeline as `BoundForm.cloud_params`.
- `cloud_options` renders every parameter of the model that is an `x.` extra or, with
  `include_unbound: true` (the default), a canonical one no other field is bound to. Its value is an
  object keyed by full parameter name, for example `{"x.style": "noir"}`; unknown keys are dropped
  and values are validated like any other parameter.
- Generation is also re-checked before anything is sent (see "What is checked before a job"), on the
  final form, after any `before_start` plugin hook has run.

`GET /api/cloud/models/{model_id}/capabilities` returns `model_id`, `slug`, `label`, `vendor`,
`description`, `driver`, `tasks`, `outputs`, `max_outputs_per_job`, `deprecated`, `params` (each with
`name`, `kind` of `enum`, `range`, `boolean` or `text`, `values`, `minimum`, `maximum`, `step`,
`integer`, `default`, `required`, `label`, `description`, `tasks`, `extra`), `inputs` (each with
`role`, `modality`, `min_items`, `max_items`, `max_bytes`, `formats`, `tasks`) and `by_task`
(the parameter names and media roles that apply to each task). An empty `tasks` list means "all tasks".

### Canonical parameters and media roles

Core owns one vocabulary so that shared form blocks and every provider adapter agree:

- **Parameters** (`CANONICAL_PARAMS`): `prompt`, `negative_prompt`, `count`, `seed`, `aspect_ratio`,
  `resolution`, `size`, `duration_s`, `fps`, `quality`, `output_format`, `background`, `guidance`,
  `steps`, `strength`, `generate_audio`, `lyrics`, `voice`, `instrumental`, `enhance_prompt`.
- **Provider extras**: any other parameter is `x.<wire name>`.
- **Media roles** (`MEDIA_ROLES`): `reference`, `first_frame`, `last_frame`, `mask`, `source_image`,
  `source_video`, `source_audio`.
- **Tasks** (`TASK_KINDS`): `txt2img`, `img_edit`, `inpaint`, `txt2video`, `img2video`, `ref2video`,
  `video2video`, `txt2audio`, `txt2speech`, `upscale_image`, `upscale_video`.

### Shared blocks and the scaffold

Core ships form fragments under `content/presets/_shared/cloud/`, so a provider's presets stay small.
They are pulled in with `children: "{{ paths._shared }}/cloud/..."` (the `_shared` token works in
`children:` paths of any preset; `preset_lint` checks that the file exists). `CLOUD_BLOCKS` in
`src.plugin_api.cloud` lists them, so a plugin's own tests can assert that its presets reference only
blocks that exist:

- `models/<task>.yml`: a one-field model picker (`model`, `model_type: cloud`) restricted to that task,
  for each task kind;
- `tabs/image.yml`, `tabs/image_edit.yml`, `tabs/video.yml`, `tabs/img2video.yml`: the generation
  controls for each kind, all capability-bound, with shared parts in `tabs/_image_params.yml` and
  `tabs/_video_params.yml`;
- `tabs/provider_options.yml`: an accordion holding one `cloud_options` field.

The blocks use fixed field names (`model`, `count`, `seed`, `aspect_ratio`, `resolution`, `quality`,
`output_format`, `background`, `duration`, `generate_audio`, `strength`, `references`, `first_frame`,
`last_frame`, `provider_options`), which is what lets the standard pipeline work unedited.

Scaffold a preset for a provider with:

```bash
python scripts/preset_new.py Acme/studio --engine cloud --driver cloud.acme --modes txt2img,edit
python scripts/preset_new.py Acme/motion --engine cloud --driver cloud.acme --modes txt2video,img2video --category video
```

`--modes` takes the cloud mode names `txt2img`, `edit`, `txt2video` and `img2video`. Each mode gets a
`form.yml` assembled from the blocks and the standard pipeline:

```text
dynamic_prompts_renderer -> seed_generator -> (media loaders for edit / img2video)
  -> param_emitter -> cloud_generate -> gallery
```

`cloud_generate` (see [pipes.md](pipes.md)) takes `task`, `model` (the slug), `quantity`, `prompts`,
`params` (canonical parameters), `options` (the `cloud_options` object, merged under `params`) and
`roles` (which media input is which role). `preset_lint` checks that an `engine: cloud` mode runs
`cloud_generate`, names a literal `task`, and that the task is among the model field's `tasks`.
Write the `task` as a plain string: the server reads it from the pipeline to check that the model
supports it.

### What is checked before a job

After routing, and before any record is created or anything is sent to the provider, the server checks
the final form:

1. the model is enabled, and still offered, on the backend the job was routed to;
2. the model supports the mode's task;
3. the model is in the preset's scope, if it has one;
4. each media input meets the model's limits: at least `min_items`, at most `max_items`, and an
   accepted `formats` entry (extensions, with `image/png`-style entries and `jpeg`/`jpg` treated alike).

A failure is refused with a plain message such as "'Acme Studio' cannot do img2video. Choose a model
that supports it."

## Writing a provider plugin

Everything a provider plugin imports comes from `src.plugin_api.cloud` (the test kit from
`src.plugin_api.cloud_testing`). A complete provider is four things: a config class, a provider class,
a registration hook and (recommended) presets. The example below is a made-up "Acme" service with a
queue API: submit a job, poll it, download the files.

### 1. The plugin folder

```text
content/plugins/marketplace/acme-provider/
├── manifest.yml
├── hooks/backend_hooks.py
├── provider/acme.py
├── presets/Acme/studio/...        # scaffolded, see above
└── tests/test_acme_contract.py
```

```yaml
id: "acme-provider"
name: "Acme"
version: "1.0.0"
description: "Generate images and video with Acme Studio"
type: "backend-only"
category: "backends"

presets:
  - path: "presets"

hooks:
  backend:
    - hook: "backend.register"
      handler: "hooks.backend_hooks.register_backend"
```

```python
from src.plugin_api import HookContext
from src.plugin_api.cloud import register_cloud_provider


def register_backend(context: HookContext) -> HookContext:
    from ..provider.acme import AcmeProvider

    register_cloud_provider(context, AcmeProvider)
    return context
```

`register_cloud_provider` records the provider and its config class under the driver
`cloud.<provider key>`. It refuses a key that does not match `^[a-z][a-z0-9_-]*$`, a missing `label`,
a config class that is not a `CloudBackendConfig`, a config whose default `driver` is not
`cloud.<key>`, and a second class for the same key. The driver then appears in
`GET /api/backends/engines` as a creatable driver of the `cloud` engine, with the config class's
fields as its admin form.

### 2. The config

```python
from typing import ClassVar, Optional

from pydantic import Field

from src.plugin_api.cloud import CloudBackendConfig


class AcmeConfig(CloudBackendConfig):
    driver: str = Field(default="cloud.acme")
    base_url: str = Field(default="https://api.acme.example/v1", title="API URL")
    api_key: str = Field(default="", title="API key", json_schema_extra={"secret": True})

    engine_label: ClassVar[Optional[str]] = "Acme"
```

`CloudBackendConfig` already provides the `cloud` engine, `timeout_seconds` and `max_parallel`. A field
marked `secret` is stored encrypted and is never returned by the API.

### 3. The provider

```python
from pathlib import Path
from typing import Any, ClassVar, Mapping, Optional

from src.plugin_api.cloud import (
    CloudArtifact, CloudError, CloudHealth, CloudJob, CloudModelSpec, CloudProvider, CloudRequest,
    CloudResult, CloudStatus, MediaInputSpec, ParamSpec,
)
from .config import AcmeConfig


class AcmeProvider(CloudProvider):
    key: ClassVar[str] = "acme"
    label: ClassVar[str] = "Acme"
    config_class: ClassVar[type] = AcmeConfig
    data_notice: ClassVar[str] = (
        "Prompts and input images are sent to Acme. Acme keeps results for 24 hours; PotionUI downloads "
        "them immediately."
    )
    supports_cancel: ClassVar[bool] = True
    idempotent_submit: ClassVar[bool] = False

    @classmethod
    def api_base_url(cls, config: AcmeConfig) -> str:
        return config.base_url

    @classmethod
    def auth_headers(cls, config: AcmeConfig) -> Mapping[str, str]:
        return {"Authorization": f"Bearer {config.api_key}"}

    async def check(self) -> CloudHealth:
        await self.http.request_json("GET", "/account")
        return CloudHealth(ok=True)

    async def discover(self) -> list[CloudModelSpec]:
        payload = await self.http.request_json("GET", "/models")
        return [self._spec(item) for item in payload["models"]]

    async def submit(self, request: CloudRequest) -> CloudJob:
        body = {"model": request.model.provider_model_id, "prompt": request.prompt, "n": request.count,
                **{name: value for name, value in request.params.items()}}
        if request.seed is not None:
            body["seed"] = request.seed
        created = await self.http.request_json("POST", "/jobs", json=body)
        return CloudJob(job_id=created["id"], handle={"id": created["id"]}, poll_after_s=3)

    async def poll(self, job: CloudJob) -> CloudStatus:
        data = await self.http.request_json("GET", f"/jobs/{job.handle['id']}")
        if data["state"] == "done":
            artifacts = tuple(
                CloudArtifact(modality="image", index=index, url=url, media_type="image/png")
                for index, url in enumerate(data["files"])
            )
            return CloudStatus(state="succeeded", result=CloudResult(artifacts=artifacts))
        if data["state"] == "failed":
            return CloudStatus(state="failed", message=data.get("reason"))
        return CloudStatus(state="running", progress=data.get("progress"))

    async def cancel(self, job: CloudJob) -> bool:
        await self.http.request("DELETE", f"/jobs/{job.handle['id']}")
        return True

    def _spec(self, item: Mapping[str, Any]) -> CloudModelSpec:
        ...
```

What each method owes the platform:

| Method | Contract |
|---|---|
| `discover()` | Return one `CloudModelSpec` per model. Invalid specs are skipped and reported (see `spec_problems`), never enabled. |
| `submit(request)` | Start the job and return a `CloudJob`. For a synchronous API return the finished `CloudResult` in `CloudJob.result`; otherwise put what `poll` needs in `handle` and optionally a first `poll_after_s`. |
| `poll(job)` | Return a `CloudStatus` with `state` of `queued`, `running`, `succeeded`, `failed`, `cancelled` or `expired`. `progress` is 0 to 1 or `None`, and a success carries its `CloudResult`. States never go backwards. |
| `fetch(artifact, dest, max_bytes=...)` | The default writes inline `data` or downloads `url` with the API origin's auth rules. Override it when a result needs special headers. |
| `cancel(job)` | Try to stop the job and return whether the provider confirmed it. Leave `supports_cancel = False` when the API cannot cancel. |
| `check()` | A cheap call for the backend's health; a `CloudHealth`. |
| `map_error(status, headers, body)` | Optional: turn a provider error body into a `CloudError`. Return `None` to fall back to the default mapping. |
| `api_base_url(config)`, `auth_headers(config)` | Class methods: where the API lives and the headers that authenticate it. |

#### Model specs

A `CloudModelSpec` describes one model in normalized terms: `provider_model_id`, `label`, `vendor`,
`description`, `tasks`, `outputs` (`image`, `video`, `audio`), `params` (`ParamSpec`: `name`, `kind`,
`values`, `minimum`, `maximum`, `step`, `integer`, `default`, `required`, `label`, `description` and
`tasks`), `inputs` (`MediaInputSpec`: `role`, `modality`, `min_items`, `max_items`, `max_bytes`,
`formats`, `tasks`), `max_outputs_per_job`, `pricing` (`PriceLine`: `unit` of `request`, `image`,
`megapixel`, `second`, `token` or `sku`, `usd` as a `Decimal`, optional `applies_to`),
`typical_seconds`, `max_seconds`, `deprecated_at` and `raw`. Express whatever the provider's schema
language is in the four parameter kinds: a fixed choice is `enum`, a bounded number `range`, a flag
`boolean`, free text `text`. Use canonical parameter names wherever one fits and `x.<wire name>`
otherwise. A parameter or input with `tasks` applies only to those tasks. Prices are shown to admins
only.

#### Requests and results

`CloudRequest` carries the `model` spec, the `task`, `prompt`, `negative_prompt`, `seed` (`None` for
random), `count`, the canonical `params`, `inputs` (role to a list of `LocalMedia` files on disk) and
a `client_reference`. A `CloudResult` holds `artifacts` (a `CloudArtifact` has `modality`, `index` and
either inline `data` or a `url`, plus `media_type`), an optional `cost` (`CloudCost`: `amount_usd`,
`source` of `provider` or `estimate`), `seed_used` and `provider_job_id`. Report the cost whenever the
provider returns one. Leave it out otherwise and the estimate from the catalog prices is used.

#### Errors

Raise `CloudError(kind, user_message, detail=..., retry_after_s=..., request_sent=...)`. `user_message`
is shown to the user, so keep it plain and free of secrets; `detail` is for the admin and is redacted
and capped. The kinds and what the platform does with them:

| Kind | Meaning | Default HTTP mapping |
|---|---|---|
| `auth` | Bad or missing credentials | 401 |
| `credits` | Out of credits | 402 |
| `refused` | Policy or moderation refusal | 403 |
| `timeout` | The provider took too long | 408, 504 |
| `rate_limited` | Too many requests | 429 (honours `Retry-After`) |
| `unavailable` | The service is down | 5xx, network failure |
| `invalid_request` | The parameters were rejected | 400, 404, 405, 409, 413, 415, 422 |
| `failed` | Anything else | other statuses |
| `expired` | The result is gone | raise it yourself |

`CloudHttp` applies this mapping for you; `map_error` only has to cover provider-specific bodies.

**Retries never bill twice.** While submitting, a `rate_limited` error is retried (up to three
attempts, waiting for `retry_after_s` or backing off). An `unavailable` error is retried only when
`request_sent` is `False` (the connection never opened, so nothing reached the provider) or the
provider class sets `idempotent_submit = True` (resubmitting is harmless). A `timeout` or any other
failure after the request was sent is never resubmitted, because the job may exist and be billed.
Set `idempotent_submit` only if your API really deduplicates.

**Polling and time.** The first poll comes after `poll_after_s` (or two seconds); after that the wait
grows by half each time up to 30 seconds unless a status gives its own `poll_after_s`. Polling and
download errors worth retrying (rate limited, unavailable, timeout) are retried with a growing pause; five in a row fail the job, and any other error fails it at once. The job's deadline is the backend's `timeout_seconds`, or the
model's `max_seconds` if that is shorter; when it passes the job is cancelled at the provider (if
supported) and fails as `cloud_timeout`. Results are size-capped (64 MB per image, 256 MB per audio
file, 2 GB per video), downloaded to a scratch folder, and deleted after they are stored.

#### `CloudHttp`: the only way a provider talks to the network

Use `self.http` for every request. It is built from `api_base_url` and `auth_headers` and enforces the
rules that keep credentials safe:

- **Auth goes only to the API's own origin** (same scheme, host and port as `api_base_url`). A result
  URL on another host is fetched without your `Authorization` header. Do not add credentials to such
  requests yourself.
- **Private targets are refused for downloads.** A result URL that resolves to a loopback, private,
  link-local or reserved address is refused, so a hostile provider cannot aim PotionUI at your
  network. Downloads and `GET`/`HEAD` calls follow redirects one hop at a time (at most five), with the
  same check on every hop; other methods never follow a redirect.
- **Secrets are redacted.** Sensitive headers and query strings never reach logs, `detail` or
  `user_message`; values of your `auth_headers` are scrubbed from error text.
- **Every call has a timeout**, runs through the backend's `max_parallel` slots and a rate limiter, and
  a 429 pauses the whole backend for `Retry-After`.
- **TLS** uses the platform's certificate handling, which works on Windows, macOS and Linux.

The methods are `request(method, target, json=, params=, headers=, timeout_s=)` (returns an
`HttpResponse` with `status`, `headers`, `text()` and `json()`), `request_json(...)` (returns the parsed
body) and `download(url, dest, auth_headers=, max_bytes=)`. Error statuses raise `CloudError`; a
relative `target` is joined to the base URL.

### 4. Presets

Scaffold them as shown above, add your provider's own tab if it has controls the blocks lack, and
declare `driver: cloud.acme`. A test can assert that the presets use only known blocks:

```python
from src.plugin_api.cloud import CLOUD_BLOCKS

def test_presets_reference_known_blocks(preset_files):
    for text in preset_files:
        for line in text.splitlines():
            if "paths._shared }}/cloud/" in line:
                block = line.split("/cloud/", 1)[1].strip().strip('"\'')
                assert block in CLOUD_BLOCKS
```

### 5. Testing without the network

`src.plugin_api.cloud_testing` provides the contract test kit and a scripted provider:

- `FakeCloudProvider` (driver `cloud.fake`) plays back sync and async timelines, every error kind, and
  cancel supported or not, with a fake clock (`FakeClock`). It lets your plugin's own tests, and core
  tests, run generation end to end without any service.
- `ContractCase`, `CONTRACT_CHECKS`, `run_contract` and `applicable_checks` run the same checks against
  your provider: discovery returns valid specs, a sync submit returns a result, an async job reaches
  success and failure, `fetch` writes the files, cancel matches `supports_cancel`, and each error
  probe maps to its kind.

Run the kit against your provider talking to a loopback server that replays recorded JSON (the test
network guard allows loopback only):

```python
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.plugin_api.cloud import CloudHttp
from src.plugin_api.cloud_testing import (
    SCENARIO_ASYNC, SCENARIO_ASYNC_FAILED, SCENARIO_CANCEL, SCENARIO_SYNC, ContractCase, run_contract,
)
from ..provider.acme import AcmeProvider
from ..provider.config import AcmeConfig


@pytest.fixture
async def server():
    app = web.Application()
    app.add_routes(acme_routes())
    async with TestServer(app) as running:
        yield running


async def test_acme_passes_the_contract(server, tmp_path):
    def make_provider(scenario: str) -> AcmeProvider:
        config = AcmeConfig(id="acme-1", name="Acme", base_url=str(server.make_url("/v1")), api_key="sk-test")
        http = CloudHttp.for_provider(AcmeProvider, config, allow_private_targets=True)
        return AcmeProvider(config, http)

    async def advance(seconds: float) -> None:
        await server.app["timeline"].advance(seconds)

    case = ContractCase(
        make_provider=make_provider,
        advance=advance,
        scenarios=(SCENARIO_ASYNC, SCENARIO_ASYNC_FAILED, SCENARIO_CANCEL),
    )

    ran = await run_contract(case, tmp_path)

    assert "async_poll_reaches_success" in ran
```

`scenarios` lists what your recorded fixtures can play, and checks that need another scenario are
skipped (`applicable_checks` tells you which run). Supply `error_probes` (a mapping of error kind to an
async callable that provokes it) to run the error-mapping check, and a `make_request` function if the
default request (the first task of the first discovered model) does not suit your API. Also assert in
your own tests that an auth header is never sent to a foreign download host.

### 6. Try it

1. Enable the plugin and restart; the driver shows in **Admin → Backends → Add**.
2. Create the backend, refresh the catalog, enable a model.
3. Open the provider's preset on the Generate page, pick the model and generate.
4. Check **Admin → Generations** for the cost and **Admin → Stats** spend.

## Checklist for provider authors

- Every network call goes through `self.http`; no other HTTP client, no credentials in URLs or logs.
- `discover()` returns specs that pass `spec_problems`, with canonical names where they exist.
- Errors are `CloudError` with a plain `user_message`; `request_sent` is correct; `idempotent_submit`
  is `False` unless the API deduplicates.
- Cost is reported when the provider returns it, and the catalog carries price lines otherwise.
- `data_notice` says what is sent, where it may be routed and how long results are kept.
- The contract kit passes against recorded fixtures; presets use only `CLOUD_BLOCKS` and lint clean.

## Related

- [Backends and Engines](backends.md): engines, drivers, `max_parallel` and the admin API.
- [Generation Routing](generation-routing.md): the `PresetDriver` rule.
- [The Plugin API](plugin-api.md): the `src.plugin_api.cloud` surface.
- [Preset forms](presets/forms.md) and the [preset manifest](presets/manifest.md): `capability:`,
  `cloud_options` and `driver:`.
- [Pipes](pipes.md): the `cloud_generate` pipe.
