import { describe, expect, it } from 'vitest';
import {
	addSelectOption,
	buildAttributeDefinitionPayload,
	draftFromDefinition,
	emptyAttributeDraft,
	removeSelectOption,
	resetDraftForFieldType,
	slugifyAttributeKey
} from './attributeDefinitionForm';
import type { AttributeDefinition } from '$lib/types/models';

describe('slugifyAttributeKey', () => {
	it('lowercases and joins words with underscores', () => {
		expect(slugifyAttributeKey('Default Strength')).toBe('default_strength');
	});

	it('collapses runs of non-alphanumeric characters into one underscore', () => {
		expect(slugifyAttributeKey('  Weird!!  Label--Name  ')).toBe('weird_label_name');
	});

	it('never leaves a leading or trailing underscore', () => {
		expect(slugifyAttributeKey('-leading and trailing-')).toBe('leading_and_trailing');
	});

	it('returns an empty string for an empty label', () => {
		expect(slugifyAttributeKey('   ')).toBe('');
	});
});

describe('addSelectOption / removeSelectOption', () => {
	it('appends a blank option', () => {
		expect(addSelectOption([{ value: 'a', label: 'A' }])).toEqual([
			{ value: 'a', label: 'A' },
			{ value: '', label: '' }
		]);
	});

	it('removes only the targeted index', () => {
		const options = [
			{ value: 'a', label: 'A' },
			{ value: 'b', label: 'B' },
			{ value: 'c', label: 'C' }
		];
		expect(removeSelectOption(options, 1)).toEqual([
			{ value: 'a', label: 'A' },
			{ value: 'c', label: 'C' }
		]);
	});
});

describe('resetDraftForFieldType', () => {
	it('clears select options when switching away from select', () => {
		const draft = { ...emptyAttributeDraft(), field_type: 'select' as const, config: { options: [{ value: 'a', label: 'A' }] } };
		const next = resetDraftForFieldType(draft, 'checkbox');
		expect(next.field_type).toBe('checkbox');
		expect(next.config.options).toEqual([]);
		expect(next.default_value).toBe(false);
	});

	it('seeds one blank option when switching to select', () => {
		const next = resetDraftForFieldType(emptyAttributeDraft(), 'select');
		expect(next.config.options).toEqual([{ value: '', label: '' }]);
		expect(next.default_value).toBe('');
	});

	it('resets tags default value to an empty array', () => {
		const next = resetDraftForFieldType(emptyAttributeDraft(), 'tags');
		expect(next.default_value).toEqual([]);
	});
});

describe('draftFromDefinition', () => {
	it('round-trips a slider definition into edit-buffer shape', () => {
		const definition: AttributeDefinition = {
			id: 'd1',
			key: 'strength',
			label: 'Strength',
			field_type: 'slider',
			model_types: ['lora'],
			config: { min: 0, max: 2, step: 0.05 },
			default_value: 1,
			description: 'desc',
			per_user: false,
			admin_only: false,
			system: true,
			source: 'core'
		};
		const draft = draftFromDefinition(definition);
		expect(draft.default_value).toBe('1');
		expect(draft.config).toEqual({ min: 0, max: 2, step: 0.05, options: [] });
		expect(draft.model_types).toEqual(['lora']);
	});

	it('coerces a checkbox default to a real boolean', () => {
		const definition: AttributeDefinition = {
			id: 'd2',
			key: 'nsfw',
			label: 'NSFW',
			field_type: 'checkbox',
			model_types: [],
			config: {},
			default_value: 0,
			per_user: false,
			admin_only: false,
			system: false,
			source: 'local'
		};
		expect(draftFromDefinition(definition).default_value).toBe(false);
	});
});

describe('buildAttributeDefinitionPayload', () => {
	it('strips slider config down to declared numbers only', () => {
		const draft = { ...emptyAttributeDraft(), key: 'x', label: 'X', field_type: 'slider' as const, config: { min: 0, max: NaN, options: [] } };
		expect(buildAttributeDefinitionPayload(draft).config).toEqual({ min: 0 });
	});

	it('drops blank select options and keeps only value/label pairs with a value', () => {
		const draft = {
			...emptyAttributeDraft(),
			key: 'x',
			label: 'X',
			field_type: 'select' as const,
			config: { options: [{ value: 'a', label: 'A' }, { value: '', label: 'Blank' }] }
		};
		expect(buildAttributeDefinitionPayload(draft).config).toEqual({
			options: [{ value: 'a', label: 'A' }]
		});
	});

	it('keeps min/max/step config for a range definition, same as slider/number', () => {
		const draft = { ...emptyAttributeDraft(), key: 'x', label: 'X', field_type: 'range' as const, config: { min: -2, max: 2, step: 0.05, options: [] } };
		expect(buildAttributeDefinitionPayload(draft).config).toEqual({ min: -2, max: 2, step: 0.05 });
	});

	it('never carries slider config onto a text definition', () => {
		const draft = { ...emptyAttributeDraft(), key: 'x', label: 'X', field_type: 'text' as const, config: { min: 1, max: 2, step: 1, options: [] } };
		expect(buildAttributeDefinitionPayload(draft).config).toEqual({});
	});

	it('trims key/label/description and drops an empty description', () => {
		const draft = { ...emptyAttributeDraft(), key: ' my_key ', label: ' My Label ', description: '   ' };
		const payload = buildAttributeDefinitionPayload(draft);
		expect(payload.key).toBe('my_key');
		expect(payload.label).toBe('My Label');
		expect(payload.description).toBeUndefined();
	});

	it('coerces the default value back to the field type wire shape', () => {
		expect(buildAttributeDefinitionPayload({ ...emptyAttributeDraft(), key: 'x', label: 'X', field_type: 'checkbox', default_value: true }).default_value).toBe(true);
		expect(buildAttributeDefinitionPayload({ ...emptyAttributeDraft(), key: 'x', label: 'X', field_type: 'tags', default_value: ['a', 'b'] }).default_value).toEqual(['a', 'b']);
		expect(buildAttributeDefinitionPayload({ ...emptyAttributeDraft(), key: 'x', label: 'X', field_type: 'number', default_value: '3.5' }).default_value).toBe(3.5);
		expect(buildAttributeDefinitionPayload({ ...emptyAttributeDraft(), key: 'x', label: 'X', field_type: 'range', default_value: '0.8' }).default_value).toBe(0.8);
	});

	it('leaves a range definition with no entered default unset rather than falling back to 0', () => {
		// A slider's blank default becoming 0 is harmless; a range's would make
		// every model look like it recommends a band of 0.
		expect(
			buildAttributeDefinitionPayload({ ...emptyAttributeDraft(), key: 'x', label: 'X', field_type: 'range', default_value: '' }).default_value
		).toBeNull();
		expect(
			buildAttributeDefinitionPayload({ ...emptyAttributeDraft(), key: 'x', label: 'X', field_type: 'slider', default_value: '' }).default_value
		).toBe(0);
	});
});
