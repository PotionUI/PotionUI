import { describe, expect, it } from 'vitest';
import { planSelectionBar } from './selectionBarLayout';

const items = [
	{ full: 90, icon: 44 },
	{ full: 70, icon: 44 },
	{ full: 150, icon: 44 },
	{ full: 160, icon: 60 }
];
const base = { fixed: 300, gap: 4, items };

describe('planSelectionBar', () => {
	it('keeps every label while the bar fits', () => {
		expect(planSelectionBar({ ...base, available: 1000 })).toEqual({ mode: 'inline', iconOnly: [] });
	});

	it('collapses to icons from the first item until it fits', () => {
		expect(planSelectionBar({ ...base, available: 740 })).toEqual({ mode: 'inline', iconOnly: [0] });
		expect(planSelectionBar({ ...base, available: 720 })).toEqual({ mode: 'inline', iconOnly: [0, 1] });
	});

	it('goes to overflow when icons alone do not fit', () => {
		expect(planSelectionBar({ ...base, available: 400 })).toEqual({ mode: 'overflow', iconOnly: [] });
	});

	it('ignores hidden items and items with no label to drop', () => {
		const plan = planSelectionBar({
			available: 390,
			fixed: 300,
			gap: 4,
			items: [{ full: 0, icon: 0 }, { full: 40, icon: 40 }, { full: 80, icon: 40 }]
		});
		expect(plan).toEqual({ mode: 'inline', iconOnly: [2] });
	});

	it('fits exactly at the boundary', () => {
		expect(planSelectionBar({ available: 300 + 12 + 470, fixed: 300, gap: 4, items })).toEqual({ mode: 'inline', iconOnly: [] });
	});
});
