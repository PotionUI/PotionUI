import { describe, it, expect } from 'vitest';
import {
	DEFAULT_LLM_CONFIG_FILTERS,
	applyLLMConfigFilters,
	clearAllLLMConfigFilters,
	clearLLMConfigFilterChip,
	llmConfigFilterActiveCount,
	llmConfigFilterChips,
	llmConfigFiltersFromSearchParams,
	llmConfigFiltersToSearchParams,
	type LLMConfigFilters
} from './llmConfigFilters';

function config(overrides: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		id: 'c1',
		name: 'Local Ollama',
		model: 'llama3',
		created_at: '2026-09-01T00:00:00.000Z',
		...overrides
	};
}

describe('applyLLMConfigFilters', () => {
	const ollama = config({ id: '1', name: 'Local Ollama', model: 'llama3', created_at: '2026-09-01T00:00:00.000Z' });
	const openai = config({ id: '2', name: 'OpenAI GPT', model: 'gpt-4o', created_at: '2026-09-03T00:00:00.000Z' });
	const native = config({ id: '3', name: 'Native Qwen', model: 'qwen-image', created_at: '2026-09-02T00:00:00.000Z' });
	const all = [ollama, openai, native];

	it('passes everything through and sorts by name when filters are default', () => {
		expect(applyLLMConfigFilters(all, DEFAULT_LLM_CONFIG_FILTERS).map((c) => c.id)).toEqual(['1', '3', '2']);
	});

	it('matches the search against name and model', () => {
		expect(applyLLMConfigFilters(all, { ...DEFAULT_LLM_CONFIG_FILTERS, q: 'gpt' }).map((c) => c.id)).toEqual(['2']);
		expect(applyLLMConfigFilters(all, { ...DEFAULT_LLM_CONFIG_FILTERS, q: 'qwen-image' }).map((c) => c.id)).toEqual(['3']);
	});

	it('sorts by recently added when sortBy is created', () => {
		expect(applyLLMConfigFilters(all, { ...DEFAULT_LLM_CONFIG_FILTERS, sortBy: 'created' }).map((c) => c.id)).toEqual(['2', '3', '1']);
	});

	it('does not mutate the list it was given', () => {
		const source = [...all];
		applyLLMConfigFilters(source, { ...DEFAULT_LLM_CONFIG_FILTERS, sortBy: 'created' });
		expect(source.map((c) => c.id)).toEqual(all.map((c) => c.id));
	});
});

describe('llmConfigFiltersFromSearchParams / llmConfigFiltersToSearchParams', () => {
	it('round-trips a non-default filter set', () => {
		const filters: LLMConfigFilters = { q: 'gpt', sortBy: 'created' };
		const params = llmConfigFiltersToSearchParams(filters);
		expect(params.get('q')).toBe('gpt');
		expect(params.get('sort_by')).toBe('created');
		expect(llmConfigFiltersFromSearchParams(params)).toEqual(filters);
	});

	it('serializes the default filters to an empty query string', () => {
		expect(llmConfigFiltersToSearchParams(DEFAULT_LLM_CONFIG_FILTERS).toString()).toBe('');
	});

	it('falls back to defaults for an unknown sort_by', () => {
		const params = new URLSearchParams('sort_by=bogus');
		expect(llmConfigFiltersFromSearchParams(params)).toEqual(DEFAULT_LLM_CONFIG_FILTERS);
	});
});

describe('llm config filter chips', () => {
	it('emits no chips and zero active count for the default filters', () => {
		expect(llmConfigFilterChips(DEFAULT_LLM_CONFIG_FILTERS)).toEqual([]);
		expect(llmConfigFilterActiveCount(DEFAULT_LLM_CONFIG_FILTERS)).toBe(0);
	});

	it('has no filter fields to clear beyond search and sort', () => {
		const filters: LLMConfigFilters = { q: 'keep me', sortBy: 'created' };
		expect(clearLLMConfigFilterChip(filters, 'nothing')).toBe(filters);
		expect(clearAllLLMConfigFilters(filters)).toEqual({ q: 'keep me', sortBy: 'created' });
	});
});
