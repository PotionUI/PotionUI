/**
 * Turning an edited library resource into the media item this field persists.
 *
 * Companion to `buildUploadedMediaItem`: same output shape, different input.
 * An edit that went through the API comes back as a library resource
 * (`EditedMediaItem`) rather than an upload response, and the field's value has
 * to be indistinguishable either way - `parseMediaLocator`, the acceptance
 * gate, the metadata cache and every downstream consumer all read the same six
 * fields.
 *
 * `relative_path` is rebuilt from `filename` rather than taken from `url`: the
 * stored convention is `uploads/<filename>` (see `handleSelectFromUpload`), and
 * `url` is a route, not a path. A REPLACE lands on a new filename - that is what
 * stops a browser serving the pre-edit bytes out of its cache - so the value has
 * to be rewritten after one, not left pointing at the old name.
 */

import type { EditedMediaItem } from '$lib/services/api/media';
import type { MediaKind } from './mediaLoaderConfig';
import type { UploadedMediaItem } from './mediaLoaderUpload';

function kindOf(mediaType: string | undefined): MediaKind {
	if (mediaType === 'video' || mediaType === 'audio') return mediaType;
	return 'image';
}

export function buildEditedMediaItem(item: EditedMediaItem): UploadedMediaItem {
	const relativePath = `uploads/${item.filename}`;
	return {
		path: relativePath,
		relative_path: relativePath,
		url: item.url,
		name: item.original_filename || item.filename,
		type: kindOf(item.media_type),
		metadata: {
			width: item.width ?? null,
			height: item.height ?? null,
			duration_seconds: item.duration_seconds ?? null,
			fps: item.fps ?? null,
			size: item.size ?? null
		}
	};
}
