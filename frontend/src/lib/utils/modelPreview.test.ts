import { describe, it, expect } from 'vitest';
import {
	filesWithPreview,
	getModelPreview,
	mediaFileThumbnailUrl,
	previewItemsAsFiles,
	PREVIEW_FILE_ID,
	type ModelPreviewMediaItem
} from './modelPreview';

describe('mediaFileThumbnailUrl', () => {
	it('prefers a video file\'s own thumbnail_medium', () => {
		expect(
			mediaFileThumbnailUrl({ file_type: 'video', url: '/api/media/files/v1', thumbnail_medium: '/api/media/files/v1/thumb' })
		).toBe('/api/media/files/v1/thumb');
	});

	it('a video with no thumbnail_medium still gets the ?size=medium image treatment for a servable url', () => {
		// Matches the pre-extraction behavior: the servable-url branch below only
		// checks the url pattern, not file_type - a real video thumbnail_medium
		// is expected before this ever runs, so this only matters as a fallback.
		expect(mediaFileThumbnailUrl({ file_type: 'video', url: '/api/media/files/v1' })).toBe(
			'/api/media/files/v1?size=medium'
		);
	});

	it('falls back to the raw url for a non-servable video url with no thumbnail_medium', () => {
		expect(mediaFileThumbnailUrl({ file_type: 'video', url: 'https://cdn.example.com/v1.mp4' })).toBe(
			'https://cdn.example.com/v1.mp4'
		);
	});

	it('appends ?size=medium to a servable image url with no existing query', () => {
		expect(mediaFileThumbnailUrl({ file_type: 'image', url: '/api/media/files/i1' })).toBe(
			'/api/media/files/i1?size=medium'
		);
	});

	it('appends &size=medium to a servable image url that already has a query', () => {
		expect(mediaFileThumbnailUrl({ file_type: 'image', url: '/api/media/files/i1?v=2' })).toBe(
			'/api/media/files/i1?v=2&size=medium'
		);
	});

	it('leaves a non-servable url untouched', () => {
		expect(mediaFileThumbnailUrl({ file_type: 'image', url: 'https://cdn.example.com/i1.png' })).toBe(
			'https://cdn.example.com/i1.png'
		);
	});

	it('is empty for a missing/urlless file', () => {
		expect(mediaFileThumbnailUrl(null)).toBe('');
		expect(mediaFileThumbnailUrl(undefined)).toBe('');
		expect(mediaFileThumbnailUrl({ file_type: 'image' })).toBe('');
	});
});

const providerImage = {
	id: 'f1',
	file_type: 'image',
	url: '/api/media/files/f1',
	thumbnail_small: '/api/media/files/f1?size=small',
	display_order: 0
};

describe('getModelPreview', () => {
	it('returns null when unset', () => {
		expect(getModelPreview({})).toBeNull();
		expect(getModelPreview(null)).toBeNull();
	});

	it('ignores a malformed preview (missing url or type)', () => {
		expect(getModelPreview({ preview_media: { type: 'image' } as any })).toBeNull();
		expect(getModelPreview({ preview_media: { url: '/x' } as any })).toBeNull();
	});

	it('returns a well-formed preview', () => {
		const preview = { url: '/api/media/uploads/p.png', type: 'image' as const };
		expect(getModelPreview({ preview_media: preview })).toEqual(preview);
	});
});

