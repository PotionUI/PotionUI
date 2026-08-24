import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';
import type { LibraryQuery } from '$lib/library/libraryQuery';

/**
 * One item in the user's private library. Mirrors `LibraryItem`
 * (src/features/library/dto.py). `filename` is the on-disk uuid the file was
 * stored under and must never be shown - use `original_filename`.
 *
 * A library item carries no generation fields even when it started life as a
 * copy of one; that is the point of the copy.
 */
export interface LibraryItem {
	id: string;
	filename: string;
	original_filename?: string;
	media_type: string;
	mime_type?: string;
	url: string;
	thumbnail_small?: string;
	thumbnail_medium?: string;
	thumbnail_large?: string;
	width?: number;
	height?: number;
	duration_seconds?: number;
	fps?: number;
	size?: number;
	created_at?: string;
	tags: LibraryItemTag[];
}

export interface LibraryItemTag {
	id: string;
	name: string;
	type?: string;
	color?: string;
}

/** Mirrors `LibraryListResult` (src/features/library/dto.py). */
export interface LibraryListResult {
	items: LibraryItem[];
	total: number;
	limit: number;
	offset: number;
}

/** Mirrors `LibraryFacets` (src/features/library/dto.py). */
export interface LibraryFacets {
	media_types: Record<string, number>;
}

export function createLibraryApi(client: AxiosInstance) {
	return {
		/**
		 * One page of the current user's library, newest first. Scoped
		 * server-side - there is no way to list another user's library.
		 * Build `query` with `buildLibraryQuery` ($lib/library/libraryQuery).
		 */
		async listLibraryItems(query: LibraryQuery): Promise<APIResponse<LibraryListResult>> {
			const response = await client.get('/api/library/items', { params: query });
			return response.data;
		},

		async getLibraryFacets(): Promise<APIResponse<LibraryFacets>> {
			const response = await client.get('/api/library/facets');
			return response.data;
		},

		/**
		 * Deletes one library item (row, file and memberships). 404s both when
		 * the item doesn't exist and when it belongs to another user - the two
		 * cases are deliberately indistinguishable server-side.
		 */
		async deleteLibraryItem(
			itemId: string
		): Promise<APIResponse<{ id: string; deleted: boolean }>> {
			const response = await client.delete(`/api/library/items/${itemId}`);
			return response.data;
		},

		/** Replaces an item's tags; every id must be an UPLOAD tag the user owns. */
		async setLibraryItemTags(
			itemId: string,
			tagIds: string[]
		): Promise<APIResponse<{ tags: LibraryItemTag[] }>> {
			const response = await client.put(`/api/library/items/${itemId}/tags`, { tag_ids: tagIds });
			return response.data;
		},

		/**
		 * Copies one of the user's generated files into their library. A copy,
		 * not a move: the generation and its files stay exactly as they were,
		 * and the new item carries none of their metadata.
		 */
		async copyGenerationFileToLibrary(
			fileId: string
		): Promise<APIResponse<{ item: LibraryItem }>> {
			const response = await client.post('/api/library/items/from-generation', {
				file_id: fileId
			});
			return response.data;
		},

		/**
		 * Uploads a file into the library. Library items and media-loader
		 * uploads are the same `uploads` rows, so this is the same route the
		 * media loader posts to.
		 */
		async uploadLibraryMedia(file: File): Promise<APIResponse<{ filename?: string }>> {
			const formData = new FormData();
			formData.append('file', file);
			const response = await client.post('/api/media/upload', formData, {
				headers: { 'Content-Type': 'multipart/form-data' }
			});
			return response.data;
		}
	};
}
