import { describe, expect, it } from 'vitest';
import { isGeneratePageContext, isGenerationPresetContext } from './activeFormContext';

describe('isGeneratePageContext', () => {
	it('is true for the generation mode (the Generate page + home route)', () => {
		expect(isGeneratePageContext('generation')).toBe(true);
	});

	it('is false for any other page mode', () => {
		expect(isGeneratePageContext('history')).toBe(false);
		expect(isGeneratePageContext('lora-dataset')).toBe(false);
	});

	it('is false when the page mode has not resolved yet', () => {
		expect(isGeneratePageContext(null)).toBe(false);
	});
});

describe('isGenerationPresetContext', () => {
	it('is true for a generation-mode session', () => {
		expect(isGenerationPresetContext('generation')).toBe(true);
	});

	it('is false for a plugin-mode session', () => {
		expect(isGenerationPresetContext('lora-dataset')).toBe(false);
	});
});
