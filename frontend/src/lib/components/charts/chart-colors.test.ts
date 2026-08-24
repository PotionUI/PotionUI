import { describe, it, expect } from 'vitest';
import { colorForKey, slotForKey, foldTail, createOrderedSlots, VIZ_SLOTS } from './chart-colors';

describe('colorForKey / slotForKey', () => {
	it('is deterministic for the same key across calls', () => {
		const a = colorForKey('SDXL');
		const b = colorForKey('SDXL');
		expect(a).toBe(b);
	});

	it('returns a slot in range 1..VIZ_SLOTS', () => {
		for (const key of ['a', 'b', 'preset-1', 'QwenImage', 'comfyui']) {
			const slot = slotForKey(key);
			expect(slot).toBeGreaterThanOrEqual(1);
			expect(slot).toBeLessThanOrEqual(VIZ_SLOTS);
		}
	});

	it('does not change other keys slot when one key is removed from a set', () => {
		const keys = ['alpha', 'beta', 'gamma', 'delta'];
		const before = new Map(keys.map((k) => [k, slotForKey(k)]));
		const remaining = keys.filter((k) => k !== 'beta');
		for (const k of remaining) {
			expect(slotForKey(k)).toBe(before.get(k));
		}
	});
});

describe('foldTail', () => {
	const items = [
		{ key: 'a', label: 'A', count: 10 },
		{ key: 'b', label: 'B', count: 9 },
		{ key: 'c', label: 'C', count: 8 },
		{ key: 'd', label: 'D', count: 7 },
		{ key: 'e', label: 'E', count: 6 },
		{ key: 'f', label: 'F', count: 5 },
		{ key: 'g', label: 'G', count: 4 },
		{ key: 'h', label: 'H', count: 3 },
		{ key: 'i', label: 'I', count: 2 },
		{ key: 'j', label: 'J', count: 1 }
	];

	it('passes items through unchanged when under the limit', () => {
		const small = items.slice(0, 3);
		expect(foldTail(small, 8)).toEqual(small);
	});

	it('folds the tail into "Other" and preserves total', () => {
		const result = foldTail(items, 8);
		expect(result).toHaveLength(9);
		const other = result[result.length - 1];
		expect(other).toEqual({ key: 'other', label: 'Other', count: 3 }); // 2 + 1

		const total = items.reduce((s, i) => s + i.count, 0);
		const resultTotal = result.reduce((s, i) => s + i.count, 0);
		expect(resultTotal).toBe(total);
	});

	it('keeps the top-N by count regardless of input order', () => {
		const shuffled = [...items].reverse();
		const result = foldTail(shuffled, 8);
		expect(result.slice(0, 8).map((i) => i.key)).toEqual([
			'a',
			'b',
			'c',
			'd',
			'e',
			'f',
			'g',
			'h'
		]);
	});
});

describe('createOrderedSlots', () => {
	it('assigns slots in first-appearance order', () => {
		const slots = createOrderedSlots();
		expect(slots.slotIndex('first')).toBe(1);
		expect(slots.slotIndex('second')).toBe(2);
		expect(slots.slotIndex('first')).toBe(1);
	});
});
