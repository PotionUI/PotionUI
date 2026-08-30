import type { AxiosInstance } from 'axios';
import type {
	APIResponse,
	GenerationRequest,
	GenerationStatus,
	StartGenerationResponseData,
	GenerationQueueSnapshot
} from '$lib/types/api';
import type {
	GenerationHistoryItem,
	ImportBundleResult,
	Tag as HistoryTag,
	HistoryFacets,
	SortBy,
	SortDir
} from '$lib/types/history';
import type { Workspace, GenerationParamModel } from '$lib/types/generation';

interface UploadedGenerationFile {
	id: number;
	file_path: string;
	file_type: string;
	file_size?: number;
}

export function createGenerationsApi(client: AxiosInstance) {
	return {
		async startGeneration(
			request: GenerationRequest
		): Promise<APIResponse<StartGenerationResponseData>> {
			const response = await client.post('/api/generations/start', request);
			return response.data;
		},

		async getGenerationQueue(tabId?: string): Promise<APIResponse<GenerationQueueSnapshot>> {
			const response = await client.get('/api/generations/queue', {
				params: tabId ? { tab_id: tabId } : undefined
			});
			return response.data;
		},

		async clearGenerationQueue(
			tabId: string
		): Promise<APIResponse<{ cancelled: string[]; count: number }>> {
			const response = await client.post('/api/generations/queue/clear', { tab_id: tabId });
			return response.data;
		},

		async cancelGeneration(generationId: string): Promise<APIResponse> {
			const response = await client.post(`/api/generations/${generationId}/cancel`);
			return response.data;
		},

		async getGenerationStatus(generationId: string): Promise<APIResponse<GenerationStatus>> {
			const response = await client.get(`/api/generations/${generationId}/status`);
			return response.data;
		},

		async getGenerationHistory(params?: {
			limit?: number;
			offset?: number;
			status?: string;
			createdFrom?: string;
			createdTo?: string;
			completedFrom?: string;
			completedTo?: string;
			tagIds?: string[];
			includeTags?: boolean;
			mediaType?: 'image' | 'video' | 'audio';
			search?: string;
			semanticQuery?: string;
			mode?: string;
			presetId?: string;
			modelName?: string;
			collectionId?: string;
			usedPhrasebookValueId?: string;
			systemTag?: string;
			minRating?: number;
			favoritesOnly?: boolean;
			sortBy?: SortBy;
			sortDir?: SortDir;
		}): Promise<APIResponse<{ generations: GenerationHistoryItem[]; total: number }>> {
			const searchParams = new URLSearchParams();
			if (params?.limit) searchParams.append('limit', params.limit.toString());
			if (params?.offset) searchParams.append('offset', params.offset.toString());
			if (params?.status) searchParams.append('status', params.status);
			if (params?.createdFrom) searchParams.append('created_from', params.createdFrom);
			if (params?.createdTo) searchParams.append('created_to', params.createdTo);
			if (params?.completedFrom) searchParams.append('completed_from', params.completedFrom);
			if (params?.completedTo) searchParams.append('completed_to', params.completedTo);
			if (params?.tagIds && params.tagIds.length > 0)
				searchParams.append('tag_ids', params.tagIds.join(','));
			if (params?.includeTags !== undefined)
				searchParams.append('include_tags', params.includeTags.toString());
			if (params?.mediaType) searchParams.append('media_type', params.mediaType);
			if (params?.search) searchParams.append('search', params.search);
			if (params?.semanticQuery) searchParams.append('semantic_query', params.semanticQuery);
			if (params?.mode) searchParams.append('mode', params.mode);
			if (params?.presetId) searchParams.append('preset_id', params.presetId);
			if (params?.modelName) searchParams.append('model_name', params.modelName);
			if (params?.collectionId) searchParams.append('collection_id', params.collectionId);
			if (params?.usedPhrasebookValueId)
				searchParams.append('used_phrasebook_value_id', params.usedPhrasebookValueId);
			if (params?.systemTag) searchParams.append('system_tag', params.systemTag);
			if (params?.minRating) searchParams.append('min_rating', params.minRating.toString());
			if (params?.favoritesOnly) searchParams.append('favorites_only', 'true');
			if (params?.sortBy) searchParams.append('sort_by', params.sortBy);
			if (params?.sortDir) searchParams.append('sort_dir', params.sortDir);

			const queryString = searchParams.toString();
			const response = await client.get(
				`/api/generations/history${queryString ? `?${queryString}` : ''}`
			);
			return response.data;
		},

		async getHistoryFacets(): Promise<APIResponse<HistoryFacets>> {
			const response = await client.get('/api/generations/history/facets');
			return response.data;
		},

		async setGenerationRating(
			generationId: string,
			rating: number
		): Promise<APIResponse<{ id: string; rating: number }>> {
			const response = await client.put(`/api/generations/${generationId}/rating`, {
				rating
			});
			return response.data;
		},

		async setGenerationFavorite(
			generationId: string,
			isFavorite: boolean
		): Promise<APIResponse<{ id: string; is_favorite: boolean }>> {
			const response = await client.put(`/api/generations/${generationId}/favorite`, {
				is_favorite: isFavorite
			});
			return response.data;
		},

		async getGenerationById(
			generationId: string,
			includeTags: boolean = false,
			includeFiles: boolean = false
		): Promise<APIResponse<any>> {
			const searchParams = new URLSearchParams();
			if (includeTags) searchParams.append('include_tags', 'true');
			if (includeFiles) searchParams.append('include_files', 'true');
			const queryString = searchParams.toString();
			const response = await client.get(
				`/api/generations/history/${generationId}${queryString ? `?${queryString}` : ''}`
			);
			return response.data;
		},

		// Rendered text report for a generation's resource profile (admin-only on
		// the backend). Returns the plain-text report, not an APIResponse envelope.
		async getGenerationProfileReport(generationId: string): Promise<string> {
			const response = await client.get(
				`/api/generations/${generationId}/profile`,
				{ params: { format: 'report' }, responseType: 'text' }
			);
			return response.data as string;
		},

		async getGenerationParams(
			generationId: string,
			fileIndex: number
		): Promise<APIResponse<{ parameters: Record<string, unknown>; models: GenerationParamModel[] }>> {
			const response = await client.get(
				`/api/generations/${generationId}/params/${fileIndex}`
			);
			return response.data;
		},

		async deleteGenerationHistory(
			generationId: string
		): Promise<APIResponse<{ message: string }>> {
			const response = await client.delete(`/api/generations/history/${generationId}`);
			return response.data;
		},

		async bulkDeleteGenerations(generationIds: string[]): Promise<
			APIResponse<{
				message: string;
				deleted_count: number;
				failed_count: number;
				failed_ids: string[];
				total_files_deleted: number;
			}>
		> {
			const response = await client.post('/api/generations/history/bulk-delete', {
				generation_ids: generationIds
			});
			return response.data;
		},

		// Export selected generations as a zip. The endpoint returns a BINARY zip
		// stream (not the JSON envelope); fetch it as a blob via the authenticated
		// axios client and trigger a browser download.
		async exportGenerations(generationIds: string[], stripMetadata: boolean): Promise<void> {
			const response = await client.post(
				'/api/generations/export',
				{ generation_ids: generationIds, strip_metadata: stripMetadata },
				{ responseType: 'blob' }
			);

			const blob = response.data as Blob;
			const url = URL.createObjectURL(blob);
			const anchor = document.createElement('a');
			anchor.href = url;
			anchor.download = 'potionui-export.zip';
			document.body.appendChild(anchor);
			anchor.click();
			anchor.remove();
			URL.revokeObjectURL(url);
		},

		// Export a single generation as a portable bundle (a zip carrying its
		// files plus the settings needed to reuse it). Binary stream, same
		// blob-download pattern as exportGenerations.
		async exportGenerationBundle(generationId: string): Promise<void> {
			const response = await client.get(
				`/api/generations/history/${generationId}/export-bundle`,
				{ responseType: 'blob' }
			);

			const blob = response.data as Blob;
			const url = URL.createObjectURL(blob);
			const anchor = document.createElement('a');
			anchor.href = url;
			anchor.download = `potionui-generation-${generationId}.zip`;
			document.body.appendChild(anchor);
			anchor.click();
			anchor.remove();
			URL.revokeObjectURL(url);
		},

		async importGenerationBundle(file: File): Promise<APIResponse<ImportBundleResult>> {
			const formData = new FormData();
			formData.append('file', file);

			const response = await client.post('/api/generations/import-bundle', formData, {
				headers: { 'Content-Type': 'multipart/form-data' }
			});
			return response.data;
		},

		async countGenerationsByTags(tagIds: string[]): Promise<APIResponse<{ count: number }>> {
			const response = await client.post('/api/generations/history/count-by-tags', {
				tag_ids: tagIds
			});
			return response.data;
		},

		async bulkDeleteByTags(tagIds: string[]): Promise<APIResponse<any>> {
			const response = await client.post('/api/generations/history/bulk-delete-by-tags', {
				tag_ids: tagIds
			});
			return response.data;
		},

		async uploadGenerations(
			files: File[],
			tagIds: string[] = []
		): Promise<
			APIResponse<{
				message: string;
				generation_id: string;
				files: UploadedGenerationFile[];
			}>
		> {
			const formData = new FormData();

			files.forEach((file) => {
				formData.append('files', file);
			});

			const searchParams = new URLSearchParams();
			tagIds.forEach((tagId) => {
				searchParams.append('tag_ids', tagId);
			});

			const queryString = searchParams.toString();
			const url = `/api/generations/upload${queryString ? `?${queryString}` : ''}`;

			const response = await client.post(url, formData, {
				headers: {
					'Content-Type': 'multipart/form-data'
				}
			});
			return response.data;
		},

		async updateGenerationTags(
			generationId: string,
			tagIds: string[]
		): Promise<APIResponse<{ message: string; tags: HistoryTag[] }>> {
			const response = await client.put(`/api/generations/${generationId}/tags`, {
				tag_ids: tagIds
			});
			return response.data;
		},

		// Workspace API
		async getWorkspaces(): Promise<APIResponse<Workspace[]>> {
			const response = await client.get('/api/workspaces');
			return response.data;
		},

		async saveWorkspace(request: { name: string; data: unknown }): Promise<APIResponse<Workspace>> {
			const response = await client.post('/api/workspaces', request);
			return response.data;
		},

		async updateWorkspace(
			workspaceId: string,
			request: { name?: string; data?: unknown }
		): Promise<APIResponse<Workspace>> {
			const response = await client.put(`/api/workspaces/${workspaceId}`, request);
			return response.data;
		},

		async deleteWorkspace(workspaceId: string): Promise<APIResponse<unknown>> {
			const response = await client.delete(`/api/workspaces/${workspaceId}`);
			return response.data;
		}
	};
}
