import type { AxiosInstance } from 'axios';
import type { APIResponse, DocTree, DocContent } from '$lib/types/api';

export function createDocsApi(client: AxiosInstance) {
	return {
		async getDocsTree(): Promise<APIResponse<DocTree>> {
			const response = await client.get('/api/docs/tree');
			return response.data;
		},

		async getDocContent(id: string): Promise<APIResponse<DocContent>> {
			const response = await client.get('/api/docs/content', { params: { id } });
			return response.data;
		},

		// Live reference data - shapes are loosely typed per the API contract;
		// consumers (docs/components/live/*) render defensively.
		async getHooksCatalog(): Promise<APIResponse<any>> {
			const response = await client.get('/api/plugins/hooks/catalog');
			return response.data;
		},

		async getDocsFieldTypes(): Promise<APIResponse<any>> {
			const response = await client.get('/api/fields/types');
			return response.data;
		},

		async getDocsLivePipes(): Promise<APIResponse<any>> {
			const response = await client.get('/api/docs/live/pipes');
			return response.data;
		},

		async getDocsLiveOutputTypes(): Promise<APIResponse<any>> {
			const response = await client.get('/api/docs/live/output-types');
			return response.data;
		}
	};
}
