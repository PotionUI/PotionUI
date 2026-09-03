---
title: Bringing Your Own ComfyUI Workflows
order: 35
---

# Bringing Your Own ComfyUI Workflows

Some presets run on **ComfyUI**, a separate server your administrator connects PotionUI to, rather
than on PotionUI's own built-in generation engine. If you already have a ComfyUI workflow you like
— something you built yourself, downloaded, or got from a friend — you can turn it into a preset of
your own, without writing any code. This page is for anyone who wants to do that; it doesn't assume
you know how presets are built internally (see **Presets & Forms** for that side of things).

## Where the ComfyUI backend lives

ComfyUI presets need a ComfyUI server to actually run on. That connection — host, port, and whether
it's your own machine or a remote one — is set up once by an administrator in **Administration →
Backends**, where a **ComfyUI** backend is added alongside whatever other backends the instance
uses. As a regular user you don't need to touch this; you just need a preset whose ComfyUI backend
is already configured and enabled. If generations fail immediately with a connection error, that's
the first thing to ask your administrator about.

## Exporting a workflow from ComfyUI

If you built or edited the workflow yourself in ComfyUI's own web interface, you need to export it
in the right format before PotionUI can use it:

1. Open the workflow in ComfyUI.
2. Use the **Workflow** menu, then **Export (API)** — not the plain **Export** option, and not
   **Save**. Those save the version ComfyUI's editor uses to draw the graph on screen, which PotionUI
   can't read. **Export (API)** saves the version the ComfyUI *server* actually runs, which is what
   the import needs.
3. This downloads a `.json` file. That file is what you hand to PotionUI in the next step.

If someone already gave you a workflow `.json` file, check with them (or open it in a text editor
and look for a `class_type` key on each entry) that it's the API-exported kind — a UI export looks
noticeably different and won't import.

## Importing it as a preset

An **Import workflow** button in **Administration → Presets** (icon-only header action) opens a modal: paste or drop your exported ComfyUI JSON. The modal detects the workflow's mode, node count, and LoRA chain; select which inputs become form fields (checkpoint, steps, and other obvious ones are pre-ticked, more are available under "More inputs"), set model family / variant / display name, then create. Lint results appear inline; "Open in Presets" opens the generated preset for tweaking.

For scripting, the raw endpoints remain available — see "The two-minute path: import a workflow" in the developer [Preset Authoring Guide](../presets.md#comfyui-presets) for the exact calls.

Imported presets are saved as **your own**, separate from anything your administrator ships or
manages centrally, so importing one never changes what other people on the instance see.

## Where it goes, and what you get

An imported preset appears in your preset picker like any other, named after the workflow you
imported. It starts as a reasonable first draft: expect to open it and tidy a few things up, the
same way you'd adjust settings on any preset that isn't quite tuned for you yet. If the import
missed something — a slider that should exist, a field with the wrong label — that's expected for
an unusual graph, not a sign the import failed; it can be corrected afterward through the same
Administration screen, or by someone comfortable editing preset files by hand (see the developer
[Preset Authoring Guide](../presets.md#comfyui-presets) for that path).

## Sharing a preset with someone else

An imported or hand-tuned preset lives entirely as files on the server's disk, so sharing it is
just sharing those files: whoever manages your PotionUI installation can copy the preset's folder
over to another PotionUI instance and it works the same way there, as long as that instance also
has a ComfyUI backend configured and the same models installed. There's no export button for this
today — it's a file copy an administrator does, not a self-serve action.

## See also

- [Presets & Forms](presets-and-forms.md) — what a preset and its form are, from a user's point of view.
- [Administration](admin.md) — where backends and preset management live.
- [Preset Authoring Guide](../presets.md#comfyui-presets) — the developer-level reference for
  writing or hand-editing a ComfyUI preset, including troubleshooting a workflow that runs in
  ComfyUI but not here.
