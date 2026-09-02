import { describe, it, expect } from 'vitest';
import {
	apiErrorDetail,
	buildFindParams,
	defaultFilters,
	diffSegments,
	highlightSegments,
	isSearching,
	isTopLevelPath,
	nonDefaultFilterCount,
	rangeIds,
	retainSelection,
	selectedFields,
	pluginBatchOps,
	toggleAll,
	toggleId,
	topLevelCategories
} from './phrasebookSearch';

describe('isSearching', () => {
	it('is false for blank queries', () => {
		expect(isSearching('')).toBe(false);
		expect(isSearching('   ')).toBe(false);
		expect(isSearching(' dog ')).toBe(true);
	});
});

describe('nonDefaultFilterCount', () => {
	it('is zero for the defaults', () => {
		expect(nonDefaultFilterCount(defaultFilters())).toBe(0);
	});

	it('counts case, fields, scope, include-inactive and subtree independently', () => {
		expect(nonDefaultFilterCount({ ...defaultFilters(), caseSensitive: true })).toBe(1);
		expect(nonDefaultFilterCount({ ...defaultFilters(), inLabel: false })).toBe(1);
		expect(nonDefaultFilterCount({ ...defaultFilters(), inValue: false })).toBe(1);
		expect(nonDefaultFilterCount({ ...defaultFilters(), scope: 'values' })).toBe(1);
		expect(nonDefaultFilterCount({ ...defaultFilters(), includeInactive: false })).toBe(1);
		expect(nonDefaultFilterCount({ ...defaultFilters(), pathPrefix: 'animals' })).toBe(1);
	});

	it('ignores query and mode', () => {
		expect(nonDefaultFilterCount({ ...defaultFilters(), query: 'dog', mode: 'regex' })).toBe(0);
	});

	it('sums independent deviations', () => {
		expect(
			nonDefaultFilterCount({
				...defaultFilters(),
				caseSensitive: true,
				scope: 'categories',
				pathPrefix: 'animals'
			})
		).toBe(3);
	});

	it('counts a non-"all" browsing state filter, defaulting to "all" when omitted', () => {
		expect(nonDefaultFilterCount(defaultFilters())).toBe(0);
		expect(nonDefaultFilterCount(defaultFilters(), 'all')).toBe(0);
		expect(nonDefaultFilterCount(defaultFilters(), 'active')).toBe(1);
		expect(nonDefaultFilterCount(defaultFilters(), 'inactive')).toBe(1);
	});

	it('sums the state filter alongside search filter deviations', () => {
		expect(nonDefaultFilterCount({ ...defaultFilters(), caseSensitive: true }, 'active')).toBe(2);
	});
});

describe('buildFindParams', () => {
	it('trims the query and defaults fields to both when none are ticked', () => {
		const params = buildFindParams({ ...defaultFilters(), query: '  dog ', inLabel: false, inValue: false });
		expect(params.q).toBe('dog');
		expect(params.fields).toEqual(['label', 'value']);
		expect(params.limit).toBe(200);
	});

	it('passes every filter through', () => {
		const params = buildFindParams(
			{
				query: 'x',
				mode: 'regex',
				caseSensitive: true,
				scope: 'values',
				includeInactive: false,
				pathPrefix: 'animals',
				inLabel: true,
				inValue: false
			},
			50
		);
		expect(params).toEqual({
			q: 'x',
			mode: 'regex',
			case_sensitive: true,
			scope: 'values',
			include_inactive: false,
			path_prefix: 'animals',
			fields: ['label'],
			limit: 50
		});
	});

	it('selectedFields keeps the ticked order label then value', () => {
		expect(selectedFields({ inLabel: false, inValue: true })).toEqual(['value']);
		expect(selectedFields({ inLabel: true, inValue: true })).toEqual(['label', 'value']);
	});
});

describe('highlightSegments', () => {
	it('splits text into matched and unmatched segments for the given field', () => {
		const segments = highlightSegments('a small dog', [{ field: 'value', start: 8, end: 11 }], 'value');
		expect(segments).toEqual([
			{ text: 'a small ', match: false },
			{ text: 'dog', match: true }
		]);
	});

	it('ignores spans for other fields and returns one plain segment', () => {
		expect(highlightSegments('puppy', [{ field: 'label', start: 0, end: 3 }], 'value')).toEqual([
			{ text: 'puppy', match: false }
		]);
	});

	it('merges overlapping and adjacent spans and clamps out-of-range ones', () => {
		const segments = highlightSegments(
			'abcdef',
			[
				{ field: 'label', start: 3, end: 40 },
				{ field: 'label', start: 0, end: 2 },
				{ field: 'label', start: 2, end: 4 },
				{ field: 'label', start: 5, end: 5 }
			],
			'label'
		);
		expect(segments).toEqual([{ text: 'abcdef', match: true }]);
	});

	it('returns nothing for empty text', () => {
		expect(highlightSegments('', [], 'label')).toEqual([]);
	});
});

