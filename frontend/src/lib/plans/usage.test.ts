import { describe, it, expect } from 'vitest';
import {
	describeSource,
	formatImpactCell,
	impactNote,
	isUsageSort,
	sortByUsage,
	sourceLabel,
	usageSortOptions,
	usageState,
	MOST_USED_SORT
} from './usage';
import type { ImpactLimit, ImpactMember, LimitKindDescriptor, UsageRow, UserUsageRow } from './types';

const GB = 1024 ** 3;

const kinds: LimitKindDescriptor[] = [
	{ key: 'storage_bytes', label: 'Storage space', short_label: 'Storage', description: '', value_type: 'bytes', unit: 'GB', input_scale: GB, window: 'none' },
	{ key: 'generations_per_day', label: 'Generations per day', short_label: 'Generations today', description: '', value_type: 'count', unit: 'per day', input_scale: 1, window: 'day' }
];

function usage(kind: string, percent: number | null): UsageRow {
	return {
		kind,
		label: kind,
		format: 'count',
		window: 'none',
		used: 1,
		limit: percent === null ? null : 100,
		percent,
		state: 'ok',
		resets_at: null,
		enforced: true,
		plan: null,
		source: 'group',
		group: null
	};
}

function row(user_id: string, limits: UsageRow[], max_percent: number | null): UserUsageRow {
	return {
		user_id,
		username: user_id,
		email: '',
		account_type: 'USER',
		plan: null,
		source: 'group',
		group: null,
		exempt: false,
		max_percent,
		limits
	};
}

const rows = new Map<string, UserUsageRow>([
	['a', row('a', [usage('storage_bytes', 10), usage('generations_per_day', 90)], 90)],
	['b', row('b', [usage('storage_bytes', 50)], 50)],
	['c', row('c', [usage('storage_bytes', null)], null)]
]);
const users = [
	{ id: 'c', username: 'carl' },
	{ id: 'b', username: 'bea' },
	{ id: 'a', username: 'abe' },
	{ id: 'd', username: 'dan' }
];

describe('usageState', () => {
	it('is ok below 80 percent, warns from 80, full at 100, none without a limit', () => {
		expect(usageState(79, 100)).toBe('ok');
		expect(usageState(80, 100)).toBe('warn');
		expect(usageState(100, 100)).toBe('full');
		expect(usageState(5, null)).toBe('none');
		expect(usageState(0, 0)).toBe('full');
	});
});

describe('most used limit sort', () => {
	it('orders by the highest ratio and puts unlimited and unknown users last, then by name', () => {
		expect(sortByUsage(users, rows, MOST_USED_SORT).map((u) => u.id)).toEqual(['a', 'b', 'c', 'd']);
	});

	it('sorts by one kind', () => {
		expect(sortByUsage(users, rows, 'kind:storage_bytes').map((u) => u.id)).toEqual(['b', 'a', 'c', 'd']);
		expect(sortByUsage(users, rows, 'kind:generations_per_day')[0].id).toBe('a');
	});

	it('does not mutate its input', () => {
		const input = [...users];
		sortByUsage(input, rows, MOST_USED_SORT);
		expect(input.map((u) => u.id)).toEqual(users.map((u) => u.id));
	});

	it('offers most-used plus one option per kind', () => {
		expect(usageSortOptions(kinds)).toEqual([
			{ value: MOST_USED_SORT, label: 'Most-used limit' },
			{ value: 'kind:storage_bytes', label: 'Storage used' },
			{ value: 'kind:generations_per_day', label: 'Generations today used' }
		]);
		expect(isUsageSort('kind:x')).toBe(true);
		expect(isUsageSort('username')).toBe(false);
	});
});

describe('source text', () => {
	const base = { enforced: true, plan: { id: 'p', name: 'Tier 1' }, group: { id: 'g', name: 'premium-tier-1' } };
	it('names the plan and where it came from', () => {
		expect(describeSource({ ...base, source: 'group' })).toBe('Tier 1 via premium-tier-1');
		expect(describeSource({ ...base, source: 'override', group: null })).toBe('Tier 1 via personal override');
		expect(describeSource({ ...base, source: 'default', group: null })).toBe('Tier 1 via All users (default)');
		expect(describeSource({ ...base, source: 'group', enforced: false })).toBe('Admin, exempt from limits');
		expect(describeSource({ ...base, source: 'group', detail: 'Tier 1 via premium-tier-1; All users says 5 GB, smaller' })).toBe(
			'Tier 1 via premium-tier-1; All users says 5 GB, smaller'
		);
	});

	it('labels the list source column', () => {
		expect(sourceLabel('group')).toBe('group');
		expect(sourceLabel('group', true)).toBe('exempt');
	});
});

function side(value: number | null, group: string | null = null): ImpactLimit['before'] {
	return { value, source: 'group', plan: null, group: group ? { id: group, name: group } : null };
}

function limit(partial: Partial<ImpactLimit>): ImpactLimit {
	return { kind: 'storage_bytes', before: side(5 * GB), after: side(20 * GB), change: 'raised', note: 'from_this_group', used: 0, over_after: false, ...partial };
}

describe('formatImpactCell', () => {
	const storage = kinds[0];
	it('shows a change, a kept value, an unchanged value and a lifted limit', () => {
		expect(formatImpactCell(storage, limit({}))).toBe('5 GB -> 20 GB');
		expect(
			formatImpactCell(storage, limit({ before: side(100 * GB), after: side(100 * GB, 'g2'), change: 'unchanged', note: 'kept_from_other_group' }))
		).toBe('100 GB (kept)');
		expect(formatImpactCell(storage, limit({ before: side(5 * GB), after: side(5 * GB), change: 'unchanged', note: 'default' }))).toBe('5 GB');
		expect(formatImpactCell(storage, limit({ after: side(null), change: 'lifted' }))).toBe('5 GB -> no limit');
	});
});

describe('impactNote', () => {
	const member = (limits: ImpactLimit[], extra: Partial<ImpactMember> = {}): ImpactMember => ({
		user_id: 'u',
		username: 'u',
		exempt: false,
		override: false,
		limits,
		...extra
	});

	it('explains where the new limit comes from', () => {
		expect(impactNote(member([limit({})]))).toBe('from this group');
		expect(
			impactNote(member([limit({ change: 'unchanged', note: 'kept_from_other_group', after: side(100 * GB, 'premium-tier-2') })]))
		).toBe('bigger from premium-tier-2');
	});

	it('explains overrides and exempt admins', () => {
		expect(impactNote(member([limit({ note: 'override' })], { override: true }))).toBe('personal override');
		expect(impactNote(member([limit({ note: 'exempt' })], { exempt: true }))).toBe('admin, exempt');
	});
});
