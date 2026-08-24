/**
 * Wraps the CSS Custom Highlight API (`CSS.highlights`) so `InlineChipEditor.svelte`
 * can mark trigger-word occurrences inside its contenteditable without ever
 * touching the DOM tree — no wrapper spans, no caret jump, no interference with
 * IME composition or the browser's native undo stack (the usual failure modes of
 * highlighting-by-DOM-mutation in a live-typed contenteditable).
 *
 * `CSS.highlights` is a single registry for the whole document, keyed by
 * highlight name. All `InlineChipEditor` instances share one `Highlight` object
 * under `HIGHLIGHT_NAME`; each instance owns a subset of the `Range`s in it
 * (tracked by an opaque `owner` key) so unmounting one editor, or it no longer
 * having any matches, doesn't clear another editor's highlights.
 *
 * Support: Chrome/Edge 105+, Safari 17.2+ (Firefox behind a flag as of this
 * writing). `isTriggerHighlightSupported()` lets callers no-op cleanly where
 * it's unavailable — this is a "nice to spot it faster" affordance, not
 * functionality anything depends on.
 */

const HIGHLIGHT_NAME = 'potionui-trigger-word';

let sharedHighlight: Highlight | null = null;
const rangesByOwner = new Map<symbol, Range[]>();

function isTriggerHighlightSupported(): boolean {
	return (
		typeof window !== 'undefined' &&
		typeof Highlight !== 'undefined' &&
		typeof CSS !== 'undefined' &&
		!!CSS.highlights
	);
}

function ensureHighlight(): Highlight | null {
	if (!isTriggerHighlightSupported()) return null;
	if (!sharedHighlight) {
		sharedHighlight = new Highlight();
		CSS.highlights.set(HIGHLIGHT_NAME, sharedHighlight);
	}
	return sharedHighlight;
}

export function setOwnerTriggerHighlightRanges(owner: symbol, ranges: Range[]): void {
	const highlight = ensureHighlight();
	if (!highlight) return;
	for (const range of rangesByOwner.get(owner) || []) highlight.delete(range);
	if (ranges.length > 0) {
		rangesByOwner.set(owner, ranges);
	} else {
		rangesByOwner.delete(owner);
	}
	for (const range of ranges) highlight.add(range);
}

export function clearOwnerTriggerHighlightRanges(owner: symbol): void {
	setOwnerTriggerHighlightRanges(owner, []);
}
