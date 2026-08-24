import { describe, it, expect } from 'vitest';
import { buildUploadedMediaItem } from './mediaLoaderUpload';

const uploadResponse = {
	path: '/abs/uploads/cat.png',
	relative_path: 'uploads/cat.png',
	url: '/api/media/uploads/cat.png',
	width: 512,
	height: 512,
	duration_seconds: null,
	fps: null,
	size: 12345
};

describe('buildUploadedMediaItem', () => {
	// The bug: MediaLoaderField used to persist a `URL.createObjectURL(file)`
	// blob handle instead of the server URL the upload response already
	// carries. Blob URLs only resolve for the life of the document, so every
	// stored reference image 404'd (`ERR_FILE_NOT_FOUND`) after a refresh.
	it('persists the server URL from the upload response, never a blob URL', () => {
		const item = buildUploadedMediaItem(uploadResponse, 'cat.png', 'image');
		expect(item.url).toBe('/api/media/uploads/cat.png');
		expect(item.url.startsWith('blob:')).toBe(false);
	});

	it('carries path, relative_path, name and type through unchanged', () => {
		const item = buildUploadedMediaItem(uploadResponse, 'cat.png', 'image');
		expect(item.path).toBe('/abs/uploads/cat.png');
		expect(item.relative_path).toBe('uploads/cat.png');
		expect(item.name).toBe('cat.png');
		expect(item.type).toBe('image');
	});

	it('folds width/height/duration/fps/size into metadata', () => {
		const item = buildUploadedMediaItem(uploadResponse, 'cat.png', 'image');
		expect(item.metadata).toEqual({
			width: 512,
			height: 512,
			duration_seconds: null,
			fps: null,
			size: 12345
		});
	});
});
