# Black Forest Labs

Generate and edit pictures with the FLUX models hosted by [Black Forest Labs](https://bfl.ai) (BFL),
with no graphics card of your own. This page walks you from a fresh install to your first generation,
then covers the models, costs and the usual problems.

## Getting started

### What the plugin adds

- **A Black Forest Labs backend.** You add it once, in Administration → Backends. It holds your API key,
  the region and the limits.
- **BFL's model catalog.** FLUX 3 Image, the FLUX.2 family, FLUX.1 Kontext, FLUX1.1 [pro] and
  FLUX.1 [dev], listed on the backend's Catalog tab. You choose which ones people may use.
- **One preset, FLUX by Black Forest Labs**, with two modes: text to image, and editing pictures you
  add (FLUX.1 Kontext, FLUX.2 and FLUX 3 Image).

> Every generation runs at BFL and is **billed to your BFL account**, whoever in PotionUI starts it.
> The prompt and any pictures a user adds leave your server to do so.

### Before you start

1. An account on [api.bfl.ai](https://api.bfl.ai).
2. An API key: on api.bfl.ai, open **API Keys** and create one. BFL shows it only once, so copy it
   now; you will paste it into PotionUI in a moment.
3. Credits: buy some on api.bfl.ai. One credit is one US cent. Without credits, generations fail.

### Step 1: Enable the plugin

1. Go to **Administration → Plugins** and open **Black Forest Labs**.
2. Turn on the switch at the top of the plugin's page (**Enable plugin**).
3. The plugin's page now shows a **Setup** section with the next step first, and the plugin's card
   says **Setup needed** until every step is done. Each step has a button that takes you there.
4. **Restart PotionUI.** The Black Forest Labs backend type is only picked up when PotionUI starts, so
   until you restart it is missing from the list in the next step. Choose **Restart now** in the Setup
   section. Generations that are running get interrupted.
   - If you started PotionUI with `./potionui start`, you can also run `./potionui stop` and then
     `./potionui start`.

The preset and this guide appear as soon as the plugin is enabled; only the backend needs the restart.

### Step 2: Add the Black Forest Labs backend

1. Choose **Add backend** in the plugin's Setup section; the form opens with **Black Forest Labs**
   picked as the engine. (Or go to **Administration → Backends**, choose **Add backend** and pick
   **Black Forest Labs** under **Engine**.)
2. Give it a **Name** (for example "BFL").
3. Fill in the fields below, then choose **Create Backend**.

| Field | What to put there |
|---|---|
| **API key** | The key from api.bfl.ai. It is stored encrypted and never shown again; to change it, paste a new one. PotionUI sends it only to BFL's API, in the `x-key` header, never to the addresses pictures are downloaded from. |
| **Region** | `global` (the default) lets BFL send each job to the nearest cluster and fail over when one is busy. `eu` keeps every request inside the EU, `us` inside the US. |
| **API address** | Leave it empty. Set it only to reach BFL through a proxy (for example `https://proxy.example/v1`); it then replaces the region. |
| **Timeout (seconds)** | How long one picture may take before PotionUI stops waiting. The form starts at 600 (10 minutes); most pictures take seconds. |
| **Send an anonymous user id** | Off by default, so BFL gets no user id at all. When on, BFL gets a different anonymous id for each PotionUI user, so it can tell them apart without knowing who they are. Nobody can trace the id back to a person without your server's secret key. |
| **Parallel jobs** | How many generations this backend runs at the same time (1 to 32, default 4). BFL allows 24 jobs at once per account, and only 6 for FLUX.1 Kontext [max]. |

Leave **Priority**, **Enabled** and the **Scheduling** settings as they are.

### Step 3: Choose the models

1. Open the new backend and go to its **Catalog** tab.
2. Choose **Refresh catalog**. The FLUX models below appear. The list ships with the plugin, so a
   refresh works before the key is checked.
3. **Nothing is enabled by default.** Turn on the **Enabled** switch for each model you want people to
   use. **Suggested** marks FLUX.2 [pro] (latest), FLUX.1 Kontext [pro] and FLUX.2 [klein] 9B (latest)
   as a good place to start.
4. **Price** shows BFL's list price per picture. Only admins see prices; nobody else does, anywhere in
   PotionUI.

### Step 4: Make the preset available

1. Go to **Administration → Presets** and open **FLUX by Black Forest Labs**.
2. Choose **Install preset**.
3. On its **Access** tab, choose **Add user** next to yourself and everyone else who should use it,
   or switch to **Groups** and choose **Add group**.

Who can use which model, and which presets offer it, work as for every cloud model: see the model's
**Access** tab and its **Allowed in presets** panel in **Administration → Models**.

### Step 5: Generate

1. Open **Generate**, choose **Choose a preset** and pick **FLUX by Black Forest Labs**.
2. Pick a mode: **text to image**, or **edit** to change pictures you add on the **References** tab.
3. Pick a **model**. Only models enabled in the catalog, allowed for you and able to do this mode are
   listed. The form shows only what the chosen model supports: aspect ratio, resolution and file
   format in the **Image** section, and BFL's own settings (safety tolerance, prompt upsampling,
   steps and guidance, raw mode, web grounding) on the **Provider options** tab.
4. **Seed.** Every model except FLUX 3 Image honours the seed, so the same seed, prompt and settings
   give the same picture again. Leave it at -1 for a new one each time.
5. Write your prompt and generate. Each picture is one request; a quantity of 4 sends 4 requests and is
   billed 4 times.

PotionUI sends the job to BFL, checks on it after a second and then a little less often each time (at
most every 8 seconds), and downloads the picture the moment it is ready. BFL deletes finished pictures
after 10 minutes, so nothing is left to fetch later.

**Editing.** FLUX.1 Kontext takes up to 4 pictures (more than one is experimental at BFL), FLUX.2
[klein] up to 4, FLUX.2 [pro], [max] and [flex] up to 8, and FLUX 3 Image up to 10. Leave **Aspect
ratio** on `auto` to keep the shape of the first picture.

**Cancelling.** BFL has no way to stop a job. When you choose **Cancel generation**, PotionUI stops
waiting straight away and tells you: "Stopped waiting. The provider may still finish this job and bill
it." Check your usage on api.bfl.ai if you need to know whether it was billed.

## The models

| Model | Catalog id | Text to image | Edit (pictures) | Seed | Size controls | List price |
|---|---|---|---|---|---|---|
| FLUX 3 Image | `bfl~flux-3-image` | yes | up to 10 | no | aspect ratio, resolution 768sq to 4k | $0.041 (768sq) to $0.607 (4k) |
| FLUX.2 [pro] (latest) | `bfl~flux-2-pro-preview` | yes | up to 8 | yes | aspect ratio, 1K or 2K | from $0.03 |
| FLUX.2 [pro] | `bfl~flux-2-pro` | yes | up to 8 | yes | aspect ratio, 1K or 2K | from $0.03 |
| FLUX.2 [max] | `bfl~flux-2-max` | yes | up to 8 | yes | aspect ratio, 1K or 2K | from $0.07 |
| FLUX.2 [flex] | `bfl~flux-2-flex` | yes | up to 8 | yes | aspect ratio, 1K or 2K, steps, guidance | from $0.05 |
| FLUX.2 [klein] 9B (latest) | `bfl~flux-2-klein-9b-preview` | yes | up to 4 | yes | aspect ratio, 1K or 2K | from $0.015 |
| FLUX.2 [klein] 9B | `bfl~flux-2-klein-9b` | yes | up to 4 | yes | aspect ratio, 1K or 2K | from $0.015 |
| FLUX.2 [klein] 4B | `bfl~flux-2-klein-4b` | yes | up to 4 | yes | aspect ratio, 1K or 2K | from $0.014 |
| FLUX.1 Kontext [max] | `bfl~flux-kontext-max` | yes | up to 4 | yes | aspect ratio | $0.08 |
| FLUX.1 Kontext [pro] | `bfl~flux-kontext-pro` | yes | up to 4 | yes | aspect ratio | $0.04 |
| FLUX1.1 [pro] Ultra | `bfl~flux-pro-1.1-ultra` | yes | no | yes | aspect ratio, raw mode | $0.06 |
| FLUX1.1 [pro] | `bfl~flux-pro-1.1` | yes | no | yes | aspect ratio | $0.04 |
| FLUX.1 [dev] | `bfl~flux-dev` | yes | no | yes | aspect ratio, steps, guidance | not listed |

- The "(latest)" FLUX.2 models always run BFL's newest version; the others are fixed versions whose
  results stay the same over time.
- For FLUX.2, FLUX1.1 [pro] and FLUX.1 [dev], PotionUI turns the aspect ratio and resolution into a
  width and height on the model's grid (multiples of 16 for FLUX.2 up to 4 megapixels, multiples of 32
  up to 1440 pixels a side for FLUX1.1 [pro] and FLUX.1 [dev]).
