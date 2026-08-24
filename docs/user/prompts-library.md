---
title: Prompts Workspace
order: 50
---

# Prompts Workspace

The **Prompts** page is the home for reusable prompt composition. It combines four libraries in one workspace:

- **Prompts** — complete, ordered segment lists.
- **Segments** — reusable single building blocks.
- **Segment Templates** — reusable ordered sets of segment slots.
- **Segment Categories** — the colored organization used by saved Segments.

These libraries store prompt composition, not generation setups. Presets, modes, form values, sessions, backends, seeds, tags, and other generation settings remain separate.

## Rich segments

A Prompt is an ordered list containing at least one rich segment. Each segment can hold:

- prompt content and phrasebook chips, including each chip's selected value and shuffle settings,
- an enabled or disabled state,
- a **content** or **BREAK** type,
- and optional name, color, and description metadata.

Empty segments are allowed, which is useful for starter slots in a template. Collapsing a card is only an editor display choice; it is not part of the saved composition. Disabling a card is the way to retain it without including it in the resolved prompt.

Prompts are channel-agnostic. The same Prompt can be applied to a positive or negative editor, and the positive and negative lists are saved and applied independently. A Prompt name is optional; when it is omitted, the library uses a shortened content preview.

## The four library tabs

### Prompts

Use this tab to create, edit, search, import, and remove complete Prompt compositions. Search and duplicate detection use the resolved, flattened text of enabled segments, including chip values and `BREAK` separators.

Imported items may also carry model, provider, source, tags, dimensions, sampler, steps, CFG, and community reaction information. This is browsing and search metadata only. Applying the Prompt never changes generation configuration.

### Segments

A saved Segment is one named reusable chunk. It must have a category and may have tags, rich chip state, type, enabled state, color, and description. A Segment's own color wins when set; otherwise its category color is used when the Segment is inserted.

Use saved Segments for pieces such as a lighting description, character definition, or quality phrase that should replace one card in an editor.

### Segment Templates

A Segment Template is an ordered composition of one or more rich segment slots. Slots may contain starter text or may be empty, and each retains its own chips, type, enabled state, name, color, and description.

Use a Template when you want a repeatable structure—such as subject, environment, lighting, and camera cards—rather than one reusable chunk.

### Segment Categories

Categories have a name, color, and optional description. Names are unique within your account. A category cannot be deleted while saved Segments still use it; move or delete those Segments first.

## Saving from an editor

List editors on Generate and in the Prompts workspace provide **Save as Prompt**. Saving always creates a new detached Prompt; it does not establish a live link back to the editor or update a library source later.

On an individual card, **Save as Segment** opens a form prefilled from that card. Give the saved Segment a name and choose a category before saving it.

Every editor keeps at least one card. A new list starts with one blank segment, and the final card cannot be deleted.

## Applying library items

Prompt and Segment Template pickers require an explicit mode:

- **Append** — add the incoming cards after the current list.
- **Prepend** — add them before the current list.
- **Replace** — replace the targeted list; PotionUI asks for confirmation when that list already contains meaningful content.

A single untouched blank placeholder is treated as an empty list, so it is not kept beside incoming cards. Every inserted card is a deep, detached copy with a fresh editor identity. Editing it later does not change the library item, and editing the library item does not change existing generations or editors.

Applying affects only the positive, negative, multi-prompt, or Video Director segment list you targeted. It leaves the preset, mode, dynamic form, session, backend, seed, tags, and all other generation settings untouched.

Use **Replace from saved Segment** on a card to replace exactly that card. Use **Apply Prompt** or **Apply Segment Template** at list level when you want to bring in a complete composition.

## Imports and duplicates

Provider imports create a one-segment Prompt from positive text. When an imported item also has negative text, PotionUI creates a second, independent Prompt marked for negative use; the two records share source metadata but are not an editable pair.

Text imports also create regular detached Prompts. Duplicate detection compares flattened prompt content, while provider and model metadata remain available for browsing and filtering.
