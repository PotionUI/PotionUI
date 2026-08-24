---
title: Segments
order: 58
---

# Segments

Segments are the cards that make up a Prompt. They let you keep subject, style, lighting, camera, and other ideas separate while still resolving to one prompt string at generation time.

Open **Prompts** from the sidebar to manage them. The **Segments**, **Segment Templates**, and **Segment Categories** tabs live alongside the complete **Prompts** library in that workspace.

## Three related concepts

- An editor **segment** is one card in the current positive, negative, multi-prompt, or Video Director list.
- A saved **Segment** is one named, categorized card you can reuse by replacing another card.
- A **Segment Template** is an ordered list of one or more rich slots that you apply to a whole list.

A complete **Prompt** is also an ordered segment list, but represents reusable authored content rather than a reusable slot structure.

## What a segment can contain

Each rich segment supports:

- content with phrasebook chips and their full selection and shuffle state,
- **content** or **BREAK** type,
- enabled or disabled state,
- and optional name, color, and description.

Collapsing a card only changes how much of it the editor shows. Disabling it excludes it from the resolved prompt while keeping the card available for later.

Every list contains at least one card. PotionUI starts an empty editor with one blank card and prevents deletion of the final card.

## Saved Segments and categories

A saved Segment requires both a name and a category, and can also have tags and an optional color. If it has no color of its own, PotionUI copies the category color when inserting it.

Categories have a name, color, and optional description. A category cannot be deleted while a saved Segment refers to it. Category and saved-Segment names need to be unique only within your own account.

Use **Save as Segment** on any editor card to open a form prefilled with its composition. Use **Replace from saved Segment** to copy a library Segment into exactly one card. The replacement receives fresh editor data and has no live connection to the saved item.

## Segment Templates

A Segment Template stores an ordered set of rich slots. A slot can be blank or can provide starter content, chips, a BREAK, enabled state, name, color, and description. Templates themselves require a name and at least one slot, and their names are unique within your account.

Templates are useful for repeatable composition patterns. For example, a four-card template might provide **Subject**, **Environment**, **Lighting**, and **Camera** slots without prescribing a preset or any generation values.

## Applying a Prompt or Template

Apply Prompts and Segment Templates at list level. Before insertion, choose **Append**, **Prepend**, or **Replace**. Replace asks for confirmation when the target contains meaningful content. An untouched blank placeholder is removed instead of being kept beside the incoming cards.

Application is always a detached deep copy. It changes only the targeted segment list and never changes your preset, mode, form values, session, backend, seed, tags, or other generation settings.

## Segments and phrasebook

Phrasebook suggestions are short values inserted as chips inside segment content. Saved Segments, Prompts, and Segment Templates retain those chips and their settings, so both tools work together: phrasebook supplies variable terms, while segment libraries preserve reusable composition.
