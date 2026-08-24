/**
 * The boundary between a host and the media editors.
 *
 * The host owns the entry points — which tool a given kind offers, which item
 * the tool was pressed on, and what to do with whatever comes back. It does
 * NOT own the editing surfaces (crop & frame, trim in/out, trim on waveform,
 * extract a frame, create inpainting mask); those are separate components with
 * their own backend.
 *
 * Everything crosses this boundary as one request object and one result, so a
 * new editor is wired by handling one more `kind` rather than by growing
 * another pair of `showXModal` flags and another `{#if}` in a template.
 *
 * An edit is always performed by the SERVER and always yields a real library
 * resource. Two reasons, and the first is decisive: there is no reasonable
 * in-browser re-encode of a video or an audio container, which is exactly what
 * the ffmpeg-backed edit endpoint exists for - an editor that handed back
 * rendered bytes could not implement the two kinds that need it most. The
 * second is identity: re-uploading a rendered file mints a new resource and
 * loses the original's tags and collection memberships, so it cannot express
 * the replace-in-place the API deliberately supports.
 */

import type { EditedMediaItem } from '$lib/services/api/media';

/**
 * Every editor there is.
 *
 * A runtime list rather than a bare union so the surfaces that offer editors
 * can be checked for completeness against it — see `RESOURCE_EDIT_TOOLS`.
 */
export const MEDIA_EDITOR_KINDS = [
	/** Crop & frame — images. */
	'crop',
	/** Trim in/out — video on a rail, audio on a waveform. */
	'trim',
	/** Extract a still from a video at the playhead. */
	'frame',
	/** Paint an inpainting mask. */
	'mask',
	/** Cut audio into consecutive parts of a given length. */
	'split'
] as const;

export type MediaEditorKind = (typeof MEDIA_EDITOR_KINDS)[number];

export type EditorMediaKind = 'image' | 'video' | 'audio';

/** What an editor opens on, however the host came by it. */
export interface MediaEditorSource {
	/** Servable URL. Never a blob handle - see `mediaLoaderUpload.ts`. */
	url: string;
	kind: EditorMediaKind;
	/** What to show in the title bar. */
	fileName: string;
	/**
	 * The `uploads` row id, when the caller already knows it. Absent for a
	 * MediaLoader value, which carries a path rather than a row id; the editors
	 * resolve it themselves, copying a generated file into the library first
	 * when that is the only way to give the edit something to work on.
	 */
	itemId?: string | null;
	/** The stored path, e.g. `uploads/<uuid>.png` - how the row is resolved. */
	storedPath?: string | null;
	width?: number | null;
	height?: number | null;
	durationSeconds?: number | null;
	fps?: number | null;
}

export interface MediaEditorRequest {
	kind: MediaEditorKind;
	source: MediaEditorSource;
	/**
	 * Position in the host's value, or null when there is only one item. An
	 * editor result must be written back to the item it came from — a multi
	 * field that applies a crop to "the current item" applies it to the wrong
	 * tile as soon as the user reorders while the editor is open.
	 */
	itemIndex: number | null;
}

/**
 * What an editor hands back.
 *
 * An `item` result is a real library resource with a served URL - either a new
 * one, or the same row with different bytes behind it, which is what `replaced`
 * distinguishes. An `items` result is what a split produces - several new
 * resources, none of which replace the original (there is no "replace" for a
 * one-becomes-many edit). A `mask` result is a server path for the
 * `${name}_inpaint_mask` sibling channel, which is not a value at all.
 */
export type MediaEditorResult =
	| { type: 'item'; item: EditedMediaItem; replaced: boolean }
	| { type: 'items'; items: EditedMediaItem[] }
	| { type: 'mask'; maskPath: string };

/** Whether the edit takes the resource's place or sits beside it. */
export type MediaEditorSaveMode = 'new' | 'replace';

/**
 * What an editor asks the host to do when the user presses save.
 *
 * The editors know the geometry; they deliberately do not know which endpoint
 * that implies, how the resource behind the media was resolved, or what to do
 * with the answer. That decision is made once, in `MediaEditors.svelte`, rather
 * than four times.
 */
export type EditorCommitRequest =
	| { via: 'operations'; operations: import('./editOperations').EditOperation[]; mode: MediaEditorSaveMode }
	| { via: 'frame'; timeSeconds: number }
	/** Cut into consecutive parts of `partSeconds` each - never a replace. */
	| { via: 'split'; partSeconds: number }
	/** The painted mask as a PNG data URL; the host stores it and returns a path. */
	| { via: 'mask'; dataUrl: string };

export type EditorCommitFn = (request: EditorCommitRequest) => void | Promise<void>;

/** True when this kind of item has an editor to open at all. */
export function hasEditor(kind: MediaEditorKind, mediaKind: EditorMediaKind | null): boolean {
	if (!mediaKind) return false;
	if (kind === 'crop' || kind === 'mask') return mediaKind === 'image';
	if (kind === 'frame') return mediaKind === 'video';
	if (kind === 'split') return mediaKind === 'audio';
	return mediaKind === 'video' || mediaKind === 'audio';
}

/**
 * The editors a stored library resource offers, in the order they are shown.
 *
 * `mask` is deliberately absent: a mask is a generation input bound to a form
 * field, not a property of a stored resource. Every OTHER editor kind must
 * appear here, and `types.test.ts` pins that — this list was once written out
 * by hand at its call site, and a new editor reached the media-loader field
 * while staying invisible in the library for exactly as long as nobody looked.
 */
export const RESOURCE_EDIT_TOOLS: { key: MediaEditorKind; label: string; icon: string }[] = [
	{ key: 'crop', label: 'Crop', icon: 'pencil-square' },
	{ key: 'trim', label: 'Trim', icon: 'sliders' },
	{ key: 'split', label: 'Split', icon: 'split' },
	{ key: 'frame', label: 'Frame', icon: 'photo' }
];

/** Title bar copy, and the label the button carries. */
export function editorTitle(kind: MediaEditorKind, mediaKind: EditorMediaKind | null): string {
	switch (kind) {
		case 'crop':
			return 'Crop & frame';
		case 'frame':
			return 'Extract a frame';
		case 'mask':
			return 'Create inpainting mask';
		case 'split':
			return 'Split into parts';
		case 'trim':
			return mediaKind === 'audio' ? 'Trim on waveform' : 'Trim in / out';
	}
}

/**
 * True when the editor edits the media itself, and so needs a library resource
 * behind it. A mask is painted over the media and stored beside it; it changes
 * nothing about the resource and needs no row.
 */
export function editsTheResource(kind: MediaEditorKind): boolean {
	return kind !== 'mask';
}
