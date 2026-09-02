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

/** Outcome for one file (or the pasted-text part) of `POST /api/prompts/import`. */
export interface PromptImportFileOutcome {
	filename: string;
	format: string;
	imported: number;
	skipped: number;
	reason?: string;
}

export interface PromptImportResult {
	imported: number;
	skipped: number;
	total: number;
	items: Prompt[];
	files: PromptImportFileOutcome[];
}

export interface PromptExportParams {
	collection_id?: string;
}

export function createPromptsApi(client: AxiosInstance) {
	return {
		async listPromptImporters(): Promise<APIResponse<PromptImporter[]>> {
			const response = await client.get('/api/prompts/importers');
			return response.data;
		},

		/** `POST /api/prompts/import` - files and/or pasted text, built by
		 *  `buildPromptImportFormData`. `success=false, error="nothing_imported"`
		 *  when nothing usable came in; per-file outcomes are still returned so
		 *  the modal can show why. */
		async importPrompts(formData: FormData): Promise<APIResponse<PromptImportResult>> {
			const response = await client.post('/api/prompts/import', formData);
			return response.data;
		},

		/** Downloads `GET /api/prompts/export` as `styles.csv` (A1111 / Forge /
		 *  SD.Next / InvokeAI compatible). Binary stream, not the JSON envelope -
		 *  same blob-download pattern as generations export. */
		async downloadPromptsExport(params: PromptExportParams = {}): Promise<void> {
			const response = await client.get('/api/prompts/export', {
				params: { format: 'styles-csv', ...params },
				responseType: 'blob'
			});

			const blob = response.data as Blob;
			const url = URL.createObjectURL(blob);
			const anchor = document.createElement('a');
			anchor.href = url;
			anchor.download = 'styles.csv';
			document.body.appendChild(anchor);
			anchor.click();
			anchor.remove();
			URL.revokeObjectURL(url);
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
