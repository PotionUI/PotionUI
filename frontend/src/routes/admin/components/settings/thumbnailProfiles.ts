/**
 * Pure normalize/build/match helpers for `settings/ThumbnailsPanel.svelte`,
 * split out so they're testable without mounting the component. Mirrors the
 * `fileStorageSettings.ts` idiom next to it.
 */
import type { ThumbnailProfileName, ThumbnailProfileSettings, ThumbnailProfiles } from '$lib/services/admin-api';

export type ThumbnailValues = ThumbnailProfileSettings;

const KNOWN_SIZES = ['small', 'medium', 'large'];
const PROFILE_ORDER: ThumbnailProfileName[] = ['compact', 'balanced', 'full'];

function asNumber(value: unknown): number {
	return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

function asSizes(value: unknown): string[] {
	if (!Array.isArray(value)) return [];
	return value.filter((v): v is string => typeof v === 'string' && KNOWN_SIZES.includes(v));
}

function sameSizes(a: string[], b: string[]): boolean {
	if (a.length !== b.length) return false;
	const sorted = [...b].sort();
	return [...a].sort().every((v, i) => v === sorted[i]);
}

/** `GET /api/settings` returns the whole flat settings map - this picks out
 * and types only the thumbnail-relevant keys. */
export function parseThumbnailSettings(raw: Record<string, unknown>): ThumbnailValues {
	return {
		sizes: asSizes(raw.thumbnail_sizes),
		video_fps: asNumber(raw.thumbnail_video_fps),
		video_seconds: asNumber(raw.thumbnail_video_seconds),
		video_quality: asNumber(raw.thumbnail_video_quality),
		image_quality: asNumber(raw.thumbnail_image_quality)
	};
}

/** The `PUT /api/settings` batch body for the current form state. */
export function buildThumbnailSettingsPayload(values: ThumbnailValues): Record<string, unknown> {
	return {
		thumbnail_sizes: values.sizes,
		thumbnail_video_fps: values.video_fps,
		thumbnail_video_seconds: values.video_seconds,
		thumbnail_video_quality: values.video_quality,
		thumbnail_image_quality: values.image_quality
	};
}

/** Which named profile (if any) the current values exactly match - sizes
 * compared order-insensitively, every other field exactly. Any deviation
 * (including a size added/removed) falls through to 'custom'. */
export function matchProfile(values: ThumbnailValues, profiles: ThumbnailProfiles): ThumbnailProfileName | 'custom' {
	for (const name of PROFILE_ORDER) {
		const p = profiles[name];
		if (!p) continue;
		if (
			sameSizes(values.sizes, p.sizes) &&
			values.video_fps === p.video_fps &&
			values.video_seconds === p.video_seconds &&
			values.video_quality === p.video_quality &&
			values.image_quality === p.image_quality
		) {
			return name;
		}
	}
	return 'custom';
}
