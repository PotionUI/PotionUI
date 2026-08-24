---
title: Administration
order: 90
---

# Administration

The **Administration** area is available only to accounts with admin rights. If you have it, you'll find it in the sidebar and linked from **Settings**. This page is a brief tour of what each admin tab does. Regular users won't see any of this, and nothing here is needed for everyday generating.

Administration is organized into tabs:

## System Settings

Global application settings for the server — the top-level configuration that applies to everyone.

## Models

Server-side model management: reviewing installed models and, together with the **Model Downloader** plugin, adding new ones. This is the admin counterpart to the user-facing **Models** page.

## Presets

Manage which presets are installed and **who can use them**. Admins see the list of presets (with installed / not installed status), filter by name and type, and control access by assigning presets to individual **users** and **groups**. This is how you make a preset available to some people but not others.

## Backends

Configure where generations actually run. An admin can add and enable multiple backends and pick the right type for each:

- **ComfyUI** — connect to a ComfyUI server (host, port, HTTPS/SSL options).
- **Remote HTTP** — a remote generation endpoint, with optional authentication.
- **RunPod** — a RunPod-hosted backend.

Each backend can be enabled or disabled, and users can select an available backend when generating.

## Users

Create and manage user accounts. Admins set the **account type** (Administrator or Regular User), manage credentials, and control what each user can access by assigning **presets** and **LLM configurations** to them individually.

## User Groups

Manage groups as a way to assign access in bulk. A group has a name and description and a set of **members**, and admins can assign **presets**, **LLM configurations**, and **models** to the whole group at once — simpler than configuring each user separately.

## LLM Configuration

Set up the language-model providers that power prompt assistance and chat features. For each configuration an admin sets the **type** (such as Ollama or OpenRouter), a **base URL** and **API key** where needed, the **model** name, and tuning options like **max tokens**, **temperature**, a **system message**, whether it **supports vision**, and any pre-chat actions. Configured LLMs can then be assigned to users and groups. See the **LLM Assistant** for how these are used day to day.

## Plugins

Install, inspect, enable, and disable plugins. Admins can scan for installed plugins, review each plugin's author, description, and registered hooks, open its settings, and toggle it on or off. See **Plugins** for what the shipped plugins do.

## Developer

A reference and inspection area for people building presets and plugins. It documents the available form field types, template functions, icons, and Jinja2 templating syntax, with live previews of rendered output. It's a lookup tool for authors rather than something you change to run the app.

---

Note that some plugins add their own tabs to Administration, so your install may show more than the tabs listed here.
