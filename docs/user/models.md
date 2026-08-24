---
title: Models
order: 70
---

# Models

The **Models** page (in the sidebar) shows the model files installed on your PotionUI server. These are the actual weights that presets run — base checkpoints, LoRAs, VAEs, upscalers, and other model types. This page lets you see what's available, inspect a model's details, and understand how models connect to the presets you generate with.

## Browsing installed models

Models are shown as a grid of cards. The toolbar lets you narrow and order the list:

- **Type filter** — pills across the top, one per model type present on your server (for example checkpoint, LoRA, VAE, upscaler), each with a count. Click **All** to see everything or a type to focus on it.
- **Tags** — filter to models carrying particular tags.
- **Search** — find a model by name.
- **Sort** — by **Date Added**, **Name**, or **File Size**, in ascending or descending order.
- **Per page** — choose 12, 24, 48, or 96 cards per page and page through the rest.

A **Clear filters** control resets everything when a filter is active.

## Model details

Click a model card to open its detail page. There you can see:

- The **file name** and **file size**.
- The model **type**.
- A **description**, when one is available.
- Its content **hash**, which uniquely identifies the exact file.
- **Preview images** for the model, when present.
- **Generations** made with that model, so you can see it in action (or a note that none exist yet).

When the **CivitAI provider** plugin is installed and has fetched metadata, models can carry richer information such as descriptions and preview artwork pulled from CivitAI. See **Plugins** for what that provider adds.

## How models relate to presets

You don't generate directly from the Models page — you generate from **presets**, and presets are what actually load and run models. The connection works like this:

- A preset targets a particular model or model family, and its form's **model pickers** let you choose from the installed models of the right type.
- **LoRA slots** in a preset's form pull from your installed LoRAs, so anything you see under the LoRA type here is available to stack in a generation.
- After a run, the generation's details list exactly which models were applied — you can see that both in **History** and, for a model, on its detail page.

So the Models page is your inventory: it tells you what's on the server and available to presets. To add new models, an administrator uses the download and management tools (often via the **Model Downloader** plugin). See **Presets & Forms** for how models are chosen at generation time, and **Administration** for how they're added.

## Model licensing

PotionUI is distributed **without any model weights**. The repository and its releases contain code and configuration only; the sole model-adjacent files are a few tokenizer and config JSONs (no weights), listed in [`vendor/NOTICE.md`](../../vendor/NOTICE.md).

Models are downloaded or installed only at your explicit request, from third-party sources you choose (a provider plugin's marketplace, a direct download, or files you place on disk yourself). Every model ships under its own license — permissive, OpenRAIL-style use-restricted, non-commercial, and region-restricted terms all exist — and those terms are an agreement between you and the model's licensor. PotionUI's GPL-3.0 license covers the software only; it neither grants you rights to any model nor adds restrictions on any model.

**You are solely responsible for the models you obtain and for what you do with them**: verifying that you may download and run a given model under its license and the laws that apply to you, and for the content you generate with it. The PotionUI authors distribute no models and make no claims about the legality, licensing, or fitness of any model you connect to it.
