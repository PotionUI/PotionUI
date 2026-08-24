import { describe, it, expect } from 'vitest';
import { modelDisplayName } from './modelDisplay';

describe('modelDisplayName', () => {
	it('prefers the API-computed name field', () => {
		expect(
			modelDisplayName({
				name: 'Detail Enhancer',
				custom_name: 'My Custom',
				providers: [{ name: 'Provider Name' }],
				filename: 'detail.safetensors'
			})
		).toBe('Detail Enhancer');
	});

	it('falls back to custom_name when name is absent', () => {
		expect(
			modelDisplayName({
				custom_name: 'My Custom',
				providers: [{ name: 'Provider Name' }],
				filename: 'detail.safetensors'
			})
		).toBe('My Custom');
	});

	it('falls back to the first provider name when name and custom_name are absent', () => {
		expect(
			modelDisplayName({
				providers: [{ name: 'Provider Name' }],
				filename: 'detail.safetensors'
			})
		).toBe('Provider Name');
	});

	it('falls back to filename as a last resort', () => {
		expect(modelDisplayName({ filename: 'style/foo.safetensors' })).toBe('style/foo.safetensors');
	});

	it('returns an empty string for null/undefined input', () => {
		expect(modelDisplayName(null)).toBe('');
		expect(modelDisplayName(undefined)).toBe('');
	});

	it('returns an empty string when nothing is available', () => {
		expect(modelDisplayName({})).toBe('');
	});
});
