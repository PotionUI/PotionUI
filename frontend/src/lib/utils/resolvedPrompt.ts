import type { RichSegment, Segment } from '$lib/types/segments';
import { flattenRichSegments, isSegmentEnabled } from './richSegments';

/** Counts for the resolved panel's header. Both describe what the model
 *  actually receives: a disabled segment is absent from the panel entirely,
 *  so it contributes neither characters nor breaks. */
export interface ResolvedPromptStats {
	chars: number;
	breaks: number;
}

export function resolvedPromptStats(
	segments: readonly (Segment | RichSegment)[] = []
): ResolvedPromptStats {
	return {
		chars: flattenRichSegments(segments).length,
		breaks: segments.filter((segment) => segment.type === 'break' && isSegmentEnabled(segment)).length
	};
}

/**
 * A span of the resolved string, tagged with how the panel should render it.
 * `value` is a substitution — a chip's chosen value or a `${variable}` marker —
 * and reads at full strength against the muted body. `emphasis` is attention
 * syntax, `muted` is bracketed de-emphasis, `break` is the BREAK pill.
 */
export type ResolvedPromptToken =
	| { kind: 'text'; text: string }
	| { kind: 'value'; text: string }
	| { kind: 'emphasis'; text: string }
	| { kind: 'muted'; text: string }
	| { kind: 'break'; text: string };

interface Candidate {
	start: number;
	end: number;
	priority: number;
	kind: ResolvedPromptToken['kind'];
}

const PATTERNS: Array<{ regex: RegExp; kind: ResolvedPromptToken['kind'] }> = [
	{ regex: /\bBREAK\b/g, kind: 'break' },
	{ regex: /\$\{[^}]*\}/g, kind: 'value' },
	{ regex: /\({2,}[^()]*\){2,}/g, kind: 'emphasis' },
	{ regex: /\([^()]*\)/g, kind: 'emphasis' },
	{ regex: /\[[^\]]*\]/g, kind: 'muted' }
];

function collectChipValues(segments: readonly (Segment | RichSegment)[]): string[] {
	const values = new Set<string>();
	for (const segment of segments) {
		if (!isSegmentEnabled(segment) || segment.type === 'break') continue;
		for (const chip of Object.values(segment.chips || {})) {
			const value = (chip?.value || '').trim();
			if (value) values.add(value);
		}
	}
	return [...values];
}

/** A chip value only counts where it stands as its own run of text, so a chip
 *  whose value is "sun" never lights up the middle of "sunset". */
function isFreeStanding(text: string, start: number, end: number): boolean {
	const before = start > 0 ? text[start - 1] : ' ';
	const after = end < text.length ? text[end] : ' ';
	return /[\s,(\[]/.test(before) && /[\s,)\].]/.test(after);
}

export function resolvedPromptTokens(
	segments: readonly (Segment | RichSegment)[] = []
): ResolvedPromptToken[] {
	const text = flattenRichSegments(segments);
	if (!text) return [];

	const candidates: Candidate[] = [];

	PATTERNS.forEach(({ regex, kind }, priority) => {
		for (const match of text.matchAll(regex)) {
			candidates.push({
				start: match.index,
				end: match.index + match[0].length,
				priority,
				kind
			});
		}
	});

	const chipPriority = PATTERNS.length;
	for (const value of collectChipValues(segments)) {
		let from = 0;
		while (true) {
			const at = text.indexOf(value, from);
			if (at === -1) break;
			if (isFreeStanding(text, at, at + value.length)) {
				candidates.push({ start: at, end: at + value.length, priority: chipPriority, kind: 'value' });
			}
			from = at + 1;
		}
	}

	candidates.sort((a, b) => a.start - b.start || a.priority - b.priority || b.end - a.end);

	const tokens: ResolvedPromptToken[] = [];
	let cursor = 0;

	for (const candidate of candidates) {
		if (candidate.start < cursor) continue;
		if (candidate.start > cursor) {
			tokens.push({ kind: 'text', text: text.slice(cursor, candidate.start) });
		}
		tokens.push({ kind: candidate.kind, text: text.slice(candidate.start, candidate.end) });
		cursor = candidate.end;
	}

	if (cursor < text.length) tokens.push({ kind: 'text', text: text.slice(cursor) });

	return tokens;
}
