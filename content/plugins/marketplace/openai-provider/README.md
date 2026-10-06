# OpenAI

Generate and edit pictures with OpenAI's GPT Image models, with no graphics card of your own. This
page walks you from a fresh install to your first generation, then covers costs and the usual
problems.

## Getting started

### What the plugin adds

- **An OpenAI backend.** You add it once, in Administration → Backends. It holds your API key and
  limits.
- **OpenAI's image models**, listed on the backend's Catalog tab. You choose which ones people may
  use.
- **One preset, OpenAI Images**, with two modes:
  - **Text to image**: describe a picture and get it back.
  - **Edit**: add the picture to change, optionally paint a mask on it so only that area changes,
    add more pictures as references if you like, and describe the change.

> Every generation runs on OpenAI and is **billed to your OpenAI account**, whoever in PotionUI
> starts it. The prompt and any pictures a user adds leave your server to do so.

### Before you start

1. An account on [platform.openai.com](https://platform.openai.com).
2. An API key: on platform.openai.com, open **API keys** and create one. Copy it now; you will paste
   it into PotionUI in a moment. A project key (`sk-proj-...`) works; if you restrict its
   permissions, allow **Images** (write) and, ideally, **Models** (read).
3. Billing: add a payment method or credit under **Billing**. Without it, generations fail.
4. Organization verification: OpenAI asks some organizations to verify before they may use the GPT
   Image models. If generations fail with "the organization may need to be verified", do this under
   **Settings → Organization → General** on platform.openai.com.

### Step 1: Enable the plugin

1. Go to **Administration → Plugins** and open **OpenAI**.
2. Turn on the switch at the top of the plugin's page (**Enable plugin**).
3. The plugin's page now shows a **Setup** section with the next step first, and the plugin's card
   says **Setup needed** until every step is done. Each step has a button that takes you there.
4. **Restart PotionUI.** The OpenAI backend type is only picked up when PotionUI starts, so until
   you restart, OpenAI is missing from the list in the next step. Choose **Restart now** in the Setup
   section. Generations that are running get interrupted.
   - If you started PotionUI with `./potionui start`, you can also run `./potionui stop` and then
     `./potionui start`.

The preset and this guide appear as soon as the plugin is enabled; only the backend needs the
restart.

### Step 2: Add the OpenAI backend

1. Choose **Add backend** in the plugin's Setup section; the form opens with **OpenAI** picked as the
   engine. (Or go to **Administration → Backends**, choose **Add backend** and pick **OpenAI** under
   **Engine**.)
2. Give it a **Name** (for example "OpenAI").
3. Fill in the fields below, then choose **Create Backend**.

| Field | What to put there |
|---|---|
| **API key** | The key from platform.openai.com. It is stored encrypted and never shown again; to change it, paste a new one. |
| **API address** | Leave the default (`https://api.openai.com/v1`). Change it only to reach OpenAI through a proxy. |
| **Organization ID** | Optional. When your key belongs to several organizations, the one to bill (`org-...`). Leave empty to use the key's default. |
| **Project ID** | Optional. The project to bill (`proj_...`). Leave empty to use the key's default project. |
| **Content filter** | `auto` (the default) is OpenAI's standard filter. `low` filters less strictly. Your own content safety settings in PotionUI apply either way. |
| **Send an anonymous user id** | Off by default, and then no user field is sent at all. When on, OpenAI gets a different anonymous id for each PotionUI user, which helps OpenAI tell users apart when it looks into abuse, without knowing who they are. Nobody can trace the id back to a person without your server's secret key. |
| **Parallel jobs** | How many generations this backend runs at the same time (1 to 32, default 4). Any more wait in the queue. Keep it within your OpenAI rate limit: tier 1 allows 5 images a minute. |
| **Timeout (seconds)** | How long one generation may take before PotionUI gives up. OpenAI can take up to about two minutes for a large, high quality picture, so keep at least 300. |

Leave **Priority**, **Enabled** and the **Scheduling** settings as they are.

### Step 3: Choose the models

1. Open the new backend and go to its **Catalog** tab.
2. Choose **Refresh catalog**. PotionUI asks OpenAI which models your key may use and lists the image
   models among them.
3. **Nothing is enabled by default.** Turn on the **Enabled** switch for each model you want people to
   use.

The models, as OpenAI lists them on 2026-10-06:

| Model | Good for | Sizes | Transparent background |
|---|---|---|---|
| **GPT Image 2.5 Sunburst** (`gpt-image-2.5-sunburst`, suggested) | The most capable model, best for precise edits. | Any aspect ratio from 1:3 to 3:1, at 1K, 2K or 4K | Yes |
| **GPT Image 2.5 Flare** (`gpt-image-2.5-flare`, suggested) | Fast, high quality everyday pictures. | Any aspect ratio from 1:3 to 3:1, at 1K, 2K or 4K | Yes |
| **GPT Image 2** (`gpt-image-2`) | The previous generation. | 1:1, 3:2, 2:3 | No |
| **GPT Image 1.5** (`gpt-image-1.5`) | Older. OpenAI removes it on 2026-12-01. | 1:1, 3:2, 2:3 | Yes |
| **GPT Image 1 Mini** (`gpt-image-1-mini`) | Older and cheaper. OpenAI removes it on 2026-12-01. | 1:1, 3:2, 2:3 | Yes |

- **Price** shows OpenAI's list price per million tokens for text input, picture input and picture
  output. Only admins see prices; nobody else does, anywhere in PotionUI.
- Models OpenAI plans to retire carry the **Deprecated** badge.
- The **OpenAI data notice** at the top says what leaves your server when someone generates.

### Step 4: Make the preset available

Presets have to be installed and given to people, and that includes you: the Generate page only lists
presets assigned to your account.

1. Go to **Administration → Presets** and open **OpenAI Images**.
2. Choose **Install preset**.
3. On its **Access** tab, choose **Add user** next to yourself and everyone else who should use it,
   or switch to **Groups** and choose **Add group**.

### Step 5 (optional): Control who uses which model

- **Who can use a model.** As an admin, you can use every enabled model. Everyone else only sees the
  models given to them: go to **Administration → Models**, find the model, open its **Access** tab and
  add users or groups.
- **Which presets offer a model.** On the model's **Overview** tab, **Allowed in presets** limits the
  model to the presets you pick.
- **Content safety still applies.** Your settings under **Administration → System Settings → Content
  Safety** also cover OpenAI generations.

### Step 6: Generate

1. Open **Generate**, choose **Choose a preset** and pick **OpenAI Images**.
2. Pick a mode, then a **model**. Only models enabled in the catalog and allowed for you are listed.
3. The form only shows the settings the chosen model supports:
   - **Aspect ratio** (and **Resolution** on the 2.5 models). `auto` lets OpenAI pick, and in edit
     mode keeps the shape of your picture.
   - **Quality**: `low` is quickest and cheapest; the 2.5 models also offer `xhigh` and `max`.
   - **File format** and **Transparent background**. A transparent background needs PNG or WebP.
   - **Quantity**: up to 10 pictures come back from one request; more are split into several.
4. **Provider options** holds **Compression** (for JPEG and WebP files) and, on the older models,
   **Match the input pictures** for edits.
5. In edit mode, open the **References** tab:
   - **Picture to edit** is the one the model changes. To change only part of it, choose **Create
     inpainting mask** on the picture and paint over the area to change. PotionUI turns your painting
     into the transparent mask OpenAI expects, at the size of the picture.
   - **More reference pictures** are optional: up to 15 more pictures the model may draw on, for
     example a product or a person to bring into the scene.
6. Write your prompt and generate. OpenAI takes no seed and no negative prompt, so the same prompt
   gives a different picture each time.

**Cancelling.** OpenAI has no way to stop a picture once it is asked for. When you choose **Cancel
generation**, PotionUI stops waiting straight away and tells you: "Stopped waiting. The provider may
still finish this job and bill it."

### Costs

Only admins see costs.

- **Administration → Generations** has a **Cost** column. Open a generation to see its **Cost** panel
  with one line per request.
- **Administration → Stats** shows **Cloud spend** for the date range you pick.

Where the numbers come from: OpenAI reports how many tokens each request used (text in, pictures in,
picture out) but not what it cost. PotionUI multiplies those tokens by the list prices in the catalog
and records the amount marked **est.**, with the token counts in the detail. If OpenAI reports no
token counts, the request is counted without an amount. Your OpenAI billing page is the final word on
what you were billed.

### Troubleshooting

| What you see | What to do |
|---|---|
| The plugin is enabled, but **OpenAI** is not in the **Engine** list when adding a backend. | Restart PotionUI (see Step 1). |
| **OpenAI Images** is not in **Choose a preset** on Generate. | Install it and add yourself (or the user) on the preset's **Access** tab (Step 4). |
| Users see "OpenAI rejected the API key. Check it in Administration, Backends." | The key is wrong, revoked or missing. Open the backend, paste a new **API key** and save. |
| Users see "The OpenAI account is out of credit or over its spending limit." | Add credit or raise the limit under **Billing** on platform.openai.com. |
| Users see "The model's content filter refused this request. Try a different prompt or picture." | OpenAI's safety system refused the prompt or a picture. As an admin, open the generation in **Administration → Generations**: its **Failure** panel lists the stage and categories OpenAI gave, and the request id. Users never see those details. |
| Users see "OpenAI refused this request. The key or project may not be allowed to use this model, or the organization may need to be verified." | Verify the organization (see Before you start), check the key's permissions, or pick another model. The Failure panel has OpenAI's own words. |
| Users see "OpenAI is rate limiting requests." | Your tier's images per minute ran out. PotionUI waits as long as OpenAI asks and tries again; lower **Parallel jobs** if it keeps happening. |
| Users see "A transparent background needs the PNG or WebP file format." | Pick PNG or WebP, or turn off the transparent background. Nothing was sent or billed. |
| The refresh lists fewer models than the table above. | The key may not use the missing ones (organization verification, project limits). If the key may not list models at all, the whole catalog is offered and the health check says so. |

## For testing and troubleshooting: technical notes

The plugin registers the backend driver `cloud.openai` on the `cloud` engine. Model slugs are
`openai~<model id>`, for example `openai~gpt-image-2.5-flare`. The general mechanics of cloud models
are described in the **Cloud Models** page of the Developer documentation.

### Sources

Read on 2026-10-06:

- Models: <https://developers.openai.com/api/docs/models/all>,
  <https://developers.openai.com/api/docs/models/gpt-image-2.5-sunburst>,
  <https://developers.openai.com/api/docs/models/gpt-image-2.5-flare>,
  <https://developers.openai.com/api/docs/models/gpt-image-2>
- Image API guide (sizes, transparency, masks, quality): <https://developers.openai.com/api/docs/guides/image-generation>
- Image edit reference: <https://developers.openai.com/api/reference/python/resources/images/methods/edit>
- Pricing: <https://developers.openai.com/api/docs/pricing>
- Errors and rate limits: <https://developers.openai.com/api/docs/guides/error-codes>,
  <https://developers.openai.com/api/docs/guides/rate-limits>
- Deprecations (GPT Image 1.x, DALL·E, Sora): <https://developers.openai.com/api/docs/deprecations>
- Model listing: <https://developers.openai.com/api/reference/resources/models/methods/list>

### What the plugin assumes about the OpenAI API

- OpenAI's model listing (`GET /models`) says which models a key may use, but nothing about what they
  can do or cost. The capabilities and prices therefore come from `cloud_models.yml` in this plugin,
  checked against the docs above on the date in its `checked:` line. A refresh offers the catalog
  models the listing names; if the listing is refused (a key without the Models permission), the
  whole catalog is offered. A `shutdown_date` in the listing marks a model deprecated.
- Text to image is `POST /images/generations` with a JSON body. Edit is `POST /images/edits` as
  `multipart/form-data`: every picture as an `image[]` part (at most 16, each under 50 MB, PNG, JPEG
  or WebP) and the mask as one `mask` part. The Responses API image tool is not used: it is meant for
  chat style flows, and the Image API is the documented path for single generations.
- The mask is made from the painted mask PotionUI stores (white where to change, black elsewhere):
  white becomes fully transparent and the rest opaque, resized to the first picture's size, as a PNG
  under 4 MB. OpenAI applies the mask to the first picture.
- Sizes: the 2.5 models take any `WIDTHxHEIGHT` in multiples of 16, edges up to 3840, 655,360 to
  8,294,400 pixels, aspect between 1:3 and 3:1. PotionUI turns the aspect ratio and the resolution
  into such a size: 1K and 2K set the short edge to 1024 or 2048, 4K uses the most pixels allowed.
  The other models get `1024x1024`, `1536x1024` or `1024x1536`. `auto` sends no size.
- `background: "transparent"` is sent only when asked for; nothing is sent otherwise. The docs list
  transparency for the 2.5 models; the GPT Image 1.x models have long supported it; GPT Image 2 is
  assumed not to.
- `output_compression` is sent only for JPEG and WebP, `input_fidelity` only for edits on the 1.x
  models, `moderation` only when the backend's Content filter is `low`, and `user` only when the
  anonymous user id is switched on. No seed or negative prompt is sent: the API takes neither.
- The answer is `data[]` with `b64_json`, the `output_format`, and `usage` with `input_tokens`,
  `input_tokens_details.text_tokens` and `image_tokens`, and `output_tokens`. The cost is
  `text tokens × text input price + picture input tokens × image input price + output tokens × image
  output price`. Cached input prices are not used: for GPT Image 2 and 2.5 they apply only through
  the Responses API. The `x-request-id` header is kept as the job id.
- Errors come as `{"error": {"message", "type", "param", "code"}}`. `code: moderation_blocked` (also
  `content_policy_violation`, or an `image_generation_user_error` naming the safety system) is a
  refusal: its code, `moderation_details.moderation_stage`, `categories` and the request id are kept
  in the admin-only detail, and the message is not. `insufficient_quota` and billing codes mean out of
  credit; 401 is the key; 403 is a refused key, project or unverified organization; 429 is a rate
  limit, waited out by `Retry-After` or else the `x-ratelimit-reset-requests` / `-tokens` headers;
  500 and 503 are OpenAI being down or overloaded.
- The key, organization and project go only to the API address.
- Video: OpenAI removed the Sora Videos API on 2026-09-24, so the plugin offers no video.

### Trying it with a real key

This run needs an OpenAI account with billing set up, so it is done by hand and is not part of the
tests.

1. Enable the plugin (Administration, Plugins) and restart once.
2. Add an OpenAI backend (Administration, Backends) with your key, and save.
3. Install "OpenAI Images" (Administration, Presets) and add yourself on its Access tab.
4. Open the backend's Catalog tab and refresh. Check which models are listed.
5. Enable GPT Image 2.5 Flare and GPT Image 2.
6. Generate in text to image mode at 16:9 2K, with a transparent PNG background, and with a count of 2.
   Check the pictures and the Cost panel in Administration → Generations (amount marked est.).
7. Generate in edit mode with one picture and no mask, then with a painted mask (only the painted
   area should change), then with two more reference pictures.
8. Check that a prompt OpenAI refuses shows the plain message to the user and the stage and
   categories in the admin Failure panel.
9. With the anonymous user id switched on, generate once more and check the request carries `user`
   (for example through a proxy log); with it off, check it does not.
