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

- **Model Downloader** — download and manage model files, with real-time progress tracking. This is the usual way new models get onto the server.
- **CivitAI Provider** — fetches model metadata, preview images, and download links from CivitAI, enriching the Models page and powering community prompt imports.
- **Hugging Face Provider** — fetches model metadata and download links from Hugging Face.
- **System Monitor** — real-time GPU, RAM, and CPU monitoring shown in the sidebar so you can keep an eye on server load.
- **Clear Local VRAM** — a quick action that unloads cached models from the app and frees GPU memory.
- **Ollama** — a quick action to free VRAM held by a local Ollama LLM daemon.

Because plugins can bring in new interfaces, the best way to learn a specific plugin is to open its page and explore — each provides its own controls for the job it does.

## Plugins and the Prompts workspace

A provider plugin may advertise prompt importing. Its imported examples become ordinary detached Prompts in the **Prompts** workspace. Positive text creates one Prompt; negative text, when supplied, creates a second independent Prompt with the same source metadata and a negative-use hint.

Provider details such as model name, source, tags, sampler, steps, CFG, dimensions, and reactions help with browsing and search. They are never applied as generation configuration. Applying an imported Prompt changes only the segment list you target.

Prompt-related tools and plugin integrations distinguish between a saved **Segment** (one named, categorized reusable card) and a **Segment Template** (an ordered list of one or more rich slots). They are separate contracts, so a tool asking for a Segment does not silently create or apply a multi-segment Template.
