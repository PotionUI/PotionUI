import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';
import type { ModelCollection } from '$lib/types/models';

export function createModelCollectionsApi(client: AxiosInstance) {
	return {
		async listModelCollections(): Promise<
			APIResponse<{ collections: ModelCollection[]; total: number }>
		> {
			const response = await client.get('/api/models/collections');
			return response.data;
		},

		async createModelCollection(
			name: string,
			parentId?: string | null
		): Promise<APIResponse<{ message: string; collection: ModelCollection }>> {
			const response = await client.post('/api/models/collections', {
				name,
				parent_id: parentId ?? null
			});
			return response.data;
		},

		async renameModelCollection(id: string, name: string): Promise<APIResponse<any>> {
			const response = await client.put(`/api/models/collections/${id}`, { name });
			return response.data;
		},

		// Reparent a model collection. parentId === null moves it to the root.
		async moveModelCollection(id: string, parentId: string | null): Promise<APIResponse<any>> {
			const response = await client.put(`/api/models/collections/${id}/move`, {
				parent_id: parentId
			});
			return response.data;
		},

		// Reparent several model collections at once. parentId === null moves
		// them to the root. Unlike moveModelCollection, a bad id doesn't fail
		// the batch - the response reports per-id outcomes.
		async bulkMoveModelCollections(
			ids: string[],
			parentId: string | null
		): Promise<APIResponse<{ moved: number; failed: number; errors: { id: string; reason: string }[] }>> {
			const response = await client.post('/api/models/collections/bulk-move', {
				collection_ids: ids,
				parent_id: parentId
			});
			return response.data;
		},

		async deleteModelCollection(id: string): Promise<APIResponse<any>> {
			const response = await client.delete(`/api/models/collections/${id}`);
			return response.data;
		},

		async addToModelCollection(
			id: string,
			modelIds: string[]
		): Promise<APIResponse<{ added: number }>> {
			const response = await client.post(`/api/models/collections/${id}/members`, {
				model_ids: modelIds
			});
			return response.data;
		},

		async removeFromModelCollection(
			id: string,
			modelIds: string[]
		): Promise<APIResponse<{ removed: number }>> {
			// axios requires a request body on DELETE to be passed via `data`.
			const response = await client.delete(`/api/models/collections/${id}/members`, {
				data: { model_ids: modelIds }
			});
			return response.data;
		}
	};
}
