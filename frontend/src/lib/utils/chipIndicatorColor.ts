// Shared category-indicator color for the chip components, which used
// to carry their own 17-entry raw Tailwind palette (including banned zinc-*);
// this maps the same externally-assigned `colorIndex` onto the fixed 8-slot
// colorblind-safe categorical series in tokens.css (--viz-1..--viz-8) instead.
const CHIP_INDICATOR_SLOTS = 8;

/** `colorIndex` -> `rgb(var(--viz-N))`, N in 1..8. Negative indices wrap. */
export function chipIndicatorColor(colorIndex: number): string {
	const slot = (((colorIndex % CHIP_INDICATOR_SLOTS) + CHIP_INDICATOR_SLOTS) % CHIP_INDICATOR_SLOTS) + 1;
	return `rgb(var(--viz-${slot}))`;
}
