/**
 * The glyphs this field draws inline.
 *
 * `$lib/utils/IconLibrary` carries most of them, but not the media-editing
 * verbs (crop, scissors, expand) or the two measurement marks the metadata
 * chips use (ruler, weight) — and the chips need a raw `d` anyway, because
 * they draw a 10px glyph inside a chip rather than an `<Icon>` box.
 *
 * Both maps are TOTAL over their key type on purpose: a partial map forces a
 * null check into the markup, and a null check in the markup is how a tool
 * ends up rendering an empty `<path>`.
 */

import type { MediaToolKey } from './mediaLoaderFaces';
import type { MetaChipIcon } from './mediaLoaderMeta';

const CROP = 'M6.13 1L6 16a2 2 0 002 2h15M1 6.13L16 6a2 2 0 012 2v15';
const SCISSORS =
	'M9.879 14.121a3 3 0 10-4.243 4.243 3 3 0 004.243-4.243zm0 0L20 4M14.12 14.121a3 3 0 114.243 4.243 3 3 0 01-4.243-4.243zm0 0L4 4';
const EXPAND = 'M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4';
const RULER =
	'M2.25 15.75l5.159-5.159a2.25 2.25 0 013.182 0l5.159 5.159m-1.5-1.5l1.409-1.409a2.25 2.25 0 013.182 0l2.909 2.909m-18 3.75h16.5a1.5 1.5 0 001.5-1.5V6a1.5 1.5 0 00-1.5-1.5H3.75A1.5 1.5 0 002.25 6v12a1.5 1.5 0 001.5 1.5z';
const WEIGHT = 'M21 5a9 3 0 11-18 0 9 3 0 0118 0zM3 5v14a9 3 0 0018 0V5M3 12a9 3 0 0018 0';
const CLOCK = 'M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z';
const FILM =
	'M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z';
const DOC = 'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z';
const BRUSH =
	'M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z';
const FRAME =
	'M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z';
const SWAP = 'M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15';
const TRASH =
	'M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16';

/** The edit verb a multi tile's edit button carries. */
export const EDITOR_ICON_PATHS = { crop: CROP, trim: SCISSORS } as const;

export const TOOL_ICON_PATHS: Record<MediaToolKey, string> = {
	crop: CROP,
	mask: BRUSH,
	trim: SCISSORS,
	frame: FRAME,
	full: EXPAND,
	swap: SWAP,
	remove: TRASH
};

export const META_ICON_PATHS: Record<MetaChipIcon, string> = {
	ruler: RULER,
	clock: CLOCK,
	film: FILM,
	weight: WEIGHT,
	doc: DOC
};
