import { describe, expect, it } from 'vitest';
import {
	buildSelection,
	clampPath,
	pointInBounds,
	pointSelected,
	rectPath,
	translatePath
} from './selection';

describe('buildSelection', () => {
	it('rasterizes the path and measures its bounds', () => {
		const selection = buildSelection(rectPath({ x: 2, y: 3 }, { x: 6, y: 5 }), 10, 10);
		expect(selection?.bounds).toEqual({ x: 2, y: 3, width: 4, height: 2 });
		expect(pointSelected(selection!, { x: 3.5, y: 3.5 }, 10)).toBe(true);
		expect(pointSelected(selection!, { x: 7.5, y: 3.5 }, 10)).toBe(false);
	});

	it('refuses degenerate paths', () => {
		expect(
			buildSelection(
				[
					{ x: 0, y: 0 },
					{ x: 4, y: 4 }
				],
				10,
				10
			)
		).toBeNull();
		expect(buildSelection(rectPath({ x: 5, y: 5 }, { x: 5, y: 9 }), 10, 10)).toBeNull();
	});
});

describe('path helpers', () => {
	it('normalises a rectangle drawn in any direction', () => {
		expect(rectPath({ x: 9, y: 8 }, { x: 1, y: 2 })).toEqual([
			{ x: 1, y: 2 },
			{ x: 9, y: 2 },
			{ x: 9, y: 8 },
			{ x: 1, y: 8 }
		]);
	});

	it('clamps a path to the document', () => {
		expect(clampPath([{ x: -4, y: 20 }], 10, 10)).toEqual([{ x: 0, y: 10 }]);
	});

	it('translates every point', () => {
		expect(translatePath([{ x: 1, y: 1 }], 2, -3)).toEqual([{ x: 3, y: -2 }]);
	});

	it('tests a point against the bounds', () => {
		const selection = buildSelection(rectPath({ x: 2, y: 2 }, { x: 6, y: 6 }), 10, 10)!;
		expect(pointInBounds(selection, { x: 4, y: 4 })).toBe(true);
		expect(pointInBounds(selection, { x: 1, y: 4 })).toBe(false);
	});
});
