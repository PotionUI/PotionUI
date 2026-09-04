import type { Segment } from '$lib/types/segments';
import { richTextToPlainText } from './richTextUtils';
import { isSegmentEnabled } from './richSegments';

/** The card's header action cluster is the same set on every card: it never
 *  changes shape between states, only the first action's sense flips. Keeping
 *  that here rather than in the component is what makes "identical on all
 *  cards" testable instead of a thing to eyeball. Below the header's own
 *  collapse width these actions move into the overflow menu instead of
 *  disappearing — see PromptSegment.svelte's `clusterCollapsed`. */
export type SegmentFooterActionId = 'toggleDisabled' | 'duplicate' | 'editDetails' | 'saveAsSegment';

export interface SegmentFooterAction {
	/** Also the event PromptSegment dispatches, so the component maps 1:1. */
	id: SegmentFooterActionId;
	label: string;
	icon: string;
}

/** The card head's placeholder for a segment with no name yet. Short by
 *  design — it has to survive the header's narrowest collapsed width. */
export const UNNAMED_SEGMENT_PLACEHOLDER = 'Unnamed';

export const DISABLED_SEGMENT_NOTE = 'excluded from the resolved prompt';

/** Segments are numbered by position in the list, from one. A BREAK row and a
 *  disabled card each consume a number, so "#3" means the same segment
 *  wherever you read it. */

/** The card gutter: zero-padded so a column of numbers aligns. */
export function formatSegmentIndex(index: number): string {
	return String(index + 1).padStart(2, '0');
}

export function segmentDisplayName(segment: Pick<Segment, 'name' | 'title'>): string | null {
	return (segment.name || segment.title || '').trim() || null;
}

/** The card's own char count: the text this segment would contribute, with its
 *  chips resolved to their chosen values. Independent of `enabled` — a disabled
 *  card still reports what it holds, even though it contributes nothing to the
 *  resolved prompt. */
export function segmentCharCount(segment: Pick<Segment, 'content' | 'chips'>): number {
	const chips = segment.chips || {};
	const content = segment.content || '';
	return (Object.keys(chips).length ? richTextToPlainText(content, chips) : content).length;
}

// Order and glyphs match the mock's header cluster: details, save, duplicate,
// then toggle — details/save are also the two actions the `optional` class
// hides first at a narrow container width (see PromptSegment.svelte / the
// ported `@container (max-width: 43rem)` rule).
export function segmentFooterActions(segment: Segment): SegmentFooterAction[] {
	const disabled = !isSegmentEnabled(segment);
	return [
		{ id: 'editDetails', label: 'Details', icon: 'pencil' },
		{ id: 'saveAsSegment', label: 'Save', icon: 'save' },
		{ id: 'duplicate', label: 'Duplicate', icon: 'copy' },
		{
			id: 'toggleDisabled',
			label: disabled ? 'Enable' : 'Disable',
			icon: disabled ? 'eye' : 'eye-off'
		}
	];
}

/** The two footer actions the mock's `.optional` class folds away first at a
 *  narrow container width — everything else in the cluster stays put. */
export function isOptionalFooterAction(id: SegmentFooterActionId): boolean {
	return id === 'editDetails' || id === 'saveAsSegment';
}
