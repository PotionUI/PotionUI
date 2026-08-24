---
title: Prompt Phrasebook
order: 60
---

# Prompt Phrasebook

Phrasebook gives you suggestions as you type prompts, so you can insert known terms — art styles, camera angles, lighting, character traits, quality tags, and anything else you use often — without retyping them or misremembering the exact wording. You build and manage these suggestions on the **Phrasebook** page in the sidebar.

## Dictionaries: categories and values

Suggestions are organized as a tree of **categories** holding **values**:

- A **category** is a named group, and categories can nest (a category has an optional **parent**, so you can build a path like `style.lighting`). Each has a name and an optional description.
- A **value** is a single suggestion. Every value has:
  - a **Label** — the friendly name you see in the suggestion list,
  - a **Value** — the actual text inserted into your prompt (it can be a short tag or a longer multi-line snippet),
  - a **category** it belongs to,
  - a **sort order** to control where it appears among its siblings,
  - and an optional **preview image** (see below).

This split between label and value is useful: you can label something "Golden hour" while the inserted text is a longer, model-friendly phrase.

## Managing your dictionaries

The Phrasebook page uses a tree on the left and an editor on the right:

- Create a **root category** from the toolbar, or add child categories under an existing one.
- Select a category to edit its name, description, and parent, or to view what it contains.
- Add, edit, and delete **values** within a category. When creating a value you must fill in both the **Label** and the **Value**; the **Create** / **Save** buttons stay disabled until you do.
- An **AI assist** button next to the value field can help you write or refine the inserted text, when an LLM is configured.
- Filter the tree by **Active**, **Inactive**, or **All** to focus on the entries currently in use.

## How suggestions appear while typing

When you write a prompt on the Generate page, matching values from your dictionaries surface as you type. Accepting a suggestion inserts it into the prompt as a **chip** — a tidy, self-contained token rather than loose text.

Chips have a handy trick: a chip can be set to **shuffle**, so on each generation PotionUI automatically picks a different value from that same category. That turns a single prompt into an easy way to explore variations (a random camera angle or art style every run). See **Generating Images** for how chips behave in the prompt editor.

## Testing values with preview generation

To check that a value actually produces the look you want, use **Generate Preview Images**. This runs a real generation using one of your presets so you can see the value's effect:

1. Pick a **preset** and **mode**, and optionally a saved **session** for that preset to inherit its settings.
2. Optionally set a **negative prompt** (leave it empty to use the session's) and a **seed**.
3. Generate the preview.

The resulting image can be attached to the value as its **preview image**, which then shows next to the value when you edit it — a visual reminder of what that term does. If a preset has no sessions or no modes yet, the panel tells you so.

## Tips

- Keep values small and composable — individual styles, angles, and quality tags — so you can mix and match them in prompts.
- Use nested categories to keep large libraries navigable (for example a `lighting` category with values for each lighting style).
- Combine phrasebook with the **Prompts Workspace**. Prompts, saved Segments, and Segment Templates retain their rich chips and shuffle settings.
