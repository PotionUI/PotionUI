import { describe, it, expect } from 'vitest';
import { isSameLibraryRow, mergeEditedLibraryItem } from './libraryItemEdit';
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
});

describe('isSameLibraryRow', () => {
	it('recognises a replace and a save-as-new apart', () => {
		expect(isSameLibraryRow(existing, edited)).toBe(true);
		expect(isSameLibraryRow(existing, { ...edited, id: 'row-2' })).toBe(false);
	});
});
