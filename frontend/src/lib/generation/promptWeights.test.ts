import { describe, expect, it } from 'vitest';
import { hasPromptWeights, promptWeightsIgnoredWithPictures, promptWeightsSupported } from './promptWeights';

describe('hasPromptWeights', () => {
	it.each(['a (red:1.3) cat', '(word:1)', '(word: -0.5)', 'x (a b c:2.0) y'])('detects %s', (text) => {
		expect(hasPromptWeights(text)).toBe(true);
	});

	it.each(['', null, undefined, 'a plain cat', '(a cat)', 'ratio 16:9', '(word:abc)', 'note: (aside)'])(
		'ignores %s',
		(text) => {
			expect(hasPromptWeights(text)).toBe(false);
		}
	);
});

describe('promptWeightsSupported', () => {
	it('is false only when the preset declares false', () => {
		expect(promptWeightsSupported({ supports_prompt_weights: false })).toBe(false);
		expect(promptWeightsSupported({ supports_prompt_weights: true })).toBe(true);
		expect(promptWeightsSupported({})).toBe(true);
		expect(promptWeightsSupported(null)).toBe(true);
	});
});

describe('promptWeightsIgnoredWithPictures', () => {
	const vars = {
		prompt_weights_ignored_with_images: [{ mode: 'edit', fields: ['source_image', 'source_image_b'] }]
	};

	it('is true when a declared picture field has a value in a declared mode', () => {
		expect(promptWeightsIgnoredWithPictures(vars, 'edit', { source_image: { path: 'uploads/a.png' } })).toBe(true);
		expect(promptWeightsIgnoredWithPictures(vars, 'edit', { source_image_b: ['uploads/b.png'] })).toBe(true);
	});

	it('is false without a picture, in another mode, or without a declaration', () => {
		expect(promptWeightsIgnoredWithPictures(vars, 'edit', { source_image: null })).toBe(false);
		expect(promptWeightsIgnoredWithPictures(vars, 'edit', { source_image: [] })).toBe(false);
		expect(promptWeightsIgnoredWithPictures(vars, 'txt2img', { source_image: 'uploads/a.png' })).toBe(false);
		expect(promptWeightsIgnoredWithPictures({}, 'edit', { source_image: 'uploads/a.png' })).toBe(false);
		expect(promptWeightsIgnoredWithPictures(null, 'edit', null)).toBe(false);
	});
});
