import { describe, expect, it } from 'vitest';
import {
	modelSourceLabel,
	modelFilenameStem,
	modelSummaryParts,
	modelTagLabels,
	modelTypePresentation
} from './modelPresentation';

describe('modelPresentation', () => {
	it('turns internal model types into user-facing roles', () => {
		expect(modelTypePresentation('checkpoint')).toEqual({
			label: 'Base model',
			purpose: 'Main generation model'
		});
		expect(modelTypePresentation('lora').purpose).toBe('Style or concept adapter');
	});

	it('normalizes provider names and deduplicates tags', () => {
		const model = {
			model_type: 'lora',
			tags: [{ name: 'Portrait' }],
			providers: [{ provider: 'civitai-provider', tags: ['portrait', 'cinematic'] }]
		};
		expect(modelSourceLabel(model)).toBe('Civitai');
		expect(modelTagLabels(model)).toEqual(['Portrait', 'cinematic']);
		expect(modelSummaryParts(model)).toEqual(['LoRA', 'Civitai', 'Portrait', 'cinematic']);
	});

	it('shows the model filename without its path or extension', () => {
		expect(modelFilenameStem({ filename: 'models/checkpoints/potion-xl-v2.safetensors' })).toBe(
			'potion-xl-v2'
		);
	});
});
