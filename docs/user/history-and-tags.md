---
title: History & Tags
order: 40
---

# History & Tags

Every image and video you generate is saved automatically to your **History**. This is where you go to find past work, inspect exactly how something was made, organize with tags, and clean up. Open it from **History** in the sidebar.

## Browsing your generations

History shows your generations in a grid of thumbnails, newest first, with a running count of how many you have. Use the per-page control to choose how many appear at once (12, 24, 48, or 96) and page through the rest.

Each item shows its result and status. Click a generation to open its details.

## Filtering

The toolbar across the top gives you several ways to narrow the list:

- **Search** — type in the search box to match generations (for example by prompt text).
- **Date** — quick presets: **All**, **Today**, **Yesterday**, **7 Days**, **30 Days**.
- **Media type** — show **All**, images only, or videos only.
- **Status** — **All Status**, **Completed**, **Failed**, **Cancelled**, **Pending**, or **Running**.
- **Mode, preset, and model** — narrow results to a particular generation setup.
- **Phrasebook used** — find runs whose recorded segment composition resolved a particular phrasebook value.
- **Tags** — the tags bar lets you filter to generations carrying specific tags.

When any filter is active a **Clear** control appears to reset everything at once. The **Refresh** button reloads the list from the server.

## Viewing generation details

Clicking a generation opens its details, which show everything recorded about that run:

- The detached positive and negative **segment composition**, including resolved text, content or `BREAK` type, enabled state, phrasebook values, and optional segment name, color, and description.
- The **preset** and its **version**.
- The **models** used (checkpoint, LoRAs, and so on), or "No models" if none were recorded.
- The generation **parameters** (such as resolution, and other settings the pipeline captured).
- When it was **created** and its status.

History records what was sent to that generation, not a live link to a Prompt, saved Segment, or Segment Template. Applying a library item creates a detached copy, so there is no **Prompt used** source filter and later library edits do not rewrite past generations.

From the details view you can **Download** the result, **Copy** information, open the image in a new tab, and **Delete** the generation.

## Tags

Tags are labels you attach to generations to group and find them later.

- **Add a tag** to generations from the toolbar's overflow menu (the "more actions" button) using **Add tag**. You can create new tags and apply existing ones.
- **Filter by tag** using the tags bar, which has its own search box for finding a tag among many.
- Tags you apply show on the generations and can be combined with the other filters.

You can also have generations tagged automatically at creation time — see the generation settings on the Generate page.

## Selecting and bulk actions

To act on several generations at once, click **Select** in the toolbar to enter selection mode. A floating toolbar appears showing how many are selected, with:

- **Select All** — select every generation currently shown.
- **Clear** — deselect everything.
- **Delete** — permanently remove the selected generations (you'll be asked to confirm).

Click the close button on the floating toolbar (or **Cancel** in the top toolbar) to leave selection mode.

## Delete by tags

For larger cleanups there is a dedicated **Delete by tags** action in the toolbar's overflow menu. You choose one or more tags, and PotionUI deletes the generations carrying them. This is a fast way to clear out, say, everything you tagged as a throwaway experiment.

Deletion is permanent, so PotionUI always asks you to confirm before removing anything, whether it's a single item, a bulk selection, or a delete-by-tags operation.

## Uploading existing generations

The overflow menu also offers **Upload generations**, letting you bring outside images into your history so they live alongside everything else you've made in PotionUI.
