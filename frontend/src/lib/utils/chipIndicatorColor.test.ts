import { describe, it, expect } from 'vitest';
import { chipIndicatorColor } from './chipIndicatorColor';

describe('chipIndicatorColor', () => {
	it('maps indices 0..7 onto viz slots 1..8', () => {
		for (let i = 0; i < 8; i++) {
			expect(chipIndicatorColor(i)).toBe(`rgb(var(--viz-${i + 1}))`);
		}
	});

	it('wraps around after 8 slots', () => {
		expect(chipIndicatorColor(8)).toBe('rgb(var(--viz-1))');
		expect(chipIndicatorColor(9)).toBe('rgb(var(--viz-2))');
		expect(chipIndicatorColor(16)).toBe('rgb(var(--viz-1))');
	});

	it('handles negative indices without producing a negative or zero slot', () => {
		expect(chipIndicatorColor(-1)).toBe('rgb(var(--viz-8))');
		expect(chipIndicatorColor(-8)).toBe('rgb(var(--viz-1))');
	});
});
