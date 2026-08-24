/**
 * Set by Tooltip.svelte so descendants can tell they already have a tooltip.
 *
 * Controls that carry their own native `title` as a fallback hint (IconButton)
 * would otherwise render twice inside a Tooltip: the styled one and the
 * browser's. Reading this lets them drop the attribute instead of every call
 * site having to remember to.
 */
export const INSIDE_TOOLTIP_CONTEXT_KEY = 'insideTooltip';
