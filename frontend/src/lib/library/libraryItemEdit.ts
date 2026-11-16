/**
 * Folding an edit's result back into the loaded library page.
 *
 * A REPLACE keeps the row - and so its id, its tags and its collection
 * memberships - and swaps the file behind it, which means the loaded page can
 * be patched in place. It also lands on a NEW filename and url, deliberately:
 * that is what stops a browser serving the pre-edit bytes out of its cache. A
 * patch that kept the old url would show the user the picture they just
 * changed.
 *
 * A save-as-new is a different row entirely and cannot be patched into a page
 * it may not even belong on - that is a reload, not a merge.
 */

import type { LibraryItem } from '$lib/services/api/library';
import type { EditedMediaItem } from '$lib/services/api/media';

/**
 * The existing item with the edit's media fields written over it. Tags are the
 * item's, never the result's: the API does not return them because an edit
 * cannot change them.
 */
export type EditedLibraryResult = EditedMediaItem & {
	thumbnail_small?: string | null;
	thumbnail_medium?: string | null;
	thumbnail_large?: string | null;
};

function thumbnailsOf(edited: EditedLibraryResult) {
	return {
		thumbnail_small: edited.thumbnail_small ?? undefined,
		thumbnail_medium: edited.thumbnail_medium ?? undefined,
		thumbnail_large: edited.thumbnail_large ?? undefined
	};
}

export function mergeEditedLibraryItem(
	existing: LibraryItem,
	edited: EditedLibraryResult
): LibraryItem {
	return {
		...existing,
		...thumbnailsOf(edited),
		filename: edited.filename,
		original_filename: edited.original_filename ?? existing.original_filename,
		media_type: edited.media_type || existing.media_type,
		mime_type: edited.mime_type ?? existing.mime_type,
		url: edited.url,
		width: edited.width,
		height: edited.height,
		duration_seconds: edited.duration_seconds,
		fps: edited.fps,
		size: edited.size,
		has_audio: edited.has_audio ?? null,
		tags: existing.tags
	};
}

/** True when the edit landed on this row rather than beside it. */
export function isSameLibraryRow(item: LibraryItem, edited: EditedMediaItem): boolean {
	return item.id === edited.id;
}

export function libraryItemFromEdit(edited: EditedLibraryResult): LibraryItem {
	return {
		...thumbnailsOf(edited),
		id: edited.id,
		filename: edited.filename,
		original_filename: edited.original_filename,
		media_type: edited.media_type,
		mime_type: edited.mime_type,
		url: edited.url,
		width: edited.width,
		height: edited.height,
		duration_seconds: edited.duration_seconds,
		fps: edited.fps,
		size: edited.size,
		created_at: edited.created_at,
		has_audio: edited.has_audio ?? null,
		tags: [],
		is_favorite: false
	};
}