describe('filesWithPreview', () => {
	it('returns the original files untouched when no preview is set', () => {
		const model = { files: [providerImage] };
		expect(filesWithPreview(model)).toEqual([providerImage]);
	});

	it('returns [] for a model with neither files nor preview', () => {
		expect(filesWithPreview({})).toEqual([]);
		expect(filesWithPreview(null)).toEqual([]);
	});

	it('prepends the admin preview ahead of provider files', () => {
		const model = {
			preview_media: { url: '/api/media/uploads/p.png', type: 'image' as const, name: 'p.png' },
			files: [providerImage]
		};
		const result = filesWithPreview(model);
		expect(result).toHaveLength(2);
		expect(result[0].id).toBe(PREVIEW_FILE_ID);
		expect(result[0].file_type).toBe('image');
		expect(result[0].url).toBe('/api/media/uploads/p.png');
		expect(result[1]).toBe(providerImage);
	});

	it('sorts ahead of provider files via display_order -1', () => {
		const model = {
			preview_media: { url: '/api/media/uploads/p.png', type: 'image' as const },
			files: [providerImage]
		};
		const sorted = [...filesWithPreview(model)].sort(
			(a, b) => (a.display_order ?? 0) - (b.display_order ?? 0)
		);
		expect(sorted[0].id).toBe(PREVIEW_FILE_ID);
	});

	it('points image thumbnails at the sized variants so pickers fetch a small image', () => {
		const model = {
			preview_media: { url: '/api/media/files/abc', type: 'image' as const }
		};
		const entry = filesWithPreview(model).find((f) => f.file_type === 'image');
		expect(entry?.thumbnail_small).toBe('/api/media/files/abc?size=small');
		expect(entry?.thumbnail_medium).toBe('/api/media/files/abc?size=medium');
		expect(entry?.thumbnail_large).toBe('/api/media/files/abc?size=large');
	});

	it('does not inject a video preview into img-only sites by default', () => {
		const model = {
			preview_media: { url: '/api/media/uploads/v.mp4', type: 'video' as const },
			files: [providerImage]
		};
		expect(filesWithPreview(model)).toEqual([providerImage]);
	});

	it('injects a video preview when allowVideo is set, with no thumbnail', () => {
		const model = {
			preview_media: { url: '/api/media/uploads/v.mp4', type: 'video' as const },
			files: [providerImage]
		};
		const result = filesWithPreview(model, { allowVideo: true });
		expect(result[0].id).toBe(PREVIEW_FILE_ID);
		expect(result[0].file_type).toBe('video');
		expect(result[0].url).toBe('/api/media/uploads/v.mp4');
		// No thumbnail - a video URL must never land in an <img>.
		expect(result[0].thumbnail_small).toBeUndefined();
	});

	it('never injects an audio preview into the file list', () => {
		const model = {
			preview_media: { url: '/api/media/uploads/a.mp3', type: 'audio' as const },
			files: [providerImage]
		};
		expect(filesWithPreview(model)).toEqual([providerImage]);
		expect(filesWithPreview(model, { allowVideo: true })).toEqual([providerImage]);
	});
});

describe('previewItemsAsFiles', () => {
	const previews: ModelPreviewMediaItem[] = [
		{ id: 'p0', file_id: 'f0', url: '/api/media/files/p0', type: 'image', position: 0 },
		{ id: 'p1', file_id: 'f1', url: '/api/media/files/p1', type: 'image', position: 1 }
	];

	it('returns [] for no previews', () => {
		expect(previewItemsAsFiles(null)).toEqual([]);
		expect(previewItemsAsFiles(undefined)).toEqual([]);
		expect(previewItemsAsFiles([])).toEqual([]);
	});

	it('returns every preview, ordered by position', () => {
		const result = previewItemsAsFiles(previews);
		expect(result).toHaveLength(2);
		expect(result[0].id).toBe('p0');
		expect(result[1].id).toBe('p1');
	});

	it('sorts by position even when the input array is out of order', () => {
		const shuffled = [previews[1], previews[0]];
		const result = previewItemsAsFiles(shuffled);
		expect(result.map((r) => r.id)).toEqual(['p0', 'p1']);
	});

	it('points image thumbnails at the sized variants', () => {
		const [entry] = previewItemsAsFiles([previews[0]]);
		expect(entry.thumbnail_small).toBe('/api/media/files/p0?size=small');
		expect(entry.thumbnail_medium).toBe('/api/media/files/p0?size=medium');
		expect(entry.thumbnail_large).toBe('/api/media/files/p0?size=large');
	});

	it('excludes a video preview by default (img-only sites)', () => {
		const withVideo: ModelPreviewMediaItem[] = [
			...previews,
			{ id: 'p2', url: '/api/media/files/p2', type: 'video', position: 2 }
		];
		expect(previewItemsAsFiles(withVideo).map((r) => r.id)).toEqual(['p0', 'p1']);
	});

	it('includes a video preview when allowVideo is set, with no thumbnail', () => {
		const withVideo: ModelPreviewMediaItem[] = [
			{ id: 'p2', url: '/api/media/files/p2', type: 'video', position: 0 }
		];
		const result = previewItemsAsFiles(withVideo, { allowVideo: true });
		expect(result).toHaveLength(1);
		expect(result[0].file_type).toBe('video');
		expect(result[0].thumbnail_small).toBeUndefined();
	});

	it('never includes an audio preview', () => {
		const withAudio: ModelPreviewMediaItem[] = [
			{ id: 'p2', url: '/api/media/files/p2', type: 'audio', position: 0 }
		];
		expect(previewItemsAsFiles(withAudio)).toEqual([]);
		expect(previewItemsAsFiles(withAudio, { allowVideo: true })).toEqual([]);
	});

	it('sorts ahead of provider files via a negative display_order', () => {
		const result = previewItemsAsFiles([previews[0]]);
		expect(result[0].display_order).toBeLessThan(providerImage.display_order ?? 0);
	});
});