- **Safety tolerance** runs from 0 (strictest) to 6 on FLUX.1 models, 5 on FLUX.2 and 4 on FLUX 3
  Image; BFL's default is 2.
- FLUX 3 Image may look things up on the web while it plans a picture (**Web grounding**, on by
  default at BFL). Turn it off on the Provider options tab to keep the prompt from being used for web
  searches.

Video (FLUX 3 Video), inpainting and outpainting, and BFL's tools (erase, virtual try-on, video edit and
upscale) are not offered by this plugin yet.

## Costs

Only admins see costs.

- **Administration → Generations** has a **Cost** column. Open a generation to see its **Cost** panel
  with one line per request.
- **Administration → Stats** shows **Cloud spend** for the date range you pick: the total, by backend
  and by model.

Where the numbers come from: BFL reports what each job cost, in credits, when the job is accepted and
again when it is settled. PotionUI records the settled amount (or the accepted amount when no settled
one comes back) in dollars, at one cent per credit, labelled **Reported**. Only when BFL reports nothing
does PotionUI estimate from the list prices above and mark the amount **est.**; FLUX.1 [dev] and FLUX 3
Image at 1.5k have no list price, so such a job is counted with no amount. Your BFL account is the final
word on what you were billed.

## Troubleshooting

| What you see | What to do |
|---|---|
| The plugin is enabled, but **Black Forest Labs** is not in the **Engine** list when adding a backend. | Restart PotionUI (see Step 1). |
| Users see "BFL rejected the API key. An administrator can check it in Administration, Backends." | The key is wrong, revoked or missing. Open the backend, paste a new **API key** and save. |
| Users see "The BFL account is out of credits. An administrator can add credits at api.bfl.ai." | Buy credits on api.bfl.ai. Nothing else needs changing. |
| Users see "BFL's content filter refused this prompt or picture. Try a different prompt or picture." | BFL's input filter stopped the request before drawing (BFL calls this Request Moderated). As an admin, open the generation in **Administration → Generations**: its **Failure** panel lists the categories BFL gave. Users never see them. Raising **Safety tolerance** on the Provider options tab makes the filter more lenient. |
| Users see "BFL's content filter blocked the finished picture. Try a different prompt." | BFL drew the picture and its output filter withheld it (Content Moderated). The job may still be billed. The Failure panel lists the categories. |
| Users see "BFL refused this request. The key may not be allowed to use this model." | The key's project has no access to this model. Check it on api.bfl.ai, or pick another model. |
| Users see "BFL is busy with this account's other jobs. Try again shortly." | The account already runs 24 jobs (6 for Kontext [max]). PotionUI retries a few times on its own; lower **Parallel jobs** if other apps share the key. |
| Users see "The finished picture expired at BFL before it could be downloaded. Generate it again." | The picture was not fetched within BFL's 10 minutes, for example because the network to BFL's download servers (`delivery.*.bfl.ai`) is blocked. Allow those addresses through your firewall. |
| A picture fails with "The provider did not finish in time." | The backend's **Timeout (seconds)** ran out. BFL may still finish and bill that job. |
| Users see "BFL rejected the request settings." | BFL refused a value, for example a size it does not make. The Failure panel names the setting. |

