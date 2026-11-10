export const COVER_IMAGE_MAX_BYTES = 10 * 1024 * 1024;
export const COVER_VIDEO_MAX_BYTES = 1.5 * 1024 * 1024;

export const COVER_ACCEPT = 'image/png,image/jpeg,image/webp,video/mp4,video/webm';

const IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);
const VIDEO_TYPES = new Set(['video/mp4', 'video/webm']);

export interface CoverMediaResult {
	file: File | null;
	error: string | null;
}

export function validateCoverFile(file: { type: string; size: number }): string | null {
	if (IMAGE_TYPES.has(file.type)) {
		return file.size > COVER_IMAGE_MAX_BYTES ? 'Cover images must be 10 MB or smaller.' : null;
	}
	if (VIDEO_TYPES.has(file.type)) {
		return file.size > COVER_VIDEO_MAX_BYTES ? 'Cover videos must be 1.5 MB or smaller.' : null;
	}
	return 'Choose a PNG, JPG or WEBP image, or an MP4 or WEBM video.';
}

export async function resolveCoverFileFromMediaItem(
	item: { url: string; name?: string },
	fetchImpl: typeof fetch = fetch
): Promise<CoverMediaResult> {
	let response: Response;
	try {
		response = await fetchImpl(item.url);
	} catch {
		return { file: null, error: 'Could not load the selected file.' };
	}
	if (!response.ok) return { file: null, error: 'Could not load the selected file.' };
	const blob = await response.blob();
	const file = new File([blob], item.name || 'cover', { type: blob.type });
	const error = validateCoverFile(file);
	return error ? { file: null, error } : { file, error: null };
}

export function mediaPickItem(value: unknown): { url: string; name?: string } | null {
	if (!value || typeof value !== 'object') return null;
	const item = value as { url?: unknown; name?: unknown };
	if (typeof item.url !== 'string' || !item.url) return null;
	return { url: item.url, name: typeof item.name === 'string' ? item.name : undefined };
}
