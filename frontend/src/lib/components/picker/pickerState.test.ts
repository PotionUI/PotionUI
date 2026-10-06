import { describe, expect, it } from 'vitest';
import { buildCollisionIndex } from './pickFacts';
import {
	applyLabel,
	clearFiltered,
	computeDiff,
	diffIsEmpty,
	diffSummary,
	filterRows,
	filteredSelectionState,
	rowsForView,
	selectFiltered,
	viewCounts
} from './pickerState';
import { groupsKind, presetsKind } from './kinds';
import type { PresetInfo } from '$lib/types/api';
import type { UserGroup } from '$lib/services/admin-api';

function preset(id: string, name: string, extra: Partial<PresetInfo> = {}): PresetInfo {
	return { id, name, version: '1.0.0', tags: [], engine: 'native', category: 'image', modes: ['txt2img'], ...extra };
}

describe('computeDiff', () => {
	it('splits additions and removals against the initial set', () => {
		const diff = computeDiff(new Set(['a', 'b']), new Set(['b', 'c', 'd']));
		expect(diff).toEqual({ add: ['c', 'd'], remove: ['a'] });
	});

	it('is empty when nothing changed', () => {
		expect(diffIsEmpty(computeDiff(new Set(['a']), new Set(['a'])))).toBe(true);
	});

	it('orders ids by the visible order when given', () => {
		const diff = computeDiff(new Set(), new Set(['c', 'a', 'b']), ['a', 'b', 'c']);
		expect(diff.add).toEqual(['a', 'b', 'c']);
	});

	it('summarises and labels the apply action', () => {
		expect(diffSummary({ add: ['a', 'b', 'c'], remove: ['x'] })).toBe('+3 add / -1 remove');
		expect(diffSummary({ add: [], remove: ['x'] })).toBe('-1 remove');
		expect(applyLabel({ add: ['a', 'b'], remove: [] }, 'presets', 'preset')).toBe('Add 2 presets');
		expect(applyLabel({ add: ['a'], remove: [] }, 'presets', 'preset')).toBe('Add 1 preset');
		expect(applyLabel({ add: [], remove: ['x', 'y'] }, 'presets', 'preset')).toBe('Remove 2');
		expect(applyLabel({ add: ['a'], remove: ['x'] }, 'presets', 'preset')).toBe('Apply 2 changes');
		expect(applyLabel({ add: [], remove: [] }, 'presets', 'preset')).toBe('Apply');
	});
});

describe('views', () => {
	const rows = [preset('a', 'A'), preset('b', 'B'), preset('c', 'C')];
	const assigned = new Set(['b']);

	it('splits rows into assigned, not assigned and all', () => {
		expect(rowsForView(rows, (r) => r.id, 'unassigned', assigned).map((r) => r.id)).toEqual(['a', 'c']);
		expect(rowsForView(rows, (r) => r.id, 'assigned', assigned).map((r) => r.id)).toEqual(['b']);
		expect(rowsForView(rows, (r) => r.id, 'all', assigned)).toHaveLength(3);
	});

	it('counts each view', () => {
		expect(viewCounts(['a', 'b', 'c'], assigned)).toEqual({ unassigned: 2, assigned: 1, all: 3 });
	});
});

describe('select-all of the filtered set', () => {
	it('adds every filtered id and keeps selections outside the filter', () => {
		const next = selectFiltered(new Set(['z']), ['a', 'b']);
		expect([...next].sort()).toEqual(['a', 'b', 'z']);
	});

	it('does not touch locked ids', () => {
		const next = selectFiltered(new Set(), ['a', 'b'], new Set(['b']));
		expect([...next]).toEqual(['a']);
		const cleared = clearFiltered(new Set(['a', 'b']), ['a', 'b'], new Set(['b']));
		expect([...cleared]).toEqual(['b']);
	});

	it('reports a tri-state for the filtered ids', () => {
		expect(filteredSelectionState(['a', 'b'], new Set())).toBe('none');
		expect(filteredSelectionState(['a', 'b'], new Set(['a']))).toBe('some');
		expect(filteredSelectionState(['a', 'b'], new Set(['a', 'b']))).toBe('all');
		expect(filteredSelectionState(['a'], new Set(), new Set(['a']))).toBe('none');
	});
});

describe('filterRows', () => {
	const rows = [
		preset('1', 'Krea-2', { engine: 'native', category: 'image' }),
		preset('2', 'Krea 2', { engine: 'comfyui', category: 'image' }),
		preset('3', 'Wan', { engine: 'native', category: 'video', modes: ['txt2vid', 'img2vid'] })
	];
	const collisions = buildCollisionIndex(rows, presetsKind);

	it('searches across name, engine and mode', () => {
		const q = (text: string) => filterRows(rows, presetsKind, { q: text, filters: {}, sort: 'name' }, collisions).map((r) => r.id);
		expect(q('krea')).toEqual(expect.arrayContaining(['1', '2']));
		expect(q('comfyui')).toEqual(['2']);
		expect(q('img2vid')).toEqual(['3']);
		expect(q('native image')).toEqual(['1']);
	});

	it('applies kind filters', () => {
		const out = filterRows(rows, presetsKind, { q: '', filters: { category: 'video' }, sort: 'name' }, collisions);
		expect(out.map((r) => r.id)).toEqual(['3']);
	});

	it('same-name filter keeps only colliding rows', () => {
		const out = filterRows(rows, presetsKind, { q: '', filters: { same_name: 'same' }, sort: 'name' }, collisions);
		expect(out.map((r) => r.id).sort()).toEqual(['1', '2']);
	});

	it('sorts by the chosen sort and falls back to the default', () => {
		const used = [
			preset('1', 'B', { assignment_count: 1 }),
			preset('2', 'A', { assignment_count: 5 })
		];
		const idx = buildCollisionIndex(used, presetsKind);
		expect(filterRows(used, presetsKind, { q: '', filters: {}, sort: 'use' }, idx).map((r) => r.id)).toEqual(['2', '1']);
		expect(filterRows(used, presetsKind, { q: '', filters: {}, sort: 'bogus' }, idx).map((r) => r.id)).toEqual(['2', '1']);
	});

	it('works for the groups kind without filters set', () => {
		const groups: UserGroup[] = [
			{ id: 'g1', name: 'Zed', is_system: false },
			{ id: 'g2', name: 'Alpha', is_system: true }
		];
		const idx = buildCollisionIndex(groups, groupsKind);
		const out = filterRows(groups, groupsKind, { q: '', filters: { system: 'system' }, sort: 'name' }, idx);
		expect(out.map((g) => g.id)).toEqual(['g2']);
	});
});
