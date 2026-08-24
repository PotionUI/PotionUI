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

/**
 * One operation in a media edit. Mirrors the `EditOperation` union
 * (src/features/media/editing/dto.py); build these with the helpers in
 * `$lib/media/editors`, which clamp the geometry the way the server does.
 */
export type MediaEditOperation =
	| { type: 'crop'; x: number; y: number; width: number; height: number }
	| { type: 'resize'; width?: number; height?: number }
	| { type: 'rotate'; degrees: 90 | 180 | 270 }
	| { type: 'flip'; axis: 'horizontal' | 'vertical' }
	| { type: 'trim'; start_seconds: number; end_seconds: number };

/**
 * The library resource an edit produced. Mirrors `EditedMediaItem`
 * (src/features/media/editing/dto.py). `url` is a real served path, so an
 * `<img>` or `<video>` can render one directly.
 */
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
}

/** Mirrors `EditMediaResult` (src/features/media/editing/dto.py). */
export interface EditMediaResult {
	item: EditedMediaItem;
	replaced: boolean;
}

/** Mirrors the split endpoint's response (src/features/media/editing). */
export interface SplitMediaResult {
	items: EditedMediaItem[];
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
		},

		/**
		 * Crops / resizes / rotates / flips / trims one library resource.
		 *
		 * `mode: 'replace'` keeps the row - and so its tags and collection
		 * memberships - and swaps the file behind it; `'new'` leaves the
		 * original untouched and records a second resource. Either way the
		 * result is a real library resource with a served `url`.
		 *
		 * The geometry is validated and clamped server-side, so a 400 here means
		 * the client's own bounds check missed something.
		 */
		async editMediaItem(
			itemId: string,
			operations: MediaEditOperation[],
			mode: 'new' | 'replace' = 'new'
		): Promise<APIResponse<EditMediaResult>> {
			const response = await client.post(`/api/media/edit/${itemId}`, { operations, mode });
			return response.data;
		},

		/**
		 * Saves one frame of a video as a NEW image resource - never a replace,
		 * because a row that a form field or a collection was given as a video
		 * cannot become a still underneath them.
		 */
		async extractMediaFrame(
			itemId: string,
			timeSeconds: number
		): Promise<APIResponse<EditMediaResult>> {
			const response = await client.post(`/api/media/edit/${itemId}/frame`, {
				time_seconds: timeSeconds
			});
			return response.data;
		},

		/**
		 * Cuts one library resource into consecutive parts of `partSeconds` each,
		 * with a final short clip for whatever remainder is left. Unlike
		 * `editMediaItem`, this always produces NEW resources - the original is
		 * never modified, so there is no `mode` to choose here.
		 */
		async splitMediaItem(
			itemId: string,
			partSeconds: number
		): Promise<APIResponse<SplitMediaResult>> {
			const response = await client.post(`/api/media/split/${itemId}`, {
				part_seconds: partSeconds
			});
			return response.data;
		}
	};
}
