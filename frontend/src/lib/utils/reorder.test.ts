import { describe, it, expect } from 'vitest';
import { moveItem, dropIndexFor } from './reorder';

describe('moveItem', () => {
	it('moves an item forward', () => {
		expect(moveItem(['a', 'b', 'c', 'd'], 0, 2)).toEqual(['b', 'c', 'a', 'd']);
	});

	it('moves an item backward', () => {
		expect(moveItem(['a', 'b', 'c', 'd'], 3, 1)).toEqual(['a', 'd', 'b', 'c']);
	});

	it('is a no-op when from and to resolve to the same slot', () => {
		const items = ['a', 'b', 'c'];
		expect(moveItem(items, 1, 1)).toBe(items);
	});

	it('clamps an out-of-range to above the end', () => {
		expect(moveItem(['a', 'b', 'c'], 0, 100)).toEqual(['b', 'c', 'a']);
	});

	it('clamps a negative to to the start', () => {
		expect(moveItem(['a', 'b', 'c'], 2, -5)).toEqual(['c', 'a', 'b']);
	});

	it('returns the same reference for an out-of-range from', () => {
		const items = ['a', 'b', 'c'];
		expect(moveItem(items, -1, 0)).toBe(items);
		expect(moveItem(items, 3, 0)).toBe(items);
	});

	it('returns the same reference for an empty array', () => {
		const items: string[] = [];
		expect(moveItem(items, 0, 0)).toBe(items);
	});

	it('preserves every other item and its own keys/contents untouched', () => {
		const rows = [
			{ model: 'a', strength: 1 },
			{ model: 'b', strength: 0, saved_strength: 0.7 },
			{ model: 'c', strength: -0.5 }
		];
		const moved = moveItem(rows, 0, 2);
		expect(moved).toEqual([
			{ model: 'b', strength: 0, saved_strength: 0.7 },
			{ model: 'c', strength: -0.5 },
			{ model: 'a', strength: 1 }
		]);
		// Same object references, not clones - a moved row's saved_strength
		// etc. survives by construction, not by re-serialization.
		expect(moved[2]).toBe(rows[0]);
		expect(moved[0]).toBe(rows[1]);
		expect(moved[1]).toBe(rows[2]);
	});
});

describe('dropIndexFor', () => {
	// Hand-verified against moveItem for all four (from < target / from >
	// target) x (before / after) quadrants - see the combined test below for
	// the end-to-end behavior this arithmetic exists to produce.
	it('dropping before a later row', () => {
		expect(dropIndexFor(0, 2, 'before')).toBe(1);
	});

	it('dropping after a later row', () => {
		expect(dropIndexFor(0, 2, 'after')).toBe(2);
	});

	it('dropping before an earlier row', () => {
		expect(dropIndexFor(3, 1, 'before')).toBe(1);
	});

	it('dropping after an earlier row', () => {
		expect(dropIndexFor(3, 1, 'after')).toBe(2);
	});
});

describe('moveItem + dropIndexFor combined (drag-and-drop end to end)', () => {
	const items = ['A', 'B', 'C', 'D'];

	it('dragging the first row and dropping it before the third', () => {
		const to = dropIndexFor(0, 2, 'before');
		expect(moveItem(items, 0, to)).toEqual(['B', 'A', 'C', 'D']);
	});

	it('dragging the first row and dropping it after the third', () => {
		const to = dropIndexFor(0, 2, 'after');
		expect(moveItem(items, 0, to)).toEqual(['B', 'C', 'A', 'D']);
	});

	it('dragging the last row and dropping it before the second', () => {
		const to = dropIndexFor(3, 1, 'before');
		expect(moveItem(items, 3, to)).toEqual(['A', 'D', 'B', 'C']);
	});

	it('dragging the last row and dropping it after the second', () => {
		const to = dropIndexFor(3, 1, 'after');
		expect(moveItem(items, 3, to)).toEqual(['A', 'B', 'D', 'C']);
	});

	it('dropping a row on itself is a no-op', () => {
		const to = dropIndexFor(1, 1, 'before');
		expect(moveItem(items, 1, to)).toBe(items);
	});
});
