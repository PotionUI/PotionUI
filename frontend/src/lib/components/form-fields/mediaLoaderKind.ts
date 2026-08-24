/**
 * What kind of media a candidate is, from whatever the source gave us.
 *
 * A file picked from disk carries a MIME type; a value restored from a saved
 * session carries only a path; a generation file carries a `file_type` in
 * either casing (see `$lib/utils/fileType`). The field had this extension list
 * inlined at four call sites, which is how an audio pick could arrive typed as
 * `null` and render as an image tile.
 */

import type { MediaKind } from './mediaLoaderConfig';

const IMAGE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'avif'];
const VIDEO_EXTENSIONS = ['mp4', 'webm', 'avi', 'mov', 'mkv', 'm4v'];
const AUDIO_EXTENSIONS = ['mp3', 'wav', 'flac', 'ogg', 'm4a', 'aac', 'opus'];

export function kindFromMimeType(mimeType: unknown): MediaKind | null {
	if (typeof mimeType !== 'string') return null;
	const value = mimeType.trim().toLowerCase();
	if (value.startsWith('image/')) return 'image';
	if (value.startsWith('video/')) return 'video';
	if (value.startsWith('audio/')) return 'audio';
	return null;
}

export function kindFromFilename(filename: unknown): MediaKind | null {
	if (typeof filename !== 'string') return null;
	const ext = filename.split('?')[0].split('.').pop()?.toLowerCase();
	if (!ext) return null;
	if (IMAGE_EXTENSIONS.includes(ext)) return 'image';
	if (VIDEO_EXTENSIONS.includes(ext)) return 'video';
	if (AUDIO_EXTENSIONS.includes(ext)) return 'audio';
	return null;
}

/** A declared `type`/`file_type`/`media_type`, in any casing. */
export function kindFromDeclared(declared: unknown): MediaKind | null {
	if (typeof declared !== 'string') return null;
	const value = declared.trim().toLowerCase();
	if (value === 'image' || value === 'video' || value === 'audio') return value;
	return kindFromMimeType(value);
}

/**
 * The kind of a stored media item: its declared type first, then its name or
 * either path convention (uploads are CWD-relative, history entries are
 * storage-root-relative — both end in the filename we need).
 */
export function kindOfMediaItem(item: unknown): MediaKind | null {
	if (typeof item === 'string') return kindFromFilename(item);
	if (!item || typeof item !== 'object') return null;
	const record = item as Record<string, unknown>;

	const declared = kindFromDeclared(record.type ?? record.file_type ?? record.media_type);
	if (declared) return declared;

	for (const key of ['name', 'relative_path', 'path', 'url'] as const) {
		const kind = kindFromFilename(record[key]);
		if (kind) return kind;
	}
	return null;
}
