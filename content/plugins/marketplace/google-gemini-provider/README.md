# Google Gemini

Generate pictures and short videos with Google's hosted models through the Gemini API, using a key
from [Google AI Studio](https://aistudio.google.com), with no graphics card of your own. This page
walks you from a fresh install to your first generation, then covers costs and the usual problems.

## Getting started

### What the plugin adds

- **A Google Gemini backend.** You add it once, in Administration → Backends. It holds your API key
  and limits.
- **Google's model catalog.** The image and video models the Gemini API offers your key, listed on
  the backend's Catalog tab. You choose which ones people may use.
- **Two presets:**
  - **Gemini Images**: text to image, and editing pictures you add, with Nano Banana 2, Nano Banana
    Pro and Nano Banana 2 Lite.
  - **Gemini Video**: text to video, image to video from a start picture (optionally with an end
    picture), and multi-shot films made with the Video Director, with Veo 3.1.

> Every generation runs on Google's servers and is **billed to the Google Cloud project of your API
> key**, whoever in PotionUI starts it. The prompt and any pictures a user adds leave your server to
> do so.

### Before you start

1. A Google account, signed in at [aistudio.google.com](https://aistudio.google.com).
2. An API key: in Google AI Studio choose **Get API key**, then **Create API key**. Copy it now; you
   will paste it into PotionUI in a moment.
3. Billing: the image and video models are paid models. In Google AI Studio, open the key's project
   and turn on billing (**Set up billing**). Without it, Google refuses image and video requests.

### Step 1: Enable the plugin

1. Go to **Administration → Plugins** and open **Google Gemini**.
2. Turn on the switch at the top of the plugin's page (**Enable plugin**).
3. The plugin's page now shows a **Setup** section with the next step first, and the plugin's card
   says **Setup needed** until every step is done. Each step has a button that takes you there.
4. **Restart PotionUI.** The Google Gemini backend type is only picked up when PotionUI starts, so
   until you restart, Google Gemini is missing from the list in the next step. Choose **Restart now**
   in the Setup section. Generations that are running get interrupted.
   - If you started PotionUI with `./potionui start`, you can also run `./potionui stop` and then
     `./potionui start`.

The two presets and this guide appear as soon as the plugin is enabled; only the backend needs the
restart.

### Step 2: Add the Google Gemini backend

1. Choose **Add backend** in the plugin's Setup section; the form opens with **Google Gemini** picked
   as the engine. (Or go to **Administration → Backends**, choose **Add backend** and pick **Google
   Gemini** under **Engine**.)
2. Give it a **Name** (for example "Google").
3. Fill in the fields below, then choose **Create Backend**.

| Field | What to put there |
|---|---|
| **API key** | The key from Google AI Studio. It is stored encrypted and never shown again; to change it, paste a new one. |
| **API address** | Leave the default (`https://generativelanguage.googleapis.com/v1beta`). Change it only to reach the Gemini API through a proxy. |
| **Parallel jobs** | How many generations this backend runs at the same time (1 to 32, default 4). Any more wait in the queue. Google also limits how many requests a key may send per minute; see Troubleshooting. |
| **Timeout (seconds)** | How long one generation may take before PotionUI gives up. Pictures take well under a minute. Videos usually take one to six minutes, so keep at least 600 if you plan to use video. |

Leave **Priority**, **Enabled** and the **Scheduling** settings as they are.

The key is sent to Google in a request header, never in a web address, and only to the API
address. No user name, email or user id is ever sent; the Gemini API has no field for one.

### Step 3: Choose the models

1. Open the new backend and go to its **Catalog** tab.
2. Choose **Refresh catalog**. PotionUI asks Google which models your key can use and keeps the
   image and video ones.
3. **Nothing is enabled by default.** Turn on the **Enabled** switch for each model you want people
   to use, or select several rows and choose **Enable**.

What you will find there:

| Model | Id | What it does |
|---|---|---|
| Nano Banana 2 | `gemini-3.1-flash-image` | Text to image and editing with up to 14 pictures, 1K, 2K or 4K. A good default. |
| Nano Banana Pro | `gemini-3-pro-image` | Google's highest quality image model, best at complex scenes and lettering. 1K, 2K or 4K. |
| Nano Banana 2 Lite | `gemini-3.1-flash-lite-image` | The cheapest and quickest image model, 1K only. |
| Veo 3.1 Lite | `veo-3.1-lite-generate-preview` | Lowest cost video with sound, 720p or 1080p. |
| Veo 3.1 | `veo-3.1-generate-preview` | Highest quality video with sound, up to 4K. Google retires it on 22 October 2026. |
| Veo 3.1 Fast | `veo-3.1-fast-generate-preview` | Quicker, cheaper Veo 3.1, up to 4K. Google retires it on 22 October 2026. |

- **Suggested** marks the models the plugin recommends as a place to start.
- **Price** shows Google's list price. Prices are Google's and can change. Only admins see prices;
  nobody else does, anywhere in PotionUI. For pictures the list shows the price of a picture at the
  smallest size and, as a separate line, what a larger size adds.
- Badges mark models Google has stopped offering your key (**Missing from provider**) and models it
  plans to retire (**Deprecated**).
- A model Google lists that the plugin does not know yet is still shown, with plain defaults (aspect
  ratio only) and no price.

Refresh again from time to time to pick up new models. A refresh never enables anything on its own.

### Step 4: Make the presets available

1. Go to **Administration → Presets** and open **Gemini Images**.
2. Choose **Install preset**.
3. On its **Access** tab, choose **Add user** next to yourself and everyone else who should use it,
   or switch to **Groups** and choose **Add group**.
4. Do the same for **Gemini Video** if you want video.

### Step 5 (optional): Control who uses which model

- **Who can use a model.** As an admin, you can use every enabled model. Everyone else only sees the
  models given to them on the model's **Access** tab in **Administration → Models**.
- **Which presets offer a model.** On the model's **Overview** tab, **Allowed in presets** limits the
  model to the presets you pick.
- **Content safety still applies.** Your settings under **Administration → System Settings → Content
  Safety** also cover Google generations.

### Step 6: Generate

1. Open **Generate**, choose **Choose a preset** and pick **Gemini Images** or **Gemini Video**.
2. Pick a mode: text to image or edit for pictures, text to video or image to video for video.
3. Pick a **model**. Only models enabled in the catalog, allowed for you and able to do this mode are
   listed.
4. The form only shows the settings the chosen model supports: aspect ratio and size for pictures;
   length, aspect ratio and resolution for videos.
5. In edit mode, add the pictures to change on the **References** tab and describe the change.

**Pictures** come back from a single request, usually in 10 to 40 seconds. Each request makes one
picture, so a quantity of four is four requests and is billed as four pictures.

**Videos** take longer, often one to six minutes. PotionUI sends the job to Google, checks on it
after 10 seconds and then every 15 seconds, and downloads the video when it is ready. Veo makes
4, 6 or 8 second videos with sound; 1080p and 4K videos are always 8 seconds long, and PotionUI
refuses a shorter one before anything is sent.

**Films with the Video Director** work as in the other video presets: each shot is one paid video,
a shot can continue from the last frame of the shot before it, and the shots are joined at the end.

**Cancelling.** Google offers no way to stop a job once it has been sent. When you choose **Cancel
generation**, PotionUI stops waiting straight away and tells you: "Stopped waiting. The provider
may still finish this job and bill it."

### Costs

Only admins see costs: the **Cost** column and panel in **Administration → Generations**, and
**Cloud spend** in **Administration → Stats**.

Google does not report what a request cost, so every amount is an estimate, marked **est.**:

- **Pictures** are priced from the token counts Google reports with each answer (the prompt and
  input pictures, the picture made, and the model's own text and thinking), at Google's list price
  per million tokens for that model. If an answer carries no token counts, the list price per
  picture for the chosen size is used.
- **Videos** are priced at the list price per second for the chosen resolution, times the length.
- A model the plugin has no price for is counted but adds nothing to the total.

The Google Cloud billing page of the key's project is the final word on what you were billed.

### Troubleshooting

| What you see | What to do |
|---|---|
| The plugin is enabled, but **Google Gemini** is not in the **Engine** list when adding a backend. | Restart PotionUI (see Step 1). |
| Users see "Google rejected the API key. Check it in Administration, Backends." | The key is wrong, deleted or restricted. Create a new one in Google AI Studio, paste it on the backend and save. |
| Users see "This model needs billing turned on for the key's project." | Turn on billing for the key's project in Google AI Studio (Before you start, step 3). |
| Users see "The Google account has no prepaid credit left." | Add credit to the project's billing account. |
| Users see "Google does not offer this model where this server is." | The Gemini API is not available in the server's region. |
| Users see "Google is limiting requests from this key. Try again shortly." | The key hit its per-minute or per-day limit. PotionUI waits as long as Google asks and tries again. If it keeps happening, lower **Parallel jobs** or raise the project's limits in Google AI Studio. |
| Users see "The model's content filter refused this request. Try a different prompt or picture." | Google's safety filter blocked the prompt, an input picture or the result. As an admin, open the generation in **Administration → Generations**: its **Failure** panel shows which filter and category Google gave. Users never see those details. |
| Users see "The model answered without a picture. Try rephrasing the prompt." | The model replied in words only. The **Failure** panel shows the start of what it said. |
| Users see "Google refused this request. The key may not be allowed to use this model." | The key's project lacks access to that model. Pick another model or check the project in Google AI Studio. |
| A video fails with "The provider did not finish in time." | Raise the backend's **Timeout (seconds)**. Google may still finish and bill that job. |
| **Refresh catalog** lists only the built-in models. | Google's model listing could not be read, so the plugin's own list was used; the log says why. Your enabled models are unaffected. Refresh again later. |

## For testing and troubleshooting: technical notes

The plugin registers the backend driver `cloud.google` on the `cloud` engine; its model slugs are
`google~<model id>`, for example `google~gemini-3.1-flash-image`. The general mechanics of cloud
models are described in the **Cloud Models** page of the Developer documentation.

### Sources

Read on **6 October 2026**:

- Image generation: <https://ai.google.dev/gemini-api/docs/image-generation>
- Models and their status: <https://ai.google.dev/gemini-api/docs/models>,
  <https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-image>,
  <https://ai.google.dev/gemini-api/docs/models/gemini-3-pro-image>
- Retirement dates: <https://ai.google.dev/gemini-api/docs/deprecations>
- Imagen (shut down 17 August 2026): <https://ai.google.dev/gemini-api/docs/models/imagen>
- Veo: <https://ai.google.dev/gemini-api/docs/veo>
- `generateContent` reference: <https://ai.google.dev/api/generate-content>
- Errors: <https://ai.google.dev/gemini-api/docs/troubleshooting>
- Prices: <https://ai.google.dev/gemini-api/docs/pricing>

### What the plugin assumes about the Gemini API

- **Auth.** Every call carries the key in the `x-goog-api-key` header.
- **Listing.** `GET /models?pageSize=1000`, following `nextPageToken` (at most 10 pages). A model is
  kept when its id starts with `gemini-` and contains `-image` and it lists `generateContent`, or its
  id starts with `veo-` and it lists `predictLongRunning`. Models with `modelStage: RETIRED` are left
  out; `DEPRECATED` ones carry their `retirementTime` as the retirement date. Known ids get the
  plugin's curated capabilities and prices (`backend/catalog.py`); unknown ones get aspect ratios
  only, up to 3 reference pictures, and no price. If the listing fails for any reason other than the
  key, credit or permission, or names no image or video model, the built-in list is used instead.
- **Pictures.** `POST /models/{id}:generateContent` with
  `{"contents": [{"role": "user", "parts": [{"text": ...}, {"inlineData": {"mimeType", "data"}}...]}],
  "generationConfig": {"responseModalities": ["TEXT", "IMAGE"], "imageConfig": {"aspectRatio", "imageSize"}}}`.
  `imageConfig` is left out when no size setting applies. The prompt comes first, then the reference
  pictures in order. Seeds are not sent. Parts marked `thought: true` (the model's draft pictures)
  are skipped; every other `inlineData` image part becomes a picture.
- **Refusals.** A `promptFeedback.blockReason`, or a candidate without a picture whose `finishReason`
  is `SAFETY`, `IMAGE_SAFETY`, `PROHIBITED_CONTENT`, `IMAGE_PROHIBITED_CONTENT`, `BLOCKLIST`, `SPII`,
  `RECITATION` or `IMAGE_RECITATION`, is a refusal: the user sees the plain content-filter sentence,
  the admin detail names the reason and any medium or high safety ratings. Any other answer without a
  picture fails plainly, with the finish reason and the first 200 characters of the model's text in
  the admin detail.
- **Errors.** Google answers `{"error": {"code", "message", "status", "details"}}`. 401, or a 400 or
  403 whose `ErrorInfo.reason` is an `API_KEY_*` reason or whose message mentions the API key, is
  `auth`; 402 is `credits`; 400 `FAILED_PRECONDITION` is `credits` (billing) unless the message is
  about location or region, which is `refused`; other 403 is `refused`; 429 is `rate_limited` with
  the `Retry-After` header or the `RetryInfo.retryDelay` detail as the wait; 404 and other 400 are
  `invalid_request`; 503 and other 5xx are `unavailable`; 504 is `timeout`.
- **Video.** `POST /models/{id}:predictLongRunning` with
  `{"instances": [{"prompt", "image": {"inlineData": ...}, "lastFrame": {"inlineData": ...}}],
  "parameters": {"durationSeconds": "8", "aspectRatio", "resolution"}}`. The answer's `name`
  (`models/{id}/operations/{op}`) is polled at `GET /{name}` after 10 seconds and then every 15. A
  `done` operation with `error` fails (a message about safety or policy is a refusal); otherwise
  `response.generateVideoResponse.generatedSamples[].video.uri` is downloaded, with the key only when
  the address is on the API host. A finished job without videos whose `raiMediaFilteredCount` or
  `raiMediaFilteredReasons` is set is a refusal. `personGeneration`, `negativePrompt`, `seed` and
  reference pictures are not sent.
- **Cancel.** Neither endpoint can be cancelled, so the driver declares no cancel support.

### Trying it with a real key

This needs a key with billing turned on, so it is done by hand and is not part of the tests.

1. Enable the plugin, restart, add the backend with your key, and refresh the catalog. Check that
   the three Nano Banana models and the Veo models you have access to are listed.
2. Install both presets and add yourself on their Access tab. Enable Nano Banana 2 and Veo 3.1 Lite.
3. In **Gemini Images**, generate text to image at 16:9 and 2K, then edit mode with one picture and
   with three. Check the pictures arrive and that **Administration → Generations** shows a cost.
4. Try a prompt Google refuses and check users see only the plain sentence while the Failure panel
   names the reason.
5. In **Gemini Video**, generate a 4 second 720p video, then image to video with a start and an end
   picture. Try 1080p at 4 seconds and check it is refused before anything is sent.
6. Start a video and cancel it. The message should say the job may still be billed.
