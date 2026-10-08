import { describe, it, expect } from 'vitest';
import { get } from 'svelte/store';
import {
	clearMissingModel,
	missingModelByTab,
	missingModelFor,
	missingRequiredModelField,
	publishMissingModel
} from './requiredModelField';
import type { FieldConfig } from '$lib/form/reactions';

function modelField(overrides: Partial<FieldConfig> = {}): FieldConfig {
	return { type: 'model', name: 'model', title: 'Model', required: true, ...overrides } as FieldConfig;
}

describe('missingRequiredModelField', () => {
	it('blocks when a required model field is empty or absent', () => {
		expect(missingRequiredModelField([modelField()], { model: '' })).toEqual({ name: 'model', label: 'Model' });
		expect(missingRequiredModelField([modelField()], {})).toEqual({ name: 'model', label: 'Model' });
	});

	it('blocks when the field holds an empty {modelPath} component value', () => {
		expect(missingRequiredModelField([modelField()], { model: { modelPath: '', tagFilters: [] } })).toEqual({
			name: 'model',
			label: 'Model'
		});
	});

	it('passes when the field holds a model ref or a component value with a path', () => {
		expect(missingRequiredModelField([modelField()], { model: 'model:abc' })).toBeNull();
		expect(missingRequiredModelField([modelField()], { model: { modelPath: 'model:abc' } })).toBeNull();
	});

	it('ignores optional, locked, hidden and non-model fields', () => {
		expect(missingRequiredModelField([modelField({ required: false })], { model: '' })).toBeNull();
		expect(missingRequiredModelField([modelField({ readonly: true })], { model: '' })).toBeNull();
		expect(missingRequiredModelField([modelField({ visible: false })], { model: '' })).toBeNull();
		expect(
			missingRequiredModelField([{ type: 'string', name: 'prompt', required: true } as FieldConfig], { prompt: '' })
		).toBeNull();
	});

	it('does not look inside a hidden container', () => {
		const section = { type: 'section', name: 's', visible: false, children: [modelField()] } as FieldConfig;
		expect(missingRequiredModelField([section], {})).toBeNull();
	});

	it('finds a required model field nested in sections and names it by title or name', () => {
		const section = { type: 'section', name: 's', children: [modelField({ title: undefined })] } as FieldConfig;
		expect(missingRequiredModelField([section], {})).toEqual({ name: 'model', label: 'model' });
	});
});

describe('published missing model', () => {
	it('is only trusted for the schema key it was published for', () => {
		publishMissingModel('t', 'a-txt2img-', { name: 'model', label: 'Model' });
		expect(missingModelFor(get(missingModelByTab), 't', 'a-txt2img-')).toEqual({ name: 'model', label: 'Model' });
		expect(missingModelFor(get(missingModelByTab), 't', 'b-txt2img-')).toBeNull();
		clearMissingModel('t');
		expect(missingModelFor(get(missingModelByTab), 't', 'a-txt2img-')).toBeNull();
	});
});
