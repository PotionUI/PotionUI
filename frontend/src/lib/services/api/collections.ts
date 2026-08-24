import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';
import type { Collection, CollectionScope } from '$lib/types/history';

// Every call takes `scope` explicitly - the backend has no default and
// rejects a request missing it, so a caller that forgets which tree
// (History vs Library) it means fails loudly instead of touching the wrong one.
export function createCollectionsApi(client: AxiosInstance) {
	return {
		async listCollections(
			scope: CollectionScope
		): Promise<APIResponse<{ collections: Collection[]; total: number }>> {
			const response = await client.get('/api/collections', { params: { scope } });
			return response.data;
		},

		async createCollection(
			name: string,
			scope: CollectionScope,
			parentId?: string | null
		): Promise<APIResponse<{ message: string; collection: Collection }>> {
			const response = await client.post('/api/collections', {
				name,
				scope,
				parent_id: parentId ?? null
			});
			return response.data;
		},

		async renameCollection(id: string, name: string, scope: CollectionScope): Promise<APIResponse<any>> {
			const response = await client.put(`/api/collections/${id}`, { name, scope });
			return response.data;
		},

		// Reparent a collection. parentId === null moves it to the root.
		async moveCollection(
			id: string,
			parentId: string | null,
			scope: CollectionScope
		): Promise<APIResponse<any>> {
			const response = await client.put(`/api/collections/${id}/move`, {
				parent_id: parentId,
				scope
			});
			return response.data;
		},

		// Reparent several collections at once. parentId === null moves them to
		// the root. Unlike moveCollection, a bad id doesn't fail the batch - the
		// response reports per-id outcomes.
		async bulkMoveCollections(
			ids: string[],
			parentId: string | null,
			scope: CollectionScope
		): Promise<APIResponse<{ moved: number; failed: number; errors: { id: string; reason: string }[] }>> {
			const response = await client.post('/api/collections/bulk-move', {
				collection_ids: ids,
				parent_id: parentId,
				scope
			});
			return response.data;
		},

		async deleteCollection(id: string, scope: CollectionScope): Promise<APIResponse<any>> {
			const response = await client.delete(`/api/collections/${id}`, { params: { scope } });
			return response.data;
		},

		async addToCollection(
			id: string,
			generationIds: string[],
			scope: CollectionScope
		): Promise<APIResponse<{ added: number }>> {
			const response = await client.post(`/api/collections/${id}/members`, {
				generation_ids: generationIds,
				scope
			});
			return response.data;
		},

		async removeFromCollection(
			id: string,
			generationIds: string[],
			scope: CollectionScope
		): Promise<APIResponse<{ removed: number }>> {
			// axios requires a request body on DELETE to be passed via `data`.
			const response = await client.delete(`/api/collections/${id}/members`, {
				data: { generation_ids: generationIds, scope }
			});
			return response.data;
		},

		// Library items live in their own 'library'-scope folder tree,
		// separate from History's 'history'-scope tree (migration 137), on
		// their own junction table.
		async addUploadsToCollection(
			id: string,
			uploadIds: string[],
			scope: CollectionScope
		): Promise<APIResponse<{ added: number }>> {
			const response = await client.post(`/api/collections/${id}/uploads`, {
				upload_ids: uploadIds,
				scope
			});
			return response.data;
		},

		async removeUploadsFromCollection(
			id: string,
			uploadIds: string[],
			scope: CollectionScope
		): Promise<APIResponse<{ removed: number }>> {
			const response = await client.delete(`/api/collections/${id}/uploads`, {
				data: { upload_ids: uploadIds, scope }
			});
			return response.data;
		},

		// Saved prompts live in their own 'prompts'-scope folder tree, on their
		// own junction table (migration 137).
		async addPromptsToCollection(
			id: string,
			promptIds: string[],
			scope: CollectionScope
		): Promise<APIResponse<{ added: number }>> {
			const response = await client.post(`/api/collections/${id}/prompts`, {
				prompt_ids: promptIds,
				scope
			});
			return response.data;
		}
	};
}
