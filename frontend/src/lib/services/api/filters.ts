import type { AxiosInstance } from 'axios';
import type { FilterCatalogData, FilterDraft, FilterItem, FilterPatch } from '$lib/filters/types';

function unwrap<T>(body: unknown): T {
	const wrapped = body as { data?: T } | null;
	return (wrapped && typeof wrapped === 'object' && 'data' in wrapped && wrapped.data !== undefined
		? wrapped.data
		: body) as T;
}

export function createFiltersApi(client: AxiosInstance) {
	return {
		async listFilters(): Promise<FilterCatalogData> {
			const response = await client.get('/api/filters');
			const data = unwrap<Partial<FilterCatalogData>>(response.data);
			return {
				filters: data.filters ?? [],
				ops: data.ops ?? [],
				groups: data.groups ?? []
			};
		},

		async getFilterLut(filterId: string): Promise<string> {
			const response = await client.get(`/api/filters/${encodeURIComponent(filterId)}/lut`, {
				responseType: 'text',
				transformResponse: (raw) => raw
			});
			return String(response.data);
		},

		async createMineFilter(draft: FilterDraft): Promise<FilterItem> {
			const response = await client.post('/api/filters/mine', draft);
			return unwrap<FilterItem>(response.data);
		},

		async updateMineFilter(filterId: string, patch: FilterPatch): Promise<FilterItem> {
			const response = await client.patch(`/api/filters/mine/${encodeURIComponent(filterId.replace(/^mine:/, ''))}`, patch);
			return unwrap<FilterItem>(response.data);
		},

		async deleteMineFilter(filterId: string): Promise<void> {
			await client.delete(`/api/filters/mine/${encodeURIComponent(filterId.replace(/^mine:/, ''))}`);
		}
	};
}
