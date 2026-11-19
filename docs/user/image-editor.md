---
title: Image Editor & Saved Edits
order: 45
---

# Image Editor & Saved Edits

The Image editor lets you crop, resize, paint, mask and apply looks to a picture. Every change stays undoable until you save the result as a picture, and the editor also keeps your work for you: **edits save themselves as you go**, so you can close a tab, come back later, and carry on.

## Starting an edit

Open a new Image editor tab from the **+** button in the tab strip. A tab with no picture shows two things side by side:

- **Start from a picture** — pick **Upload file**, **History** or **Library**, or drop a picture anywhere on the screen, or paste one with Ctrl V.
- **Recent edits** — everything you have already started, newest first.

Opening a picture from History, Library or a picture field in a form goes straight to the editor with that picture loaded.

## Layers

The **Edits** list is also your layer list. Each paint stroke group, picture you add and generated region edit is a layer, listed newest on top and the original picture at the bottom. Looks, crops and other whole-picture edits sit in the same list as slimmer rows.

**Add a layer.** Press **+** in the Edits header. The menu says where the layer will go: *Add above* the row you have selected, or *Add on top* when nothing is selected. Choose:

- **Empty paint layer** opens Paint on a fresh transparent layer.
- **Picture from Library**, **Picture from History** or **Picture from a file** places the picture as a layer. Drag it to move, and use the handles to scale and rotate, then press **Apply**.

A layer added above a look is simply not tinted by that look. Nothing is reordered behind your back.

**Paint on a layer.** Click a layer row to select it. The brush then paints on that layer. If you pick a brush while a look, crop or the original is selected, the first stroke starts a new layer above it.

**Fade, hide, rename.** The selected layer shows an **Opacity** slider under its row. The eye hides a layer without deleting it. Use the row menu (the three dots) for **Rename**, **Duplicate**, **Merge down**, **Move up**, **Move down** and **Delete**.

**Reorder.** Drag a row by its grip, or select it and press **Alt+Up** or **Alt+Down**. On a phone, use **Move up** and **Move down** in the row menu. A crop trims everything beneath it, so layers do not cross one: the drop line turns red and says why. Looks and layers swap freely.

**On a phone.** Edits is a bottom sheet with 48 pixel rows and a 44 pixel **+** button. Adding a layer closes the sheet and opens Paint.

When you save, the layers are flattened in the order shown, with their opacity and visibility, so the saved picture matches what you see.

## Remove an object

Pick **Remove an object** in the **Generate** group, mark what you want gone with the lasso or the brush, and press **Remove**. There is nothing to describe: the marked area is filled so it matches what is around it.

- **Include its shadow** is a checkbox in the options. Tick it when the object leaves a shadow or reflection behind and you want that cleaned up with it. It is off by default and is remembered for **Try again** on that edit.
- It runs on your default setup for Remove, the same way Edit a region does. Pick another preset or mode in the bar above the prompt, or press the star to make it your default.
- Some setups remove objects through **Edit a region**. When yours does, the setup says **Uses Edit a region with a built-in instruction.** under it, so you know why it behaves like a region edit.

## Replace the background

Pick **Replace the background** in the **Generate** group, right after Remove.

1. **Find the subject.** Press **Find the subject** and the editor marks everything except the subject. The marked part is the background, shown with the same tint as any other marked area. This needs a matting model; if there is none, the panel says to set up **Remove background** in ImageTools.
2. **Fix the mask.** The mark is a normal one, so draw with the **Brush** or the **Lasso** to add or take away background, and **Clear area** starts over. You can also skip step 1 and mark the background yourself.
3. **Choose the new background.**
   - **Describe** asks a model for it. Write what the background should be in the box below, press **Generate**, and pick from the results like any other region edit.
   - **Picture** uses a picture from your Library, History or a file. It is fitted to cover the whole picture.
   - **Colour** fills the background with one colour.
   - **Transparent** removes the background, so the saved picture keeps transparency. Save it as PNG.

   Picture, Colour and Transparent need no model and finish at once with **Replace the background**. Until there is a subject or a marked background, the button says **Find the subject or mark the background first**.

