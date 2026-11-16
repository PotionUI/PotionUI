import { describe, expect, it } from 'vitest';
import { pressureScale } from './pressure';

const base = {
	pressure: 0.5,
	pointerType: 'mouse',
	altKey: false,
	shiftKey: false
};

describe('pressureScale', () => {
	it('ignores pressure for mice and fingers', () => {
		expect(pressureScale({ ...base, pointerType: 'mouse', pressure: 0.1 })).toBe(1);
		expect(pressureScale({ ...base, pointerType: 'touch', pressure: 0.9 })).toBe(1);
	});

	it('scales a pen stroke with pressure inside 0.15 to 1', () => {
		expect(pressureScale({ ...base, pointerType: 'pen', pressure: 0.5 })).toBeCloseTo(0.7);
		expect(pressureScale({ ...base, pointerType: 'pen', pressure: 0 })).toBe(0.15);
		expect(pressureScale({ ...base, pointerType: 'pen', pressure: 1 })).toBe(1);
	});
});
