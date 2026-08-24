import { describe, expect, it, vi } from 'vitest';
import { resolveAvatarFileFromMediaItem } from './avatarMediaPick';

function fakeFetch(response: Partial<Response> & { blob: () => Promise<Blob> }) {
	return vi.fn().mockResolvedValue(response as Response);
}

describe('resolveAvatarFileFromMediaItem', () => {
	it('fetches the picked url and returns a validated File', async () => {
		const blob = new Blob(['pixels'], { type: 'image/png' });
		const fetchImpl = fakeFetch({ ok: true, blob: async () => blob });

		const result = await resolveAvatarFileFromMediaItem(
			{ url: '/api/media/uploads/pic.png', name: 'pic.png' },
			fetchImpl
		);

		expect(fetchImpl).toHaveBeenCalledWith('/api/media/uploads/pic.png');
		expect(result.error).toBeNull();
		expect(result.file).toBeInstanceOf(File);
		expect(result.file?.name).toBe('pic.png');
		expect(result.file?.type).toBe('image/png');
	});

	it('falls back to avatar.png when the media item has no name', async () => {
		const blob = new Blob(['pixels'], { type: 'image/jpeg' });
		const fetchImpl = fakeFetch({ ok: true, blob: async () => blob });

		const result = await resolveAvatarFileFromMediaItem({ url: '/api/media/uploads/x' }, fetchImpl);

		expect(result.file?.name).toBe('avatar.png');
	});

	it('reports an error when the fetch itself fails', async () => {
		const fetchImpl = fakeFetch({ ok: false, blob: async () => new Blob() });

		const result = await resolveAvatarFileFromMediaItem({ url: '/api/media/uploads/gone.png' }, fetchImpl);

		expect(result.file).toBeNull();
		expect(result.error).toBe('Failed to load the selected image.');
	});

	it('rejects a fetched file that fails avatar validation (oversized)', async () => {
		const bigBlob = new Blob([new Uint8Array(6 * 1024 * 1024)], { type: 'image/png' });
		const fetchImpl = fakeFetch({ ok: true, blob: async () => bigBlob });

		const result = await resolveAvatarFileFromMediaItem({ url: '/api/media/uploads/huge.png' }, fetchImpl);

		expect(result.file).toBeNull();
		expect(result.error).toBe('Image must be 5 MB or smaller.');
	});

	it('rejects a fetched file with a disallowed MIME type', async () => {
		const blob = new Blob(['<svg/>'], { type: 'image/svg+xml' });
		const fetchImpl = fakeFetch({ ok: true, blob: async () => blob });

		const result = await resolveAvatarFileFromMediaItem({ url: '/api/media/uploads/x.svg' }, fetchImpl);

		expect(result.file).toBeNull();
		expect(result.error).toBe('Please choose a PNG, JPG, WEBP, or GIF image.');
	});
});
