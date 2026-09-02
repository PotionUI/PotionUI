import type { PhrasebookCategory, PhrasebookFindResult, PhrasebookFindValueHit } from '$lib/types/api';

export interface HighlightPart {
	text: string;
	match: boolean;
}

export interface FindGroup<T> {
	items: T[];
	count: number;
}

export interface FindGroups {
	categories: FindGroup<PhrasebookCategory>;
	values: FindGroup<PhrasebookFindValueHit>;
	total: number;
}

export function isSearching(query: string): boolean {
	return query.trim().length > 0;
}

export function splitHighlight(text: string, query: string): HighlightPart[] {
	const needle = query.trim().toLowerCase();
	if (!text) return [];
	if (!needle) return [{ text, match: false }];
	const haystack = text.toLowerCase();
	const parts: HighlightPart[] = [];
	let cursor = 0;
	let index = haystack.indexOf(needle);
	while (index !== -1) {
		if (index > cursor) parts.push({ text: text.slice(cursor, index), match: false });
		parts.push({ text: text.slice(index, index + needle.length), match: true });
		cursor = index + needle.length;
		index = haystack.indexOf(needle, cursor);
	}
	if (cursor < text.length) parts.push({ text: text.slice(cursor), match: false });
	return parts;
}

export function excerpt(text: string, query: string, maxLength = 80): string {
	const collapsed = text.replace(/\s+/g, ' ').trim();
	if (collapsed.length <= maxLength) return collapsed;
	const needle = query.trim().toLowerCase();
	const index = needle ? collapsed.toLowerCase().indexOf(needle) : -1;
	if (index === -1) return `${collapsed.slice(0, maxLength - 1)}…`;
	const lead = Math.floor((maxLength - needle.length) / 2);
	const start = Math.max(0, Math.min(index - lead, collapsed.length - maxLength));
	const end = Math.min(collapsed.length, start + maxLength);
	return `${start > 0 ? '…' : ''}${collapsed.slice(start, end)}${end < collapsed.length ? '…' : ''}`;
}

export function groupFindResults(result: PhrasebookFindResult, maxPerGroup = 50): FindGroups {
	const categories = result.categories.slice(0, maxPerGroup);
	const values = result.values.slice(0, maxPerGroup);
	return {
		categories: { items: categories, count: result.total_categories },
		values: { items: values, count: result.total_values },
		total: result.total_categories + result.total_values
	};
}

export function emptyFindResult(query: string): PhrasebookFindResult {
	return { query: query.trim(), categories: [], values: [], total_categories: 0, total_values: 0 };
}