The result is a row in the **Edits** list called **Replace background** (or a region edit for Describe). Hide it with the eye or delete it to bring the original background back, and press **Replace the background** again to try another choice on the same mask.
## Expand a picture to another format

**Expand** (in the Generate group of the rail) puts your picture on a larger canvas and generates the new area so it continues the picture. Use it to turn a square into a story, a landscape or a wide banner.

1. Pick one or more **formats**: Square 1:1, Portrait 4:5, Story 9:16, Landscape 16:9, Wide banner 3:1, any format your administrator added for everyone or for your group, or **Custom** with your own width and height. Each row shows the size the picture would grow to. The picture is never shrunk or cropped: the canvas is the smallest one of that shape that holds it.
2. **Drag the picture** in the Position preview to choose where the new area goes, or select it and use the arrow keys (Shift for bigger steps). **Center** puts it back in the middle. The position applies to every format you picked.
3. Write what should appear in the new area, or leave the box empty: the model then continues the picture.
4. Press **Generate**.

Nothing under the new step is changed: hide the step and the original is back. A big expansion is made in passes, each one growing the picture by a bounded step so the model always works at sizes it handles well; the panel says when a format takes more than one pass.

**Several formats at once.** The first format becomes a step in the edit you are working on. Every other format becomes an edit of its own, a copy of this edit with its own Expand step, started at the same time and queued with your other generations. Changing the canvas size is a different result for each format, so they cannot be steps of one stack. Open the others from **Recent edits**; they finish by themselves whether or not you open them.

Expand changes the size of the picture, so like a crop it is a barrier: paint layers and generated patches stay above or below it and cannot be dragged across.

**Your own formats.** An administrator adds named formats in Admin, Settings, Generation, *Expand formats*: a name, a ratio such as 5:2, and who is offered it (everyone or one group).

## Edits save themselves

As soon as you change a picture, the editor saves the edit in the background. You do not need to press anything. A small indicator in the editor shows when it is saving, saved, or waiting for your connection. If you lose your connection, the editor keeps your changes and sends them when you are back online.

Saved edits are private to your account and stay in your list until you delete them. Saving an edit does not create a new picture; use **Save** or **Save as new** when you want the finished result in your Library.

## Finding and continuing an edit

Open a new Image editor tab to see **Recent edits**, twelve to a page, with numbered pages underneath when you have more.

- **Continue** — click a picture or its name (**Continue this edit**). The edit opens in the tab you are in, with the picture and every step you made, ready to carry on.
- **Search** — type in the search box to find an edit by its name or by the name of the picture it started from.
- Each edit shows its name, where its picture came from (History, Library or an uploaded file), and when you last changed it. An edit you have not named uses the picture's file name.

## Rename, duplicate and delete

Use the **More** button (three dots) on an edit:

- **Rename** — type a name in the tile, then press Enter to save or Esc to cancel. Leaving the name empty goes back to the picture's file name.
- **Duplicate** — adds a copy at the top of the list, named after the original, so you can try something different without losing the first version.
- **Delete** — asks you to confirm, then removes the edit and its steps. The original picture is not affected.

## Open in another tab

An edit can only be open in one tab at a time. If you already have an edit open in another tab, its tile in the list is marked **Open in another tab**, and clicking it takes you to that tab instead of opening a second copy.

## The same edit changed in two places

If the same edit is changed somewhere else while you have it open, for example on another device, the editor does not overwrite either version. It shows a notice with two choices:

- **Load the newer one** — replaces what is in your tab with the newer saved version.
- **Keep mine as a copy** — saves what you have as a new edit, so both versions survive.

If an edit has grown too large to save, the editor tells you in the same place. Your work in the tab is not lost.

If you delete an edit that is open in another tab, that tab keeps its picture and steps and saves them again as a new edit the next time you make a change.
