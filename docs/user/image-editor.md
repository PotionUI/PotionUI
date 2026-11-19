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

The side panel has two lists: **Layers** on top and **Edits** under it. Layers stack up to make your picture; edits then change that whole layered picture. Click the header of either list to fold or unfold it: the count stays in the header, the other list takes the room, and the editor remembers which lists you folded. The **+** and the three dots in the header do their own job and do not fold anything. You can also reach a header with the keyboard and press **Enter** or **Space**.

The **Layers** list holds the original picture at the bottom and everything placed on it: paint layers, pictures you add, and the results of **Edit a region**, **Remove an object** and **Replace the background**. The newest layer is on top. Each row shows a thumbnail, the name, the opacity and an eye.

**Add a layer.** Press **+** in the Layers header. The menu says where the layer will go: *Add above* the layer you have selected, or *Add on top* otherwise. Choose:

- **Empty layer** adds a transparent layer right away, selects it and opens the brush on it.
- **Picture from Library**, **Picture from History** or **Picture from a file** adds the picture as a layer right away. Drag it to move and use the handles to scale and rotate; it settles in place when you pick another tool or layer, or press **Enter**.

The new row shows in the Layers list the moment you add it, even after you added a look or a crop, so that look or crop applies to it too. Nothing else ever adds a layer: switching tools, clicking elsewhere or leaving Paint leaves the list as it is.

**Paint on a layer.** Click a layer row to select it. The brush, eraser, fill and selection tools then work straight on that layer, and the Paint panel says which one (*Painting on Paint 2*). There is no Apply step: every stroke is part of the layer as soon as you lift the pen, and the layer saves itself in the background a moment after you stop. If the selected row is **Original** or a layer you cannot paint on, such as a region edit, your first stroke adds one new layer right above it and selects it. The Layers list is the only layer list, so adding, reordering, renaming, fading and hiding all happen there, never inside the Paint panel. Layers always use the size of the original picture: while you paint, the canvas shows the layers without your edits, so the strokes stay in place when you change a crop later.

**Undo.** **Undo** steps back one stroke at a time on whichever layer it was made, and also undoes adding, removing, reordering, fading, renaming, duplicating and merging layers, all in the order you did them.

**Cut and paste.** A cut or pasted piece floats above the selected layer until you press **Enter** or pick another tool, which places it into that layer. **Keep as layer** makes the piece a layer of its own instead.

**Fade, hide, rename.** The selected layer shows an **Opacity** slider under its row. The eye hides a layer without deleting it. Use the row menu (the three dots) for **Rename**, **Duplicate**, **Merge down**, **Move up**, **Move down** and **Delete**. A region edit's row also shows its mask, with **Show mask** to outline it on the canvas.

**Reorder.** Drag a row by its grip, or select it and press **Alt+Up** or **Alt+Down**. On a phone, use **Move up** and **Move down** in the row menu. Layers move among layers.

**Region edits.** **Edit a region**, **Remove an object** and **Replace the background** work on the layered picture before your edits: while one of them is open, the canvas shows the layers without the crop or looks, and you mark the area there. The result lands on top of the layers, and a later crop or look applies to it like to any other layer.

## Edits

The **Edits** list holds everything that changes the whole picture: looks and effects, light and colour, crop, resize, rotate, canvas size and upscale. They are numbered in the order they apply, newest on top, each with an eye and a delete button on hover. Drag them, or use **Alt+Up** and **Alt+Down**, to change their order among the edits.

Edits apply to the whole layered picture: first all visible layers are combined, bottom to top with their opacity, then each visible edit is applied in order. When you save, the file is made the same way, so it matches what you see.

**Older edits.** An edit made before layers and edits were split may have a look or a crop between its layers, where it only reached the layers beneath it. Such an edit still opens and saves exactly as before, and a note in the panel says so. Its layers and edits cannot be reordered until you press **Switch to layers**, which moves every layer under the edits. The result may change, for example a layer painted after a crop is then placed on the original picture; **Undo** brings the old order back. Dismiss the note to keep the old order.

