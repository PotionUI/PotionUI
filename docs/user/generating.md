---
title: Generating Images
order: 20
---

# Generating Images

The Generate page is where you do the actual work of making images and video. This page covers everything on it: workspaces, choosing what to generate with, writing prompts, filling in the form, running and cancelling generations, and reading the results.

## Workspaces (tabs)

The bar across the very top holds your **workspace tabs**. Each tab is an independent setup — its own preset, mode, prompts, form values, and results — so you can keep several ideas going side by side. Add a tab with the plus button, switch by clicking a tab, and close one when you are done. Your tabs and their contents are remembered between visits.

Only one tab can generate at a time. If you start a generation in one tab and switch to another, PotionUI tells you which tab is busy and offers to jump back to it.

## Choosing a preset and mode

At the top of each tab is the **preset and session bar**:

- **Preset** — selects the model configuration you want to use. Changing the preset resets the mode and form for that tab, because a different model has different controls.
- **Mode** — appears after a preset is chosen and lists the generation types that preset supports (for example **txt2img**, **img2img**, **inpaint**). The form and prompt layout adapt to the mode you pick.
- **Reload** — a refresh control that reloads the preset's form from the server. Use it if an administrator has just updated a preset and you want the latest version without leaving the page.

You cannot generate until both a preset and a mode are selected; the page shows a "Ready to Generate" placeholder until then.

## Writing prompts

The prompt area sits below (or beside, on desktop) the workbench. Depending on the preset and mode, it takes one of three forms:

- **Standard positive and negative editors** — the most common layout. Each channel is its own ordered segment list: positive describes what you want, while negative describes what to avoid. Saving or applying one channel does not change the other.
- **Multi-prompt** — when a preset produces several distinct images per run, each output gets its own positive and negative segment lists.
- **Video Director and timeline contexts** — video presets use the same segment-list composition for global, timed, chain, and negative directions, depending on the selected mode.

Each segment card can contain rich text and phrasebook chips, be a content or `BREAK` card, and carry an optional name, color, and description. Reorder, duplicate, disable, or delete cards as you refine the list. Collapsing a card only reduces its editor footprint; disabling it is what excludes it from the generated prompt. Every list retains at least one card, so the final blank card cannot be deleted.

Two prompt conveniences worth knowing:

- **Phrasebook chips.** As you type, suggestions from your phrasebook dictionaries appear. Accepting one inserts it as a chip. A chip can be set to **shuffle**, meaning PotionUI picks a fresh value from that category on every generation — a quick way to explore variations. See **Prompt Phrasebook** for how these dictionaries are built.
- **Per-segment AI chat.** If an LLM is configured, you can open a chat on a prompt segment to have it rewritten or expanded, then accept the suggested text back into your prompt.

## Saving and applying prompt composition

List-level actions let you **Save as Prompt**, **Apply Prompt**, **Apply Segment Template**, or **Add Segment**. A saved Prompt is a channel-agnostic ordered composition; saving from a positive or negative editor always creates a new detached library item.

When applying a Prompt or Segment Template, choose **Append**, **Prepend**, or **Replace**. Replacing meaningful content requires confirmation. If the target is only its untouched blank starter card, PotionUI removes that placeholder before inserting the incoming cards.

On one card, use **Save as Segment** to save a reusable chunk; the form requires a name and category. Use **Replace from saved Segment** to replace only that card, including its rich content and effective color.

Library application copies composition only. It does not change the other prompt channel or any preset, mode, dynamic-form value, session, backend, seed, tag, or generation setting. Inserted cards are detached copies, so later edits do not update their library source and library edits do not alter the current editor. Use a **session** when you want to save preset, mode, and form configuration.

## The generation form

The form panel holds the preset's controls. Because it is defined by the preset, its exact fields vary, but common ones include:

- **Model / checkpoint pickers** — choose the base model and optional add-ons.
- **Resolution** — often a set of presets (portrait, square, landscape) rather than free-typed numbers.
- **Steps** and **guidance / CFG** — sliders controlling how long and how strongly the model works.
- **Seed** — the number that makes a result reproducible. Set it to **-1** for a new random seed every run; set a specific number to recreate an earlier image. The actual seed used is reported back with each result.
- **Quantity** — how many images to produce in one run.
- **LoRA slots** — optional style/character add-on models with their own strength sliders, when the preset offers them.

Fields can react to each other: choosing one option may reveal or hide others. Everything you enter is saved per tab. For a deeper explanation of why forms differ, see **Presets & Forms**.

## Starting and cancelling

Press the **Generate** button in the panel at the bottom of the page (or the floating sparkle button on mobile). Generate is only enabled once you have a preset, a mode, and at least one non-empty prompt.

While a run is active the button becomes a **Cancel** control. Cancelling stops the generation on the server and clears the busy state. You can safely switch tabs, navigate away, or reload the browser during a run — PotionUI keeps the generation alive server-side and restores its state (including finished results) when you return.

## Live progress and the workbench

During generation you see real-time updates:

- A **progress bar** with a percentage and a status line naming the current pipeline step (loading the model, encoding the prompt, generating, upscaling, and so on).
- The **workbench**, the large preview area, shows intermediate images as they are refined. For batches or video, use the previous/next controls to step through the items, and drag the handle to resize the preview.

If the live connection drops you will see a warning banner; updates resume once it reconnects.

## Results: gallery and artifacts

When the run completes, the finished images (or videos) collect in the **gallery**. From there you can view them full size and move any item back onto the workbench for a closer look. Everything is also saved to your **History** automatically.

Alongside the results, generations expose **artifacts** — extra details the pipeline recorded:

- the **seed** actually used (so you can reproduce or vary a result),
- the **list of models** that were applied (checkpoint, LoRAs, VAE, and so on, with their weights),
- and **comparison** images where a step shows a before/after, such as an upscale or detail pass.

To find, re-open, tag, or reuse any of these results later, head to **History & Tags**.
