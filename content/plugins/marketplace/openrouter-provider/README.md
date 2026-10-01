# OpenRouter

Generate pictures and short videos with models hosted on [OpenRouter](https://openrouter.ai), with no
graphics card of your own. This page walks you from a fresh install to your first generation, then
covers costs and the usual problems.

## Getting started

### What the plugin adds

- **An OpenRouter backend.** You add it once, in Administration → Backends. It holds your API key and
  limits.
- **OpenRouter's model catalog.** The image and video models OpenRouter offers, listed on the
  backend's Catalog tab. You choose which ones people may use.
- **Two presets:**
  - **OpenRouter Images**: text to image, and editing pictures you add.
  - **OpenRouter Video**: text to video, and image to video from a start picture.

> Every generation runs on OpenRouter and is **billed to your OpenRouter account**, whoever in
> PotionUI starts it. The prompt and any pictures a user adds leave your server to do so.

### Before you start

1. An account on [openrouter.ai](https://openrouter.ai).
2. An API key: on openrouter.ai, open **Keys** and create one. Copy it now; you will paste it into
   PotionUI in a moment.
3. Credits: buy some on openrouter.ai under **Credits**. Without credits, generations fail.

### Step 1: Enable the plugin

1. Go to **Administration → Plugins** and open **OpenRouter**.
2. Turn on the switch at the top of the plugin's page (**Enable plugin**).
3. **Restart PotionUI.** The OpenRouter backend type is only picked up when PotionUI starts, so until
   you restart, OpenRouter is missing from the list in the next step.
   - If you started PotionUI with `./potionui start`, run `./potionui stop` and then
     `./potionui start`.
   - Or restart from inside PotionUI: go to **Administration → Backends**, open the built-in
     **Local Generation** backend (engine Native), go to its **Optimizations** tab and choose
     **Restart app**. Generations that are running get interrupted.

The two presets and this guide appear as soon as the plugin is enabled; only the backend needs the
restart.

### Step 2: Add the OpenRouter backend

This is where all of the OpenRouter settings live.

1. Go to **Administration → Backends** and choose **Add backend**.
2. Under **Engine**, choose **OpenRouter** first. Changing the engine clears the form, so fill in the
   rest afterwards.
3. Give it a **Name** (for example "OpenRouter").
4. Fill in the fields below, then choose **Create Backend**.

| Field | What to put there |
|---|---|
| **API key** | The key from openrouter.ai. It is stored encrypted and never shown again; to change it, paste a new one. |
| **API address** | Leave the default (`https://openrouter.ai/api/v1`). Change it only to reach OpenRouter through a proxy. |
| **App name** | The name OpenRouter shows for this app in its usage statistics. Defaults to "PotionUI". |
| **App address** | The web address OpenRouter shows for this app, `https://potionui.com` by default. Together with **App name** it lists PotionUI on OpenRouter's app rankings. Requests carry only these two values, nothing about who uses the app. Leave it empty to send none. |
| **Allowed upstream providers** | Optional. A comma separated list of the providers OpenRouter may send requests to, named as OpenRouter names them. Leave it empty to allow all of them. |
| **Send an anonymous user id** | Off by default. When on, OpenRouter gets a different anonymous id for each PotionUI user, so it can tell them apart without knowing who they are. Nobody can trace the id back to a person without your server's secret key. |
| **Parallel jobs** | How many generations this backend runs at the same time (1 to 32, default 4). Any more wait in the queue. |
| **Timeout (seconds)** | How long one generation may take before PotionUI gives up. The form starts at 300 (5 minutes). That is fine for pictures, but videos can take longer, so if you plan to use video, set it to something like 1800 (30 minutes). |

Leave **Priority**, **Enabled** and the **Scheduling** settings as they are.

### Step 3: Choose the models

1. Open the new backend and go to its **Catalog** tab.
2. Choose **Refresh catalog**. PotionUI asks OpenRouter for its current image and video models. The
   line next to the button then shows when the catalog was last refreshed and how many models are
   enabled.
3. **Nothing is enabled by default.** Turn on the **Enabled** switch for each model you want people to
   use. To enable several at once, select their rows and choose **Enable** in the bar that appears.

Things that help you choose:

- **Search and filters.** Search by name, or open the filters to narrow the list by **Task** (for
  example text to video), **Output** (image or video) or **Show** (all models, or enabled only).
- **Suggested** marks a few models that are a good place to start.
- **Price** shows what OpenRouter lists for each model, for example per image or per second of video.
  Prices are OpenRouter's and can change. Only admins see prices; nobody else does, anywhere in
  PotionUI.
- Badges mark models OpenRouter has stopped listing (**Missing from provider**) and models it plans
  to retire (**Deprecated**).
- The **OpenRouter data notice** at the top says what leaves your server when someone generates.

Refresh again from time to time to pick up new models. A refresh never enables anything on its own.

### Step 4: Make the presets available

Presets have to be installed and given to people, and that includes you: the Generate page only lists
presets assigned to your account.

1. Go to **Administration → Presets** and open **OpenRouter Images**.
2. Choose **Install preset**.
3. On its **Access** tab, choose **Add user** next to yourself and everyone else who should use it,
   or switch to **Groups** and choose **Add group**.
4. Do the same for **OpenRouter Video** if you want video.

### Step 5 (optional): Control who uses which model

- **Who can use a model.** As an admin, you can use every enabled model. Everyone else only sees the
  models given to them. Go to **Administration → Models**, find the model by name, open its
  **Access** tab, and add users or groups there. Until you do, the model is not offered to them.
- **Which presets offer a model.** On the model's **Overview** tab, the **Allowed in presets** panel
  limits the model to the presets you pick. With no presets chosen, the model is offered in every
  preset that can use it. Save your changes with the bar at the bottom.
- **Content safety still applies.** Your settings under **Administration → System Settings → Content
  Safety** also cover OpenRouter generations. A prompt with a banned word is stopped before anything
  is sent to OpenRouter, and results go through the same checks as any other generation.

### Step 6: Generate

1. Open **Generate**, choose **Choose a preset** and pick **OpenRouter Images** or **OpenRouter Video**.
2. Pick a mode: text to image or edit for pictures, text to video or image to video for video.
3. Pick a **model**. Only models enabled in the catalog, allowed for you and able to do this mode are
   listed.
4. The form only shows the settings the chosen model supports. Pick another model and the controls
   change with it.
5. Some models have extra settings of their own. They are on the **Provider options** tab, which
   only shows when field visibility is set to Advanced: open the three-dot **More view options** menu
   next to the tabs and choose **Advanced** under **Field visibility**.
6. Write your prompt, add pictures if the mode needs them, and generate.

**Pictures** come back from a single request, usually quickly.

**Videos take longer**, often a few minutes. PotionUI sends the job to OpenRouter, checks on it after
5 seconds and then every 30 seconds, and downloads the video when it is ready. While you wait, the
progress line shows "Waiting at the provider" (with a queue position when OpenRouter gives one), then
"Generating" with the time so far, then "Downloading the result". You can keep working in the
meantime.

**Cancelling.** OpenRouter offers no way to stop a video job. When you choose **Cancel generation**,
PotionUI stops waiting straight away and tells you: "Stopped waiting. The provider may still finish
this job and bill it." Check your OpenRouter activity page if you need to know whether it was billed.

### Costs

Only admins see costs.

- **Administration → Generations** has a **Cost** column. Open a generation to see its **Cost** panel
  with one line per request.
- **Administration → Stats** shows **Cloud spend** for the date range you pick: the total, by backend
  and by model. It appears once there is spend in that range.

Where the numbers come from: when OpenRouter reports what a request cost, PotionUI records that
amount (labelled **Reported**). When it does not, PotionUI estimates the cost from the catalog prices
and marks the amount **est.** A job with no known price is counted but adds nothing to the total. Your
OpenRouter account is the final word on what you were billed.

### Troubleshooting

| What you see | What to do |
|---|---|
| The plugin is enabled, but **OpenRouter** is not in the **Engine** list when adding a backend. | Restart PotionUI (see Step 1). |
| The OpenRouter presets are not in **Choose a preset** on Generate. | Install them and add yourself (or the user) on the preset's **Access** tab (Step 4). |
| Users see "The provider rejected the API key. Check it in Administration, Backends." | The key is wrong, revoked or missing. Open the backend, paste a new **API key** and save. |
| Users see "The OpenRouter account is out of credits. An administrator can add credits at openrouter.ai." | Buy credits on openrouter.ai. Nothing else needs changing. |
| Users see "The model's content filter refused this request. Try a different prompt or picture." | The model's own filter refused the prompt or picture. As an admin, open the generation in **Administration → Generations**: its **Failure** panel lists the reasons OpenRouter gave. Users never see those details. |
| Users see "OpenRouter refused this request. The key may not be allowed to use this model." | Check the key's limits on openrouter.ai, or pick another model. |
| Users see "No provider could serve this request with the current routing settings." | **Allowed upstream providers** is too narrow for this model. Add providers or clear the field. |
| **Refresh catalog** shows an error. | Usually the key or the network. Check the **API key**, check that **API address** is the default, and make sure the backend is enabled ("is not active. Enable it first."). The red message says what went wrong. |
| A refresh says "The provider returned no models; nothing was changed." | OpenRouter answered with an empty list, probably for a moment only. Your enabled models stay as they were. Try again later. |
| The refresh summary counts some models as skipped. | OpenRouter described them in a way PotionUI could not read. Choose **Show skipped models** to see why. The rest of the catalog still works. |
| A model is missing from the preset's model picker. | Check that it is turned on in the **Catalog** tab, that it can do this mode (an image model is not offered for video, and a model without editing is not offered in edit mode), and that **Allowed in presets** on its model page does not leave this preset out. For users who are not admins, also check the model's **Access** tab. |
| A video fails with "The provider did not finish in time." | The backend's **Timeout (seconds)** ran out. Raise it on the backend's Overview tab. OpenRouter may still finish and bill that job. |
| Videos seem stuck. | This is usually normal. Video jobs often take minutes, and PotionUI checks only every 30 seconds. Watch the progress line; the job ends on its own once the time limit passes. |
| A model setting you expected is missing. | It may be on the **Provider options** tab, which needs field visibility set to Advanced. If not, the model does not offer it. |

## For testing and troubleshooting: technical notes

The rest of this page is reference material: how the plugin reads OpenRouter's API, and the manual
checks to run against a real key. The plugin registers the backend driver `cloud.openrouter` on the
`cloud` engine. The general mechanics of cloud models are described in the **Cloud Models** page of
the Developer documentation.

### What the plugin assumes about the OpenRouter image API

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

### What the plugin assumes about the OpenRouter video API

- `GET /videos/models` answers `{"data": [...]}`. A missing route only hides the video models. Each model has `id`, optional
  `name`, `description`, `supported_durations`, `supported_resolutions`, `supported_aspect_ratios`, `supported_sizes`,
  `pricing_skus`, `allowed_passthrough_parameters`, and optionally `generate_audio` (true when the model can make sound),
  `supported_frame_images` (`first_frame`, `last_frame`), `max_input_references` and `supported_parameters`.
- Durations that are three or more whole numbers in even steps become a range, otherwise a list of choices.
- A model accepts image to video when it lists frame images, or when `image` is among its input modalities (then only a
  first frame is assumed). `pricing_skus` is an object of name to cost, or a list of `{sku, cost_usd}`; a name containing
  "second" is priced per second, the others as named extras. `seed` is sent unless `supported_parameters` exists without it.
- `POST /videos` answers 202 with `{id, polling_url, status}`. The body carries `duration`, `resolution`, `aspect_ratio`,
  `size`, `generate_audio`, `seed`, `frame_images` (objects with `type: image_url`, `image_url.url` as a data address and
  `frame_type`), `input_references` (objects with `type: image_url` and `image_url.url` as a data address) and the same `provider` and `user` fields as images.
- A polling address is used only when it is on the API host; otherwise `/videos/{id}` is polled. Polling starts after
  5 seconds and then every 30 seconds.
- A poll answers `{status, progress?, queue_position?, error?, unsigned_urls?, usage?}`. `pending` is queued;
  `in_progress` is running; `completed`, `failed`, `cancelled` and `expired` end the job. `progress` may be 0 to 1 or 0 to 100.
  A failure whose message mentions moderation, safety, policy or blocking is a refusal; other reasons stay in the admin-only
  detail. The finished files are the `unsigned_urls`; if none are listed, `/videos/{id}/content?index=0` is used.
- Files on the API host are downloaded with the key and files on any other host without it, with a size cap.
- OpenRouter documents no way to cancel a video. Stopping a video only stops PotionUI from waiting, and the user is told the
  job may still finish and be billed.

`cloud_models.yml` lists the model ids the catalog marks as suggested. Ids OpenRouter does not list are
ignored.

### Trying images with a real key

This run needs an OpenRouter account with credits and a key, so it is done by hand and is not part of the tests.

1. Enable the plugin (Administration, Plugins) and restart once.
2. Add an OpenRouter backend (Administration, Backends) with your key, and save.
3. Install "OpenRouter Images" and "OpenRouter Video" (Administration, Presets) and add yourself on their Access tab.
4. Open the backend's Catalog tab and refresh. Note any models listed as skipped: they show where the assumptions above do not hold.
5. Enable two image models, preferably one that accepts reference pictures.
6. Generate from "OpenRouter Images" in text-to-image mode. Try a transparent background and a count above one.
7. Generate in edit mode with one picture, then with two.
8. Check the pictures arrive in history, and that a prompt the model refuses is shown as blocked.
9. With the anonymous user id switched on, check in the OpenRouter activity log that requests carry an id that is not a user name.

### Trying video with a real key

Needs the same key and credits as above, and video costs more than pictures, so pick a short duration.

1. With the backend set up (its Timeout raised to 1800 seconds) and the catalog refreshed, enable one video model, preferably
   one that takes a first frame.
2. Generate from "OpenRouter Video" in text to video mode. Watch the progress: it should show the queue position and then
   elapsed time, and the video should arrive in history.
3. Generate in image to video mode with a start picture, and with a last picture if the model offers it.
4. Start another video and stop it while it runs. Polling should stop at once and the message should say the job may still
   finish and be billed. Check the OpenRouter activity log to see whether it was billed.
5. If a model is missing or misses controls, see the skipped list in the Catalog tab and the assumptions above.
