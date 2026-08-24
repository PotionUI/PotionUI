import { describe, it, expect } from 'vitest';
import {
	libraryActionsForCount,
	libraryItemAspect,
	libraryItemDisplayName,
	libraryItemGridSrc,
	libraryItemIcon,
	libraryItemMetaParts
} from './libraryItemMeta';

describe('libraryItemDisplayName', () => {
	it('uses the original filename', () => {
		expect(libraryItemDisplayName({ original_filename: 'cat.png' })).toBe('cat.png');
	});

	it('never falls back to the on-disk uuid name', () => {
		const name = libraryItemDisplayName({
			original_filename: null,
			filename: '0d3f8e2a-1111-2222-3333-444455556666.png'
		});
		expect(name).toBe('Untitled');
	});

	it('treats a blank original filename as missing', () => {
		expect(libraryItemDisplayName({ original_filename: '   ' })).toBe('Untitled');
	});
});

describe('libraryItemIcon', () => {
	it('maps each media type', () => {
		expect(libraryItemIcon('video')).toBe('video');
		expect(libraryItemIcon('audio')).toBe('audio');
		expect(libraryItemIcon('image')).toBe('image');
	});

	it('falls back to the image icon for an unknown type', () => {
		expect(libraryItemIcon(undefined)).toBe('image');
	});
});

describe('libraryItemAspect', () => {
	it('uses the item dimensions', () => {
		expect(libraryItemAspect({ width: 1024, height: 512 })).toBe(2);
	});

	it('is square when dimensions are missing', () => {
		expect(libraryItemAspect({ media_type: 'audio' })).toBe(1);
	});

	it('is square when only one dimension is present', () => {
		expect(libraryItemAspect({ width: 1024 })).toBe(1);
	});

	it('clamps an extreme panorama so it cannot hog a row', () => {
		expect(libraryItemAspect({ width: 4000, height: 200 })).toBeLessThanOrEqual(2.6);
	});
});

describe('libraryItemMetaParts', () => {
	it('orders duration, fps then size', () => {
		const parts = libraryItemMetaParts({
			media_type: 'video',
			duration_seconds: 4,
			fps: 24,
			size: 2048
		});
		expect(parts).toEqual(['4.0s', '24fps', '2 KB']);
	});

	it('omits duration for an image', () => {
		expect(libraryItemMetaParts({ media_type: 'image', duration_seconds: 4, size: 1024 })).toEqual([
			'1 KB'
		]);
	});

	it('keeps duration for audio but not fps', () => {
		expect(libraryItemMetaParts({ media_type: 'audio', duration_seconds: 4, fps: 24 })).toEqual([
			'4.0s'
		]);
	});

	it('never reports dimensions - the card gives those their own lane', () => {
		expect(libraryItemMetaParts({ media_type: 'image', width: 512, height: 512 })).toEqual([]);
	});
});

describe('libraryItemGridSrc', () => {
	it('prefers the medium thumbnail when the item has one', () => {
		expect(
			libraryItemGridSrc({
				url: '/api/media/uploads/abc.png',
				thumbnail_medium: '/api/media/uploads/abc.png?size=medium'
			})
		).toBe('/api/media/uploads/abc.png?size=medium');
	});

	it('falls back to the full media for an item with no thumbnail', () => {
		expect(libraryItemGridSrc({ url: '/api/media/uploads/abc.png' })).toBe(
			'/api/media/uploads/abc.png'
		);
	});
});

describe('libraryActionsForCount', () => {
	it('never offers favorite or rating - an upload has no such state', () => {
		expect(libraryActionsForCount(4)).toEqual(['view', 'download', 'delete']);
	});

	it('drops view before delete as the tile shrinks', () => {
		expect(libraryActionsForCount(2)).toEqual(['view', 'delete']);
		expect(libraryActionsForCount(1)).toEqual(['delete']);
	});
});
