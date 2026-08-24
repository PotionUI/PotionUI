---
title: Presets & Forms
order: 30
---

# Presets & Forms

Presets are the heart of how PotionUI works. Understanding them makes the rest of the app click into place. This page explains presets and their forms from a user's point of view — you don't need to author or edit anything to follow along.

## What a preset is

A **preset** is a ready-made configuration for one model. It bundles two things:

1. **A model setup** — which model to run and how, so you don't have to wire that up yourself.
2. **A curated form** — the exact set of controls that make sense for that model, with sensible defaults already filled in.

Because a preset already knows the model, its quirks, and its good defaults, you can get quality results without being an expert in that particular model. Picking a different preset is like switching to a different tool that has been set up by someone who knows it well.

Presets are named for the model and the look they aim for — for example a photorealistic SDXL preset, an anime-focused SDXL preset, or a QwenImage preset that excels at rendering text inside images. Your administrator decides which presets are installed, so the list you see is specific to your server.

## Modes: the kinds of generation

A single preset can support several **modes** — the different jobs you can ask that model to do. You choose the mode right after choosing the preset. Common modes are:

- **txt2img** — create an image purely from a text prompt.
- **img2img** — start from an image you provide and transform it according to your prompt.
- **inpaint** — repaint only a masked region of an image, leaving the rest intact.

Video and audio presets have their own modes (such as text-to-video). Not every preset offers every mode; the mode selector only lists what that preset actually supports, and the form changes to match the mode you pick.

## Why forms look different per preset

Every preset shows a **different form**, and that is intentional. A form only exposes the controls that its model genuinely understands. A model with special camera-angle or lighting vocabulary can surface those as ready-made choices; a model that renders text well might offer text-specific options. This is the whole point of the preset system — instead of a lowest-common-denominator set of sliders, each preset gives you the right controls for its model, so you spend less time guessing and more time creating.

Forms are usually organized into **tabs** (for example a main Generation tab, a LoRA tab, and an Advanced tab) so the options stay tidy. Within a tab you'll find controls grouped into sections.

## Kinds of controls you'll meet

While the exact fields depend on the preset, these show up often:

- **Sliders** — for numeric settings like steps, guidance strength (CFG), or denoising amount. They come pre-set to good values.
- **Model pickers** — choose the base model (checkpoint) and, where offered, extra models. Many pickers can show model artwork and details pulled from the model's info.
- **Resolution presets** — instead of typing width and height, you usually pick from named options (square, portrait, landscape) that are known to work well with that model.
- **Seed** — the number behind a result. Leave it at **-1** for a fresh random image each time, or enter a specific number to reproduce a previous result exactly.
- **Selects, toggles, and option groups** — curated lists such as art styles, camera angles, or samplers, offered as menus or checkboxes rather than free text.
- **Quantity** — how many images to make in one run.

## Fields that react to your choices

Forms can be dynamic. Selecting one option may **reveal, hide, or change** other fields — for instance, turning on a feature might expose its detailed settings, or picking one model type might swap in a different set of options. This keeps the form showing only what's relevant to the choices you've made, so you're never faced with settings that don't apply.

## LoRA slots

Many image presets include **LoRA slots**. A LoRA is a small add-on model that nudges the base model toward a particular style, character, or subject. Each slot lets you pick a LoRA and set its **strength** with a slider — higher applies the effect more strongly. You can usually stack several LoRAs at once, and the ones actually applied to a run show up in that generation's model list (see **Generating Images**).

## Sessions: saving a setup

Everything you configure lives in your current workspace tab and is remembered between visits. When you generate, the preset, mode, prompts, and form values you used are recorded with the result, so you can always look back in **History** to see exactly how an image was made and reuse those settings.
