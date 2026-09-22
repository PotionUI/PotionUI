import type { Prompt } from '$lib/types/segments';
import type { APIResponse } from '$lib/types/api';
import type { PromptListParams } from '$lib/services/api/prompts';
import { promptFiltersToListParams, type PromptFilters } from './promptFilters';
import { mergePromptSearchResults } from './promptSearchMerge';

export interface PromptLibraryApi {
	listPrompts(params: PromptListParams): Promise<APIResponse<{ items: Prompt[]; total: number }>>;
	searchPrompts(params: {
		q: string;
		limit?: number;
		model_id?: string;
		source_provider?: string;
	}): Promise<APIResponse<Prompt[]>>;
}

export interface PromptLibraryQueryOptions {
	collectionId?: string;
	limit: number;
	offset: number;
}

export interface PromptLibraryQueryResult {
	rows: Prompt[];
	total: number;
	semanticHits: number;
}

export async function queryPromptLibrary(
	apiLike: PromptLibraryApi,
	filters: PromptFilters,
	options: PromptLibraryQueryOptions
): Promise<PromptLibraryQueryResult> {
	const listParams = promptFiltersToListParams(filters, options);
	const query = filters.q.trim();
	if (query && options.offset === 0) {
		const [semanticResponse, plainResponse] = await Promise.all([
			apiLike.searchPrompts({
				q: query,
				limit: options.limit,
				model_id: filters.modelId || undefined,
				source_provider: listParams.source_provider
			}),
			apiLike.listPrompts({ ...listParams, q: query })
		]);
		const semanticHits = semanticResponse.success ? semanticResponse.data || [] : [];
		const plainResults = plainResponse.success ? plainResponse.data?.items || [] : [];
		const rows = mergePromptSearchResults(semanticHits, plainResults);
		return {
			rows,
			total: plainResponse.success ? plainResponse.data?.total ?? rows.length : rows.length,
			semanticHits: semanticHits.length
		};
	}
	const response = await apiLike.listPrompts({ ...listParams, q: query || undefined });
	return {
		rows: response.success ? response.data?.items || [] : [],
		total: response.success ? response.data?.total ?? 0 : 0,
		semanticHits: 0
	};
}