describe('selection set ops', () => {
	it('toggleId adds and removes without mutating the input', () => {
		const start = new Set(['a']);
		const added = toggleId(start, 'b');
		expect([...added]).toEqual(['a', 'b']);
		expect([...start]).toEqual(['a']);
		expect([...toggleId(added, 'a')]).toEqual(['b']);
	});

	it('toggleAll selects everything unless everything is already selected', () => {
		expect([...toggleAll(new Set(['a']), ['a', 'b'])]).toEqual(['a', 'b']);
		expect([...toggleAll(new Set(['a', 'b']), ['a', 'b'])]).toEqual([]);
		expect([...toggleAll(new Set(), [])]).toEqual([]);
	});

	it('rangeIds spans from the anchor to the target in either direction', () => {
		const ids = ['a', 'b', 'c', 'd'];
		expect(rangeIds(ids, 'b', 'd')).toEqual(['b', 'c', 'd']);
		expect(rangeIds(ids, 'd', 'b')).toEqual(['b', 'c', 'd']);
		expect(rangeIds(ids, null, 'c')).toEqual(['c']);
		expect(rangeIds(ids, 'zzz', 'c')).toEqual(['c']);
		expect(rangeIds(ids, 'a', 'zzz')).toEqual([]);
	});

	it('retainSelection drops ids that no longer match', () => {
		expect([...retainSelection(new Set(['a', 'b', 'c']), ['b', 'c', 'd'])]).toEqual(['b', 'c']);
	});
});

describe('diffSegments', () => {
	it('isolates the changed middle', () => {
		expect(diffSegments('a small dog', 'a small cat')).toEqual({
			prefix: 'a small ',
			removed: 'dog',
			added: 'cat',
			suffix: ''
		});
	});

	it('handles pure insertions and deletions', () => {
		expect(diffSegments('dog', 'hot dog')).toEqual({ prefix: '', removed: '', added: 'hot ', suffix: 'dog' });
		expect(diffSegments('hot dog', 'dog')).toEqual({ prefix: '', removed: 'hot ', added: '', suffix: 'dog' });
	});

	it('does not let the suffix overlap the prefix', () => {
		expect(diffSegments('aaa', 'aaaa')).toEqual({ prefix: 'aaa', removed: '', added: 'a', suffix: '' });
		expect(diffSegments('same', 'same')).toEqual({ prefix: 'same', removed: '', added: '', suffix: '' });
	});
});

describe('pluginBatchOps', () => {
	it('keeps only non-core ops, sorted by label', () => {
		const core = { id: 'replace', label: 'Replace…', component: null, has_preview: true, source: 'core' };
		const b = { id: 'b', label: 'Zap', component: 'plugin:x:B.svelte', has_preview: false, source: 'x' };
		const a = { id: 'a', label: 'Alpha', component: null, has_preview: false, source: 'y' };
		expect(pluginBatchOps([core, b, a]).map((op) => op.id)).toEqual(['a', 'b']);
		expect(pluginBatchOps([core])).toEqual([]);
	});
});

describe('subtree picker helpers', () => {
	it('isTopLevelPath accepts only dot-free non-empty paths', () => {
		expect(isTopLevelPath('animals')).toBe(true);
		expect(isTopLevelPath('animals.dogs')).toBe(false);
		expect(isTopLevelPath('')).toBe(false);
	});

	it('topLevelCategories keeps roots sorted by path', () => {
		const roots = topLevelCategories([
			{ path: 'zoo', parent_id: undefined },
			{ path: 'animals.dogs', parent_id: 'x' },
			{ path: 'animals', parent_id: undefined },
			{ path: 'orphan.child', parent_id: undefined }
		]);
		expect(roots.map((c) => c.path)).toEqual(['animals', 'zoo']);
	});
});

describe('apiErrorDetail', () => {
	it('reads the FastAPI detail envelope', () => {
		const err = { response: { data: { detail: { success: false, error: 'invalid_pattern', message: 'bad' } } } };
		expect(apiErrorDetail(err)).toEqual({ error: 'invalid_pattern', message: 'bad' });
	});

	it('reads a bare error body and falls back to the code as message', () => {
		expect(apiErrorDetail({ response: { data: { error: 'unknown_values' } } })).toEqual({
			error: 'unknown_values',
			message: 'unknown_values'
		});
	});

	it('returns null for anything else', () => {
		expect(apiErrorDetail(new Error('boom'))).toBeNull();
		expect(apiErrorDetail({ response: { data: 'text' } })).toBeNull();
		expect(apiErrorDetail(null)).toBeNull();
	});
});
