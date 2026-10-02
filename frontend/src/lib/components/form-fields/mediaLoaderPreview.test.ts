import { describe, expect, it } from 'vitest';
import { locateMediaPath, mediaPathPreviewUrl } from './mediaLoaderPreview';

describe('mediaPathPreviewUrl', () => {
	it('serves a library pick (relative uploads path) from the uploads route', () => {
		expect(mediaPathPreviewUrl('uploads/abc.png')).toBe('/api/media/uploads/abc.png');
	});

	it('serves an absolute uploads path from the uploads route', () => {
		expect(mediaPathPreviewUrl('/home/u/potionui/storage/uploads/abc.png')).toBe('/api/media/uploads/abc.png');
	});

	it('does not mistake a storage root under /tmp for a temporary file', () => {
		expect(mediaPathPreviewUrl('/tmp/potionui-e2e-1/storage/uploads/abc.png')).toBe('/api/media/uploads/abc.png');
	});

	it('serves a file in the tmp folder from the tmp route', () => {
		expect(mediaPathPreviewUrl('/home/u/potionui/storage/tmp/abc.png')).toBe('/api/media/tmp/abc.png');
	});

	it('serves a relative generation path from the generation route', () => {
		expect(mediaPathPreviewUrl('outputs/2025-10-10/01K77DF21Z/0.png')).toBe('/api/media/generations/01K77DF21Z/0.png');
	});

	it('serves an absolute generation path from the generation route', () => {
		expect(mediaPathPreviewUrl('/home/u/storage/generations/01K77DF21Z/0.png')).toBe(
			'/api/media/generations/01K77DF21Z/0.png'
		);
	});

	it('keeps treating other absolute paths as uploads', () => {
		expect(locateMediaPath('/somewhere/else/abc.png')).toEqual({ kind: 'upload', filename: 'abc.png' });
	});

	it('has no preview for a bare name or empty path', () => {
		expect(mediaPathPreviewUrl('abc.png')).toBeNull();
		expect(mediaPathPreviewUrl('')).toBeNull();
	});
});

describe('mediaPathPreviewUrl with Windows paths', () => {
	it('serves a backslash uploads path from the uploads route', () => {
		expect(mediaPathPreviewUrl('C:\\Users\\runner\\potionui\\storage\\uploads\\abc.png')).toBe('/api/media/uploads/abc.png');
	});

	it('serves a backslash tmp path from the tmp route', () => {
		expect(mediaPathPreviewUrl('C:\\Users\\runner\\storage\\tmp\\abc.png')).toBe('/api/media/tmp/abc.png');
	});

	it('serves a backslash generation path from the generation route', () => {
		expect(mediaPathPreviewUrl('D:\\storage\\generations\\01K77DF21Z\\0.png')).toBe('/api/media/generations/01K77DF21Z/0.png');
	});

	it('keeps treating other drive-letter paths as uploads', () => {
		expect(locateMediaPath('C:\\somewhere\\else\\abc.png')).toEqual({ kind: 'upload', filename: 'abc.png' });
	});
});

describe('mediaPathPreviewUrl with awkward file names', () => {
	it.each([
		['uploads/a #1 100%.png', '/api/media/uploads/a%20%231%20100%25.png'],
		['storage/tmp/a?b.png', '/api/media/tmp/a%3Fb.png'],
		['generations/G#1/a b.png', '/api/media/generations/G%231/a%20b.png']
	])('encodes %s', (path, url) => {
		expect(mediaPathPreviewUrl(path)).toBe(url);
	});
});
