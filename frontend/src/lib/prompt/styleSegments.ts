import type { PresetStyle } from '$lib/types/api';
import type { Segment } from '$lib/types/segments';
import { isPristinePlaceholderList } from '$lib/utils/richSegments';

/** A style's two prompt-wrapping cards and (when the style declares one) its negative
 *  card each get a deterministic id, so re-applying finds and replaces exactly the
 *  right cards and "which style is applied" needs no state beyond the segments
 *  themselves — see `appliedStyleTag`. */
export type StyleSegmentPart = 'prepend' | 'append' | 'negative';

export interface StyleSegmentTag {
	presetId: string;
	styleId: string;
	part: StyleSegmentPart;
}

const STYLE_TAG_PREFIX = 'style:';

export function styleSegmentId(presetId: string, styleId: string, part: StyleSegmentPart): string {
	return `${STYLE_TAG_PREFIX}${presetId}:${styleId}:${part}`;
}

export function parseStyleSegmentId(id: string): StyleSegmentTag | null {
	if (!id || !id.startsWith(STYLE_TAG_PREFIX)) return null;
	const rest = id.slice(STYLE_TAG_PREFIX.length);
	const parts = rest.split(':');
	if (parts.length !== 3) return null;
	const [presetId, styleId, part] = parts;
	if (!presetId || !styleId) return null;
	if (part !== 'prepend' && part !== 'append' && part !== 'negative') return null;
	return { presetId, styleId, part };
}

/**
 * The style currently applied to the prompt, derived purely from the tagged
 * prepend/append pair in `promptSegments` — both cards must still be present and
 * reference the same (presetId, styleId). Deleting either one through the ordinary
 * segment delete action breaks the pair and this returns `null` with no separate
 * "applied style" bookkeeping to clear.
 */
export function appliedStyleTag(promptSegments: readonly Segment[]): { presetId: string; styleId: string } | null {
	let prepend: StyleSegmentTag | null = null;
	let append: StyleSegmentTag | null = null;
	for (const segment of promptSegments) {
		const tag = parseStyleSegmentId(segment.id);
		if (!tag) continue;
		if (tag.part === 'prepend') prepend = tag;
		else if (tag.part === 'append') append = tag;
	}
	if (!prepend || !append) return null;
	if (prepend.presetId !== append.presetId || prepend.styleId !== append.styleId) return null;
	return { presetId: prepend.presetId, styleId: prepend.styleId };
}

export function isStyleApplied(promptSegments: readonly Segment[], presetId: string, styleId: string): boolean {
	const tag = appliedStyleTag(promptSegments);
	return !!tag && tag.presetId === presetId && tag.styleId === styleId;
}

function withoutStyleSegments(segments: readonly Segment[]): Segment[] {
	return segments.filter((segment) => !parseStyleSegmentId(segment.id));
}

function baseList(segments: readonly Segment[]): Segment[] {
	const list = segments.length && isPristinePlaceholderList(segments as Segment[]) ? [] : [...segments];
	return withoutStyleSegments(list);
}

function styleContentSegment(id: string, content: string, name: string): Segment {
	return {
		id,
		type: 'content',
		content,
		chips: {},
		enabled: true,
		name
	};
}

// The segment joiner already inserts its own separator between cards — a
// prepend/append/negative string ending or starting with one of its own
// (author data, not something we control) doubles up as a visible ",," or
// dangling ", " once wrapped. Trimming here only affects the card's own
// content, never `style.prepend`/`append`/`negative` themselves.
const LEADING_SEPARATORS = /^[\s,]+/;
const TRAILING_SEPARATORS = /[\s,]+$/;

function trimTrailingSeparators(text: string): string {
	return text.replace(TRAILING_SEPARATORS, '');
}

function trimLeadingSeparators(text: string): string {
	return text.replace(LEADING_SEPARATORS, '');
}

function trimSeparators(text: string): string {
	return trimLeadingSeparators(trimTrailingSeparators(text));
}

export interface StylePromptState {
	promptSegments: Segment[];
	negativeSegments: Segment[];
}

/**
 * Apply `style` to the prompt: its `prepend` card becomes the first positive
 * segment, its `append` card the last, and — when the style declares one — a
 * `negative` card is appended to the negative segment list. Every user segment
 * already between the previous prepend/append pair is preserved untouched.
 *
 * Any previously applied style's cards (whichever preset/style they came from,
 * positive or negative) are removed first, so applying a second style replaces
 * the first rather than stacking.
 */
export function applyStyleToPrompt(
	promptSegments: readonly Segment[],
	negativeSegments: readonly Segment[],
	presetId: string,
	style: PresetStyle
): StylePromptState {
	const basePositive = baseList(promptSegments);
	const baseNegative = baseList(negativeSegments);

	const prependSegment = styleContentSegment(
		styleSegmentId(presetId, style.id, 'prepend'),
		trimTrailingSeparators(style.prepend),
		`${style.name} · start`
	);
	const appendSegment = styleContentSegment(
		styleSegmentId(presetId, style.id, 'append'),
		trimLeadingSeparators(style.append),
		`${style.name} · end`
	);

	const nextNegative = style.negative
		? [
				...baseNegative,
				styleContentSegment(
					styleSegmentId(presetId, style.id, 'negative'),
					trimSeparators(style.negative),
					`${style.name} · negative`
				)
			]
		: baseNegative;

	return {
		promptSegments: [prependSegment, ...basePositive, appendSegment],
		negativeSegments: nextNegative
	};
}

/**
 * Removes whichever style's tagged cards are present — the picker's "Remove
 * style" action. Unlike `applyStyleToPrompt`'s `baseList`, this never touches
 * a pristine placeholder segment; it only strips the tagged cards.
 */
export function clearStyle(promptSegments: readonly Segment[], negativeSegments: readonly Segment[]): StylePromptState {
	return {
		promptSegments: withoutStyleSegments(promptSegments),
		negativeSegments: withoutStyleSegments(negativeSegments)
	};
}
