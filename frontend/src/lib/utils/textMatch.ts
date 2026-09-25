import type { PhrasebookFindMode } from '$lib/types/api';

export type TextMatchMode = Extract<PhrasebookFindMode, 'contains' | 'regex'>;

export const REGEX_MAX_LENGTH = 200;

export interface TextMatcher {
	mode: TextMatchMode;
	pattern: RegExp;
}

export interface CompiledTextMatcher {
	matcher: TextMatcher | null;
	error: string | null;
}

export interface HighlightSegment {
	text: string;
	match: boolean;
}

function escapeRegExp(text: string): string {
	return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function compileTextMatcher(
	query: string,
	mode: TextMatchMode = 'contains',
	caseSensitive = false
): CompiledTextMatcher {
	const trimmed = query.trim();
	if (!trimmed) return { matcher: null, error: null };
	const flags = caseSensitive ? 'g' : 'gi';
	if (mode === 'contains') {
		return { matcher: { mode, pattern: new RegExp(escapeRegExp(trimmed), flags) }, error: null };
	}
	if (trimmed.length > REGEX_MAX_LENGTH) {
		return { matcher: null, error: `Regular expression is longer than ${REGEX_MAX_LENGTH} characters` };
	}
	try {
		return { matcher: { mode, pattern: new RegExp(trimmed, flags) }, error: null };
	} catch (err) {
		const reason = err instanceof Error ? err.message : String(err);
		return { matcher: null, error: `Invalid regular expression: ${reason}` };
	}
}

export function matchRanges(text: string, matcher: TextMatcher | null): [number, number][] {
	if (!text || !matcher) return [];
	const pattern = new RegExp(matcher.pattern.source, matcher.pattern.flags);
	const ranges: [number, number][] = [];
	let found: RegExpExecArray | null;
	while ((found = pattern.exec(text)) !== null) {
		const end = found.index + found[0].length;
		if (end > found.index) ranges.push([found.index, end]);
		else pattern.lastIndex = found.index + 1;
	}
	return ranges;
}

export function textMatches(matcher: TextMatcher | null, ...texts: (string | null | undefined)[]): boolean {
	if (!matcher) return true;
	return texts.some((text) => matchRanges(text ?? '', matcher).length > 0);
}

export function segmentsFromRanges(text: string, ranges: readonly (readonly [number, number])[]): HighlightSegment[] {
	if (!text) return [];
	const sorted = ranges
		.map(([start, end]) => [Math.max(0, Math.min(text.length, start)), Math.max(0, Math.min(text.length, end))] as const)
		.filter(([start, end]) => end > start)
		.sort((a, b) => a[0] - b[0]);
	const merged: [number, number][] = [];
	for (const [start, end] of sorted) {
		const last = merged[merged.length - 1];
		if (last && start <= last[1]) last[1] = Math.max(last[1], end);
		else merged.push([start, end]);
	}
	const segments: HighlightSegment[] = [];
	let cursor = 0;
	for (const [start, end] of merged) {
		if (start > cursor) segments.push({ text: text.slice(cursor, start), match: false });
		segments.push({ text: text.slice(start, end), match: true });
		cursor = end;
	}
	if (cursor < text.length) segments.push({ text: text.slice(cursor), match: false });
	return segments;
}

export function highlightSegments(text: string, matcher: TextMatcher | null): HighlightSegment[] {
	return segmentsFromRanges(text, matchRanges(text, matcher));
}
