import { describe, it, expect, vi } from 'vitest';
import type { Prompt } from '$lib/types/segments';
import { DEFAULT_PROMPT_FILTERS } from './promptFilters';
import { queryPromptLibrary } from './promptLibraryQuery';

function prompt(id: string): Prompt {
	return { id, display_name: id, segments: [], flattened_text: id };
}

function fakeApi(semantic: Prompt[], plain: Prompt[], total: number) {
	return {
		searchPrompts: vi.fn(async () => ({ success: true, data: semantic })),
		listPrompts: vi.fn(async () => ({ success: true, data: { items: plain, total } }))
	};
}

describe('queryPromptLibrary', () => {
	it('puts semantic hits first and dedupes the plain results behind them when a query starts a fresh page', async () => {
		const api = fakeApi([prompt('b'), prompt('c')], [prompt('a'), prompt('b')], 7);
		const result = await queryPromptLibrary(api, { ...DEFAULT_PROMPT_FILTERS, q: 'dancer' }, { limit: 48, offset: 0 });
		expect(result.rows.map((row) => row.id)).toEqual(['b', 'c', 'a']);
		expect(result.total).toBe(7);
		expect(result.semanticHits).toBe(2);
		expect(api.searchPrompts).toHaveBeenCalledWith({ q: 'dancer', limit: 48, model_id: undefined, source_provider: undefined });
		expect(api.listPrompts).toHaveBeenCalledWith(expect.objectContaining({ q: 'dancer', limit: 48, offset: 0 }));
	});

	it('pages with the plain list only, carrying the query and offset, and reports no semantic hits', async () => {
		const api = fakeApi([prompt('z')], [prompt('d'), prompt('e')], 7);
		const result = await queryPromptLibrary(api, { ...DEFAULT_PROMPT_FILTERS, q: 'dancer' }, { limit: 48, offset: 48 });
		expect(api.searchPrompts).not.toHaveBeenCalled();
		expect(api.listPrompts).toHaveBeenCalledWith(expect.objectContaining({ q: 'dancer', limit: 48, offset: 48 }));
		expect(result.rows.map((row) => row.id)).toEqual(['d', 'e']);
		expect(result.semanticHits).toBe(0);
		expect(result.total).toBe(7);
	});

	it('skips the semantic search without a query and passes the filters through as list params', async () => {
		const api = fakeApi([], [prompt('a')], 1);
		const result = await queryPromptLibrary(
			api,
			{ ...DEFAULT_PROMPT_FILTERS, source: 'civitai', showNsfw: true },
			{ collectionId: 'col-1', limit: 48, offset: 0 }
		);
		expect(api.searchPrompts).not.toHaveBeenCalled();
		expect(api.listPrompts).toHaveBeenCalledWith(
			expect.objectContaining({ q: undefined, collection_id: 'col-1', source_provider: 'civitai', nsfw: 'include' })
		);
		expect(result).toEqual({ rows: [prompt('a')], total: 1, semanticHits: 0 });
	});

	it('returns an empty page with a zero total when the list call fails', async () => {
		const api = {
			searchPrompts: vi.fn(async () => ({ success: false as const })),
			listPrompts: vi.fn(async () => ({ success: false as const, error: 'boom' }))
		};
		const result = await queryPromptLibrary(api, DEFAULT_PROMPT_FILTERS, { limit: 48, offset: 0 });
		expect(result).toEqual({ rows: [], total: 0, semanticHits: 0 });
	});
});
