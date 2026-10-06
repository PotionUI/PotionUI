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

export function rangeBetween(ids: readonly string[], anchorId: string | null, targetId: string): string[] {
	const to = ids.indexOf(targetId);
	if (to < 0) return [];
	const from = anchorId === null ? -1 : ids.indexOf(anchorId);
	if (from < 0) return [targetId];
	const [lo, hi] = from <= to ? [from, to] : [to, from];
	return ids.slice(lo, hi + 1);
}

export function applyRange(
	selected: ReadonlySet<string>,
	range: readonly string[],
	select: boolean,
	locked: ReadonlySet<string> = new Set()
): Set<string> {
	const next = new Set(selected);
	for (const id of range) {
		if (locked.has(id)) continue;
		if (select) next.add(id);
		else next.delete(id);
	}
	return next;
}

export function moveActiveIndex(current: number, key: string, count: number): number {
	if (count <= 0) return -1;
	if (key === 'Home') return 0;
	if (key === 'End') return count - 1;
	if (current < 0) return key === 'ArrowUp' ? count - 1 : 0;
	if (key === 'ArrowUp') return Math.max(0, current - 1);
	if (key === 'ArrowDown') return Math.min(count - 1, current + 1);
	return current;
}
