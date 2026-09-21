import { describe, it, expect } from 'vitest';
import { missingRequiredModelField } from './requiredModelField';
import type { FieldConfig } from '$lib/form/reactions';

function modelField(overrides: Partial<FieldConfig> = {}): FieldConfig {
	return {
		type: 'model',
		name: 'model',
		title: 'Model',
		required: true,
		...overrides
	} as FieldConfig;
}

describe('missingRequiredModelField', () => {
	it('blocks when a required model field is an empty string', () => {
		const result = missingRequiredModelField([modelField()], { model: '' });
		expect(result).toEqual({ name: 'model', label: 'Model' });
	});

	it('blocks when a required model field holds an empty {modelPath} component value', () => {
		const result = missingRequiredModelField([modelField()], { model: { modelPath: '', tagFilters: [] } });
		expect(result).toEqual({ name: 'model', label: 'Model' });
	});

	it('passes when the field holds a model ref', () => {
		const result = missingRequiredModelField([modelField()], { model: 'model:abc' });
		expect(result).toBeNull();
	});

	it('passes when the field holds a component value with a modelPath', () => {
		const result = missingRequiredModelField([modelField()], {
			model: { modelPath: 'model:abc', tagFilters: [] }
		});
		expect(result).toBeNull();
	});

	it('passes when the model field is not required', () => {
		const result = missingRequiredModelField([modelField({ required: false })], { model: '' });
		expect(result).toBeNull();
	});

	it('passes when a required, locked (readonly) model field is empty', () => {
		const result = missingRequiredModelField([modelField({ readonly: true })], { model: '' });
		expect(result).toBeNull();
	});

	it('ignores required fields that are not model fields', () => {
		const result = missingRequiredModelField(
			[{ type: 'string', name: 'prompt', title: 'Prompt', required: true } as FieldConfig],
			{ prompt: '' }
		);
		expect(result).toBeNull();
	});

	it('finds a required model field nested inside sections/tabs', () => {
		const nested = modelField({ name: 'base_model', title: 'Base Model' });
		const fields: FieldConfig[] = [
			{ type: 'tab', name: 'tab_1', children: [{ type: 'section', name: 'section_1', children: [nested] } as FieldConfig] } as FieldConfig
		];
		const result = missingRequiredModelField(fields, { base_model: '' });
		expect(result).toEqual({ name: 'base_model', label: 'Base Model' });
	});

	it('falls back to the field name when it has no title', () => {
		const result = missingRequiredModelField([modelField({ title: undefined })], { model: '' });
		expect(result).toEqual({ name: 'model', label: 'model' });
	});
});
