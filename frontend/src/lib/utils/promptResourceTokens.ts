import {
	RESOURCE_INDEX_PLACEHOLDER,
	encodeResourceMarker,
	itemAtPosition,
	mediaFieldItems,
	mediaItemKey,
	resourceGroupLabel,
	resourceHandleLabel,
	resourceMarkerRegex,
	type PromptResourceSpec
} from './promptResources';

export interface ResourceTokenMatch {
	start: number;
	end: number;
	text: string;
	spec: PromptResourceSpec;
	position: number;
	itemKey: string | null;
}

export interface ResourceTokenContext {
	specs: readonly PromptResourceSpec[];
	formValues: Record<string, unknown>;
	fieldLabels?: Record<string, string>;
}

function escapeLiteral(literal: string): string {
	return literal
		.split(/\s+/)
		.map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
		.join('\\s+');
}

export function resourceTokenPattern(spec: PromptResourceSpec): RegExp | null {
	const parts = spec.token.split(RESOURCE_INDEX_PLACEHOLDER);
	if (parts.length !== 2) return null;
	const prefix = parts[0].trimEnd();
	const suffix = parts[1].trimStart();
	if (!prefix.trim() && !suffix.trim()) return null;
	const head = prefix ? `${escapeLiteral(prefix)}\\s*` : '';
	const tail = suffix ? `\\s*${escapeLiteral(suffix)}` : '';
	return new RegExp(`${head}(\\d+)${tail}`, 'gi');
}

function markerRanges(text: string): Array<[number, number]> {
	const ranges: Array<[number, number]> = [];
	if (!text.includes('@[')) return ranges;
	for (const match of text.matchAll(resourceMarkerRegex())) {
		const start = match.index ?? 0;
		ranges.push([start, start + match[0].length]);
	}
	return ranges;
}

function overlaps(ranges: readonly (readonly [number, number])[], start: number, end: number): boolean {
	return ranges.some(([a, b]) => start < b && end > a);
}

export function findResourceTokens(
	text: string,
	specs: readonly PromptResourceSpec[],
	formValues: Record<string, unknown>
): ResourceTokenMatch[] {
	if (!text || !specs.length) return [];
	const taken: Array<[number, number]> = markerRanges(text);
	const found: ResourceTokenMatch[] = [];
	for (const spec of specs) {
		const pattern = resourceTokenPattern(spec);
		if (!pattern) continue;
		for (const match of text.matchAll(pattern)) {
			const start = match.index ?? 0;
			const end = start + match[0].length;
			if (overlaps(taken, start, end)) continue;
			const position = Number(match[1]);
			const itemKey = position >= 1 ? mediaItemKey(itemAtPosition(formValues[spec.field], position)) : null;
			taken.push([start, end]);
			found.push({ start, end, text: match[0], spec, position, itemKey });
		}
	}
	return found.sort((a, b) => a.start - b.start);
}

export interface ResourceTokenConversion {
	text: string;
	linked: ResourceTokenMatch[];
	unresolved: ResourceTokenMatch[];
}

export function convertResourceTokens(
	text: string,
	specs: readonly PromptResourceSpec[],
	formValues: Record<string, unknown>
): ResourceTokenConversion {
	const matches = findResourceTokens(text, specs, formValues);
	if (!matches.length) return { text, linked: [], unresolved: [] };
	let out = '';
	let cursor = 0;
	const linked: ResourceTokenMatch[] = [];
	const unresolved: ResourceTokenMatch[] = [];
	for (const match of matches) {
		out += text.slice(cursor, match.start);
		if (match.itemKey) {
			out += encodeResourceMarker(match.spec.field, match.itemKey);
			linked.push(match);
		} else {
			out += match.text;
			unresolved.push(match);
		}
		cursor = match.end;
	}
	out += text.slice(cursor);
	return { text: out, linked, unresolved };
}

export function unresolvedResourceTokenMessage(
	match: ResourceTokenMatch,
	formValues: Record<string, unknown>,
	fieldLabels: Record<string, string> = {}
): string {
	const label = fieldLabels[match.spec.field] ?? resourceGroupLabel(match.spec);
	const count = mediaFieldItems(formValues[match.spec.field]).length;
	const holds = count === 0 ? 'is empty' : `has ${count} ${count === 1 ? 'item' : 'items'}`;
	return `${match.text} stays plain text: ${label} ${holds}`;
}

export function resourceTokenWarnings(text: string, context: ResourceTokenContext): string[] {
	const messages: string[] = [];
	for (const match of findResourceTokens(text, context.specs, context.formValues)) {
		if (match.itemKey) continue;
		const message = unresolvedResourceTokenMessage(match, context.formValues, context.fieldLabels);
		if (!messages.includes(message)) messages.push(message);
	}
	return messages;
}

export type ResourceTokenPreviewPart =
	| { kind: 'text'; text: string }
	| { kind: 'linked'; text: string; label: string }
	| { kind: 'unresolved'; text: string };

export function splitResourceTokenPreview(
	text: string,
	specs: readonly PromptResourceSpec[],
	formValues: Record<string, unknown>
): ResourceTokenPreviewPart[] {
	const matches = findResourceTokens(text, specs, formValues);
	if (!matches.length) return text ? [{ kind: 'text', text }] : [];
	const parts: ResourceTokenPreviewPart[] = [];
	let cursor = 0;
	for (const match of matches) {
		if (match.start > cursor) parts.push({ kind: 'text', text: text.slice(cursor, match.start) });
		parts.push(
			match.itemKey
				? { kind: 'linked', text: match.text, label: resourceHandleLabel(match.spec, match.position) }
				: { kind: 'unresolved', text: match.text }
		);
		cursor = match.end;
	}
	if (cursor < text.length) parts.push({ kind: 'text', text: text.slice(cursor) });
	return parts;
}
