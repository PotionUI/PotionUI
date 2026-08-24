import { describe, it, expect } from 'vitest';
import { buildEditedMediaItem } from './mediaLoaderEdited';
import { buildUploadedMediaItem } from './mediaLoaderUpload';
import type { EditedMediaItem } from '$lib/services/api/media';

const edited: EditedMediaItem = {
	id: 'row-9',
	filename: 'b6d1-4c.png',
	original_filename: 'portrait.png',
	media_type: 'image',
	mime_type: 'image/png',
	url: '/api/media/uploads/b6d1-4c.png',
	width: 832,
	height: 1216,
	size: 481203,
	created_at: '2026-08-13T10:00:00'
};

describe('buildEditedMediaItem', () => {
	it('stores the uploads-relative path, not the route', () => {
		const item = buildEditedMediaItem(edited);
		expect(item.path).toBe('uploads/b6d1-4c.png');
		expect(item.relative_path).toBe('uploads/b6d1-4c.png');
	});

	it('keeps the served url the API returned, never a blob handle', () => {
		expect(buildEditedMediaItem(edited).url).toBe('/api/media/uploads/b6d1-4c.png');
	});

	it('follows the new filename a replace produced', () => {
		// A replace writes a new file behind the same row - a value left pointing
		// at the old name would serve the pre-edit bytes out of the browser cache.
		const replaced = buildEditedMediaItem({ ...edited, filename: 'fresh-77.png' });
		expect(replaced.relative_path).toBe('uploads/fresh-77.png');
	});

	it('shows the original filename rather than the stored uuid', () => {
		expect(buildEditedMediaItem(edited).name).toBe('portrait.png');
	});

	it('falls back to the stored name when there is no original', () => {
		const item = buildEditedMediaItem({ ...edited, original_filename: undefined });
		expect(item.name).toBe('b6d1-4c.png');
	});

	it('carries the metadata the edit reported', () => {
		expect(buildEditedMediaItem(edited).metadata).toEqual({
			width: 832,
			height: 1216,
			duration_seconds: null,
			fps: null,
			size: 481203
		});
	});

	it('narrows the media type to the three kinds the field knows', () => {
		expect(buildEditedMediaItem({ ...edited, media_type: 'video' }).type).toBe('video');
		expect(buildEditedMediaItem({ ...edited, media_type: 'audio' }).type).toBe('audio');
		expect(buildEditedMediaItem({ ...edited, media_type: 'mesh' }).type).toBe('image');
	});

	it('produces the same field shape an upload does', () => {
		const uploaded = buildUploadedMediaItem(
			{
				path: 'uploads/b6d1-4c.png',
				relative_path: 'uploads/b6d1-4c.png',
				url: '/api/media/uploads/b6d1-4c.png',
				width: 832,
				height: 1216,
				size: 481203
			},
			'portrait.png',
			'image'
		);
		expect(Object.keys(buildEditedMediaItem(edited)).sort()).toEqual(Object.keys(uploaded).sort());
	});
});
