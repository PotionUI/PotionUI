import type { PresetGalleryItem, PresetInfo, PresetMedia } from '$lib/types/api';

/** True when the preset has a cover or at least one gallery item to show. */
export function hasPresetMedia(preset: Pick<PresetInfo, 'media'> | undefined | null): boolean {
	const media = preset?.media;
	if (!media) return false;
	return !!media.cover || !!(media.gallery && media.gallery.length > 0);
}

/** Returns the cover path (raw, relative) or null if the preset has none. */
export function getPresetCover(media: PresetMedia | undefined | null): string | null {
	return media?.cover || null;
}

/** Alt text for a preset's thumbnail/cover image. */
export function presetAltText(presetName: string): string {
	return `${presetName} preview`;
}

/** Alt text for a single gallery example. */
export function exampleAltText(presetName: string, caption?: string | null): string {
	return caption ? `${presetName} example: ${caption}` : `${presetName} example`;
}

/** True when a gallery entry's `src` looks like a video file. */
export function isVideoExample(item: Pick<PresetGalleryItem, 'src'>): boolean {
	return /\.(mp4|webm)$/i.test(item.src);
}

/**
 * Maps a preset category to the Icon name used as the fallback glyph when no
 * cover/media is available. Falls back to `layers` (the admin empty-state icon)
 * for categories without a dedicated glyph (e.g. audio).
 */
export function fallbackIconForCategory(category: string | undefined | null): string {
	switch (category) {
		case 'image':
			return 'photo';
		case 'video':
			return 'film';
		case 'audio':
			return 'audio';
		case '3d':
			return 'cube';
		default:
			return 'layers';
	}
}
