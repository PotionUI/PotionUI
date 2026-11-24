import { describe, expect, it } from 'vitest';
import { hasPromptWeights, promptWeightsSupported } from './promptWeights';

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
