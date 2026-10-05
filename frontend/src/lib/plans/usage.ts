import { formatLimitValue } from './format';
import type {
	ImpactLimit,
	ImpactMember,
	LimitKindDescriptor,
	LimitSourceType,
	LimitState,
	UsageRow,
	UserUsageRow
} from './types';

export const WARN_RATIO = 0.8;

export type UsageState = LimitState | 'none';

export function usageState(used: number, limit: number | null): UsageState {
	if (limit === null) return 'none';
	const ratio = limit <= 0 ? 1 : used / limit;
	if (ratio >= 1) return 'full';
	if (ratio >= WARN_RATIO) return 'warn';
	return 'ok';
}

export function rowPercent(row: UserUsageRow | undefined, kind: string): number {
	const entry = row?.limits.find((limit) => limit.kind === kind);
	return entry?.percent ?? -1;
}

export function mostUsedPercent(row: UserUsageRow | undefined): number {
	return row?.max_percent ?? -1;
}

export function usageRowFor(row: UserUsageRow | undefined, kind: string): UsageRow | undefined {
	return row?.limits.find((limit) => limit.kind === kind);
}

export const MOST_USED_SORT = 'most_used';
export const KIND_SORT_PREFIX = 'kind:';

export function usageSortOptions(kinds: readonly LimitKindDescriptor[]): Array<{ value: string; label: string }> {
	return [
		{ value: MOST_USED_SORT, label: 'Most-used limit' },
		...kinds.filter((kind) => !kind.per_item).map((kind) => ({ value: `${KIND_SORT_PREFIX}${kind.key}`, label: `${kind.short_label ?? kind.label} used` }))
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
		return sortBy === MOST_USED_SORT ? mostUsedPercent(row) : rowPercent(row, sortBy.slice(KIND_SORT_PREFIX.length));
	};
	return [...users].sort((a, b) => score(b) - score(a) || a.username.localeCompare(b.username));
}

const SOURCE_LABELS: Record<LimitSourceType, string> = {
	override: 'override',
	group: 'group',
	default: 'default',
	none: 'none'
};

export function sourceLabel(source: LimitSourceType, exempt = false): string {
	return exempt ? 'exempt' : (SOURCE_LABELS[source] ?? source);
}

export function describeSource(row: Pick<UsageRow, 'source' | 'plan' | 'group' | 'enforced' | 'detail'>): string {
	if (row.detail) return row.detail;
	const plan = row.plan?.name ?? 'Plan';
	if (!row.enforced) return 'Admin, exempt from limits';
	if (row.source === 'override') return `${plan} via personal override`;
	if (row.source === 'group') return row.group ? `${plan} via ${row.group.name}` : `${plan} via group`;
	if (row.source === 'default') return `${plan} via All users (default)`;
	return 'No plan applies';
}

export function formatImpactCell(kind: Pick<LimitKindDescriptor, 'value_type'>, entry: ImpactLimit): string {
	const before = formatLimitValue(kind, entry.before.value);
	const after = formatLimitValue(kind, entry.after.value);
	if (entry.change === 'unchanged') return entry.note === 'kept_from_other_group' ? `${before} (kept)` : after;
	return `${before} -> ${after}`;
}

export function impactNote(member: ImpactMember): string {
	if (member.exempt) return 'admin, exempt';
	if (member.override) return 'personal override';
	const notes = new Set<string>();
	for (const entry of member.limits) {
		if (entry.note === 'kept_from_other_group') {
			notes.add(entry.after.group ? `bigger from ${entry.after.group.name}` : 'bigger from another group');
		} else if (entry.note === 'from_this_group') notes.add('from this group');
		else if (entry.note === 'default') notes.add('falls to default plan');
		else if (entry.note === 'none') notes.add('no limit');
	}
	return [...notes].join(', ');
}
