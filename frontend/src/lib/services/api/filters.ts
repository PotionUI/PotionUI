import type { AxiosInstance } from 'axios';
import type { FilterCatalogData, FilterDraft, FilterItem, FilterPatch } from '$lib/filters/types';

export function createFiltersApi(client: AxiosInstance) {
	return {
		async listFilters(): Promise<FilterCatalogData> {
			const response = await client.get<FilterCatalogData>('/api/filters');
			const data = response.data;
			return {
				schema: data.schema,
				filters: data.filters ?? [],
				ops: data.ops ?? [],
				groups: data.groups ?? [],
				load_errors: data.load_errors ?? {}
			};
		},

		async getFilterLut(url: string): Promise<string> {
			const response = await client.get(url, {
				responseType: 'text',
				transformResponse: (raw) => raw
			});
			return String(response.data);
		},

		async createMineFilter(draft: FilterDraft): Promise<FilterItem> {
			const response = await client.post<{ data: FilterItem }>('/api/filters/mine', draft);
			return response.data.data;
		},

		async updateMineFilter(filterId: string, patch: FilterPatch): Promise<FilterItem> {
			const response = await client.patch<{ data: FilterItem }>(
				`/api/filters/mine/${encodeURIComponent(filterId)}`,
				patch
			);
			return response.data.data;
		},

		async deleteMineFilter(filterId: string): Promise<void> {
			await client.delete(`/api/filters/mine/${encodeURIComponent(filterId)}`);
		}
	};
}
