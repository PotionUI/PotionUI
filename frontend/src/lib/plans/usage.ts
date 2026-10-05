import type { LimitKindDescriptor, LimitSourceType, UserUsageRow } from './types';

export const WARN_RATIO = 0.8;

export type UsageState = 'ok' | 'warn' | 'full' | 'none';

export function usageRatio(used: number, limit: number | null): number | null {
	if (limit === null) return null;
	if (limit <= 0) return 1;
	return used / limit;
}

export function usageState(used: number, limit: number | null): UsageState {
	const ratio = usageRatio(used, limit);
	if (ratio === null) return 'none';
	if (ratio >= 1) return 'full';
	if (ratio >= WARN_RATIO) return 'warn';
	return 'ok';
}

export function mostUsedRatio(row: UserUsageRow | undefined): number {
	if (!row) return -1;
	let best = -1;
	for (const entry of row.limits) {
		const ratio = usageRatio(entry.used, entry.limit);
		if (ratio !== null && ratio > best) best = ratio;
	}
	return best;
}

export function kindRatio(row: UserUsageRow | undefined, kind: string): number {
	const entry = row?.limits.find((limit) => limit.kind === kind);
	if (!entry) return -1;
	return usageRatio(entry.used, entry.limit) ?? -1;
}

export function activeUsageKinds(rows: readonly UserUsageRow[], kinds: readonly LimitKindDescriptor[]): LimitKindDescriptor[] {
	const present = new Set<string>();
	for (const row of rows) for (const entry of row.limits) if (entry.limit !== null) present.add(entry.kind);
	return kinds.filter((kind) => present.has(kind.key) && kind.active !== false);
}

export const MOST_USED_SORT = 'most_used';
export const KIND_SORT_PREFIX = 'kind:';

export function usageSortOptions(kinds: readonly LimitKindDescriptor[]): Array<{ value: string; label: string }> {
	return [
		{ value: MOST_USED_SORT, label: 'Most-used limit' },
		...kinds.map((kind) => ({ value: `${KIND_SORT_PREFIX}${kind.key}`, label: `${kind.label} used` }))
	];
}

export function isUsageSort(sortBy: string): boolean {
	return sortBy === MOST_USED_SORT || sortBy.startsWith(KIND_SORT_PREFIX);
}

export function sortByUsage<T extends { id: string; username: string }>(
	users: readonly T[],
	usage: ReadonlyMap<string, UserUsageRow>,
	sortBy: string
): T[] {
	const score = (user: T): number => {
		const row = usage.get(user.id);
		return sortBy === MOST_USED_SORT ? mostUsedRatio(row) : kindRatio(row, sortBy.slice(KIND_SORT_PREFIX.length));
	};
	return [...users].sort((a, b) => score(b) - score(a) || a.username.localeCompare(b.username));
}

const SOURCE_LABELS: Record<LimitSourceType, string> = {
	override: 'override',
	group: 'group',
	default: 'default',
	exempt: 'exempt',
	none: 'none'
};

export function sourceLabel(source: LimitSourceType): string {
	return SOURCE_LABELS[source] ?? source;
}
