---
title: Plugins
order: 80
---

# Plugins

Plugins extend PotionUI with extra features that aren't part of the core app — new pages, background tools, generation backends, and integrations with outside services. What your installation can do depends on which plugins your administrator has enabled.

## What plugins add, from a user's view

A plugin can contribute several kinds of things:

- **New pages** that appear in the sidebar, with their own interface.
- **Sidebar widgets**, such as a live resource monitor.
- **Quick actions** you can trigger (for example freeing up GPU memory).
- **Automation templates** you can copy into a disabled workflow and configure for your installation.
- **Generation backends** that let presets run somewhere other than the local machine.
- **Providers** that connect to external catalogs and services.

When a plugin adds a page, it shows up as an entry in the left sidebar alongside the built-in ones. Opening it loads that plugin's interface inside PotionUI. If a plugin is disabled, its pages and features simply don't appear.

## Enabling and disabling plugins

Plugins are managed by administrators on the **Plugins** tab of the **Administration** area. There, an admin can scan for installed plugins, see each one's author, description, and the hooks it registers, adjust its settings, and turn it on or off. As a regular user you don't manage plugins yourself, but this is why the exact features you see can differ from another PotionUI install. See **Administration** for the admin side.

## Plugins that ship with PotionUI

These plugins come with PotionUI. Any of them may or may not be enabled on your server:

- **CivitAI Provider** — fetches model metadata, preview images, and download links from CivitAI, enriching the Models page and powering community prompt imports.
- **Hugging Face Provider** — fetches model metadata and download links from Hugging Face; an access token is only needed for gated or private repos.
- **Model Browser** — browse and download models from CivitAI, Hugging Face and OpenModelDB.
- **System Monitor** — real-time GPU, RAM, and CPU monitoring shown in the sidebar so you can keep an eye on server load.
- **Ollama** — a quick action that unloads the models a local Ollama daemon holds in VRAM.
- **ComfyUI Backend** — runs presets on an external ComfyUI server and imports ComfyUI workflows as presets from Admin → Presets.
- **RunPod Provider** — provisions a remote native worker on RunPod from the Infrastructure tab of a Remote Native backend in Admin → Backends.
- **Cloud providers** — OpenAI, Google Gemini, Black Forest Labs and OpenRouter each add a cloud backend with its model catalog and ready-made presets for hosted image and video models. **DeepSeek** adds a DeepSeek type to the LLM configurations.
- **OpenID Connect Login** — sign in through an external identity provider such as Authentik, Keycloak or Entra ID.
- **A1111 Metadata Export** — exports generations as PNGs that carry A1111/Forge-style parameters metadata.
- **Image Modal Viewer** — views generated images full screen with zoom and pan.
- **NVIDIA RTX Upscale** — upscales, denoises and deblurs images and videos with NVIDIA RTX Video Super Resolution on an RTX GPU, and raises a video's frame rate with RTX Video Frame Generation on an Ada or Blackwell GPU; needs the `nvidia-vfx` package from NVIDIA's index, which the preset's requirements list until it is installed.

Model downloads themselves are built in: they run through the download queue in Admin → Downloads, with no plugin needed.

Because plugins can bring in new interfaces, the best way to learn a specific plugin is to open its page and explore — each provides its own controls for the job it does.

## Plugins from a catalog

An administrator can also install plugins published outside PotionUI, with **Install from catalog** on the **Plugins** tab of the **Administration** area. A catalog is a `plugins.json` file at an https address. Plugins from a catalog are maintained by their authors, not by the PotionUI project.

Community catalogs:

- **[potionui-plugins](https://github.com/M4-S4/potionui-plugins)** — **Video Producer**, a page for making short films with AI video, from synopsis and shots to takes, editing, sound and export, and **Preset Editor**, to create, edit, check and publish presets without leaving PotionUI. Catalog address: `https://raw.githubusercontent.com/M4-S4/potionui-plugins/main/plugins.json`

## Plugins and the Prompts workspace

A provider plugin may advertise prompt importing. Its imported examples become ordinary detached Prompts in the **Prompts** workspace. Positive text creates one Prompt; negative text, when supplied, creates a second independent Prompt with the same source metadata and a negative-use hint.

Provider details such as model name, source, tags, sampler, steps, CFG, dimensions, and reactions help with browsing and search. They are never applied as generation configuration. Applying an imported Prompt changes only the segment list you target.

Prompt-related tools and plugin integrations distinguish between a saved **Segment** (one named, categorized reusable card) and a **Segment Template** (an ordered list of one or more rich slots). They are separate contracts, so a tool asking for a Segment does not silently create or apply a multi-segment Template.