## Where this comes from

Checked against BFL's documentation on 2026-10-06:

- Models and request fields: [FLUX 3 Image](https://docs.bfl.ai/api-reference/utility/generate-an-image-with-flux-3),
  [FLUX.2 overview](https://docs.bfl.ai/flux_2/flux2_overview),
  [FLUX.2 [pro]](https://docs.bfl.ai/api-reference/models/generate-or-edit-an-image-with-flux2-%5Bpro%5D),
  [FLUX.2 [max]](https://docs.bfl.ai/api-reference/models/generate-or-edit-an-image-with-flux2-[max].md),
  [FLUX.2 [flex]](https://docs.bfl.ai/api-reference/models/generate-or-edit-an-image-with-flux2-[flex].md),
  [FLUX.2 [klein] 9B](https://docs.bfl.ai/api-reference/models/generate-or-edit-an-image-with-flux2-[klein]-9b.md),
  [FLUX.1 Kontext [pro]](https://docs.bfl.ai/api-reference/models/edit-or-create-an-image-with-flux1-kontext-[pro].md),
  [FLUX1.1 [pro]](https://docs.bfl.ai/api-reference/models/generate-an-image-with-flux11-[pro].md),
  [FLUX1.1 [pro] Ultra](https://docs.bfl.ai/api-reference/models/generate-an-image-with-flux11-[pro]-ultra-mode.md),
  [FLUX.1 [dev]](https://docs.bfl.ai/api-reference/models/generate-an-image-with-flux1-[dev].md),
  [FLUX.2 sizes](https://help.bfl.ai/articles/6944273991-what-are-the-flux-2-technical-specifications).
- Polling and statuses: [Get result](https://docs.bfl.ai/api-reference/utility/get-result),
  [Errors](https://docs.bfl.ai/api_integration/errors.md).
- Regions, `polling_url`, the 10-minute download window and concurrency limits:
  [Integration guidelines](https://docs.bfl.ai/api_integration/integration_guidelines).
- Prices and the credit rate: [Pricing](https://docs.bfl.ai/quick_start/pricing).

BFL also offers webhooks; PotionUI does not use them and polls instead, so your server needs no
address reachable from the internet. BFL has no endpoint to cancel a job.
