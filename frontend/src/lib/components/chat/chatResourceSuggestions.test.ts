import { describe, it, expect } from 'vitest';
import type { ResourceSuggestion } from '$lib/types/chat';
import {
	mapSuggestions,
	isEmptyFormValue,
	formValuePreview,
	buildFormSuggestions,
	buildLoraRowSuggestions,
	FORM_ICON
} from './chatResourceSuggestions';

describe('mapSuggestions', () => {
	it('splits has_children suggestions into categories and the rest into values', () => {
		const suggestions: ResourceSuggestion[] = [
			{ uri: 'ns', label: 'Namespace', kind: 'ns', has_children: true },
			{ uri: 'leaf.one', label: 'Leaf One', kind: 'leaf', has_children: false }
		];
		const { child_categories, values } = mapSuggestions(suggestions);
		expect(child_categories).toHaveLength(1);
		expect(child_categories[0]).toMatchObject({ id: 'ns', name: 'Namespace', path: 'ns', attachable: false });
		expect(values).toHaveLength(1);
		expect(values[0]).toMatchObject({ id: 'leaf.one', label: 'Leaf One', value: 'leaf.one' });
	});

	it('carries a category-level attachable flag through', () => {
		const { child_categories } = mapSuggestions([
			{ uri: 'ns', label: 'Namespace', kind: 'ns', has_children: true, attachable: true }
		]);
		expect(child_categories[0].attachable).toBe(true);
	});

	it('derives category_path from the uri up to the last dot, or the whole uri if there is none', () => {
		const { values } = mapSuggestions([
			{ uri: 'a.b.c', label: 'C', kind: 'leaf', has_children: false },
			{ uri: 'root', label: 'Root', kind: 'leaf', has_children: false }
		]);
		expect(values[0].category_path).toBe('a.b');
		expect(values[1].category_path).toBe('root');
	});

	it('defaults a missing description to an empty string on categories', () => {
		const { child_categories } = mapSuggestions([{ uri: 'ns', label: 'N', kind: 'ns', has_children: true }]);
		expect(child_categories[0].description).toBe('');
	});
});

describe('isEmptyFormValue', () => {
	it('treats null and undefined as empty', () => {
		expect(isEmptyFormValue(null)).toBe(true);
		expect(isEmptyFormValue(undefined)).toBe(true);
	});

	it('treats a whitespace-only string as empty', () => {
		expect(isEmptyFormValue('   ')).toBe(true);
		expect(isEmptyFormValue('x')).toBe(false);
	});

	it('treats an empty array as empty, a non-empty array as not', () => {
		expect(isEmptyFormValue([])).toBe(true);
		expect(isEmptyFormValue([1])).toBe(false);
	});

	it('treats an empty plain object as empty', () => {
		expect(isEmptyFormValue({})).toBe(true);
		expect(isEmptyFormValue({ a: 1 })).toBe(false);
	});

	it('treats numbers and booleans as never empty, including 0/false', () => {
		expect(isEmptyFormValue(0)).toBe(false);
		expect(isEmptyFormValue(false)).toBe(false);
	});
});

describe('formValuePreview', () => {
	it('collapses any string starting with "model:" to the literal "model"', () => {
		expect(formValuePreview('model:abc123')).toBe('model');
	});

	it('truncates a long string at 40 chars with an ellipsis', () => {
		const long = 'x'.repeat(50);
		expect(formValuePreview(long)).toBe('x'.repeat(40) + '…');
	});

	it('leaves a short string untouched', () => {
		expect(formValuePreview('short')).toBe('short');
	});

	it('stringifies numbers and booleans', () => {
		expect(formValuePreview(42)).toBe('42');
		expect(formValuePreview(true)).toBe('true');
	});

	it('summarizes an array by item count, pluralizing correctly', () => {
		expect(formValuePreview([1])).toBe('1 item');
		expect(formValuePreview([1, 2])).toBe('2 items');
	});

	it('returns undefined for a plain object', () => {
		expect(formValuePreview({ a: 1 })).toBeUndefined();
	});
});

describe('buildLoraRowSuggestions', () => {
	const loraSelections = {
		char_lora: [
			{ id: 'l1', name: 'Anime Style', strength: 0.8 },
			{ id: null, name: 'Detail Boost', strength: 0.5 }
		]
	};

	it('returns nothing for a field with no selections', () => {
		expect(buildLoraRowSuggestions('missing', '', loraSelections)).toEqual([]);
	});

	it('offers an "attach all" row plus one row per selected LoRA when partial is empty', () => {
		const out = buildLoraRowSuggestions('char_lora', '', loraSelections);
		expect(out).toHaveLength(3);
		expect(out[0]).toMatchObject({ uri: 'form.char_lora', kind: 'lora_picker', attachable: true, has_children: true });
		expect(out[1]).toMatchObject({ uri: 'form.char_lora.l1', label: 'Anime Style @ 0.8', kind: 'lora' });
		// A null `id` falls back to its array index in the uri.
		expect(out[2]).toMatchObject({ uri: 'form.char_lora.1', label: 'Detail Boost @ 0.5' });
	});

	it('omits the "attach all" row and filters rows by name substring once a partial is typed', () => {
		const out = buildLoraRowSuggestions('char_lora', 'detail', loraSelections);
		expect(out).toHaveLength(1);
		expect(out[0]).toMatchObject({ label: 'Detail Boost @ 0.5' });
	});

	it('caps output at 30 rows', () => {
		const many = { field: Array.from({ length: 40 }, (_, i) => ({ id: String(i), name: `L${i}`, strength: 1 })) };
		expect(buildLoraRowSuggestions('field', 'l', many)).toHaveLength(30);
	});
});

describe('buildFormSuggestions', () => {
	it('offers "All form values" when the query is empty', () => {
		const out = buildFormSuggestions('', {}, {});
		expect(out[0]).toMatchObject({ uri: 'form', kind: 'form', icon: FORM_ICON });
	});

	it('skips empty form fields', () => {
		const out = buildFormSuggestions('', { prompt: 'a cat', negative: '' }, {});
		expect(out.map((s) => s.uri)).toContain('form.prompt');
		expect(out.map((s) => s.uri)).not.toContain('form.negative');
	});

	it('filters fields by name prefix once a query is typed', () => {
		const out = buildFormSuggestions('pro', { prompt: 'a cat', seed: '123' }, {});
		expect(out.map((s) => s.uri)).toEqual(['form.prompt']);
	});

	it('a field with active LoRA selections becomes a browsable lora_picker row instead of a form_value row', () => {
		const loraSelections = { char_lora: [{ id: 'l1', name: 'X', strength: 1 }] };
		const out = buildFormSuggestions('', { char_lora: 'unused-placeholder' }, loraSelections);
		const row = out.find((s) => s.uri === 'form.char_lora');
		expect(row).toMatchObject({ kind: 'lora_picker', has_children: true, attachable: true });
	});

	it('delegates to buildLoraRowSuggestions when the partial contains a dot (browsing into a field)', () => {
		const loraSelections = { char_lora: [{ id: 'l1', name: 'Anime Style', strength: 0.8 }] };
		const out = buildFormSuggestions('char_lora.anime', {}, loraSelections);
		expect(out).toEqual([
			expect.objectContaining({ uri: 'form.char_lora.l1', label: 'Anime Style @ 0.8' })
		]);
	});

	it('caps output at 30 rows', () => {
		const formData: Record<string, string> = {};
		for (let i = 0; i < 40; i++) formData[`field${i}`] = 'x';
		expect(buildFormSuggestions('', formData, {})).toHaveLength(30);
	});
});
