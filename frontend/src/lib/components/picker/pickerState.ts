import type { CollisionInfo, EntityKind, FilterOption, PickerDiff, PickerView } from './types';

export interface PickerQuery {
	q: string;
	filters: Record<string, string>;
	sort: string;
	view: PickerView;
}

export function computeDiff(
	initial: ReadonlySet<string>,
	selected: ReadonlySet<string>,
	order: readonly string[] = []
): PickerDiff {
	const add: string[] = [];
	const remove: string[] = [];
	for (const id of selected) if (!initial.has(id)) add.push(id);
	for (const id of initial) if (!selected.has(id)) remove.push(id);
	const rank = new Map(order.map((id, i) => [id, i]));
	const byOrder = (a: string, b: string) => (rank.get(a) ?? Infinity) - (rank.get(b) ?? Infinity);
	return { add: add.sort(byOrder), remove: remove.sort(byOrder) };
}

export function diffIsEmpty(diff: PickerDiff): boolean {
	return diff.add.length === 0 && diff.remove.length === 0;
}

export function diffSummary(diff: PickerDiff): string {
	const parts: string[] = [];
	if (diff.add.length > 0) parts.push(`+${diff.add.length} add`);
	if (diff.remove.length > 0) parts.push(`-${diff.remove.length} remove`);
	return parts.join(' / ');
}

export function applyLabel(diff: PickerDiff, plural: string, singular: string): string {
	const total = diff.add.length + diff.remove.length;
	if (total === 0) return 'Apply';
	if (diff.remove.length === 0) return diff.add.length === 1 ? `Add 1 ${singular}` : `Add ${diff.add.length} ${plural}`;
	if (diff.add.length === 0) return `Remove ${diff.remove.length}`;
	return `Apply ${total} changes`;
}

export function viewCounts(ids: readonly string[], assigned: ReadonlySet<string>): Record<PickerView, number> {
	let inAssigned = 0;
	for (const id of ids) if (assigned.has(id)) inAssigned++;
	return { unassigned: ids.length - inAssigned, assigned: inAssigned, all: ids.length };
}

export function rowsForView<Row>(
	rows: readonly Row[],
	getId: (row: Row) => string,
	view: PickerView,
	assigned: ReadonlySet<string>
): Row[] {
	if (view === 'all') return [...rows];
	return rows.filter((row) => assigned.has(getId(row)) === (view === 'assigned'));
}

export function filterRows<Row>(
	rows: readonly Row[],
	kind: EntityKind<Row>,
	query: Pick<PickerQuery, 'q' | 'filters' | 'sort'>,
	collisions: ReadonlyMap<string, CollisionInfo>
): Row[] {
	const terms = query.q.trim().toLowerCase().split(/\s+/).filter(Boolean);
	const active = kind.filters.filter((filter) => (query.filters[filter.key] ?? '') !== '');
	const out = rows.filter((row) => {
		if (terms.length > 0) {
			const haystack = kind.searchText(row).toLowerCase();
			if (!terms.every((term) => haystack.includes(term))) return false;
		}
		const ctx = { collision: collisions.get(kind.getId(row))?.size ?? 1 };
		return active.every((filter) => filter.match(row, query.filters[filter.key], ctx));
	});
	const sort = kind.sorts.find((s) => s.value === query.sort) ?? kind.sorts.find((s) => s.value === kind.defaultSort);
	return sort ? out.sort(sort.compare) : out;
}

export function activeFilterChips<Row>(
	kind: EntityKind<Row>,
	filters: Record<string, string>,
	options: Record<string, FilterOption[]>
): { key: string; label: string }[] {
	const chips: { key: string; label: string }[] = [];
	for (const filter of kind.filters) {
		const value = filters[filter.key] ?? '';
		if (!value) continue;
		const option = options[filter.key]?.find((o) => o.value === value);
		chips.push({ key: filter.key, label: `${filter.label}: ${option?.label ?? value}` });
	}
	return chips;
}

export function selectFiltered(
	selected: ReadonlySet<string>,
	filteredIds: readonly string[],
	locked: ReadonlySet<string> = new Set()
): Set<string> {
	const next = new Set(selected);
	for (const id of filteredIds) if (!locked.has(id)) next.add(id);
	return next;
}

export function clearFiltered(
	selected: ReadonlySet<string>,
	filteredIds: readonly string[],
	locked: ReadonlySet<string> = new Set()
): Set<string> {
	const next = new Set(selected);
	for (const id of filteredIds) if (!locked.has(id)) next.delete(id);
	return next;
}

export function filteredSelectionState(
	filteredIds: readonly string[],
	selected: ReadonlySet<string>,
	locked: ReadonlySet<string> = new Set()
): 'none' | 'some' | 'all' {
	const open = filteredIds.filter((id) => !locked.has(id));
	if (open.length === 0) return 'none';
	const count = open.reduce((n, id) => n + (selected.has(id) ? 1 : 0), 0);
	if (count === 0) return 'none';
	return count === open.length ? 'all' : 'some';
}
