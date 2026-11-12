import { describe, it, expect } from 'vitest';
import { buildModelOptions, modelFilterLabel, modelFileStem } from './historyModelFilter';

describe('modelFileStem', () => {
	it('drops directories and the extension', () => {
		expect(modelFileStem('a/b\\flux_fp8_scaled.safetensors')).toBe('flux_fp8_scaled');
		expect(modelFileStem('Flux 3')).toBe('Flux 3');
		expect(modelFileStem('Flux 3.1')).toBe('Flux 3.1');
	});
});

describe('buildModelOptions', () => {
	it('uses the label, falls back to the stem, and sorts by name', () => {
		const options = buildModelOptions([
			{ name: 'zeta.safetensors', label: 'Zeta', count: 1 },
			{ name: 'dir/beta_fp8.safetensors', count: 2 },
			{ name: 'alpha.safetensors', label: 'alpha', count: 3 }
		]);
		expect(options.map((o) => o.label)).toEqual(['alpha', 'beta_fp8', 'Zeta']);
		expect(options[1].value).toBe('dir/beta_fp8.safetensors');
	});

	it('merges files sharing a label into one option matching all of them', () => {
		const options = buildModelOptions([
			{ name: 'a.safetensors', label: 'Same', count: 2 },
			{ name: 'b.gguf', label: 'Same', count: 3 }
		]);
		expect(options).toEqual([{ value: 'a.safetensors\nb.gguf', label: 'Same', count: 5 }]);
	});
});

describe('modelFilterLabel', () => {
	const options = buildModelOptions([{ name: 'a.safetensors', label: 'Pretty', count: 1 }]);

	it('resolves a facet value, a remembered pick, then the stem', () => {
		expect(modelFilterLabel('a.safetensors', options)).toBe('Pretty');
		expect(modelFilterLabel('x/other.safetensors', options, { 'x/other.safetensors': 'Other' })).toBe('Other');
		expect(modelFilterLabel('x/other.safetensors', options)).toBe('other');
		expect(modelFilterLabel('', options)).toBe('');
	});
});
