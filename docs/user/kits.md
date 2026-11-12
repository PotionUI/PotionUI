---
title: Kits
order: 62
---

# Kits

A **kit** is a named list of named items that you build once and cite in prompts: a character's pictures, a location's clips, a voice sample, a few lines of text. Each item is an image, a video, an audio file or a piece of text. Kits live in the **Library**, belong to you alone, and take no schema: a kit called `mara` with an item called `profile_left` means whatever you decide it means.

## Making a kit

Open **Library** in the sidebar and choose **Kits**, then press **New kit**. You can also select items in the Library or in History and use **Add to kit** to start a kit from them or add them to an existing one. A generation's details view has the same **Add to kit** action.

On a kit's page, **Add item** takes items from the **Library**, from **History**, from an **Upload file**, from **Paste**, or from **Write text**.

- A kit holds up to 50 items. A text item holds up to 4,000 characters.
- Each item gets a name such as `image1`, `video1`, `audio1` or `text1`. Rename it in place.
- A kit and each item have a path name made of lowercase letters, digits and `_`, starting with a letter, up to 32 characters. Capitals become lowercase and spaces become `_`. If a name is taken, PotionUI suggests a free one.
- A kit can carry a **label** (up to 40 characters, such as Actor or Location) that is only used to filter the list of kits.
- **Duplicate** copies a kit, and **Delete kit** removes it. Deleting a kit never deletes the Library files it points to; text written into it goes with it.

The Library files an item points to stay in the Library. If one is deleted, the item shows as a deleted file and is skipped when a kit is used.

## Using a kit

A kit reaches a generation in two steps, and only what the prompt cites reaches the model.

1. **Load it into the form.** In a reference field of the preset (a media loader), choose **Kits** among the sources (**Upload file**, **History**, **Library**, **Kits**). **Load a kit** lists your kits; loading one adds it as one row. Loaded items count against the field's limits like any other reference.
2. **Cite it in the prompt.** Type `@` and pick from **Resources**, or write the path yourself:
   - `@kits.mara.profile_left` cites one item.
   - `@kits.mara` cites every item of the kit: its pictures, clips and audio first, then its text, in the kit's order.

A cited image, video or audio item is passed to the model as a reference. A cited text item puts its text into the prompt where the citation stands. A loaded item that the prompt does not cite is not sent to the model.

A citation never stops a generation. An item is skipped, and the run says so, when the model does not take that kind of media, when the field's limit is reached, when the Library file was deleted, when the item was removed from the kit, when the kit was deleted, or when the kit was cited but not loaded.

In a saved session, a kit that was deleted or changed since the session was saved is flagged, with the choice to pick another kit or remove it from the session.

## Kits from scripts

The kit routes live under `/api/kits`. Reading kits needs an API key with **Can read**, and creating, changing or deleting them needs **Can change and delete your content**. See [API keys and MCP](mcp.md).
