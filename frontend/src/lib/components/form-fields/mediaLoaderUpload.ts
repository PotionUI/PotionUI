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

export function postUpload(
	file: File,
	token: string | null,
	onProgress: (percent: number) => void
): Promise<{ data: UploadResponseData }> {
	return new Promise((resolve, reject) => {
		const request = new XMLHttpRequest();
		request.open('POST', '/api/media/upload');
		request.withCredentials = true;
		if (token) request.setRequestHeader('Authorization', `Bearer ${token}`);

		request.upload.onprogress = (event) => {
			if (event.lengthComputable && event.total > 0) {
				onProgress(Math.min(100, Math.round((event.loaded / event.total) * 100)));
			}
		};
		request.onload = () => {
			let parsed: { success?: boolean; data?: UploadResponseData; message?: string } | null = null;
			try {
				parsed = JSON.parse(request.responseText);
			} catch {
				parsed = null;
			}
			if (request.status >= 200 && request.status < 300 && parsed?.success && parsed?.data) {
				resolve({ data: parsed.data });
			} else {
				reject(new Error(parsed?.message || 'Failed to upload file'));
			}
		};
		request.onerror = () => reject(new Error('Failed to upload file'));

		const formData = new FormData();
		formData.append('file', file);
		request.send(formData);
	});
}
