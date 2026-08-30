import { describe, it, expect } from 'vitest';
import { findAttachedMediaThumb } from './studioDockMedia';

describe('findAttachedMediaThumb', () => {
	it('returns null for empty or missing form data', () => {
		expect(findAttachedMediaThumb(null)).toBeNull();
		expect(findAttachedMediaThumb(undefined)).toBeNull();
		expect(findAttachedMediaThumb({})).toBeNull();
	});

	it('ignores plain scalar and non-media object field values', () => {
		expect(
			findAttachedMediaThumb({
				steps: 30,
				sampler: 'DPM++ 2M',
				enabled: true,
				resolution_hint: { width: 1024, height: 1024 }
			})
		).toBeNull();
	});

	it('finds a single media-loader value by its url', () => {
		expect(
			findAttachedMediaThumb({
				source_image: { path: 'uploads/a.png', relative_path: 'uploads/a.png', url: '/api/media/a.png', name: 'a.png' }
			})
		).toEqual({ url: '/api/media/a.png', name: 'a.png' });
	});

	it('falls back to path when url is absent', () => {
		expect(findAttachedMediaThumb({ source_image: { path: 'uploads/a.png' } })).toEqual({
			url: 'uploads/a.png',
			name: undefined
		});
	});

	it('reads the first item of a multi media-loader array', () => {
		expect(
			findAttachedMediaThumb({
				references: [
					{ path: 'uploads/b.png', url: '/api/media/b.png', name: 'b.png' },
					{ path: 'uploads/c.png', url: '/api/media/c.png', name: 'c.png' }
				]
			})
		).toEqual({ url: '/api/media/b.png', name: 'b.png' });
	});

	it('skips an empty multi array and keeps scanning other fields', () => {
		expect(
			findAttachedMediaThumb({
				references: [],
				source_image: { path: 'uploads/a.png', url: '/api/media/a.png' }
			})
		).toEqual({ url: '/api/media/a.png', name: undefined });
	});
});
