import type { AxiosInstance } from 'axios';
import type {
	APIResponse,
	PhrasebookCategory,
	PhrasebookSearchResult,
	PhrasebookFindParams,
	PhrasebookFindResult,
	PhrasebookBatchOp,
	PhrasebookBatchOutcome,
	PhrasebookBatchPreview,
	PhrasebookStateFilter,
	PhrasebookValue,
	GeneratePreviewRequest,
	GeneratePreviewResult
} from '$lib/types/api';

export function createPhrasebookApi(client: AxiosInstance) {
	return {
		async searchPhrasebook(
			path: string,
			limit: number = 50,
			state: PhrasebookStateFilter = 'active'
		): Promise<APIResponse<PhrasebookSearchResult>> {
			const params = new URLSearchParams({
				path: path,
				limit: limit.toString(),
				state: state
			});
			const response = await client.get(`/api/phrasebook/search?${params}`);
			return response.data;
		},

		async findPhrasebook(params: PhrasebookFindParams): Promise<APIResponse<PhrasebookFindResult>> {
			const query = new URLSearchParams({
				q: params.q,
				mode: params.mode,
				case_sensitive: params.case_sensitive ? 'true' : 'false',
				scope: params.scope,
				include_inactive: params.include_inactive ? 'true' : 'false',
				path_prefix: params.path_prefix,
				fields: params.fields.join(',')
			});
			if (params.limit !== undefined) query.set('limit', String(params.limit));
			const response = await client.get(`/api/phrasebook/find?${query}`);
			return response.data;
		},

		async listPhrasebookBatchOps(): Promise<APIResponse<PhrasebookBatchOp[]>> {
			const response = await client.get('/api/phrasebook/batch-ops');
			return response.data;
		},

		async runPhrasebookBatch(
			op: string,
			valueIds: string[],
			params: Record<string, unknown> = {}
		): Promise<APIResponse<PhrasebookBatchOutcome>> {
			const response = await client.post('/api/phrasebook/values/batch', {
				op,
				value_ids: valueIds,
				params
			});
			return response.data;
		},

		async previewPhrasebookBatch(
			op: string,
			valueIds: string[],
			params: Record<string, unknown> = {}
		): Promise<APIResponse<PhrasebookBatchPreview>> {
			const response = await client.post('/api/phrasebook/values/batch/preview', {
				op,
				value_ids: valueIds,
				params
			});
			return response.data;
		},

		async getPhrasebookCategories(
			rootOnly: boolean = false,
			state: PhrasebookStateFilter = 'all'
		): Promise<APIResponse<{ categories: PhrasebookCategory[] }>> {
			const params = new URLSearchParams();
			if (rootOnly) params.append('root_only', 'true');
			if (state !== 'all') params.append('state', state);
			const queryString = params.toString();
			const response = await client.get(
				`/api/phrasebook/categories${queryString ? `?${queryString}` : ''}`
			);
			return response.data;
		},

		async getCategoryChildren(
			categoryId: string
		): Promise<APIResponse<{ categories: any[] }>> {
			const response = await client.get(
				`/api/phrasebook/categories/${categoryId}/children`
			);
			return response.data;
		},

		async getPhrasebookCategory(
			categoryId: string
		): Promise<APIResponse<{ category: any; values: any[] }>> {
			const response = await client.get(`/api/phrasebook/categories/${categoryId}`);
			return response.data;
		},

		async createPhrasebookCategory(category: {
			name: string;
			path: string;
			parent_id?: string | null;
			description?: string;
		}): Promise<APIResponse<any>> {
			const response = await client.post('/api/phrasebook/categories', category);
			return response.data;
		},

		async updatePhrasebookCategory(
			categoryId: string,
			category: {
				name: string;
				path: string;
				parent_id?: string | null;
				description?: string;
			}
		): Promise<APIResponse<any>> {
			const response = await client.put(
				`/api/phrasebook/categories/${categoryId}`,
				category
			);
			return response.data;
		},

		async deletePhrasebookCategory(
			categoryId: string
		): Promise<APIResponse<{ message: string }>> {
			const response = await client.delete(`/api/phrasebook/categories/${categoryId}`);
			return response.data;
		},

		async createPhrasebookValue(value: {
			category_id: string;
			label: string;
			value: string;
			sort_order?: number;
		}): Promise<APIResponse<any>> {
			const response = await client.post('/api/phrasebook/values', value);
			return response.data;
		},

		async updatePhrasebookValue(
			valueId: string,
			value: {
				category_id: string;
				label: string;
				value: string;
				sort_order?: number;
			}
		): Promise<APIResponse<any>> {
			const response = await client.put(`/api/phrasebook/values/${valueId}`, value);
			return response.data;
		},

		async deletePhrasebookValue(
			valueId: string
		): Promise<APIResponse<{ message: string }>> {
			const response = await client.delete(`/api/phrasebook/values/${valueId}`);
			return response.data;
		},

		async importPhrasebookYAML(
			file: File,
			rootCategory?: string
		): Promise<
			APIResponse<{
				success: boolean;
				categories_created: number;
				values_created: number;
				error?: string;
			}>
		> {
			const formData = new FormData();
			formData.append('file', file);
			if (rootCategory) {
				formData.append('root_category', rootCategory);
			}

			const response = await client.post('/api/phrasebook/import', formData, {
				headers: {
					'Content-Type': 'multipart/form-data'
				}
			});
			return response.data;
		},

		async exportPhrasebookCategory(categoryId: string): Promise<string> {
			const response = await client.get(`/api/phrasebook/export/${categoryId}`, {
				responseType: 'text'
			});
			return response.data;
		},

		async toggleCategoryActive(
			categoryId: string,
			isActive: boolean
		): Promise<APIResponse<PhrasebookCategory>> {
			const response = await client.patch(
				`/api/phrasebook/categories/${categoryId}/active`,
				{ is_active: isActive }
			);
			return response.data;
		},

		async toggleValueActive(
			valueId: string,
			isActive: boolean
		): Promise<APIResponse<PhrasebookValue>> {
			const response = await client.patch(`/api/phrasebook/values/${valueId}/active`, {
				is_active: isActive
			});
			return response.data;
		},

		async generatePreviews(
			categoryId: string,
			request: GeneratePreviewRequest
		): Promise<APIResponse<GeneratePreviewResult>> {
			const response = await client.post(
				`/api/phrasebook/categories/${categoryId}/generate-previews`,
				request,
				{ timeout: 300000 } // 5 minutes timeout
			);
			return response.data;
		}
	};
}
