import { describe, expect, it } from 'vitest';
import { foldGroups, foldOrder, railContentHeight, type FoldInput } from './railFold';

const CORE: FoldInput[] = [
	{ id: 'generate', count: 2, fold: 7 },
	{ id: 'shape', count: 4, fold: 6 },
	{ id: 'paint', count: 4, fold: 5 },
	{ id: 'select', count: 3, fold: 3 },
	{ id: 'look', count: 5, fold: 4 },
	{ id: 'fx', count: 4, fold: 1 },
	{ id: 'looks', count: 1 },
	{ id: 'tools', count: 2, fold: 2 }
];

const FULL = railContentHeight(CORE, new Set());

const ORDER = ['fx', 'tools', 'select', 'look', 'paint', 'shape', 'generate'];

describe('railFold', () => {
	it('keeps every entry an icon when the rail fits', () => {
		expect(foldGroups(CORE, FULL)).toEqual([]);
		expect(foldGroups(CORE, 5000)).toEqual([]);
	});

	it('folds one group at a time in the fixed order until the rail fits', () => {
		const folded = new Set<string>();
		ORDER.forEach((id, step) => {
			const before = railContentHeight(CORE, folded);
			expect(foldGroups(CORE, before)).toEqual(ORDER.slice(0, step));
			expect(foldGroups(CORE, before - 1)).toEqual(ORDER.slice(0, step + 1));
			folded.add(id);
		});
		expect(foldGroups(CORE, 1)).toEqual(ORDER);
	});

	it('folds Effects, Cut out, Select and move, Light and colour, Paint, Transform and Generate, in that order', () => {
		expect(foldOrder(CORE).map((group) => group.id)).toEqual(['fx', 'tools', 'select', 'look', 'paint', 'shape', 'generate']);
	});

	it('never folds single-entry groups', () => {
		expect(foldGroups([...CORE, { id: 'solo', count: 1, fold: 0 }], 10)).not.toContain('solo');
		expect(foldGroups(CORE, 10)).not.toContain('looks');
	});

	it('folds only Effects, Cut out and Select and move at a 1100 high editor, keeping Transform whole', () => {
		expect(foldGroups(CORE, 1100 - 118)).toEqual(['fx', 'tools', 'select']);
	});

	it('matches the counts at 720, 800, 900, 1000 and 1100 high editors', () => {
		const at = (height: number) => {
			const folded = new Set(foldGroups(CORE, height - 118));
			return { folded: [...folded], icons: CORE.reduce((sum, group) => sum + (folded.has(group.id) ? 1 : group.count), 0) };
		};
		expect(at(720)).toEqual({ folded: ['fx', 'tools', 'select', 'look', 'paint', 'shape'], icons: 9 });
		expect(at(800)).toEqual({ folded: ['fx', 'tools', 'select', 'look', 'paint'], icons: 12 });
		expect(at(900)).toEqual({ folded: ['fx', 'tools', 'select', 'look'], icons: 15 });
		expect(at(1000)).toEqual({ folded: ['fx', 'tools', 'select', 'look'], icons: 15 });
		expect(at(1100)).toEqual({ folded: ['fx', 'tools', 'select'], icons: 19 });
	});

	it('never folds Transform before Paint', () => {
		for (let h = 1; h < FULL + 40; h++) {
			const folded = foldGroups(CORE, h);
			if (folded.includes('shape')) expect(folded).toContain('paint');
			if (folded.includes('generate')) expect(folded).toContain('shape');
		}
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
