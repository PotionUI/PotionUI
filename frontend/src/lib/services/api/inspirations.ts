import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';

/** Mirrors the author summary embedded in every inspiration/comment dto. */
export interface InspirationAuthor {
	id: string;
	username: string;
	avatar_url: string | null;
}

export interface InspirationMedia {
	url: string;
	type: string;
	width?: number | null;
	height?: number | null;
}

export interface InspirationParamPreview {
	name: string;
	value: unknown;
}

/** Mirrors the inspiration dto shape from the pinned API contract. */
export interface InspirationDto {
	id: string;
	title: string;
	description: string | null;
	author: InspirationAuthor;
	media: InspirationMedia[];
	params_preview: InspirationParamPreview[];
	technique: string | null;
	created_at: string;
	comment_count: number;
	save_count: number;
	saved_by_me: boolean;
	source_generation_id: string | null;
}

export interface InspirationsListResult {
	items: InspirationDto[];
	total: number;
}

export interface InspirationsListQuery {
	query?: string;
	limit?: number;
	offset?: number;
	collection_id?: string;
	author_id?: string;
	saved?: boolean;
}

export interface InspirationParamsResult {
	form_data: Record<string, unknown>;
	preset_id: string | null;
	preset_name: string | null;
	mode: string | null;
	omitted_fields: string[];
}

export interface InspirationComment {
	id: string;
	user: InspirationAuthor;
	body: string;
	created_at: string;
}

/** Mirrors `CollectionLike` ($lib/components/collections/types) structurally. */
export interface InspirationCollection {
	id: string;
	name: string;
	parent_id: string | null;
	item_count: number;
}

export function createInspirationsApi(client: AxiosInstance) {
	return {
		async listInspirations(
			query: InspirationsListQuery
		): Promise<APIResponse<InspirationsListResult>> {
			const response = await client.get('/api/inspirations', { params: query });
			return response.data;
		},

		async createInspiration(payload: {
			generation_id: string;
			filenames: string[];
			title: string;
			description?: string;
		}): Promise<APIResponse<{ inspiration: InspirationDto }>> {
			const response = await client.post('/api/inspirations', payload);
			return response.data;
		},

		/** Author or admin only - the backend enforces this, not the client. */
		async deleteInspiration(id: string): Promise<APIResponse<unknown>> {
			const response = await client.delete(`/api/inspirations/${id}`);
			return response.data;
		},

		async getInspirationParams(id: string): Promise<APIResponse<InspirationParamsResult>> {
			const response = await client.get(`/api/inspirations/${id}/params`);
			return response.data;
		},

		async saveInspirationToLibrary(
			id: string
		): Promise<APIResponse<{ saved: true; save_count: number }>> {
			const response = await client.post(`/api/inspirations/${id}/save-to-library`);
			return response.data;
		},

		async unsaveInspiration(id: string): Promise<APIResponse<unknown>> {
			const response = await client.delete(`/api/inspirations/${id}/save`);
			return response.data;
		},

		async listInspirationComments(
			id: string
		): Promise<APIResponse<{ items: InspirationComment[] }>> {
			const response = await client.get(`/api/inspirations/${id}/comments`);
			return response.data;
		},

		async addInspirationComment(id: string, body: string): Promise<APIResponse<unknown>> {
			const response = await client.post(`/api/inspirations/${id}/comments`, { body });
			return response.data;
		},

		/** Comment author or admin only - the backend enforces this, not the client. */
		async deleteInspirationComment(
			id: string,
			commentId: string
		): Promise<APIResponse<unknown>> {
			const response = await client.delete(`/api/inspirations/${id}/comments/${commentId}`);
			return response.data;
		},

		async listInspirationCollections(): Promise<APIResponse<{ items: InspirationCollection[] }>> {
			const response = await client.get('/api/inspirations/collections');
			return response.data;
		},

		async createInspirationCollection(
			name: string,
			parentId?: string | null
		): Promise<APIResponse<{ collection: InspirationCollection }>> {
			const response = await client.post('/api/inspirations/collections', {
				name,
				parent_id: parentId ?? null
			});
			return response.data;
		},

		async updateInspirationCollection(
			id: string,
			patch: { name?: string; parent_id?: string | null }
		): Promise<APIResponse<unknown>> {
			const response = await client.put(`/api/inspirations/collections/${id}`, patch);
			return response.data;
		},

		async deleteInspirationCollection(id: string): Promise<APIResponse<unknown>> {
			const response = await client.delete(`/api/inspirations/collections/${id}`);
			return response.data;
		},

		async addInspirationToCollection(
			collectionId: string,
			inspirationId: string
		): Promise<APIResponse<unknown>> {
			const response = await client.post(`/api/inspirations/collections/${collectionId}/items`, {
				inspiration_id: inspirationId
			});
			return response.data;
		},

		async removeInspirationFromCollection(
			collectionId: string,
			inspirationId: string
		): Promise<APIResponse<unknown>> {
			const response = await client.delete(
				`/api/inspirations/collections/${collectionId}/items/${inspirationId}`
			);
			return response.data;
		}
	};
}
