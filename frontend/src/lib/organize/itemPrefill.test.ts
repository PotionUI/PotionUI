import { describe, expect, it } from 'vitest';
import { generationItemRule, modelItemRule, uploadItemRule } from './itemPrefill';

describe('generationItemRule', () => {
	it('uses the model, kind and size of the final output', () => {
		const rule = generationItemRule({
			form_data: { model: 'model:abc' },
			files: [
				{ file_type: 'image', is_final: false, width: 512, height: 512 },
				{ file_type: 'image', is_final: true, width: 1344, height: 768 }
			]
		});
		expect(rule.subject).toBe('generation');
		expect(rule.conditions).toEqual([
			{ fact: 'model', operator: 'is', value: 'abc' },
			{ fact: 'media_kind', operator: 'is', value: 'image' },
			{ fact: 'resolution', operator: 'is', value: { width: 1344, height: 768 } }
		]);
	});

	it('skips what is unknown', () => {
		expect(generationItemRule({ form_data: {}, files: [] }).conditions).toEqual([]);
		expect(generationItemRule({ files: [{ file_type: 'weird', is_final: true }] }).conditions).toEqual([]);
	});
});

describe('uploadItemRule', () => {
	it('uses kind and size', () => {
		expect(uploadItemRule({ media_type: 'video', width: 1920, height: 1080 }).conditions).toEqual([
			{ fact: 'media_kind', operator: 'is', value: 'video' },
			{ fact: 'resolution', operator: 'is', value: { width: 1920, height: 1080 } }
		]);
	});
});

describe('modelItemRule', () => {
	it('uses type and base model when present', () => {
		expect(modelItemRule({ model_type: 'lora', base_model: 'sdxl' })).toEqual({
			subject: 'model',
			conditions: [
				{ fact: 'model_type', operator: 'is', value: 'lora' },
				{ fact: 'base_model', operator: 'is', value: 'sdxl' }
			]
		});
		expect(modelItemRule({ model_type: 'checkpoint' }).conditions).toHaveLength(1);
	});
});
