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

describe('missingRequiredModelField with values not yet checked', () => {
	it('names a filled slot while its value is still being checked', () => {
		expect(missingRequiredModelField([modelField()], { model: 'model:abc' }, new Set(['model']))).toEqual({
			name: 'model',
			label: 'Model'
		});
	});

	it('does not name a filled slot once it is checked', () => {
		expect(missingRequiredModelField([modelField()], { model: 'model:abc' }, new Set())).toBeNull();
		expect(missingRequiredModelField([modelField()], { model: 'model:abc' })).toBeNull();
	});

	it('names the slot inside a nested group', () => {
		const group = { type: 'group', children: [modelField()] } as unknown as FieldConfig;
		expect(missingRequiredModelField([group], { model: 'model:abc' }, new Set(['model']))?.name).toBe('model');
	});
});

describe('missingRequiredModelField for required tags fields', () => {
	const tagsField = { type: 'tags', name: 'style', title: 'Style tags', required: true } as FieldConfig;

	it('blocks while a required tags field is empty in any value shape', () => {
		const blocked = { name: 'style', label: 'Style tags' };
		expect(missingRequiredModelField([tagsField], {})).toEqual(blocked);
		expect(missingRequiredModelField([tagsField], { style: '' })).toEqual(blocked);
		expect(missingRequiredModelField([tagsField], { style: '  ' })).toEqual(blocked);
		expect(missingRequiredModelField([tagsField], { style: { genre: [], mood: [] } })).toEqual(blocked);
	});

	it('passes once a tag is picked and ignores optional tags fields', () => {
		expect(missingRequiredModelField([tagsField], { style: 'pop, female' })).toBeNull();
		expect(missingRequiredModelField([tagsField], { style: { genre: ['pop'] } })).toBeNull();
		expect(missingRequiredModelField([{ ...tagsField, required: false }], { style: '' })).toBeNull();
	});
});

describe('missingRequiredModelField tags exemptions', () => {
	const tagsField = { type: 'tags', name: 'style', title: 'Style tags', required: true } as FieldConfig;

	it('ignores hidden and locked tags fields', () => {
		expect(missingRequiredModelField([{ ...tagsField, visible: false }], {})).toBeNull();
		expect(missingRequiredModelField([{ ...tagsField, readonly: true }], {})).toBeNull();
	});

	it('reports a required tags field nested in a section', () => {
		const section = { type: 'section', name: 's', children: [tagsField] } as FieldConfig;
		expect(missingRequiredModelField([section], {})).toEqual({ name: 'style', label: 'Style tags' });
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
