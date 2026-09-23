import { describe, it, expect } from 'vitest';
import { createFilterCodec, type FilterFieldDescriptor } from './filterCodec';

interface DemoFilters {
	q: string;
	status: '' | 'active' | 'archived';
	owner: string;
	tags: string[];
	pinned: boolean;
	sortBy: 'name' | 'created';
	sortOrder: 'asc' | 'desc';
}

const DEFAULTS: DemoFilters = {
	q: '',
	status: '',
	owner: '',
	tags: [],
	pinned: false,
	sortBy: 'name',
	sortOrder: 'desc'
};

const FIELDS: readonly FilterFieldDescriptor<DemoFilters>[] = [
	{ kind: 'enum', key: 'status', param: 'status', label: 'Status', values: ['active', 'archived'], default: '' },
	{ kind: 'text', key: 'owner', param: 'owner', label: 'Owner', default: '', chipLabel: (value) => `owner = ${value}` },
	{ kind: 'tags', key: 'tags', param: 'tags', label: 'Tags' },
	{ kind: 'boolean', key: 'pinned', param: 'pinned', label: 'Pinned', chipLabel: 'pinned' },
	{
		kind: 'enum',
		key: 'sortOrder',
		param: 'sort_order',
		label: 'Sort order',
		values: ['asc', 'desc'],
		default: 'desc',
		filterable: false
	}
];

const codec = createFilterCodec<DemoFilters>({
	defaults: DEFAULTS,
	fields: FIELDS,
	sortValues: ['name', 'created']
});

describe('createFilterCodec: URL round-trip', () => {
	it('round-trips a fully-set filter state through the URL', () => {
		const filters: DemoFilters = {
			q: 'gold',
			status: 'active',
			owner: 'alice',
			tags: ['a', 'b'],
			pinned: true,
			sortBy: 'created',
			sortOrder: 'asc'
		};
		const params = codec.toSearchParams(filters);
		expect(codec.fromSearchParams(params)).toEqual(filters);
	});

	it('writes no keys for the default filter state', () => {
		expect(codec.toSearchParams(DEFAULTS).toString()).toBe('');
	});

	it('falls back to defaults for out-of-range enum values instead of throwing', () => {
		const params = new URLSearchParams('status=bogus&sort_order=bogus');
		const filters = codec.fromSearchParams(params);
		expect(filters.status).toBe('');
		expect(filters.sortOrder).toBe('desc');
	});

	it('parses a comma-joined tags list, trimming and dropping blanks', () => {
		const params = new URLSearchParams('tags=a, b,,c');
		expect(codec.fromSearchParams(params).tags).toEqual(['a', 'b', 'c']);
	});

	it('reads a boolean field only from the literal "1"', () => {
		expect(codec.fromSearchParams(new URLSearchParams('pinned=1')).pinned).toBe(true);
		expect(codec.fromSearchParams(new URLSearchParams('pinned=true')).pinned).toBe(false);
	});
});

describe('createFilterCodec: activeCount', () => {
	it('is zero for the default filters', () => {
		expect(codec.activeCount(DEFAULTS)).toBe(0);
	});

	it('counts tags as a single field and excludes non-filterable fields', () => {
		const filters: DemoFilters = { ...DEFAULTS, status: 'active', tags: ['a', 'b', 'c'], sortOrder: 'asc' };
		expect(codec.activeCount(filters)).toBe(2);
	});
});

describe('createFilterCodec: chips', () => {
	it('builds one chip per active field, in field order, with tags expanding to one chip each', () => {
		const filters: DemoFilters = { ...DEFAULTS, status: 'active', tags: ['x', 'y'], pinned: true };
		expect(codec.chips(filters)).toEqual([
			{ key: 'status', label: 'active' },
			{ key: 'tag:x', label: '#x' },
			{ key: 'tag:y', label: '#y' },
			{ key: 'pinned', label: 'pinned' }
		]);
	});

	it('applies a per-call override for dynamic chip labels', () => {
		const filters: DemoFilters = { ...DEFAULTS, owner: 'user-1' };
		expect(codec.chips(filters)).toEqual([{ key: 'owner', label: 'owner = user-1' }]);
		expect(codec.chips(filters, { owner: () => 'owner = Alice' })).toEqual([{ key: 'owner', label: 'owner = Alice' }]);
	});
});

describe('createFilterCodec: clearChip / clearAll', () => {
	it('clears exactly the field named by the chip key, and one tag at a time', () => {
		const filters: DemoFilters = { ...DEFAULTS, status: 'active', tags: ['a', 'b'], pinned: true };
		expect(codec.clearChip(filters, 'status').status).toBe('');
		expect(codec.clearChip(filters, 'pinned').pinned).toBe(false);
		expect(codec.clearChip(filters, 'tag:a').tags).toEqual(['b']);
	});

	it('is a no-op (same reference) for an unknown key', () => {
		expect(codec.clearChip(DEFAULTS, 'nonsense')).toBe(DEFAULTS);
	});

	it('resets every filterable field but keeps the query, sort and non-filterable fields', () => {
		const filters: DemoFilters = {
			q: 'kept',
			status: 'active',
			owner: 'alice',
			tags: ['a'],
			pinned: true,
			sortBy: 'created',
			sortOrder: 'asc'
		};
		expect(codec.clearAll(filters)).toEqual({
			...DEFAULTS,
			q: 'kept',
			sortBy: 'created',
			sortOrder: 'asc'
		});
	});
});
