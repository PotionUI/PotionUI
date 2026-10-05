import { describe, it, expect } from 'vitest';
import { activeUsageKinds, mostUsedRatio, sortByUsage, usageSortOptions, usageState, MOST_USED_SORT } from './usage';
import type { LimitKindDescriptor, UserUsageRow } from './types';

const kinds: LimitKindDescriptor[] = [
	{ key: 'storage_bytes', label: 'Storage space', description: '', value_type: 'bytes', unit: 'bytes', window: 'none' },
	{ key: 'generations_per_day', label: 'Generations per day', description: '', value_type: 'count', unit: 'count', window: 'day' },
	{ key: 'cloud', label: 'Cloud', description: '', value_type: 'usd', unit: 'usd', window: 'month' }
];

function row(user_id: string, limits: UserUsageRow['limits']): UserUsageRow {
	return { user_id, plan_name: 'p', source: 'group', limits };
}

const usage = new Map<string, UserUsageRow>([
	['a', row('a', [{ kind: 'storage_bytes', used: 10, limit: 100 }, { kind: 'generations_per_day', used: 90, limit: 100 }])],
	['b', row('b', [{ kind: 'storage_bytes', used: 50, limit: 100 }])],
	['c', row('c', [{ kind: 'storage_bytes', used: 5, limit: null }])]
]);
const users = [
	{ id: 'c', username: 'carl' },
	{ id: 'b', username: 'bea' },
	{ id: 'a', username: 'abe' },
	{ id: 'd', username: 'dan' }
];

describe('usageState', () => {
	it('is silent below 80 percent, warns from 80, full at 100, none without a limit', () => {
		expect(usageState(79, 100)).toBe('ok');
		expect(usageState(80, 100)).toBe('warn');
		expect(usageState(100, 100)).toBe('full');
		expect(usageState(5, null)).toBe('none');
	});
});

describe('most used limit sort', () => {
	it('uses the highest ratio across kinds and puts unlimited and unknown users last', () => {
		expect(mostUsedRatio(usage.get('a'))).toBeCloseTo(0.9);
		expect(sortByUsage(users, usage, MOST_USED_SORT).map((u) => u.id)).toEqual(['a', 'b', 'c', 'd']);
	});

	it('sorts by one kind', () => {
		expect(sortByUsage(users, usage, 'kind:storage_bytes').map((u) => u.id)).toEqual(['b', 'a', 'c', 'd']);
		expect(sortByUsage(users, usage, 'kind:generations_per_day').map((u) => u.id)[0]).toBe('a');
	});

	it('offers most-used plus one option per kind', () => {
		expect(usageSortOptions(kinds).map((o) => o.value)).toEqual([MOST_USED_SORT, 'kind:storage_bytes', 'kind:generations_per_day', 'kind:cloud']);
	});
});

describe('activeUsageKinds', () => {
	it('lists kinds that some user has a limit for', () => {
		expect(activeUsageKinds([...usage.values()], kinds).map((k) => k.key)).toEqual(['storage_bytes', 'generations_per_day']);
	});
});
