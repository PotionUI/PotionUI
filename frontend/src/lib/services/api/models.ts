import type { AxiosInstance } from 'axios';
import type { APIResponse, ModelDownloadStatus, TagUsageRef } from '$lib/types/api';
import type { AttributeDefinition, ModelAvailabilityResponse } from '$lib/types/models';
import type { ModelPreviewMediaItem } from '$lib/utils/modelPreview';

export interface ModelsLocationDirectory {
	directory: string;
	target: string | null;
	linked: boolean;
	resolved_target: string | null;
	has_real_files: boolean;
}

export interface ModelsLocationConfig {
	external_path: string | null;
	overrides: Record<string, string>;
	directories: ModelsLocationDirectory[];
	windows_unsupported: boolean;
}

export function createModelsApi(client: AxiosInstance) {
	return {
		async getModels(params?: {
			model_type?: string;
			search?: string;
			sort_by?: string;
			sort_order?: string;
			limit?: number;
			offset?: number;
			include_tags?: boolean;
			tag_ids?: string;
			all_models?: boolean;
			assignment_filter?: string;
			assigned_user_id?: string;
			assigned_group_id?: string;
			favorites_only?: boolean;
			collection_id?: string;
			in_any_collection?: boolean;
		}): Promise<APIResponse<{ models: any[]; total: number; availability_indexed: boolean }>> {
			const searchParams = new URLSearchParams();
			if (params?.model_type) searchParams.append('model_type', params.model_type);
			if (params?.search) searchParams.append('search', params.search);
			if (params?.sort_by) searchParams.append('sort_by', params.sort_by);
			if (params?.sort_order) searchParams.append('sort_order', params.sort_order);
			if (params?.limit) searchParams.append('limit', params.limit.toString());
			if (params?.offset) searchParams.append('offset', params.offset.toString());
			if (params?.include_tags !== undefined)
				searchParams.append('include_tags', params.include_tags.toString());
			if (params?.tag_ids) searchParams.append('tag_ids', params.tag_ids);
			if (params?.all_models) searchParams.append('all_models', 'true');
			if (params?.assignment_filter)
				searchParams.append('assignment_filter', params.assignment_filter);
			if (params?.assigned_user_id)
				searchParams.append('assigned_user_id', params.assigned_user_id);
			if (params?.assigned_group_id)
				searchParams.append('assigned_group_id', params.assigned_group_id);
			if (params?.favorites_only) searchParams.append('favorites_only', 'true');
			if (params?.collection_id) searchParams.append('collection_id', params.collection_id);
			if (params?.in_any_collection) searchParams.append('in_any_collection', 'true');

			const queryString = searchParams.toString();
			const response = await client.get(`/api/models${queryString ? `?${queryString}` : ''}`);
			return response.data;
		},

		// Models loadable by a preset's engine (union across its enabled backends),
		// each entry badged with `backend_ids`. See docs/models.md. Availability is a
		// WHERE clause on the server, so limit/offset page correctly and every filter
		// (search, tags, favorites) must go server-side rather than being applied to a page.
		async getPresetModels(
			presetId: string,
			modelType?: string,
			search?: string,
			opts?: {
				limit?: number;
				offset?: number;
				tagIds?: string;
				anyTagIds?: string;
				favoritesOnly?: boolean;
			}
		): Promise<APIResponse<{ engine: string; models: any[]; total: number; indexed: boolean }>> {
			const searchParams = new URLSearchParams();
			if (modelType) searchParams.append('model_type', modelType);
			if (search) searchParams.append('search', search);
			if (opts?.limit) searchParams.append('limit', opts.limit.toString());
			if (opts?.offset) searchParams.append('offset', opts.offset.toString());
			if (opts?.tagIds) searchParams.append('tag_ids', opts.tagIds);
			if (opts?.anyTagIds) searchParams.append('any_tag_ids', opts.anyTagIds);
			if (opts?.favoritesOnly) searchParams.append('favorites_only', 'true');
			const queryString = searchParams.toString();
			const response = await client.get(
				`/api/presets/${presetId}/models${queryString ? `?${queryString}` : ''}`
			);
			return response.data;
		},

		async setModelFavorite(modelId: string, isFavorite: boolean): Promise<APIResponse> {
			const response = await client.put(`/api/models/${modelId}/favorite`, {
				is_favorite: isFavorite
			});
			return response.data;
		},

		async setModelLibraryName(modelId: string, name: string | null): Promise<APIResponse> {
			const response = await client.put(`/api/models/${modelId}/library-name`, { name });
			return response.data;
		},

		async getTags(type?: 'MODEL' | 'GENERATION' | 'UPLOAD'): Promise<APIResponse<{ tags: any[] }>> {
			const params = type ? `?type=${type}` : '';
			const response = await client.get(`/api/tags${params}`);
			return response.data;
		},

		async searchTags(
			query: string,
			type?: 'MODEL' | 'GENERATION' | 'UPLOAD',
			limit: number = 10
		): Promise<APIResponse<{ tags: any[] }>> {
			const searchParams = new URLSearchParams();
			searchParams.append('q', query);
			if (type) searchParams.append('type', type);
			searchParams.append('limit', limit.toString());
			const queryString = searchParams.toString();
			const response = await client.get(
				`/api/tags/search${queryString ? `?${queryString}` : ''}`
			);
			return response.data;
		},

		async getModelGenerations(
			modelId: string,
			params?: {
				limit?: number;
				offset?: number;
			}
		): Promise<APIResponse<{ generations: any[]; total: number; pagination: any }>> {
			const searchParams = new URLSearchParams();
			if (params?.limit) searchParams.append('limit', params.limit.toString());
			if (params?.offset) searchParams.append('offset', params.offset.toString());
			const queryString = searchParams.toString();
			const response = await client.get(
				`/api/models/${modelId}/generations${queryString ? `?${queryString}` : ''}`
			);
			return response.data;
		},

		async getModelById(
			modelId: string,
			includeTags: boolean = false
		): Promise<APIResponse<{ model: any }>> {
			const params = includeTags ? '?include_tags=true' : '';
			const response = await client.get(`/api/models/${modelId}${params}`);
			return response.data;
		},

		async updateModelDescription(modelId: string, description: string): Promise<APIResponse> {
			const response = await client.put(`/api/models/${modelId}/description`, { description });
			return response.data;
		},

		async updateModelPromptingGuidance(modelId: string, promptingGuidance: string): Promise<APIResponse> {
			const response = await client.put(`/api/models/${modelId}/prompting-guidance`, {
				prompting_guidance: promptingGuidance
			});
			return response.data;
		},

		// Rejects with a 4xx naming the offending key (unknown/wrong-type/out-of-range
		// values are rejected, never clamped) - see docs/models.md. SHARED values,
		// admin-only.
		async updateModelMetadata(modelId: string, values: Record<string, unknown>): Promise<APIResponse<any>> {
			const response = await client.put(`/api/models/${modelId}/metadata`, { values });
			return response.data;
		},

		// The requesting user's own overlay for `per_user` attribute definitions.
		// Any authenticated user may call this for themselves - not admin-gated.
		async updateModelUserAttributes(
			modelId: string,
			values: Record<string, unknown>
		): Promise<APIResponse<{ values: Record<string, unknown> }>> {
			const response = await client.put(`/api/models/${modelId}/attributes/user`, { values });
			return response.data;
		},

		// Attribute definitions: the DB-backed schema that replaces the old static
		// metadata-field declarations. The server already filters `admin_only`
		// definitions out of this list for non-admin callers.
		async getAttributeDefinitions(): Promise<APIResponse<{ definitions: AttributeDefinition[] }>> {
			const response = await client.get('/api/models/attributes');
			return response.data;
		},

		async createAttributeDefinition(
			payload: Omit<AttributeDefinition, 'id' | 'system' | 'source'>
		): Promise<APIResponse<{ definition: AttributeDefinition }>> {
			const response = await client.post('/api/models/attributes', payload);
			return response.data;
		},

		// For a system definition, the server rejects a `key`/`field_type` change
		// but accepts everything else (label, model_types, config, default_value,
		// description, per_user, admin_only).
		async updateAttributeDefinition(
			id: string,
			payload: Partial<Omit<AttributeDefinition, 'id' | 'system' | 'source'>>
		): Promise<APIResponse<{ definition: AttributeDefinition }>> {
			const response = await client.put(`/api/models/attributes/${id}`, payload);
			return response.data;
		},

		// Rejected (4xx) for a system definition.
		async deleteAttributeDefinition(id: string): Promise<APIResponse> {
			const response = await client.delete(`/api/models/attributes/${id}`);
			return response.data;
		},

		/**
		 * A model's full ordered list of admin-set previews (position 0 is the
		 * primary, mirrored onto `model.preview_media` server-side; the legacy
		 * single-preview endpoint above keeps working unchanged for any other caller).
		 */
		async listModelPreviews(modelId: string): Promise<APIResponse<{ previews: ModelPreviewMediaItem[] }>> {
			const response = await client.get(`/api/models/${modelId}/previews`);
			return response.data;
		},

		async addModelPreview(
			modelId: string,
			preview: { source_path: string; type: 'image' | 'video' | 'audio'; name?: string }
		): Promise<APIResponse<{ id: string; previews: ModelPreviewMediaItem[] }>> {
			const response = await client.post(`/api/models/${modelId}/previews`, { preview });
			return response.data;
		},

		async deleteModelPreview(
			modelId: string,
			previewId: string
		): Promise<APIResponse<{ previews: ModelPreviewMediaItem[] }>> {
			const response = await client.delete(
				`/api/models/${modelId}/previews/${encodeURIComponent(previewId)}`
			);
			return response.data;
		},

		async reorderModelPreviews(
			modelId: string,
			orderedIds: string[]
		): Promise<APIResponse<{ previews: ModelPreviewMediaItem[] }>> {
			const response = await client.put(`/api/models/${modelId}/previews/order`, {
				ordered_ids: orderedIds
			});
			return response.data;
		},

		async updateModelTags(
			modelId: string,
			tagIds: string[]
		): Promise<APIResponse<{ model: any }>> {
			const response = await client.put(`/api/models/${modelId}/tags`, { tag_ids: tagIds });
			return response.data;
		},

		// Which backends can load this model, and under what ref/size/confidence.
		// Lazy/on-demand only (e.g. the model details modal) - list views use the
		// `backend_ids`/`availability_indexed` already embedded in getModels().
		async getModelAvailability(modelId: string): Promise<APIResponse<ModelAvailabilityResponse>> {
			const response = await client.get(`/api/models/${modelId}/availability`);
			return response.data;
		},

		async getModelTypes(params?: {
			user_scoped?: boolean;
			include_empty?: boolean;
		}): Promise<APIResponse<{ types: any[] }>> {
			const searchParams = new URLSearchParams();
			if (params?.user_scoped) searchParams.append('user_scoped', 'true');
			if (params?.include_empty) searchParams.append('include_empty', 'true');
			const queryString = searchParams.toString();
			const response = await client.get(
				`/api/models/types${queryString ? `?${queryString}` : ''}`
			);
			return response.data;
		},

		async indexModels(): Promise<
			APIResponse<{ indexed: number; failed: number; total: number }>
		> {
			const response = await client.post('/api/models/index');
			return response.data;
		},

		async getProviders(): Promise<APIResponse<any[]>> {
			const response = await client.get('/api/providers');
			return response.data;
		},

		async fetchProviderInfo(
			provider: string,
			modelIds?: string[],
			forceRefresh: boolean = false
		): Promise<APIResponse> {
			const response = await client.post('/api/models/info/fetch', {
				model_ids: modelIds || null,
				provider: provider,
				force_refresh: forceRefresh
			});
			return response.data;
		},

		async cleanupDeletedModels(): Promise<APIResponse> {
			const response = await client.post('/api/models/cleanup');
			return response.data;
		},

		async deleteModel(modelId: string): Promise<APIResponse> {
			const response = await client.delete(`/api/models/${modelId}`);
			return response.data;
		},

		async createTag(name: string, type: 'MODEL' | 'GENERATION' | 'UPLOAD'): Promise<APIResponse<any>> {
			const response = await client.post('/api/tags', { name, type });
			return response.data;
		},

		// Rejects with a 409 (`error.response.data` = `{ error, used_by }`) when the tag
		// is still referenced by a preset's configuration - callers must surface `used_by`.
		async deleteTag(tagId: string): Promise<APIResponse<{ used_by?: TagUsageRef[] }>> {
			const response = await client.delete(`/api/tags/${tagId}`);
			return response.data;
		},

		// Model download recommendations. `payload` is either provider-backed
		// (`provider`/`ref`) or a direct URL (`link`/`sha256`) - see ModelRecommendation.
		// Admin-only; rejects with 403 for non-admin users.
		async startModelDownload(payload: {
			name: string;
			model_type: string;
			provider?: string;
			ref?: string;
			link?: string;
			sha256?: string;
		}): Promise<APIResponse<{ download_id: string }>> {
			const response = await client.post('/api/models/downloads', payload);
			return response.data;
		},

		async getModelDownloadStatus(downloadId: string): Promise<APIResponse<ModelDownloadStatus>> {
			const response = await client.get(`/api/models/downloads/${downloadId}`);
			return response.data;
		},

		// Models location: the external directory the `models/<type>` symlinks point
		// at, and any per-type overrides. Admin-only.
		async getModelsLocation(): Promise<APIResponse<ModelsLocationConfig>> {
			const response = await client.get('/api/models/location');
			return response.data;
		},

		async applyModelsLocation(
			externalPath: string,
			overrides?: Record<string, string>
		): Promise<APIResponse<ModelsLocationConfig>> {
			const response = await client.post('/api/models/location/apply', {
				external_path: externalPath,
				overrides
			});
			return response.data;
		},

		// Keybindings API
		async getKeybindings(): Promise<APIResponse<any>> {
			const response = await client.get('/api/keybindings');
			return response.data;
		},

		async updateKeybinding(
			actionId: string,
			key: string | null,
			modifiers: string = ''
		): Promise<APIResponse<any>> {
			const response = await client.put(`/api/keybindings/${actionId}`, { key, modifiers });
			return response.data;
		},

		async resetKeybinding(actionId: string): Promise<APIResponse<any>> {
			const response = await client.delete(`/api/keybindings/${actionId}`);
			return response.data;
		},

		async resetAllKeybindings(): Promise<APIResponse<any>> {
			const response = await client.post('/api/keybindings/reset');
			return response.data;
		}
	};
}
