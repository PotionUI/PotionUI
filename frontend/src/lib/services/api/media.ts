import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';

/** Mirrors `MediaFileInfo` (src/features/media/dto.py). */
export interface MediaFileInfo {
	id: string;
	filename: string;
	file_type: string;
	mime_type?: string;
	size?: number;
	url: string;
	thumbnail_small?: string;
	thumbnail_medium?: string;
	thumbnail_large?: string;
	width?: number;
	height?: number;
	duration_seconds?: number;
	fps?: number;
}

/** Mirrors `UploadResult` (src/features/media/dto.py). */
export interface UploadResult {
	path: string;
	relative_path: string;
	filename: string;
	size: number;
	url: string;
	width?: number;
	height?: number;
	duration_seconds?: number;
	fps?: number;
}

/** Mirrors `UploadInfoResult` (src/features/media/dto.py). */
export interface UploadInfo {
	filename: string;
	size?: number;
	width?: number;
	height?: number;
	duration_seconds?: number;
	fps?: number;
}

/**
 * One item in a user's upload library. Mirrors
 * `UploadFileInfo` (src/features/media/dto.py). `filename` is the on-disk
 * unique name (a uuid) and must never be shown to the user - use
 * `original_filename` for display.
 */
export interface UploadFileInfo {
	id: string;
	filename: string;
	original_filename?: string;
	media_type: string;
	mime_type?: string;
	url: string;
	width?: number;
	height?: number;
	duration_seconds?: number;
	fps?: number;
	size?: number;
	created_at?: string;
}

export interface EditedMediaItem {
	id: string;
	filename: string;
	original_filename?: string;
	media_type: string;
	mime_type?: string;
	url: string;
	width?: number;
	height?: number;
	duration_seconds?: number;
	fps?: number;
	size?: number;
	created_at?: string;
	source_generation_id?: string | null;
	has_audio?: boolean | null;
}

/** Mirrors `UploadListResult` (src/features/media/dto.py). */
export interface UploadListResult {
	uploads: UploadFileInfo[];
	total: number;
	limit: number;
	offset: number;
}

export function createMediaApi(client: AxiosInstance, getBaseURL: () => string) {
	return {
		getFileURL(fileId: string, size?: string): string {
			const url = `${getBaseURL()}/api/media/files/${fileId}`;
			return size ? `${url}?size=${size}` : url;
		},

		getGenerationImageURL(generationId: string, filename: string): string {
			return `${getBaseURL()}/api/media/generations/${generationId}/${filename}`;
		},

		getGenerationThumbnailURL(
			generationId: string,
			filename: string,
			size: 'small' | 'medium' | 'large' = 'medium',
			animated?: boolean
		): string {
			const baseUrl = `${getBaseURL()}/api/media/generations/${generationId}/${filename}`;
			const params = new URLSearchParams();
			params.append('size', size);
			if (animated) {
				params.append('animated', 'true');
			}
			return `${baseUrl}?${params.toString()}`;
		},

		/**
		 * Builds a URL for a preset-owned media asset (cover, gallery image/video).
		 * `path` is passed through raw — the route is `{file_path:path}` and accepts
		 * slashes, so encoding the whole string would break multi-segment paths.
		 */
		getPresetAssetURL(
			presetId: string,
			path: string,
			size?: 'small' | 'medium' | 'large'
		): string {
			const url = `${getBaseURL()}/api/media/presets/${presetId}/${path}`;
			return size ? `${url}?size=${size}` : url;
		},

		/**
		 * Lists a generation's files with metadata (width/height/duration/fps/size)
		 * already persisted in the `files` table. Used by MediaSelect to resolve
		 * metadata for a saved field value that references generation-history
		 * media without its own metadata. Requires auth -
		 * the route checks generation ownership.
		 */
		async listGenerationMedia(
			generationId: string
		): Promise<APIResponse<{ generation_id: string; media_count: number; media: MediaFileInfo[] }>> {
			const response = await client.get(`/api/media/generations/${generationId}`);
			return response.data;
		},

		/**
		 * Best-effort metadata for an already-uploaded file, probed on demand
		 * since uploads have no DB row to resolve metadata from. Resolved
		 * server-side through the same path-containment-checked resolver the
		 * serving route uses.
		 */
		async getUploadInfo(filename: string): Promise<APIResponse<UploadInfo>> {
			const response = await client.get(`/api/media/uploads/${encodeURIComponent(filename)}/info`);
			return response.data;
		},

		/**
		 * Lists the current user's media-loader uploads, newest first.
		 * Scoped server-side to the authenticated user - there
		 * is no way to list another user's uploads.
		 */
		async listUploads(options: {
			mediaType?: 'image' | 'video' | 'audio';
			limit?: number;
			offset?: number;
		} = {}): Promise<APIResponse<UploadListResult>> {
			const params: Record<string, string | number> = {};
			if (options.mediaType) params.media_type = options.mediaType;
			if (options.limit !== undefined) params.limit = options.limit;
			if (options.offset !== undefined) params.offset = options.offset;

			const response = await client.get('/api/media/uploads', { params });
			return response.data;
		},

		/**
		 * Deletes one of the current user's uploads. 404s both when the
		 * filename doesn't exist and when it belongs to another user - the two
		 * cases are deliberately indistinguishable server-side.
		 */
		async deleteUpload(filename: string): Promise<APIResponse<{ filename: string; deleted: boolean }>> {
			const response = await client.delete(`/api/media/uploads/${encodeURIComponent(filename)}`);
			return response.data;
		},

		/**
		 * Stores a file and answers where it landed.
		 *
		 * Used for the painted inpainting mask, which is not a library resource
		 * a user browses but a generation input referenced by PATH on the
		 * `${name}_inpaint_mask` sibling channel. The MediaLoader field posts its
		 * own uploads over XHR instead - `fetch` reports no upload progress, and
		 * a 400 MB video behind a silent spinner is indistinguishable from a hung
		 * field. A mask is small enough not to need that.
		 */
		async uploadMedia(
			file: File,
			purpose?: 'user_upload' | 'derived_artifact'
		): Promise<APIResponse<UploadResult>> {
			const formData = new FormData();
			formData.append('file', file);
			if (purpose) formData.append('purpose', purpose);
			const response = await client.post('/api/media/upload', formData);
			return response.data;
		}
	};
}