**On a phone.** Layers and Edits are two sections of one bottom sheet, each folding from its header, with 48 pixel rows and a 44 pixel **+** button. Adding a layer closes the sheet and opens Paint.

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

The result is a layer called **Replace background** (or a region edit for Describe) in the **Layers** list. Hide it with the eye or delete it to bring the original background back, and press **Replace the background** again to try another choice on the same mask.
## Expand a picture to another format

**Expand** (in the Generate group of the rail) puts your picture on a larger canvas and generates the new area so it continues the picture. Use it to turn a square into a story, a landscape or a wide banner.

1. Pick one or more **formats**: Square 1:1, Portrait 4:5, Story 9:16, Landscape 16:9, Wide banner 3:1, any format your administrator added for everyone or for your group, or **Custom** with your own width and height. Each row shows the size the picture would grow to. The picture is never shrunk or cropped: the canvas is the smallest one of that shape that holds it.
2. **Drag the picture** in the Position preview to choose where the new area goes, or select it and use the arrow keys (Shift for bigger steps). **Center** puts it back in the middle. The position applies to every format you picked.
3. Write what should appear in the new area, or leave the box empty: the model then continues the picture.
4. Press **Generate**.

Nothing under the new step is changed: hide the step and the original is back. A big expansion is made in passes, each one growing the picture by a bounded step so the model always works at sizes it handles well; the panel says when a format takes more than one pass.

**Several formats at once.** The first format becomes a step in the edit you are working on. Every other format becomes an edit of its own, a copy of this edit with its own Expand step, started at the same time and queued with your other generations. Changing the canvas size is a different result for each format, so they cannot be steps of one stack. Open the others from **Recent edits**; they finish by themselves whether or not you open them.

Expand changes the size of the picture, so it is an edit, not a layer: it sits in the **Edits** list and applies to the whole layered picture, like a crop.

**Your own formats.** An administrator adds named formats in Admin, Settings, Generation, *Expand formats*: a name, a ratio such as 5:2, and who is offered it (everyone or one group).

## Upscale

**Upscale** (in the Generate group on the rail) makes the whole picture bigger with an upscaling setup. There is no prompt and nothing to mark.

1. Pick **2×** or **4×**, or type a width or a height under **Or set a size**. The other side follows, so the shape of the picture is kept. **Result** shows the size you will get. A size that is not larger than the picture, or larger than 16384 pixels on a side, cannot be started and says why.
2. Choose a setup in the bar below the canvas the first time, then press **Upscale**.
3. A large picture is cut into overlapping tiles and each tile is enlarged in turn. The step shows which tile is being worked on, for example *Upscaling 3 of 6*, and a bar that fills as the tiles finish. The tiles are blended back together, so no seams show.

The result is a step in the **Edits** list like a crop or a resize. Hide it with the eye to see the smaller picture again, remove it, or press **Try again** to run it once more. The status line under the canvas shows the new size.

**What happens to your other edits.** An upscale always works on the picture as it stands beneath it, so everything beneath is baked into the enlarged picture. Those edits stay in the list and can still be hidden or opened, but they are no longer applied a second time on top.

- If you change something beneath the upscale (a crop, a look, paint, a region edit), the upscale is marked *The picture changed* and is left out until you press **Redo on the new picture**.
- Edits above the upscale are kept and follow the new size. A crop or a resize that was measured in pixels is multiplied by the same factor, so it covers the same part of the picture. A region edit above keeps its place, because it was placed on the picture as a fraction of its width and height.
- Paint layers and picture layers keep their place, and are resampled to the larger picture.
- A marked area that you have not run yet has to be marked again after the picture changes size.
- Paint cannot be dragged across an upscale, in the same way it cannot cross a crop.

Hiding the upscale does not rescale the edits above it; they use the sizes they have.

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
