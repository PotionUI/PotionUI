import { describe, it, expect } from 'vitest';
import {
	hasPresetMedia,
	getPresetCover,
	presetAltText,
	exampleAltText,
	isVideoExample,
	fallbackIconForCategory
} from './presetMedia';

describe('hasPresetMedia', () => {
	it('is false when the preset is missing or has no media', () => {
		expect(hasPresetMedia(undefined)).toBe(false);
		expect(hasPresetMedia(null)).toBe(false);
		expect(hasPresetMedia({ media: undefined })).toBe(false);
	});

	it('is false when media is present but empty (no cover, empty gallery)', () => {
		expect(hasPresetMedia({ media: { gallery: [] } })).toBe(false);
	});

	it('is true when a cover is present', () => {
		expect(hasPresetMedia({ media: { cover: 'public/cover.png' } })).toBe(true);
	});

	it('is true when the gallery has at least one item', () => {
		expect(
			hasPresetMedia({ media: { gallery: [{ src: 'public/a.png' }] } })
		).toBe(true);
	});
});

describe('getPresetCover', () => {
	it('returns null when media is missing', () => {
		expect(getPresetCover(undefined)).toBeNull();
		expect(getPresetCover(null)).toBeNull();
	});

	it('returns null when media has no cover', () => {
		expect(getPresetCover({ gallery: [] })).toBeNull();
	});

	it('returns the raw cover path when present', () => {
		expect(getPresetCover({ cover: 'public/cover.png' })).toBe('public/cover.png');
	});
});

describe('presetAltText', () => {
	it('appends "preview" to the preset name', () => {
		expect(presetAltText('Z-Image')).toBe('Z-Image preview');
	});
});

describe('exampleAltText', () => {
	it('falls back to a generic example label when no caption is given', () => {
		expect(exampleAltText('Z-Image')).toBe('Z-Image example');
		expect(exampleAltText('Z-Image', null)).toBe('Z-Image example');
		expect(exampleAltText('Z-Image', undefined)).toBe('Z-Image example');
	});

	it('includes the caption when present', () => {
		expect(exampleAltText('Z-Image', 'Portrait, studio light')).toBe(
			'Z-Image example: Portrait, studio light'
		);
	});
});

describe('isVideoExample', () => {
	it('recognizes mp4 and webm as video, case-insensitively', () => {
		expect(isVideoExample({ src: 'public/clip.mp4' })).toBe(true);
		expect(isVideoExample({ src: 'public/clip.WEBM' })).toBe(true);
	});

	it('treats everything else as not video', () => {
		expect(isVideoExample({ src: 'public/cover.png' })).toBe(false);
		expect(isVideoExample({ src: 'public/cover.gif' })).toBe(false);
	});
});

describe('fallbackIconForCategory', () => {
	it('maps image and video categories to their glyphs', () => {
		expect(fallbackIconForCategory('image')).toBe('photo');
		expect(fallbackIconForCategory('video')).toBe('film');
	});

	it('uses the audio icon and the cube icon for 3d', () => {
		expect(fallbackIconForCategory('audio')).toBe('audio');
		expect(fallbackIconForCategory('3d')).toBe('cube');
	});

	it('falls back to layers for unknown or missing categories', () => {
		expect(fallbackIconForCategory('utility')).toBe('layers');
		expect(fallbackIconForCategory(undefined)).toBe('layers');
		expect(fallbackIconForCategory(null)).toBe('layers');
	});
});
