import { describe, expect, it } from 'vitest';
import { isEchoOnly } from './externalWrite';

describe('isEchoOnly', () => {
	it('is true when only the written names differ from what was last published', () => {
		expect(isEchoOnly({ x: 1, y: 2, z: 3 }, { x: 9, y: 2, z: 3 }, new Set(['x']))).toBe(true);
	});

	it('is false when a name that was not written changed too', () => {
		expect(isEchoOnly({ x: 1, y: 2 }, { x: 9, y: 5 }, new Set(['x']))).toBe(false);
	});

	it('is false when a derived field appeared next to the written one', () => {
		expect(isEchoOnly({ x: 1 }, { x: 9, derived: true }, new Set(['x']))).toBe(false);
	});

	it('is false with nothing published yet', () => {
		expect(isEchoOnly(null, { x: 1 }, new Set(['x']))).toBe(false);
	});

	it('compares nested values by content', () => {
		expect(isEchoOnly({ x: 1, box: [1, 2] }, { x: 2, box: [1, 2] }, new Set(['x']))).toBe(true);
		expect(isEchoOnly({ x: 1, box: [1, 2] }, { x: 2, box: [1, 3] }, new Set(['x']))).toBe(false);
	});
});
