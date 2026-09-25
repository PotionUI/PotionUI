import { describe, expect, it } from 'vitest';
import type { DiscoveredLLMModel } from '$lib/types/llm';
import { filterModels, formatModelSize, modelMeta, splitMatch } from './llmModelSearch';

const MODELS: DiscoveredLLMModel[] = [
	{ id: 'qwen3:8b', size: 5_200_000_000, details: { parameter_size: '8.2B', quantization: 'Q4_K_M' } },
	{ id: 'Llama3.2:latest', details: { family: 'llama' } },
	{ id: 'gpt-4o', details: { owned_by: 'openai' } }
];

describe('filterModels', () => {
	it('returns everything for a blank query', () => {
		expect(filterModels(MODELS, '  ')).toHaveLength(3);
	});

	it('matches case-insensitively over the name', () => {
		expect(filterModels(MODELS, 'LLAMA').map((m) => m.id)).toEqual(['Llama3.2:latest']);
	});

	it('matches over details', () => {
		expect(filterModels(MODELS, 'q4_k').map((m) => m.id)).toEqual(['qwen3:8b']);
		expect(filterModels(MODELS, 'openai').map((m) => m.id)).toEqual(['gpt-4o']);
	});

	it('returns nothing when no model matches', () => {
		expect(filterModels(MODELS, 'mistral')).toEqual([]);
	});
});

describe('splitMatch', () => {
	it('splits around the first case-insensitive hit', () => {
		expect(splitMatch('Llama3.2:latest', 'LAMA')).toEqual([
			{ text: 'L', match: false },
			{ text: 'lama', match: true },
			{ text: '3.2:latest', match: false }
		]);
	});

	it('keeps the text whole without a hit', () => {
		expect(splitMatch('gpt-4o', 'qwen')).toEqual([{ text: 'gpt-4o', match: false }]);
	});
});

describe('model meta', () => {
	it('formats sizes', () => {
		expect(formatModelSize(5_200_000_000)).toBe('4.8 GB');
		expect(formatModelSize(0)).toBeNull();
		expect(formatModelSize(undefined)).toBeNull();
	});

	it('lists params, quantization and size in order', () => {
		expect(modelMeta(MODELS[0])).toEqual(['8.2B', 'Q4_K_M', '4.8 GB']);
		expect(modelMeta(MODELS[2])).toEqual(['openai']);
	});
});
