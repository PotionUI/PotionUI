import { describe, it, expect } from 'vitest';
import {
	CHIP_INDICATOR_COLORS,
	chipIndicatorColorAt,
	chipIndicatorColorForName
} from './chipIndicatorColors';

describe('CHIP_INDICATOR_COLORS', () => {
	it('holds distinct hues', () => {
		expect(new Set(CHIP_INDICATOR_COLORS).size).toBe(CHIP_INDICATOR_COLORS.length);
	});

	it('carries no semantic token classes', () => {
		for (const color of CHIP_INDICATOR_COLORS) {
			expect(color).toMatch(/^bg-[a-z]+-400$/);
		}
	});
});

describe('chipIndicatorColorAt', () => {
	it('maps a position to its hue', () => {
		expect(chipIndicatorColorAt(0)).toBe('bg-red-400');
		expect(chipIndicatorColorAt(3)).toBe('bg-yellow-400');
	});

	it('wraps around past the end of the palette', () => {
		expect(chipIndicatorColorAt(CHIP_INDICATOR_COLORS.length)).toBe(chipIndicatorColorAt(0));
		expect(chipIndicatorColorAt(CHIP_INDICATOR_COLORS.length + 5)).toBe(chipIndicatorColorAt(5));
	});
});

describe('chipIndicatorColorForName', () => {
	it('is stable for the same name', () => {
		expect(chipIndicatorColorForName('style')).toBe(chipIndicatorColorForName('style'));
	});

	it('spreads names across the palette', () => {
		expect(chipIndicatorColorForName('style')).toBe('bg-orange-400');
		expect(chipIndicatorColorForName('subject')).toBe('bg-violet-400');
		expect(chipIndicatorColorForName('mood')).toBe('bg-teal-400');
	});

	it('gives an empty name the first hue rather than undefined', () => {
		expect(chipIndicatorColorForName('')).toBe(CHIP_INDICATOR_COLORS[0]);
	});
});
