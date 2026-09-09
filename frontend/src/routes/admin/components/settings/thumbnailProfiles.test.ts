import { describe, expect, it } from 'vitest';
import { buildThumbnailSettingsPayload, matchProfile, parseThumbnailSettings } from './thumbnailProfiles';
import type { ThumbnailProfiles } from '$lib/services/admin-api';

// Literal fixtures mirroring the documented profile contract - only this
// vitest helper is allowed to hardcode these; the panel always reads them
// from the API response.
const PROFILES: ThumbnailProfiles = {
	compact: {
		sizes: ['small'],
		video_fps: 8,
		video_seconds: 2,
		video_quality: 40,
		image_quality: 75,
		estimated_bytes: 1_000_000
	},
	balanced: {
		sizes: ['medium'],
		video_fps: 12,
		video_seconds: 3,
		video_quality: 50,
		image_quality: 85,
		estimated_bytes: 5_000_000
	},
	full: {
		sizes: ['small', 'medium', 'large'],
		video_fps: 24,
		video_seconds: 3,
		video_quality: 50,
		image_quality: 85,
		estimated_bytes: 20_000_000
	}
};

describe('matchProfile', () => {
	it('matches compact', () => {
		expect(matchProfile({ sizes: ['small'], video_fps: 8, video_seconds: 2, video_quality: 40, image_quality: 75 }, PROFILES)).toBe(
			'compact'
		);
	});

	it('matches balanced', () => {
		expect(
			matchProfile({ sizes: ['medium'], video_fps: 12, video_seconds: 3, video_quality: 50, image_quality: 85 }, PROFILES)
		).toBe('balanced');
	});

	it('matches full', () => {
		expect(
			matchProfile(
				{ sizes: ['small', 'medium', 'large'], video_fps: 24, video_seconds: 3, video_quality: 50, image_quality: 85 },
				PROFILES
			)
		).toBe('full');
	});

	it('matches full regardless of size order', () => {
		expect(
			matchProfile(
				{ sizes: ['large', 'small', 'medium'], video_fps: 24, video_seconds: 3, video_quality: 50, image_quality: 85 },
				PROFILES
			)
		).toBe('full');
	});

	it('falls back to custom when a size is added', () => {
		expect(
			matchProfile({ sizes: ['small', 'medium'], video_fps: 8, video_seconds: 2, video_quality: 40, image_quality: 75 }, PROFILES)
		).toBe('custom');
	});

	it('falls back to custom when a size is removed', () => {
		expect(matchProfile({ sizes: [], video_fps: 8, video_seconds: 2, video_quality: 40, image_quality: 75 }, PROFILES)).toBe(
			'custom'
		);
	});

	it('falls back to custom on any numeric deviation', () => {
		expect(matchProfile({ sizes: ['small'], video_fps: 9, video_seconds: 2, video_quality: 40, image_quality: 75 }, PROFILES)).toBe(
			'custom'
		);
	});
});

describe('parseThumbnailSettings', () => {
	it('extracts the five thumbnail keys from the flat settings map', () => {
		expect(
			parseThumbnailSettings({
				thumbnail_sizes: ['medium'],
				thumbnail_video_fps: 12,
				thumbnail_video_seconds: 3,
				thumbnail_video_quality: 50,
				thumbnail_image_quality: 85,
				unrelated_key: 'ignored'
			})
		).toEqual({ sizes: ['medium'], video_fps: 12, video_seconds: 3, video_quality: 50, image_quality: 85 });
	});

	it('drops unknown size values and defaults missing fields', () => {
		expect(parseThumbnailSettings({ thumbnail_sizes: ['medium', 'huge', 42] })).toEqual({
			sizes: ['medium'],
			video_fps: 0,
			video_seconds: 0,
			video_quality: 0,
			image_quality: 0
		});
	});

	it('defaults to an empty state when nothing is present', () => {
		expect(parseThumbnailSettings({})).toEqual({
			sizes: [],
			video_fps: 0,
			video_seconds: 0,
			video_quality: 0,
			image_quality: 0
		});
	});
});

describe('buildThumbnailSettingsPayload', () => {
	it('round-trips through parseThumbnailSettings', () => {
		const values = { sizes: ['small', 'large'], video_fps: 24, video_seconds: 3, video_quality: 50, image_quality: 85 };
		expect(parseThumbnailSettings(buildThumbnailSettingsPayload(values))).toEqual(values);
	});

	it('uses the thumbnail_ prefixed keys the batch settings endpoint expects', () => {
		expect(
			buildThumbnailSettingsPayload({ sizes: ['small'], video_fps: 8, video_seconds: 2, video_quality: 40, image_quality: 75 })
		).toEqual({
			thumbnail_sizes: ['small'],
			thumbnail_video_fps: 8,
			thumbnail_video_seconds: 2,
			thumbnail_video_quality: 40,
			thumbnail_image_quality: 75
		});
	});
});
