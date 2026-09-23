import { describe, it, expect } from 'vitest';
import type { AttributeDefinition } from '$lib/types/models';
import {
	DEFAULT_ATTRIBUTES_FILTERS,
	applyAttributesFilters,
	attributesFilterActiveCount,
	attributesFilterChips,
	attributesFiltersFromSearchParams,
	attributesFiltersToSearchParams,
	clearAllAttributesFilters,
	clearAttributesFilterChip,
	type AttributesFilters
} from './attributesFilters';

function definition(overrides: Partial<AttributeDefinition> = {}): AttributeDefinition {
	return {
		id: 'd1',
		key: 'strength',
		label: 'Strength',
		field_type: 'slider',
		model_types: [],
		config: {},
		default_value: null,
		per_user: false,
		admin_only: false,
		system: false,
		source: 'custom',
		...overrides
	};
}

describe('applyAttributesFilters', () => {
	const strength = definition({ id: '1', key: 'strength', label: 'Strength' });
	const triggerWords = definition({ id: '2', key: 'trigger_words', label: 'Trigger Words' });
	const baseModel = definition({ id: '3', key: 'base_model', label: 'Base Model' });
	const all = [strength, triggerWords, baseModel];

	it('passes everything through and sorts by key when filters are default', () => {
		expect(applyAttributesFilters(all, DEFAULT_ATTRIBUTES_FILTERS).map((d) => d.id)).toEqual(['3', '1', '2']);
	});

	it('matches the search against key and label', () => {
		expect(applyAttributesFilters(all, { ...DEFAULT_ATTRIBUTES_FILTERS, q: 'trigger' }).map((d) => d.id)).toEqual(['2']);
		expect(applyAttributesFilters(all, { ...DEFAULT_ATTRIBUTES_FILTERS, q: 'Base Model' }).map((d) => d.id)).toEqual(['3']);
	});

	it('sorts by label when sortBy is label', () => {
		expect(applyAttributesFilters(all, { ...DEFAULT_ATTRIBUTES_FILTERS, sortBy: 'label' }).map((d) => d.id)).toEqual([
			'3',
			'1',
			'2'
		]);
	});

	it('does not mutate the list it was given', () => {
		const source = [...all];
		applyAttributesFilters(source, { ...DEFAULT_ATTRIBUTES_FILTERS, sortBy: 'label' });
		expect(source.map((d) => d.id)).toEqual(all.map((d) => d.id));
	});
});

describe('attributesFiltersFromSearchParams / attributesFiltersToSearchParams', () => {
	it('round-trips a non-default filter set', () => {
		const filters: AttributesFilters = { q: 'trigger', sortBy: 'label' };
		const params = attributesFiltersToSearchParams(filters);
		expect(params.get('q')).toBe('trigger');
		expect(params.get('sort_by')).toBe('label');
		expect(attributesFiltersFromSearchParams(params)).toEqual(filters);
	});

	it('serializes the default filters to an empty query string', () => {
		expect(attributesFiltersToSearchParams(DEFAULT_ATTRIBUTES_FILTERS).toString()).toBe('');
	});

	it('falls back to defaults for an unknown sort_by', () => {
		const params = new URLSearchParams('sort_by=bogus');
		expect(attributesFiltersFromSearchParams(params)).toEqual(DEFAULT_ATTRIBUTES_FILTERS);
	});
});

describe('attributes filter chips', () => {
	it('has no popover fields to chip or clear beyond search and sort', () => {
		const filters: AttributesFilters = { q: 'keep me', sortBy: 'label' };
		expect(attributesFilterChips(filters)).toEqual([]);
		expect(attributesFilterActiveCount(filters)).toBe(0);
		expect(clearAttributesFilterChip(filters, 'nothing')).toBe(filters);
		expect(clearAllAttributesFilters(filters)).toEqual({ q: 'keep me', sortBy: 'label' });
	});
});
