// Fixed-position placement for the segment action menu, used by
// PromptSegment.svelte. Extracted from its original computeMenuPosition().

export const MENU_GAP = 4;
export const MENU_HEIGHT_ESTIMATE = 440;
export const MENU_EDGE_GUTTER = 8;

export interface AnchoredMenuPosition {
	top: number;
	left: number;
}

/**
 * `top`/`left` (in px) for a `position: fixed` panel anchored below `trigger`,
 * clamped so it never runs off either horizontal edge of the viewport. Used by
 * dropdowns that escape an ancestor stacking context via `use:portal` — once
 * portaled, `left: 0`/`right: 0` CSS shorthand no longer has the original
 * trigger to anchor against, so the position has to be computed in px.
 */
export function computeAnchoredMenuPosition(
	trigger: HTMLElement,
	options: { width?: number; align?: 'left' | 'right'; gap?: number; edgeGutter?: number } = {}
): AnchoredMenuPosition {
	const { width = 200, align = 'left', gap = MENU_GAP, edgeGutter = MENU_EDGE_GUTTER } = options;
	const rect = trigger.getBoundingClientRect();
	let left = align === 'right' ? rect.right - width : rect.left;
	const maxLeft = window.innerWidth - width - edgeGutter;
	if (left > maxLeft) left = maxLeft;
	if (left < edgeGutter) left = edgeGutter;
	return { top: rect.bottom + gap, left };
}

/** `right`/`top`-or-`bottom` inline style string, flipping upward when there's
 *  more room above the trigger than below and not enough room below to fit
 *  `heightEstimate`. */
export function computeFixedMenuPosition(
	trigger: HTMLElement,
	heightEstimate: number = MENU_HEIGHT_ESTIMATE,
	gap: number = MENU_GAP
): string {
	const rect = trigger.getBoundingClientRect();
	const viewportHeight = window.innerHeight;
	const spaceBelow = viewportHeight - rect.bottom;
	const spaceAbove = rect.top;
	const openUpward = spaceBelow < heightEstimate && spaceAbove > spaceBelow;
	const right = window.innerWidth - rect.right;
	return openUpward
		? `right: ${right}px; bottom: ${viewportHeight - rect.top + gap}px;`
		: `right: ${right}px; top: ${rect.bottom + gap}px;`;
}
