import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';
import type {
	CreatePromptInput,
	Prompt,
	PromptGenerationItem,
	ReplacePromptInput
} from '$lib/types/segments';

export interface PromptListParams {
	limit?: number;
	offset?: number;
	source_provider?: string;
	base_model?: string;
	model_id?: string;
	usage_hint?: 'positive' | 'negative';
	collection_id?: string;
	sort_by?: string;
	sort_order?: string;
}

export interface PromptImporter {
	id: string;
	label: string;
	component: string;
}

export function createPromptsApi(client: AxiosInstance) {
	return {
		async listPromptImporters(): Promise<APIResponse<PromptImporter[]>> {
			const response = await client.get('/api/prompts/importers');
			return response.data;
		},

		async createPrompt(data: CreatePromptInput): Promise<APIResponse<Prompt>> {
			const response = await client.post('/api/prompts', data);
			return response.data;
		},

		async replacePrompt(promptId: string, data: ReplacePromptInput): Promise<APIResponse<Prompt>> {
			const response = await client.put(`/api/prompts/${promptId}`, data);
			return response.data;
		},

		async listPrompts(params: PromptListParams = {}): Promise<
			APIResponse<{
				items: Prompt[];
				total: number;
				limit: number;
				offset: number;
			}>
		> {
			const response = await client.get('/api/prompts', { params });
			return response.data;
		},

		async searchPrompts(params: {
			q: string;
			limit?: number;
			base_model?: string;
			model_id?: string;
			source_provider?: string;
		}): Promise<APIResponse<Prompt[]>> {
			const response = await client.get('/api/prompts/search', { params });
			return response.data;
		},

		async deletePrompt(promptId: string): Promise<APIResponse<unknown>> {
			const response = await client.delete(`/api/prompts/${promptId}`);
			return response.data;
		},

		async getPromptGenerations(
			promptId: string,
			params: { limit?: number; offset?: number } = {}
		): Promise<
			APIResponse<{
				items: PromptGenerationItem[];
				total: number;
				limit: number;
				offset: number;
			}>
		> {
			const response = await client.get(`/api/prompts/${promptId}/generations`, { params });
			return response.data;
		},

		async findDuplicatePrompts(
			params: { threshold?: number; model_id?: string } = {}
		): Promise<
			APIResponse<{
				groups: Array<{ similarity: number; prompts: Prompt[] }>;
				total_duplicates: number;
			}>
		> {
			const response = await client.post('/api/prompts/find-duplicates', null, {
				params
			});
			return response.data;
		},

		async bulkDeletePrompts(promptIds: string[]): Promise<APIResponse<{ deleted: number }>> {
			const response = await client.post('/api/prompts/bulk-delete', {
				prompt_ids: promptIds
			});
			return response.data;
		}
	};
}
