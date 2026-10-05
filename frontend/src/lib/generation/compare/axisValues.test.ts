import { describe, expect, it } from 'vitest';
import {
	allChips,
	cellCount,
	checkboxAxisValues,
	chipOptionsFor,
	chipsAxisValues,
	countMatches,
	effectiveAxes,
	formatCellSummary,
	isChipSelected,
	loraAxisValues,
	parseNumberList,
	promptAxisValues,
	randomSeeds,
	rangeValues,
	toggleChip
} from './axisValues';
import type { CompareAxis } from './types';

function axis(field: string, count: number): CompareAxis {
	return {
		field,
		type: 'select',
		label: field,
		values: Array.from({ length: count }, (_, i) => ({ value: i, label: String(i) }))
	};
}

function seededRng(seed: number) {
	let state = seed;
	return () => {
		state = (state * 1664525 + 1013904223) % 4294967296;
		return state / 4294967296;
	};
}

describe('rangeValues', () => {
	it('clamps every value to min and max', () => {
		expect(rangeValues(0, 50, 10, 10, 30)).toEqual([10, 20, 30]);
	});

	it('steps by decimals without float drift', () => {
		expect(rangeValues(0, 0.5, 0.1)).toEqual([0, 0.1, 0.2, 0.3, 0.4, 0.5]);
	});

	it('walks downward when to is below from', () => {
		expect(rangeValues(30, 10, 10)).toEqual([30, 20, 10]);
	});

	it('caps the number of values at 100', () => {
		expect(rangeValues(0, 1000, 1)).toHaveLength(100);
	});

	it('returns nothing for a zero or invalid step', () => {
		expect(rangeValues(0, 10, 0)).toEqual([]);
		expect(rangeValues(NaN, 10, 1)).toEqual([]);
	});
});

describe('parseNumberList', () => {
	it('clamps and dedupes', () => {
		expect(parseNumberList('1, 5, 99, 99, abc, 5', 2, 10)).toEqual([2, 5, 10]);
	});

	it('caps at the limit', () => {
		expect(parseNumberList('1 2 3 4', null, null, 3)).toEqual([1, 2, 3]);
	});
});

describe('randomSeeds', () => {
	it('returns unique seeds within range, deterministically for a seeded rng', () => {
		const seeds = randomSeeds(5, seededRng(7));
		expect(seeds).toHaveLength(5);
		expect(new Set(seeds).size).toBe(5);
		expect(seeds.every((s) => Number.isInteger(s) && s >= 0 && s <= 4294967295)).toBe(true);
		expect(randomSeeds(5, seededRng(7))).toEqual(seeds);
	});

	it('caps the count at 100', () => {
		expect(randomSeeds(500, seededRng(1))).toHaveLength(100);
	});
});

describe('chips', () => {
	const options = [
		{ value: 'a', label: 'A' },
		{ value: 'b', label: 'B' },
		{ value: 'c', label: 'C' }
	];

	it('toggles on and keeps option order', () => {
		let values = toggleChip([], options, 'c');
		values = toggleChip(values, options, 'a');
		expect(values.map((v) => v.value)).toEqual(['a', 'c']);
	});

	it('toggles off an already selected chip', () => {
		const values = toggleChip([{ value: 'a', label: 'A' }], options, 'a');
		expect(values).toEqual([]);
	});

	it('allChips caps at 100', () => {
		const many = Array.from({ length: 150 }, (_, i) => ({ value: i, label: String(i) }));
		expect(allChips(many)).toHaveLength(100);
	});

	it('maps checkbox_group options to single-item arrays and toggles them', () => {
		const mapped = chipOptionsFor('checkbox_group', options);
		expect(mapped[1].value).toEqual(['b']);
		const values = toggleChip([], mapped, ['b']);
		expect(isChipSelected(values, ['b'])).toBe(true);
		expect(toggleChip(values, mapped, ['b'])).toEqual([]);
	});

	it('leaves other types untouched', () => {
		expect(chipOptionsFor('select', options)).toBe(options);
	});

	it('chipsAxisValues picks by deep equality', () => {
		const mapped = chipOptionsFor('checkbox_group', options);
		expect(chipsAxisValues(mapped, [['a'], ['c']]).map((v) => v.label)).toEqual(['A', 'C']);
	});
});

describe('prompt axis', () => {
	it('counts non overlapping matches', () => {
		expect(countMatches('warm light, warm light', 'warm light')).toBe(2);
		expect(countMatches('aaaa', 'aa')).toBe(2);
		expect(countMatches('abc', '')).toBe(0);
	});

	it('keeps original first, dedupes and labels', () => {
		const values = promptAxisValues('dusk', [null, 'dawn', 'dawn', 'noon']);
		expect(values.map((v) => v.label)).toEqual(['dusk', 'dawn', 'noon']);
		expect(values[0].value).toEqual({ find: 'dusk', replace: null });
		expect(values[1].value).toEqual({ find: 'dusk', replace: 'dawn' });
	});
});

describe('loraAxisValues', () => {
	it('labels name and strength', () => {
		const values = loraAxisValues('model:1', 'film-grain', [0, 0.4, 1], 0.1);
		expect(values.map((v) => v.label)).toEqual(['film-grain · 0', 'film-grain · 0.4', 'film-grain · 1']);
		expect(values[1].value).toEqual({ lora: 'model:1', strength: 0.4 });
	});
});

describe('grid shape', () => {
	it('counts cells for two axes', () => {
		expect(cellCount({ x: axis('a', 4), y: axis('b', 3) })).toBe(12);
	});

	it('counts a single axis', () => {
		expect(cellCount({ x: axis('a', 4), y: null })).toBe(4);
	});

	it('promotes a lone y axis to columns', () => {
		const { cols, rows } = effectiveAxes({ x: null, y: axis('b', 3) });
		expect(cols?.field).toBe('b');
		expect(rows).toBeNull();
	});

	it('is zero without values', () => {
		expect(cellCount({ x: axis('a', 0), y: null })).toBe(0);
	});

	it('formats the summary', () => {
		expect(formatCellSummary({ x: axis('a', 4), y: axis('b', 3) })).toBe('4 × 3 = 12 generations');
		expect(formatCellSummary({ x: axis('a', 4), y: null })).toBe('4 generations');
	});

	it('checkbox values are on then off', () => {
		expect(checkboxAxisValues().map((v) => v.value)).toEqual([true, false]);
	});
});
