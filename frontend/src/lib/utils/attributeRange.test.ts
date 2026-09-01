import { describe, expect, it } from 'vitest';
import { formatRange, normalizeRange, strengthWithinRange } from './attributeRange';

describe('normalizeRange', () => {
	it('returns null for null/undefined', () => {
		expect(normalizeRange(null)).toBeNull();
		expect(normalizeRange(undefined)).toBeNull();
	});

	it('returns null for garbage input', () => {
		expect(normalizeRange('0.7')).toBeNull();
		expect(normalizeRange({})).toBeNull();
		expect(normalizeRange(NaN)).toBeNull();
		expect(normalizeRange([Number.NaN, 1])).toBeNull();
		expect(normalizeRange(['0.7', '1'])).toBeNull();
	});

	it('returns null for an empty or oversized array', () => {
		expect(normalizeRange([])).toBeNull();
		expect(normalizeRange([0.1, 0.2, 0.3])).toBeNull();
	});

	it('turns a bare number into a degenerate range', () => {
		expect(normalizeRange(0.8)).toEqual([0.8, 0.8]);
	});

	it('turns a 1-element array into a degenerate range', () => {
		expect(normalizeRange([0.8])).toEqual([0.8, 0.8]);
	});

	it('sorts an unsorted 2-element array', () => {
		expect(normalizeRange([1, 0.7])).toEqual([0.7, 1]);
	});

	it('keeps an already-sorted 2-element array as-is', () => {
		expect(normalizeRange([0.7, 1])).toEqual([0.7, 1]);
	});

	it('supports negative values (inverted LoRAs are legitimate)', () => {
		expect(normalizeRange([-1, -0.5])).toEqual([-1, -0.5]);
		expect(normalizeRange([-0.5, -1])).toEqual([-1, -0.5]);
		expect(normalizeRange(-0.75)).toEqual([-0.75, -0.75]);
	});
});

describe('formatRange', () => {
	it('renders a proper range with an en dash', () => {
		expect(formatRange([0.7, 1])).toBe('0.70–1.00');
	});

	it('collapses a degenerate range to a single number', () => {
		expect(formatRange([1, 1])).toBe('1.00');
	});

	it('preserves extra precision on either endpoint like formatStrength does', () => {
		expect(formatRange([0.125, 1])).toBe('0.125–1.00');
	});

	it('renders a negative range', () => {
		expect(formatRange([-1, -0.5])).toBe('-1.00–-0.50');
	});
});

describe('strengthWithinRange', () => {
	it('returns defaultStrength unchanged when there is no range', () => {
		expect(strengthWithinRange(1, null)).toBe(1);
	});

	it('keeps defaultStrength when it already falls inside the range', () => {
		expect(strengthWithinRange(0.8, [0.7, 1])).toBe(0.8);
	});

	it('keeps defaultStrength at either boundary (inclusive)', () => {
		expect(strengthWithinRange(0.7, [0.7, 1])).toBe(0.7);
		expect(strengthWithinRange(1, [0.7, 1])).toBe(1);
	});

	it('falls back to the range max when defaultStrength is outside the range', () => {
		expect(strengthWithinRange(1, [0.2, 0.5])).toBe(0.5);
		expect(strengthWithinRange(0.1, [0.7, 1])).toBe(1);
	});

	it('handles a degenerate range', () => {
		expect(strengthWithinRange(1, [0.5, 0.5])).toBe(0.5);
		expect(strengthWithinRange(0.5, [0.5, 0.5])).toBe(0.5);
	});

	it('handles negative ranges', () => {
		expect(strengthWithinRange(1, [-1, -0.5])).toBe(-0.5);
		expect(strengthWithinRange(-0.8, [-1, -0.5])).toBe(-0.8);
	});
});
