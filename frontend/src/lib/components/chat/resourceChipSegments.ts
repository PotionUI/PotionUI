// Pure value<->segments parsing for ChatChipInput.svelte, extracted unchanged.
// Deliberate fork of ../chipSegments.ts's #chip pass — see that
// file's header. Do not unify: the divergence (marker syntax, no
// group/variable tokens) is exactly what a later shared core must preserve
// or consciously drop.
import type { ResourceChipData } from '$lib/types/chat';

export interface ContentSegment {
	type: 'text' | 'chip';
	content: string;
	chipId?: string;
	chipData?: ResourceChipData;
}

// Marker regex — same semantics as resourceTokens.ts, kept inline because the
// editor needs a fresh instance per parse and the boundary lookbehind.
export function markerRegex(): RegExp {
	return /(?<![\w@])@(?:\[([^\]]+)\]|([\w][\w.-]*))/g;
}

export function parseValueToSegments(
	text: string,
	resourcesObj: Record<string, ResourceChipData>
): ContentSegment[] {
	if (!text) return [];

	const segments: ContentSegment[] = [];
	const chipPattern = markerRegex();
	let lastIndex = 0;
	let match;

	// Track which chips we've used (for handling duplicates)
	const usedChipIds = new Set<string>();

	while ((match = chipPattern.exec(text)) !== null) {
		const uri = match[1] || match[2];

		// Find matching resource (not yet used)
		const matchingEntry = Object.entries(resourcesObj).find(
			([id, res]) => res.uri === uri && !usedChipIds.has(id)
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
			// No resource entry, treat as plain text
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
