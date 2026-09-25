/**
 * Where the autocomplete list goes, given the rect of the element it is
 * anchored to. Pure so the placement rules can be reasoned about without a
 * browser — `AutocompleteDropdown.svelte` reads the rect and applies the result.
 */

export interface AnchorRect {
	top: number;
	bottom: number;
	left: number;
	width: number;
}

export interface AnchorViewport {
	width: number;
	height: number;
}

export interface AnchorPlacement {
	/** Used when `openAbove` is false. */
	top: number;
	/** Distance from the viewport bottom; used when `openAbove` is true. */
	bottom: number;
	left: number;
	width: number;
	openAbove: boolean;
}

/** The list is capped at max-h-[300px]; headers and hints add a little. Used
 *  only to decide which side has room — the real height never exceeds it. */
export const AUTOCOMPLETE_ESTIMATED_HEIGHT = 340;

/** An inline anchor (a Flow-view text run) can be a few pixels wide — never let
 *  the list collapse with it. */
export const AUTOCOMPLETE_MIN_WIDTH = 320;

export const AUTOCOMPLETE_SEGMENT_WIDTH = 432;
export const AUTOCOMPLETE_SEGMENT_MIN_WIDTH = 384;

const EDGE_GUTTER = 8;

function clamp(value: number, min: number, max: number): number {
	return Math.min(Math.max(value, min), max);
}

function resolveVerticalPlacement(
	anchorTop: number,
	anchorBottom: number,
	viewport: AnchorViewport,
	estimatedHeight: number
): { top: number; bottom: number; openAbove: boolean } {
	const clampedTop = clamp(anchorTop, 0, viewport.height);
	const clampedBottom = clamp(anchorBottom, 0, viewport.height);
	const spaceBelow = viewport.height - clampedBottom;

	// Flip above the trigger when there isn't room below and above has more —
	// the chat composer sits at the bottom of the viewport, where a
	// below-anchored list renders off-screen. Deliberately unchanged.
	const openAbove = spaceBelow < estimatedHeight && clampedTop > spaceBelow;

	// A tall anchor — a multi-line segment card — can start high enough that
	// `openAbove` stays false while its bottom edge leaves less than a list's
	// height below it, which put the list partly past the bottom of the screen.
	// Pull it up far enough to fit; it never moves down, so a short anchor with
	// room below is unaffected.
	const top = Math.min(clampedBottom, Math.max(EDGE_GUTTER, viewport.height - estimatedHeight - EDGE_GUTTER));

	return { top, bottom: viewport.height - clampedTop, openAbove };
}

export function computeAutocompletePlacement(
	rect: AnchorRect,
	viewport: AnchorViewport,
	estimatedHeight: number = AUTOCOMPLETE_ESTIMATED_HEIGHT
): AnchorPlacement {
	const { top, bottom, openAbove } = resolveVerticalPlacement(rect.top, rect.bottom, viewport, estimatedHeight);
	const width = Math.max(rect.width, AUTOCOMPLETE_MIN_WIDTH);
	const left = clamp(rect.left, EDGE_GUTTER, Math.max(EDGE_GUTTER, viewport.width - width - EDGE_GUTTER));

	return { top, bottom, left, width, openAbove };
}

export interface CaretPoint {
	top: number;
	bottom: number;
	left: number;
}

export function caretPointAnchor(parent: HTMLElement, selection: CaretRectSource | null): CaretPoint {
	const box = parent.getBoundingClientRect();
	const fallback = { top: box.top, bottom: box.bottom, left: box.left };
	if (!selection || selection.rangeCount === 0) return fallback;
	const range = selection.getRangeAt(0);
	if (!parent.contains(range.startContainer) || typeof range.getBoundingClientRect !== 'function') return fallback;
	const caret = range.getBoundingClientRect() as { top: number; bottom: number; left?: number; height: number };
	if (caret.height === 0 && caret.top === 0) return fallback;
	return { top: caret.top, bottom: caret.bottom, left: typeof caret.left === 'number' ? caret.left : box.left };
}

export function computeSegmentPickerPlacement(
	point: CaretPoint,
	viewport: AnchorViewport,
	estimatedHeight: number = AUTOCOMPLETE_ESTIMATED_HEIGHT
): AnchorPlacement {
	const { top, bottom, openAbove } = resolveVerticalPlacement(point.top, point.bottom, viewport, estimatedHeight);
	const available = Math.max(viewport.width - EDGE_GUTTER * 2, 0);
	const width = Math.max(Math.min(AUTOCOMPLETE_SEGMENT_MIN_WIDTH, available), Math.min(AUTOCOMPLETE_SEGMENT_WIDTH, available));

	let left = point.left;
	if (left + width > viewport.width - EDGE_GUTTER) {
		left = point.left - width;
	}
	left = clamp(left, EDGE_GUTTER, Math.max(EDGE_GUTTER, viewport.width - width - EDGE_GUTTER));

	return { top, bottom, left, width, openAbove };
}

export interface CaretRectSource {
	rangeCount: number;
	getRangeAt(index: number): { startContainer: Node; getBoundingClientRect?: () => { top: number; bottom: number; height: number } };
}

export interface DockedPreviewPlacement {
	left: number;
	top: number;
	side: 'right' | 'left' | 'below';
}

const PREVIEW_GAP = 12;

export function computeDockedPreviewPlacement(
	pickerRect: { top: number; left: number; right: number; bottom: number },
	viewport: AnchorViewport,
	previewWidth: number
): DockedPreviewPlacement {
	if (pickerRect.right + PREVIEW_GAP + previewWidth <= viewport.width) {
		return { left: pickerRect.right + PREVIEW_GAP, top: pickerRect.top, side: 'right' };
	}
	if (pickerRect.left - PREVIEW_GAP - previewWidth >= 0) {
		return { left: pickerRect.left - PREVIEW_GAP - previewWidth, top: pickerRect.top, side: 'left' };
	}
	return {
		left: clamp(pickerRect.left, EDGE_GUTTER, Math.max(EDGE_GUTTER, viewport.width - previewWidth - EDGE_GUTTER)),
		top: pickerRect.bottom + PREVIEW_GAP,
		side: 'below'
	};
}

export function caretLineAnchor(parent: HTMLElement, selection: CaretRectSource | null): AnchorRect {
	const box = parent.getBoundingClientRect();
	const fallback = { top: box.top, bottom: box.bottom, left: box.left, width: box.width };
	if (!selection || selection.rangeCount === 0) return fallback;
	const range = selection.getRangeAt(0);
	if (!parent.contains(range.startContainer) || typeof range.getBoundingClientRect !== 'function') return fallback;
	const caret = range.getBoundingClientRect();
	if (caret.height === 0 && caret.top === 0) return fallback;
	return { top: caret.top, bottom: caret.bottom, left: box.left, width: box.width };
}
