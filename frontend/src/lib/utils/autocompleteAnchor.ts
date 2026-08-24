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

const EDGE_GUTTER = 8;

function clamp(value: number, min: number, max: number): number {
	return Math.min(Math.max(value, min), max);
}

export function computeAutocompletePlacement(
	rect: AnchorRect,
	viewport: AnchorViewport,
	estimatedHeight: number = AUTOCOMPLETE_ESTIMATED_HEIGHT
): AnchorPlacement {
	const anchorTop = clamp(rect.top, 0, viewport.height);
	const anchorBottom = clamp(rect.bottom, 0, viewport.height);
	const spaceBelow = viewport.height - anchorBottom;

	// Flip above the trigger when there isn't room below and above has more —
	// the chat composer sits at the bottom of the viewport, where a
	// below-anchored list renders off-screen. Deliberately unchanged.
	const openAbove = spaceBelow < estimatedHeight && anchorTop > spaceBelow;

	const width = Math.max(rect.width, AUTOCOMPLETE_MIN_WIDTH);
	const left = clamp(rect.left, EDGE_GUTTER, Math.max(EDGE_GUTTER, viewport.width - width - EDGE_GUTTER));
	// A tall anchor — a multi-line segment card — can start high enough that
	// `openAbove` stays false while its bottom edge leaves less than a list's
	// height below it, which put the list partly past the bottom of the screen.
	// Pull it up far enough to fit; it never moves down, so a short anchor with
	// room below is unaffected.
	const top = Math.min(anchorBottom, Math.max(EDGE_GUTTER, viewport.height - estimatedHeight - EDGE_GUTTER));

	return {
		top,
		bottom: viewport.height - anchorTop,
		left,
		width,
		openAbove
	};
}
