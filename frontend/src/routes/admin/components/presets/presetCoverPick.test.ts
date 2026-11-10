import { describe, it, expect } from 'vitest';
import { mediaPickItem, resolveCoverFileFromMediaItem, validateCoverFile } from './presetCoverPick';

function fetchReturning(blob: Blob, ok = true): typeof fetch {
	return (async () => ({ ok, blob: async () => blob })) as unknown as typeof fetch;
}

describe('validateCoverFile', () => {
	it('accepts images up to 10 MB and videos up to 1.5 MB', () => {
		expect(validateCoverFile({ type: 'image/webp', size: 10 * 1024 * 1024 })).toBeNull();
		expect(validateCoverFile({ type: 'video/mp4', size: 1.5 * 1024 * 1024 })).toBeNull();
	});

	it('refuses larger files and other types', () => {
		expect(validateCoverFile({ type: 'image/png', size: 10 * 1024 * 1024 + 1 })).toMatch(/10 MB/);
		expect(validateCoverFile({ type: 'video/webm', size: 2 * 1024 * 1024 })).toMatch(/1.5 MB/);
		expect(validateCoverFile({ type: 'image/gif', size: 10 })).toMatch(/PNG/);
	});
});

describe('resolveCoverFileFromMediaItem', () => {
	it('turns a served pick into a File named after the pick', async () => {
		const result = await resolveCoverFileFromMediaItem(
			{ url: '/api/media/x', name: 'cover.png' },
			fetchReturning(new Blob(['png'], { type: 'image/png' }))
		);
		expect(result.error).toBeNull();
		expect(result.file?.name).toBe('cover.png');
		expect(result.file?.type).toBe('image/png');
	});

	it('reports a failed fetch and a wrong type', async () => {
		expect((await resolveCoverFileFromMediaItem({ url: '/x' }, fetchReturning(new Blob([]), false))).error).toBeTruthy();
		const gif = await resolveCoverFileFromMediaItem({ url: '/x' }, fetchReturning(new Blob(['g'], { type: 'image/gif' })));
		expect(gif.file).toBeNull();
		expect(gif.error).toMatch(/PNG/);
	});
});

describe('mediaPickItem', () => {
	it('reads the url and name of a MediaLoaderField value', () => {
		expect(mediaPickItem({ url: '/u', name: 'a.png' })).toEqual({ url: '/u', name: 'a.png' });
		expect(mediaPickItem({ url: '' })).toBeNull();
		expect(mediaPickItem(null)).toBeNull();
	});
});
