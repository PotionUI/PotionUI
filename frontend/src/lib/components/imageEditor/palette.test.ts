import { describe, expect, it } from 'vitest';
import { hexToRgba, rgbaToHex } from './palette';

describe('palette', () => {
	it('parses six and three digit hex colours', () => {
		expect(hexToRgba('#ff8000')).toEqual([255, 128, 0, 255]);
		expect(hexToRgba('#0f8')).toEqual([0, 255, 136, 255]);
	});

	it('round-trips through hex', () => {
		expect(rgbaToHex(255, 128, 0)).toBe('#ff8000');
		expect(hexToRgba(rgbaToHex(12, 34, 56))).toEqual([12, 34, 56, 255]);
	});
});
