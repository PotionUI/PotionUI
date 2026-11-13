import { describe, expect, it } from 'vitest';
import { foldList } from './listFold';

const items = (n: number) => Array.from({ length: n }, (_, i) => i);

describe('foldList', () => {
	it.each([0, 3, 5])('does not fold %i items above 5', (n) => {
		expect(foldList(items(n), 5, 3, false)).toMatchObject({ foldable: false, hidden: 0, total: n });
	});

	it('folds 6 items to 3 above 5', () => {
		const f = foldList(items(6), 5, 3, false);
		expect(f).toMatchObject({ foldable: true, hidden: 3, total: 6 });
		expect(f.visible).toEqual([0, 1, 2]);
	});

	it('does not fold 8 tags and folds 9 to 6 above 8', () => {
		expect(foldList(items(8), 8, 6, false).foldable).toBe(false);
		const f = foldList(items(9), 8, 6, false);
		expect(f).toMatchObject({ foldable: true, hidden: 3 });
		expect(f.visible).toHaveLength(6);
	});

	it('shows everything when expanded', () => {
		expect(foldList(items(9), 8, 6, true)).toMatchObject({ hidden: 0, foldable: true });
	});
});
