// Pure value<->segments parsing for InlineChipEditor.svelte. Extracted
// unchanged — see InlineChipEditor.svelte for the DOM-facing half.
import type { ChipData } from '$lib/types/segments';
import { parsePromptTokens } from '$lib/utils/promptTokens';
import type { ResourceRef } from '$lib/utils/promptResources';

export interface ContentSegment {
	type: 'text' | 'chip' | 'group' | 'variable' | 'resource';
	content: string;
	chipId?: string;
	chipData?: ChipData;
	groupRaw?: string;
	variableRaw?: string;
	variableName?: string;
	resourceId?: string;
	resourceRef?: ResourceRef;
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

const CHIP_AND_RESOURCE_PATTERN = /#\[([^\]]+)\]|#([\w]+(?:[.-][\w]+)*)|@\[([A-Za-z_][A-Za-z0-9_-]*):([^\]\r\n]+)\]/g;

export function parseChipSegments(
	text: string,
	chipsObj: Record<string, ChipData>,
	resourcesObj: Record<string, ResourceRef> = {}
): ContentSegment[] {
	if (!text) return [];

	const segments: ContentSegment[] = [];
	const chipPattern = new RegExp(CHIP_AND_RESOURCE_PATTERN.source, 'g');
	let lastIndex = 0;
	let match;

	const usedChipIds = new Set<string>();
	const usedResourceIds = new Set<string>();

	while ((match = chipPattern.exec(text)) !== null) {
		if (match.index > lastIndex) {
			segments.push({
				type: 'text',
				content: text.substring(lastIndex, match.index)
			});
		}

		if (match[3] !== undefined) {
			const field = match[3];
			const itemKey = match[4];
			const matchingEntry = Object.entries(resourcesObj).find(
				([id, ref]) => ref.field === field && ref.item_key === itemKey && !usedResourceIds.has(id)
			);
			if (matchingEntry) {
				const [resourceId, resourceRef] = matchingEntry;
				usedResourceIds.add(resourceId);
				segments.push({ type: 'resource', content: match[0], resourceId, resourceRef });
			} else {
				segments.push({ type: 'resource', content: match[0], resourceRef: { field, item_key: itemKey } });
			}
			lastIndex = match.index + match[0].length;
			continue;
		}

		const categoryPath = decodePathFromMatch(match);

		// Find matching chip (not yet used)
		const matchingEntry = Object.entries(chipsObj).find(
			([id, chip]) => chip.categoryPath === categoryPath && !usedChipIds.has(id)
		);

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

export function parseValueToSegments(
	text: string,
	chipsObj: Record<string, ChipData>,
	resourcesObj: Record<string, ResourceRef> = {}
): ContentSegment[] {
	if (!text) return [];

	const chipSegments = parseChipSegments(text, chipsObj, resourcesObj);
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
