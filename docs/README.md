---
title: Documentation
order: 1
---

# Documentation

PotionUI's docs are split by audience: end-user guides, subsystem reference for anyone touching
the code, and contributor process notes. All of it also renders in-app (Help → Documentation) for
signed-in users, role-filtered.

## For users

Everyday usage — no preset authoring or backend internals assumed. Full index: [docs/user/](user/README.md).

- [Getting Started](user/getting-started.md) — what PotionUI is and how presets, forms, and generation fit together.
- [Generating Images](user/generating.md) — the Generate page: workspaces, prompts, the form, running and reading results.
- [Presets & Forms](user/presets-and-forms.md) — what a preset is and how its form maps to what you're generating.
- [History & Tags](user/history-and-tags.md) — finding past generations, inspecting how something was made, tagging and cleanup.
- [Prompts Workspace](user/prompts-library.md) — the four reusable prompt-composition libraries in one place.
- [LLM Assistant](user/llm-chat.md) — the optional AI chat assistant for writing prompts and taking actions.
- [Segments](user/segments.md) — the composable cards (subject, style, lighting, camera, ...) that build a prompt.
- [Prompt Phrasebook](user/phrasebook.md) — inline suggestions for terms you use often.
- [Models](user/models.md) — the installed model files and how they connect to presets.
- [Hardware Requirements](user/hardware-requirements.md) — honest VRAM/RAM numbers for what you're trying to run.
- [Plugins](user/plugins.md) — optional features an administrator can enable.
- [Administration](user/admin.md) — the admin-only settings area.

## Reference

Subsystem docs for anyone authoring presets, plugins, or touching the engine. Admin-only in-app.

**Models & techniques**

- [Models](models/README.md) — per-family reference pages: architecture, files, and which techniques apply.
- [Techniques](techniques/README.md) — every optional generation technique: what it does, knobs, and status.
- [How PotionUI's engine optimizations fit together](native-optimizations.md) — the map of techniques grouped by what they trade off.

**Presets, backends, plugins**

- [Preset Authoring Guide](presets.md) — how a preset teaches PotionUI to drive a model.
- [Models and Backend Availability](models.md) — how a downloaded checkpoint file becomes the row a preset's `model` field picks.
- [Backends and Engines](backends.md) — the `native`/`comfyui` engine split and how a backend is admin-configured.
- [Model Inference Path](model-inference-path.md) — one generation, followed from the **Generate** action to its saved output.
- [Native Engine v2](native-engine.md) — the in-process loading/detection/ops/sampling stack.
- [The Plugin API](plugin-api.md) — `src.plugin_api`, the only surface a plugin may import from.
- [Providers](providers.md) — plugins that talk to a model marketplace and own their own credentials.
- [Prompt Expansion](prompts.md) — how a prompt template expands per image.
- [Remote Native](remote-native.md) — the standalone native-engine worker and its wire protocol.

**Chat & directors**

- [Chat Modes, Tools, Styles & @Resources](chat-modes.md) — how a chat session is configured.
- [Chat Memory](chat-memory.md) — durable, cross-session facts the assistant can recall.
- [Video Director](video-director.md) — the composition UI/contract for native video presets.
- [Music Director](music-director.md) — the composition contract for native music/song presets.

**Legacy notes**

- [Advanced Generation Quality Techniques for SDXL](advanced-generation-techniques.md) — 2024 SDXL technique research notes; superseded by the typed [Techniques](techniques/README.md) pages for anything actually implemented.

## For contributors

- [CONTRIBUTING.md](../CONTRIBUTING.md) — dev setup and what to check before sending a change.
- [Testing notes](testing-notes.md) — environment noise to expect from the test suite, and the browser E2E harness.
