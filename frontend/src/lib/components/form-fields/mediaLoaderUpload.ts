/**
 * Turns a completed `/api/media/upload` response into the media item shape
 * this field persists.
 *
 * The response already carries a durable, server-served `url`
 * (`/api/media/uploads/{filename}` - src/features/media/manager.py). The
 * upload has already finished by the time this runs, so there is no
 * first-paint win in minting a `URL.createObjectURL` blob instead - and a
 * blob would be a bug: it only resolves for the life of the document, so a
 * page refresh 404s every stored reference image.
 */

export interface UploadResponseData {
	path: string;
	relative_path: string;
	url: string;
	width?: number | null;
	height?: number | null;
	duration_seconds?: number | null;
	fps?: number | null;
	size?: number | null;
}

export interface UploadedMediaItem {
	path: string;
	relative_path: string;
	url: string;
	name: string;
	type: 'image' | 'video' | 'audio';
	metadata: {
		width?: number | null;
		height?: number | null;
		duration_seconds?: number | null;
		fps?: number | null;
		size?: number | null;
	};
}

/**
 * Filename synthesized for a clipboard-pasted image, which usually carries
 * none of its own. Shared so every paste path - this field's own, and the
 * chat composer's - names the file identically.
 */
export function pastedImageFileName(): string {
	return `pasted-image-${Date.now()}.png`;
}

export function buildUploadedMediaItem(
	data: UploadResponseData,
	fileName: string,
	type: 'image' | 'video' | 'audio'
): UploadedMediaItem {
	return {
		path: data.path,
		relative_path: data.relative_path,
		url: data.url,
		name: fileName,
		type,
		metadata: {
			width: data.width,
			height: data.height,
			duration_seconds: data.duration_seconds,
			fps: data.fps,
			size: data.size
		}
	};
}
