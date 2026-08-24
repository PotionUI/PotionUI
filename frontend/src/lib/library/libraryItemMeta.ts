/**
 * Presentation rules for a library item (an `uploads` row - see
 * `src/features/library/dto.py`). A library item carries no generation
 * metadata, so its card leans on the file itself: name, media kind, dimensions.
 */

import { clampAspect } from '$lib/utils/justifiedLayout';
import { formatBytes, formatSeconds } from '$lib/utils/format';

export interface LibraryItemLike {
	original_filename?: string | null;
	filename?: string;
	media_type?: string;
	url?: string;
	thumbnail_small?: string | null;
	thumbnail_medium?: string | null;
	thumbnail_large?: string | null;
	width?: number | null;
	height?: number | null;
	duration_seconds?: number | null;
	fps?: number | null;
	size?: number | null;
}

/**
 * The grid tile's media source: the medium thumbnail when the item has one,
 * the full original otherwise. Older upload rows predate thumbnail
 * generation entirely, so the fallback is permanent, not a migration window.
 */
export function libraryItemGridSrc(item: LibraryItemLike): string | undefined {
	return item.thumbnail_medium ?? item.url;
}

/**
 * `filename` is the on-disk uuid the upload was stored under, so it is never a
 * display name - fall back to a generic label rather than showing it.
 */
export function libraryItemDisplayName(item: LibraryItemLike): string {
	const name = (item.original_filename ?? '').trim();
	return name || 'Untitled';
}

export function libraryItemIcon(mediaType?: string): 'image' | 'video' | 'audio' {
	const kind = (mediaType ?? '').toLowerCase();
	if (kind === 'video') return 'video';
	if (kind === 'audio') return 'audio';
	return 'image';
}

/**
 * Aspect for the justified grid. Audio has no intrinsic box and older upload
 * rows can be missing dimensions entirely, so both land on a square tile.
 */
export function libraryItemAspect(item: LibraryItemLike): number {
	const { width, height } = item;
	if (typeof width === 'number' && typeof height === 'number' && width > 0 && height > 0) {
		return clampAspect(width / height);
	}
	return 1;
}

/**
 * Secondary metadata, highest value first, so a caller that truncates drops the
 * least useful part. Dimensions are deliberately excluded - they have their own
 * lane on the card.
 */
export function libraryItemMetaParts(item: LibraryItemLike): string[] {
	const parts: string[] = [];
	const kind = (item.media_type ?? '').toLowerCase();

	if ((kind === 'video' || kind === 'audio') && typeof item.duration_seconds === 'number') {
		parts.push(formatSeconds(item.duration_seconds));
	}
	if (kind === 'video' && typeof item.fps === 'number' && item.fps > 0) {
		parts.push(`${Math.round(item.fps)}fps`);
	}
	if (typeof item.size === 'number' && item.size > 0) {
		parts.push(formatBytes(item.size));
	}
	return parts;
}

export type LibraryCardAction = 'view' | 'download' | 'delete';

/**
 * The library card's counterpart to `actionsForCount` in
 * `generationCardChrome.ts`: the same width buckets, minus the actions an
 * upload has no state for (an upload cannot be favorited or rated). Delete is
 * the one that must survive down to the smallest tile.
 */
export function libraryActionsForCount(count: number): LibraryCardAction[] {
	if (count >= 3) return ['view', 'download', 'delete'];
	if (count === 2) return ['view', 'delete'];
	return ['delete'];
}
