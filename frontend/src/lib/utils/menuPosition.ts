// Fixed-position placement for the segment action menu, used by
// PromptSegment.svelte. Extracted from its original computeMenuPosition().

export const MENU_GAP = 4;
export const MENU_HEIGHT_ESTIMATE = 440;

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
