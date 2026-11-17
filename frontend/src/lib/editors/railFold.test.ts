import { describe, expect, it } from 'vitest';
import { foldGroups, railContentHeight, type FoldInput } from './railFold';

const CORE: FoldInput[] = [
	{ id: 'shape', count: 4, fold: 4 },
	{ id: 'paint', count: 1 },
	{ id: 'generate', count: 2, fold: 5 },
	{ id: 'look', count: 5, fold: 2 },
	{ id: 'fx', count: 4, fold: 1 },
	{ id: 'looks', count: 1 },
	{ id: 'tools', count: 2, fold: 3 }
];

const FULL = railContentHeight(CORE, new Set());

describe('railFold', () => {
	it('keeps every entry an icon when the rail fits', () => {
		expect(foldGroups(CORE, FULL)).toEqual([]);
		expect(foldGroups(CORE, 5000)).toEqual([]);
	});

	it('folds one group at a time in the fixed order until the rail fits', () => {
		expect(foldGroups(CORE, FULL - 1)).toEqual(['fx']);
		const afterFx = railContentHeight(CORE, new Set(['fx']));
		expect(foldGroups(CORE, afterFx)).toEqual(['fx']);
		expect(foldGroups(CORE, afterFx - 1)).toEqual(['fx', 'look']);
		const afterLook = railContentHeight(CORE, new Set(['fx', 'look']));
		expect(foldGroups(CORE, afterLook - 1)).toEqual(['fx', 'look', 'tools']);
		const afterTools = railContentHeight(CORE, new Set(['fx', 'look', 'tools']));
		expect(foldGroups(CORE, afterTools - 1)).toEqual(['fx', 'look', 'tools', 'shape']);
		expect(foldGroups(CORE, 1)).toEqual(['fx', 'look', 'tools', 'shape', 'generate']);
	});

	it('never folds single-entry groups', () => {
		expect(foldGroups(CORE, 10)).not.toContain('paint');
		expect(foldGroups(CORE, 10)).not.toContain('looks');
	});

	it('matches the mock heights at 1100, 900 and 720 high editors', () => {
		expect(foldGroups(CORE, 1100 - 118)).toEqual([]);
		expect(foldGroups(CORE, 900 - 118)).toEqual(['fx']);
		expect(foldGroups(CORE, 720 - 118)).toEqual(['fx', 'look', 'tools']);
	});

	it('folds Effects when plugin entries are added', () => {
		const plugged = CORE.map((group) => (group.id === 'tools' ? { ...group, count: 3 } : group));
		expect(railContentHeight(plugged, new Set())).toBeGreaterThan(FULL);
		expect(foldGroups(plugged, FULL)).toEqual(['fx']);
	});

	it('only ever shrinks the fold set as the height grows, so a boundary height cannot oscillate', () => {
		let previous = foldGroups(CORE, 1).length;
		for (let h = 2; h < FULL + 40; h++) {
			const next = foldGroups(CORE, h);
			expect(next.length).toBeLessThanOrEqual(previous);
			expect(foldGroups(CORE, h)).toEqual(next);
			previous = next.length;
		}
	});

	it('folds unranked groups after ranked ones', () => {
		const video: FoldInput[] = [
			{ id: 'shape', count: 5 },
			{ id: 'audio', count: 3 },
			{ id: 'look', count: 1 }
		];
		const need = railContentHeight(video, new Set());
		expect(foldGroups(video, need)).toEqual([]);
		expect(foldGroups(video, need - 1)).toEqual(['audio']);
	});
});
