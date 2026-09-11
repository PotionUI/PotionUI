import { describe, expect, it } from 'vitest';
import { clampCompareValue, compareValueFromKey, compareValueFromPointer } from './compareFrame';

describe('clampCompareValue', () => {
	it('rounds to the nearest integer', () => {
		expect(clampCompareValue(49.6)).toBe(50);
		expect(clampCompareValue(49.4)).toBe(49);
	});

	it('clamps to the 0-100 range', () => {
		expect(clampCompareValue(-10)).toBe(0);
		expect(clampCompareValue(140)).toBe(100);
	});

	it('falls back to the default for non-finite input', () => {
		expect(clampCompareValue(Number.NaN)).toBe(50);
		expect(clampCompareValue(Number.POSITIVE_INFINITY)).toBe(50);
	});
});

describe('compareValueFromPointer', () => {
	it('maps a pointer position to a percentage across the frame width', () => {
		const rect = { left: 100, width: 200 };
		expect(compareValueFromPointer(100, rect)).toBe(0);
		expect(compareValueFromPointer(200, rect)).toBe(50);
		expect(compareValueFromPointer(300, rect)).toBe(100);
	});

	it('clamps pointer positions outside the frame', () => {
		const rect = { left: 100, width: 200 };
		expect(compareValueFromPointer(0, rect)).toBe(0);
		expect(compareValueFromPointer(1000, rect)).toBe(100);
	});

	it('falls back to the default when the frame has no width', () => {
		expect(compareValueFromPointer(50, { left: 0, width: 0 })).toBe(50);
	});
});

describe('compareValueFromKey', () => {
	it('steps left and right by 1', () => {
		expect(compareValueFromKey(50, 'ArrowLeft', false)).toBe(49);
		expect(compareValueFromKey(50, 'ArrowRight', false)).toBe(51);
	});

	it('steps by 10 with Shift', () => {
		expect(compareValueFromKey(50, 'ArrowLeft', true)).toBe(40);
		expect(compareValueFromKey(50, 'ArrowRight', true)).toBe(60);
	});

	it('clamps at the ends', () => {
		expect(compareValueFromKey(0, 'ArrowLeft', false)).toBe(0);
		expect(compareValueFromKey(100, 'ArrowRight', false)).toBe(100);
		expect(compareValueFromKey(5, 'ArrowLeft', true)).toBe(0);
	});

	it('jumps to the ends on Home/End', () => {
		expect(compareValueFromKey(50, 'Home', false)).toBe(0);
		expect(compareValueFromKey(50, 'End', false)).toBe(100);
	});

	it('returns null for keys it does not handle', () => {
		expect(compareValueFromKey(50, 'Tab', false)).toBeNull();
		expect(compareValueFromKey(50, 'a', false)).toBeNull();
	});
});
