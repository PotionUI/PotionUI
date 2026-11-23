import { describe, expect, it } from 'vitest';
import { tabChangedBeyondGeneration } from './tabGate';

describe('tabChangedBeyondGeneration', () => {
	const base = { id: 't', prompt: 'a', formData: { steps: 1 }, generation: { progress: 0 } };

	it('ignores a new generation object, which is all a progress tick changes', () => {
		expect(tabChangedBeyondGeneration(base, { ...base, generation: { progress: 0.5 } })).toBe(false);
	});

	it('reports any other field changing identity', () => {
		expect(tabChangedBeyondGeneration(base, { ...base, prompt: 'b' })).toBe(true);
		expect(tabChangedBeyondGeneration(base, { ...base, formData: { steps: 1 } })).toBe(true);
	});

	it('reports added and removed fields', () => {
		expect(tabChangedBeyondGeneration(base, { ...base, seed: 3 })).toBe(true);
		const { prompt: _removed, ...without } = base;
		expect(tabChangedBeyondGeneration(base, without)).toBe(true);
	});
});
