import { describe, it, expect } from 'vitest';
import { placeholderTint } from './placeholderTint';

describe('placeholderTint', () => {
	it('is deterministic for the same seed', () => {
		expect(placeholderTint('Cinematic lighting')).toBe(placeholderTint('Cinematic lighting'));
	});

	it('varies across different seeds', () => {
		const tints = new Set(
			['Cinematic lighting', 'Golden hour', 'Studio portrait', 'Wide shot', 'Macro'].map(placeholderTint)
		);
		expect(tints.size).toBeGreaterThan(1);
	});

	it('picks a viz slot in range and stays low-alpha', () => {
		const style = placeholderTint('Anything');
		expect(style).toMatch(/^background: linear-gradient\(-?\d+deg, rgb\(var\(--viz-[1-8]\) \/ 0\.\d+\), transparent 65%\), rgb\(var\(--surface-3\)\);$/);
	});
});
