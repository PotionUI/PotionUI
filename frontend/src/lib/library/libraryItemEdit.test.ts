import { describe, it, expect } from 'vitest';
import { isSameLibraryRow, libraryItemFromEdit, mergeEditedLibraryItem } from './libraryItemEdit';
import type { LibraryItem } from '$lib/services/api/library';
import type { EditedMediaItem } from '$lib/services/api/media';

const existing: LibraryItem = {
	id: 'row-1',
	filename: 'old-name.png',
	original_filename: 'portrait.png',
	media_type: 'image',
	mime_type: 'image/png',
	url: '/api/media/uploads/old-name.png',
	width: 1024,
	height: 1536,
	size: 900000,
	created_at: '2026-08-01T00:00:00',
	tags: [{ id: 'tag-1', name: 'keepers' }]
};

const edited: EditedMediaItem = {
	id: 'row-1',
	filename: 'new-name.png',
	original_filename: 'portrait.png',
	media_type: 'image',
	mime_type: 'image/png',
	url: '/api/media/uploads/new-name.png',
	width: 832,
	height: 1216,
	size: 480000,
	created_at: '2026-08-01T00:00:00'
};

describe('mergeEditedLibraryItem', () => {
	it('takes the new url, so the browser cannot serve the pre-edit bytes', () => {
		expect(mergeEditedLibraryItem(existing, edited).url).toBe('/api/media/uploads/new-name.png');
		expect(mergeEditedLibraryItem(existing, edited).filename).toBe('new-name.png');
	});

	it('takes the new dimensions', () => {
		const merged = mergeEditedLibraryItem(existing, edited);
		expect(merged.width).toBe(832);
		expect(merged.height).toBe(1216);
		expect(merged.size).toBe(480000);
	});

	it('keeps the row id and its tags — a replace changes neither', () => {
		const merged = mergeEditedLibraryItem(existing, edited);
		expect(merged.id).toBe('row-1');
		expect(merged.tags).toEqual(existing.tags);
	});

	it('keeps fields the edit does not report', () => {
		expect(mergeEditedLibraryItem(existing, edited).created_at).toBe('2026-08-01T00:00:00');
	});

	it('clears a duration the edit no longer reports rather than keeping a stale one', () => {
		// A still lifted out of a clip has no duration; carrying the clip's
		// forward would label the image with a length it does not have.
		const clip: LibraryItem = { ...existing, media_type: 'video', duration_seconds: 8.4, fps: 24 };
		const still: EditedMediaItem = { ...edited, media_type: 'image' };
		const merged = mergeEditedLibraryItem(clip, still);
		expect(merged.duration_seconds).toBeUndefined();
		expect(merged.fps).toBeUndefined();
		expect(merged.media_type).toBe('image');
	});

	it('falls back to the existing display name when the edit reports none', () => {
		const merged = mergeEditedLibraryItem(existing, { ...edited, original_filename: undefined });
		expect(merged.original_filename).toBe('portrait.png');
	});

	it('takes the sound the result reports, so a replaced clip does not keep its old value', () => {
		const talking: LibraryItem = { ...existing, media_type: 'video', has_audio: true };
		const silent: LibraryItem = { ...existing, media_type: 'video', has_audio: false };
		const video: EditedMediaItem = { ...edited, media_type: 'video' };
		expect(mergeEditedLibraryItem(talking, { ...video, has_audio: false }).has_audio).toBe(false);
		expect(mergeEditedLibraryItem(silent, { ...video, has_audio: true }).has_audio).toBe(true);
		expect(mergeEditedLibraryItem(silent, { ...video, has_audio: null }).has_audio).toBeNull();
		expect(mergeEditedLibraryItem(silent, video).has_audio).toBeNull();
		expect(libraryItemFromEdit({ ...video, has_audio: false }).has_audio).toBe(false);
	});
});

describe('mergeEditedLibraryItem thumbnails', () => {
	const withThumbs: LibraryItem = {
		...existing,
		thumbnail_small: '/api/media/uploads/old-name_small.webp',
		thumbnail_medium: '/api/media/uploads/old-name_medium.webp',
		thumbnail_large: '/api/media/uploads/old-name_large.webp'
	};

	it('does not keep the old thumbnails, which point at the file a replace deleted', () => {
		const merged = mergeEditedLibraryItem(withThumbs, edited);

		expect(merged.thumbnail_small).toBeUndefined();
		expect(merged.thumbnail_medium).toBeUndefined();
		expect(merged.thumbnail_large).toBeUndefined();
	});

	it('takes the thumbnails of the result when the backend provides them', () => {
		const merged = mergeEditedLibraryItem(withThumbs, {
			...edited,
			thumbnail_small: '/api/media/uploads/new-name_small.webp',
			thumbnail_medium: '/api/media/uploads/new-name_medium.webp',
			thumbnail_large: '/api/media/uploads/new-name_large.webp'
		});

		expect(merged.thumbnail_small).toBe('/api/media/uploads/new-name_small.webp');
		expect(merged.thumbnail_medium).toBe('/api/media/uploads/new-name_medium.webp');
		expect(merged.thumbnail_large).toBe('/api/media/uploads/new-name_large.webp');
	});

	it('takes only the sizes the result provides and clears the rest', () => {
		const merged = mergeEditedLibraryItem(withThumbs, {
			...edited,
			thumbnail_medium: '/api/media/uploads/new-name_medium.webp'
		});

		expect(merged.thumbnail_medium).toBe('/api/media/uploads/new-name_medium.webp');
		expect(merged.thumbnail_small).toBeUndefined();
		expect(merged.thumbnail_large).toBeUndefined();
	});

	it('treats a null thumbnail like a missing one', () => {
		const merged = mergeEditedLibraryItem(withThumbs, { ...edited, thumbnail_medium: null });

		expect(merged.thumbnail_medium).toBeUndefined();
	});

	it('still keeps the tags and the creation time of the row', () => {
		const merged = mergeEditedLibraryItem(withThumbs, edited);

		expect(merged.tags).toEqual(existing.tags);
		expect(merged.created_at).toBe(existing.created_at);
	});
});

describe('libraryItemFromEdit', () => {
	it('carries the thumbnails a result brings so its card draws the small file', () => {
		const row = libraryItemFromEdit({ ...edited, thumbnail_medium: '/api/media/uploads/new_medium.webp' });

		expect(row.thumbnail_medium).toBe('/api/media/uploads/new_medium.webp');
	});

	it('has none when the result brings none, so the card falls back to the file itself', () => {
		expect(libraryItemFromEdit(edited).thumbnail_medium).toBeUndefined();
	});
});

describe('isSameLibraryRow', () => {
	it('recognises a replace and a save-as-new apart', () => {
		expect(isSameLibraryRow(existing, edited)).toBe(true);
		expect(isSameLibraryRow(existing, { ...edited, id: 'row-2' })).toBe(false);
	});
});
