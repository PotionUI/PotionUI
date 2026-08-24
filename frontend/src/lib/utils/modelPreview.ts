/**
 * Shared helpers for a model's admin-set preview media.
 *
 * An admin can upload an image, video, or audio clip in the model-details modal
 * (via MediaLoader) and pin it as the model's preview. It is stored on the model
 * as `preview_media` ({ url, type, name?, relative_path? }) and MUST take
 * precedence over any marketplace-supplied preview files.
 *
 * Rather than teach every render site about `preview_media`, `filesWithPreview`
 * folds the admin preview into the front of the model's `files` array as a
 * synthetic entry. Existing sites that read `model.files` - cards, pickers, the
 * media viewer - then surface it first with no bespoke preview logic. Only
 * display lists should route through this; file-management views (e.g.
 * ModelFilesCard) must keep reading the real `model.files`.
 *
 * Render sites differ in what they can display. Cards and pickers render every
 * file through an `<img>` (they rely on video files carrying an image
 * `thumbnail_*`), so only an IMAGE preview is safe to inject there - a video or
 * audio URL in an `<img>` would break. The media-viewer modals render `<video>`
 * too, so they opt in via `{ allowVideo: true }`. Audio previews only ever
 * surface in the model-details preview card itself, never in this file list.
 */

/** The stored preview on a model: a servable /api/media/files/<id> URL + its type. */
export interface ModelPreviewMedia {
	url: string;
	type: 'image' | 'video' | 'audio';
	name?: string;
	file_id?: string;
}

/** What the frontend sends to set a preview: a storage-relative source path + type. */
export interface ModelPreviewInput {
	source_path: string;
	type: 'image' | 'video' | 'audio';
	name?: string;
}

/**
 * One row of a model's preview list. `position` 0 is the primary
 * preview - the backend mirrors it onto `model.preview_media`, so every
 * existing display site (cards, pickers, `filesWithPreview` below) keeps
 * reading that column unchanged and needs no awareness of the list.
 */
export interface ModelPreviewMediaItem {
	id: string;
	file_id?: string | null;
	url: string;
	type: 'image' | 'video' | 'audio';
	name?: string | null;
	position: number;
}

/** The synthetic file id used for an injected admin preview entry. */
export const PREVIEW_FILE_ID = '__admin_preview__';

export function getModelPreview(
	model: { preview_media?: ModelPreviewMedia | null } | null | undefined
): ModelPreviewMedia | null {
	const preview = model?.preview_media;
	if (!preview || typeof preview !== 'object' || !preview.url || !preview.type) {
		return null;
	}
	return preview;
}

/**
 * A model's `files` array with any admin-set preview prepended as a synthetic,
 * display-only entry. Returns the original array (or []) when no preview applies.
 *
 * By default only an image preview is injected (safe for `<img>`-only cards and
 * pickers). Pass `{ allowVideo: true }` from a site that renders `<video>` to
 * also inject a video preview. Audio previews are never injected here.
 */
export function filesWithPreview(
	model: { preview_media?: ModelPreviewMedia | null; files?: any[] } | null | undefined,
	options: { allowVideo?: boolean } = {}
): any[] {
	const files = model?.files ?? [];
	const preview = getModelPreview(model);
	if (!preview) return files;

	const injectable = preview.type === 'image' || (preview.type === 'video' && !!options.allowVideo);
	if (!injectable) return files;

	// Image previews are served through /api/media/files/<id>, which resizes on
	// `?size=`. Point the thumbnails at the sized variants so the sites that read
	// `thumbnail_small` directly (LoRA/model pickers) fetch a small image, not the
	// full-res original. (Cards and the media viewer append their own `?size=` to
	// `url`.) A video keeps no thumbnail - viewers render it via <video>.
	const isImage = preview.type === 'image';
	const previewEntry = {
		id: PREVIEW_FILE_ID,
		file_type: preview.type,
		url: preview.url,
		thumbnail_small: isImage ? `${preview.url}?size=small` : undefined,
		thumbnail_medium: isImage ? `${preview.url}?size=medium` : undefined,
		thumbnail_large: isImage ? `${preview.url}?size=large` : undefined,
		// Sort ahead of provider files wherever display_order drives ordering.
		display_order: -1,
		is_admin_preview: true
	};

	return [previewEntry, ...files];
}

/**
 * The file-shaped view of a model's *full* ordered preview list (all
 * items from `GET /api/models/{id}/previews`, not just the position-0 primary
 * that `filesWithPreview` folds in from the legacy mirror). Sites that already
 * fetch the list - the full-page model view, the user-facing details modal -
 * prepend this ahead of `model.files` (provider-supplied images) instead of
 * calling `filesWithPreview`, so every admin-uploaded preview shows, not just
 * the first.
 *
 * Same per-site opt-in as `filesWithPreview`: only inject video entries when
 * the render site plays `<video>` (`{ allowVideo: true }`).
 */
/**
 * A media file's displayable thumbnail URL: a video's own `thumbnail_medium`,
 * or an `/api/media/files/<id>` image URL resized via `?size=`. Falls back to
 * the file's raw `url`. Shared by every card/picker that renders a model's
 * media grid (previously duplicated verbatim in ModelCard.svelte and
 * ModelAssignmentPicker.svelte).
 */
export function mediaFileThumbnailUrl(file: { file_type?: string; url?: string; thumbnail_medium?: string } | null | undefined): string {
	if (!file) return '';
	if (file.file_type === 'video' && file.thumbnail_medium) {
		return file.thumbnail_medium;
	}
	if (file.url && file.url.includes('/api/media/files/')) {
		return file.url.includes('?') ? `${file.url}&size=medium` : `${file.url}?size=medium`;
	}
	return file.url || '';
}

export function previewItemsAsFiles(
	previews: ModelPreviewMediaItem[] | null | undefined,
	options: { allowVideo?: boolean } = {}
): any[] {
	if (!previews || previews.length === 0) return [];

	return [...previews]
		.sort((a, b) => a.position - b.position)
		.filter((item) => item.type === 'image' || (item.type === 'video' && !!options.allowVideo))
		.map((item, index) => {
			const isImage = item.type === 'image';
			return {
				id: item.id,
				file_type: item.type,
				url: item.url,
				thumbnail_small: isImage ? `${item.url}?size=small` : undefined,
				thumbnail_medium: isImage ? `${item.url}?size=medium` : undefined,
				thumbnail_large: isImage ? `${item.url}?size=large` : undefined,
				// Sort ahead of provider files, in preview order.
				display_order: -1000 + index,
				is_admin_preview: true
			};
		});
}
