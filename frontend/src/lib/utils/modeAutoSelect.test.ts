import { describe, it, expect } from 'vitest';
import { resolveDefaultModeSelection } from './modeAutoSelect';

describe('resolveDefaultModeSelection', () => {
	it('returns null when the preset has no modes', () => {
		expect(resolveDefaultModeSelection([], 'txt2img')).toBeNull();
	});

	it('picks the declared default_mode when present', () => {
		const result = resolveDefaultModeSelection(
			[{ name: 'txt2img' }, { name: 'img2img' }],
			'img2img'
		);
		expect(result?.mode).toBe('img2img');
	});

	it('falls back to the first mode when default_mode is missing', () => {
		const result = resolveDefaultModeSelection([{ name: 'txt2img' }, { name: 'img2img' }], null);
		expect(result?.mode).toBe('txt2img');
	});

	it('falls back to the first mode when default_mode no longer exists on the preset', () => {
		const result = resolveDefaultModeSelection([{ name: 'txt2img' }, { name: 'img2img' }], 'removed-mode');
		expect(result?.mode).toBe('txt2img');
	});

	it('resolves the default variant for the chosen mode', () => {
		const result = resolveDefaultModeSelection(
			[
				{
					name: 'txt2img',
					variants: [
						{ name: 'fast', label: 'Fast', order: 0, default: false },
						{ name: 'quality', label: 'Quality', order: 1, default: true }
					]
				}
			],
			'txt2img'
		);
		expect(result?.mode).toBe('txt2img');
		expect(result?.variant).toBe('quality');
	});

	it('returns a null variant for a mode with no variants', () => {
		const result = resolveDefaultModeSelection([{ name: 'txt2img' }], 'txt2img');
		expect(result?.variant).toBeNull();
	});
});
