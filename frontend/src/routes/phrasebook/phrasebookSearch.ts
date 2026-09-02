import type {
	PhrasebookBatchOp,
	PhrasebookFindMode,
	PhrasebookFindParams,
	PhrasebookFindScope,
	PhrasebookMatchSpan,
	PhrasebookStateFilter,
	PhrasebookValueField
} from '$lib/types/api';

export interface FindFilters {
	query: string;
	mode: PhrasebookFindMode;
	caseSensitive: boolean;
	scope: PhrasebookFindScope;
	includeInactive: boolean;
	pathPrefix: string;
	inLabel: boolean;
	inValue: boolean;
}

export const FIND_LIMIT = 200;

export function defaultFilters(): FindFilters {
	return {
		query: '',
		mode: 'contains',
		caseSensitive: false,
		scope: 'all',
		includeInactive: true,
		pathPrefix: '',
		inLabel: true,
		inValue: true
	};
}

export function isSearching(query: string): boolean {
	return query.trim().length > 0;
}

// Counts filters that differ from their defaults, for the header's Filters
// badge. `mode` and `query` are shown outside the popover and excluded here.
// `stateFilter` is the browsing "Show" group (All/Active/Inactive), a
// separate control from the search-only `filters` — see the "Show" vs
// "Include inactive" note on nonDefaultFilterCount's caller.
export function nonDefaultFilterCount(filters: FindFilters, stateFilter: PhrasebookStateFilter = 'all'): number {
	const defaults = defaultFilters();
	let count = 0;
	if (stateFilter !== 'all') count++;
	if (filters.caseSensitive !== defaults.caseSensitive) count++;
	if (filters.inLabel !== defaults.inLabel || filters.inValue !== defaults.inValue) count++;
	if (filters.scope !== defaults.scope) count++;
	if (filters.includeInactive !== defaults.includeInactive) count++;
	if (filters.pathPrefix !== defaults.pathPrefix) count++;
	return count;
}

export function selectedFields(filters: Pick<FindFilters, 'inLabel' | 'inValue'>): PhrasebookValueField[] {
	const fields: PhrasebookValueField[] = [];
	if (filters.inLabel) fields.push('label');
	if (filters.inValue) fields.push('value');
	return fields.length > 0 ? fields : ['label', 'value'];
}

export function buildFindParams(filters: FindFilters, limit = FIND_LIMIT): PhrasebookFindParams {
	return {
		q: filters.query.trim(),
		mode: filters.mode,
		case_sensitive: filters.caseSensitive,
		scope: filters.scope,
		include_inactive: filters.includeInactive,
		path_prefix: filters.pathPrefix,
		fields: selectedFields(filters),
		limit
	};
}

export interface HighlightSegment {
	text: string;
	match: boolean;
}

export function highlightSegments(
	text: string,
	spans: PhrasebookMatchSpan[],
	field: string
): HighlightSegment[] {
	if (!text) return [];
	const ranges = spans
		.filter((s) => s.field === field)
		.map((s) => [Math.max(0, Math.min(text.length, s.start)), Math.max(0, Math.min(text.length, s.end))] as const)
		.filter(([start, end]) => end > start)
		.sort((a, b) => a[0] - b[0]);
	const merged: [number, number][] = [];
	for (const [start, end] of ranges) {
		const last = merged[merged.length - 1];
		if (last && start <= last[1]) {
			last[1] = Math.max(last[1], end);
		} else {
			merged.push([start, end]);
		}
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

export function toggleId(selected: Set<string>, id: string): Set<string> {
	const next = new Set(selected);
	if (next.has(id)) next.delete(id);
	else next.add(id);
	return next;
}

export function toggleAll(selected: Set<string>, ids: string[]): Set<string> {
	const allSelected = ids.length > 0 && ids.every((id) => selected.has(id));
	return allSelected ? new Set() : new Set(ids);
}

export function rangeIds(orderedIds: string[], anchorId: string | null, targetId: string): string[] {
	const target = orderedIds.indexOf(targetId);
	if (target === -1) return [];
	const anchor = anchorId ? orderedIds.indexOf(anchorId) : -1;
	if (anchor === -1) return [targetId];
	const [from, to] = anchor < target ? [anchor, target] : [target, anchor];
	return orderedIds.slice(from, to + 1);
}

export function retainSelection(selected: Set<string>, ids: string[]): Set<string> {
	const present = new Set(ids);
	return new Set([...selected].filter((id) => present.has(id)));
}

export interface DiffSegments {
	prefix: string;
	removed: string;
	added: string;
	suffix: string;
}

export function diffSegments(before: string, after: string): DiffSegments {
	let prefix = 0;
	const maxPrefix = Math.min(before.length, after.length);
	while (prefix < maxPrefix && before[prefix] === after[prefix]) prefix++;
	let suffix = 0;
	const maxSuffix = maxPrefix - prefix;
	while (
		suffix < maxSuffix &&
		before[before.length - 1 - suffix] === after[after.length - 1 - suffix]
	) {
		suffix++;
	}
	return {
		prefix: before.slice(0, prefix),
		removed: before.slice(prefix, before.length - suffix),
		added: after.slice(prefix, after.length - suffix),
		suffix: before.slice(before.length - suffix)
	};
}

export function pluginBatchOps(ops: PhrasebookBatchOp[]): PhrasebookBatchOp[] {
	return ops.filter((op) => op.source !== 'core').sort((a, b) => a.label.localeCompare(b.label));
}

export function isTopLevelPath(path: string): boolean {
	return path.length > 0 && !path.includes('.');
}

export function topLevelCategories<T extends { path: string; parent_id?: string | null }>(all: T[]): T[] {
	return all
		.filter((c) => !c.parent_id && isTopLevelPath(c.path))
		.sort((a, b) => a.path.localeCompare(b.path));
}

export interface ApiErrorDetail {
	error: string;
	message: string;
}

export function apiErrorDetail(err: unknown): ApiErrorDetail | null {
	const response = (err as { response?: { data?: unknown } } | null)?.response;
	const data = response?.data;
	if (!data || typeof data !== 'object') return null;
	const body = ('detail' in data && data.detail && typeof data.detail === 'object'
		? (data as { detail: Record<string, unknown> }).detail
		: (data as Record<string, unknown>));
	if (typeof body.error !== 'string') return null;
	return { error: body.error, message: typeof body.message === 'string' ? body.message : body.error };
}
