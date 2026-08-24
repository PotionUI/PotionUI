// Pure value<->segments parsing for InlineChipEditor.svelte. Extracted
// unchanged — see InlineChipEditor.svelte for the DOM-facing half.
import type { ChipData } from '$lib/types/segments';
import { parsePromptTokens } from '$lib/utils/promptTokens';

export interface ContentSegment {
	type: 'text' | 'chip' | 'group' | 'variable';
	content: string;
	chipId?: string;
	chipData?: ChipData;
	groupRaw?: string;
	variableRaw?: string;
	variableName?: string;
}

// Paths with spaces use bracket format: #[path with spaces]
// Simple paths use plain format: #simplepath
export function encodePathForText(path: string): string {
	if (path.includes(' ')) {
		return `#[${path}]`;
	}
	return `#${path}`;
}

// match[1] is bracketed path, match[2] is simple path
export function decodePathFromMatch(match: RegExpExecArray): string {
	return match[1] || match[2];
}

/** Split a #chip-free text run further into text/{a|b|c}-group/${name}-variable segments. */
export function splitPromptTokensInText(text: string): ContentSegment[] {
	const tokens = parsePromptTokens(text);
	if (tokens.length === 1 && tokens[0].type === 'text') {
		return [{ type: 'text', content: text }];
	}
	const out: ContentSegment[] = [];
	for (const tok of tokens) {
		if (tok.type === 'text') {
			if (tok.raw.length > 0) out.push({ type: 'text', content: tok.raw });
		} else if (tok.type === 'group') {
			out.push({ type: 'group', content: tok.raw, groupRaw: tok.raw });
		} else {
			out.push({ type: 'variable', content: tok.raw, variableRaw: tok.raw, variableName: tok.name });
		}
	}
	return out;
}

/** Original #chip-only segmentation (unchanged), used as the first pass by parseValueToSegments. */
export function parseChipSegments(text: string, chipsObj: Record<string, ChipData>): ContentSegment[] {
	if (!text) return [];

	const segments: ContentSegment[] = [];
	// Two formats:
	// 1. Bracketed for paths with spaces: #[path with spaces]
	// 2. Simple for paths without spaces: #simplepath.subpath
	const chipPattern = /#\[([^\]]+)\]|#([\w][\w.]*)/g;
	let lastIndex = 0;
	let match;

	// Track which chips we've used (for handling duplicates)
	const usedChipIds = new Set<string>();

	while ((match = chipPattern.exec(text)) !== null) {
		const categoryPath = decodePathFromMatch(match);

		// Find matching chip (not yet used)
		const matchingEntry = Object.entries(chipsObj).find(
			([id, chip]) => chip.categoryPath === categoryPath && !usedChipIds.has(id)
		);

		// Add text before this match
		if (match.index > lastIndex) {
			segments.push({
				type: 'text',
				content: text.substring(lastIndex, match.index)
			});
		}

		if (matchingEntry) {
			const [chipId, chipData] = matchingEntry;
			usedChipIds.add(chipId);
			segments.push({
				type: 'chip',
				content: match[0],
				chipId,
				chipData
			});
		} else {
			// No chip found, treat as plain text
			segments.push({
				type: 'text',
				content: match[0]
			});
		}

		lastIndex = match.index + match[0].length;
	}

	// Add remaining text
	if (lastIndex < text.length) {
		segments.push({
			type: 'text',
			content: text.substring(lastIndex)
		});
	}

	return segments;
}

export function parseValueToSegments(text: string, chipsObj: Record<string, ChipData>): ContentSegment[] {
	if (!text) return [];

	const chipSegments = parseChipSegments(text, chipsObj);
	const out: ContentSegment[] = [];
	for (const seg of chipSegments) {
		if (seg.type === 'text') {
			out.push(...splitPromptTokensInText(seg.content));
		} else {
			out.push(seg);
		}
	}
	return out;
}
