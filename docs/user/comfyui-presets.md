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

If you built or edited the workflow yourself in ComfyUI's own web interface, export it before
handing it to PotionUI:

1. Open the workflow in ComfyUI.
2. If you don't see an **Export (API)** option in the **Workflow** menu, turn on **Dev mode
   options** in ComfyUI's settings first — it's off by default.
3. Use the **Workflow** menu, then **Export (API)**. This is the version the ComfyUI server
   actually runs, which is what PotionUI needs; the plain **Export** / **Save** option saves the
   version the editor uses to draw the graph on screen instead, and PotionUI can't import that —
   if you upload it, you'll get a message telling you to use **Export (API)** instead.
4. This downloads a `.json` file. That file is what you hand to PotionUI in the next step.

A workflow built from ComfyUI **subgraphs** works fine — subgraph node ids come through as-is in an
Export (API) file, and PotionUI handles them automatically.

## Importing it as a preset

Open **Administration → Plugins → ComfyUI Backend**, then switch to its **Import workflow** tab.
It walks through five steps:

1. **Choose a workflow.** Paste or drop your exported ComfyUI JSON. The wizard detects the
   workflow's format, node count, and any LoRA chain, and reads it into the steps that follow.
2. **Design the form.** A starting layout is pre-built for you — the obvious inputs (checkpoint,
   steps, resolution, a detected LoRA chain, and more) already turned into fields and arranged into
   tabs, grouped where that makes sense (all the model pickers together, for instance). Add, remove,
   rename, or rearrange fields — into rows, groups, or collapsible sections — and add tabs of your
   own; anything you leave out keeps the value it had in the original workflow. This step also asks
   for the preset's model family, variant, and display name.
3. **What shows in history.** Pick which of your fields (steps, cfg, the model, ...) get recorded
   against each generation, so you can see what settings produced a given image later. An image
   field is never offered here — there's nothing meaningful to log for it.
4. **Requirements.** A checklist of the custom node packs and model files this workflow needs,
   checked live against your configured ComfyUI backend. A missing one only warns — you can still
   create the preset, it just won't run until that piece is installed. Continuing from here creates
   the preset.
5. **Preset created.** Lint results appear inline; "Open in Presets" opens the generated preset for
   tweaking.

If your workflow uses a custom node your backend doesn't have installed yet, the import still goes through — you'll see a warning naming it, and its fields are still available to pick (best-effort, since PotionUI can't ask that node what its inputs are called). Install the node pack and re-import once you can, but you don't have to stop and do that first.

### Advanced sampling controls

A workflow built around **KSampler (Advanced)** gets four extra fields in its **Advanced
Sampling** section: **Add noise** and **Return with leftover noise** (each `enable`/`disable`),
and **Start at step** / **End at step**. These are what a two-stage base-plus-refiner pipeline
uses to split generation across two samplers — the base sampler ends early with **Return with
leftover noise** set to `enable`, and the refiner picks up from that same step with **Add noise**
set to `disable`. **End at step** defaults to `10000` in ComfyUI, which just means "run to the
end" rather than a real step count — leave it alone unless you're deliberately stopping early. A
workflow with two `KSampler (Advanced)` nodes (a base and a refiner) gets two full sets of these
fields, numbered so each stays mapped to its own sampler.

For scripting, the raw endpoints remain available — see "The two-minute path: import a workflow" in the developer [ComfyUI Presets](../presets/comfyui.md) reference for the exact calls.

Imported presets are saved as **your own**, separate from anything your administrator ships or
manages centrally, so importing one never changes what other people on the instance see. To come
back later and re-run, edit, or delete an import, use the plugin's **Imported presets** tab next to
Import workflow — editing reopens the wizard prefilled with what you imported.

## Where it goes, and what you get

An imported preset appears in your preset picker like any other, named after the workflow you
imported. It starts as a reasonable first draft: expect to open it and tidy a few things up, the
same way you'd adjust settings on any preset that isn't quite tuned for you yet. If the import
missed something — a slider that should exist, a field with the wrong label — that's expected for
an unusual graph, not a sign the import failed; it can be corrected afterward through the same
Administration screen, or by someone comfortable editing preset files by hand (see the developer
[ComfyUI Presets](../presets/comfyui.md) reference for that path).

## Sharing a preset with someone else

An imported or hand-tuned preset lives entirely as files on the server's disk, so sharing it is
just sharing those files: whoever manages your PotionUI installation can copy the preset's folder
over to another PotionUI instance and it works the same way there, as long as that instance also
has a ComfyUI backend configured and the same models installed. There's no export button for this
today — it's a file copy an administrator does, not a self-serve action.

## See also

- [Presets & Forms](presets-and-forms.md) — what a preset and its form are, from a user's point of view.
- [Administration](admin.md) — where backends and preset management live.
- [ComfyUI Presets](../presets/comfyui.md) — the developer-level reference for
  writing or hand-editing a ComfyUI preset, including troubleshooting a workflow that runs in
  ComfyUI but not here.
