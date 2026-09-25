export type PageSelectionState = 'none' | 'some' | 'all';

export function pageSelectionState(ids: readonly string[], selected: ReadonlySet<string>): PageSelectionState {
	if (ids.length === 0) return 'none';
	const selectedCount = ids.reduce((count, id) => count + (selected.has(id) ? 1 : 0), 0);
	if (selectedCount === 0) return 'none';
	if (selectedCount === ids.length) return 'all';
	return 'some';
}

export function toggleRow(selected: ReadonlySet<string>, id: string): Set<string> {
	const next = new Set(selected);
	if (next.has(id)) next.delete(id);
	else next.add(id);
	return next;
}

export function selectPage(selected: ReadonlySet<string>, ids: readonly string[]): Set<string> {
	const next = new Set(selected);
	for (const id of ids) next.add(id);
	return next;
}

export function clearPage(selected: ReadonlySet<string>, ids: readonly string[]): Set<string> {
	const next = new Set(selected);
	for (const id of ids) next.delete(id);
	return next;
}

export function clearAll(): Set<string> {
	return new Set();
}
