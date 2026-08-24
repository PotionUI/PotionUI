import { describe, it, expect, vi } from 'vitest';
import type { AxiosInstance } from 'axios';
import { createMediaApi } from './media';

// These tests only exercise the pure URL builders, which don't touch the client.
const media = createMediaApi({} as AxiosInstance, () => 'http://x');

describe('getPresetAssetURL', () => {
	it('builds a URL with no query string when size is omitted', () => {
		expect(media.getPresetAssetURL('preset-1', 'public/cover.png')).toBe(
			'http://x/api/media/presets/preset-1/public/cover.png'
		);
	});

	it('appends ?size=<size> when a size is given', () => {
		expect(media.getPresetAssetURL('preset-1', 'public/cover.png', 'large')).toBe(
			'http://x/api/media/presets/preset-1/public/cover.png?size=large'
		);
	});

	it('preserves multi-segment paths verbatim, without encoding slashes', () => {
		expect(media.getPresetAssetURL('preset-1', 'examples/a/b.png')).toBe(
			'http://x/api/media/presets/preset-1/examples/a/b.png'
		);
	});

	it('preserves multi-segment paths verbatim with a size suffix', () => {
		expect(media.getPresetAssetURL('preset-1', 'examples/a/b.png', 'small')).toBe(
			'http://x/api/media/presets/preset-1/examples/a/b.png?size=small'
		);
	});
});

describe('listGenerationMedia', () => {
	it('GETs the generation media list endpoint', async () => {
		const get = vi
			.fn()
			.mockResolvedValue({ data: { success: true, data: { generation_id: 'g1', media_count: 0, media: [] } } });
		const client = { get } as unknown as AxiosInstance;
		const api = createMediaApi(client, () => 'http://x');

		const result = await api.listGenerationMedia('g1');

		expect(get).toHaveBeenCalledWith('/api/media/generations/g1');
		expect(result.success).toBe(true);
	});
});

describe('getUploadInfo', () => {
	it('GETs the upload info endpoint with the filename URL-encoded', async () => {
		const get = vi.fn().mockResolvedValue({
			data: { success: true, data: { filename: 'a b.png', width: 100, height: 100 } }
		});
		const client = { get } as unknown as AxiosInstance;
		const api = createMediaApi(client, () => 'http://x');

		const result = await api.getUploadInfo('a b.png');

		expect(get).toHaveBeenCalledWith('/api/media/uploads/a%20b.png/info');
		expect(result.data?.width).toBe(100);
	});
});

describe('listUploads', () => {
	it('GETs the uploads list endpoint with no params when none are given', async () => {
		const get = vi
			.fn()
			.mockResolvedValue({ data: { success: true, data: { uploads: [], total: 0, limit: 20, offset: 0 } } });
		const client = { get } as unknown as AxiosInstance;
		const api = createMediaApi(client, () => 'http://x');

		const result = await api.listUploads();

		expect(get).toHaveBeenCalledWith('/api/media/uploads', { params: {} });
		expect(result.success).toBe(true);
	});

	it('forwards mediaType/limit/offset as media_type/limit/offset params', async () => {
		const get = vi
			.fn()
			.mockResolvedValue({ data: { success: true, data: { uploads: [], total: 0, limit: 10, offset: 5 } } });
		const client = { get } as unknown as AxiosInstance;
		const api = createMediaApi(client, () => 'http://x');

		await api.listUploads({ mediaType: 'video', limit: 10, offset: 5 });

		expect(get).toHaveBeenCalledWith('/api/media/uploads', {
			params: { media_type: 'video', limit: 10, offset: 5 }
		});
	});
});

describe('deleteUpload', () => {
	it('DELETEs the upload endpoint with the filename URL-encoded', async () => {
		const del = vi.fn().mockResolvedValue({
			data: { success: true, data: { filename: 'a b.png', deleted: true } }
		});
		const client = { delete: del } as unknown as AxiosInstance;
		const api = createMediaApi(client, () => 'http://x');

		const result = await api.deleteUpload('a b.png');

		expect(del).toHaveBeenCalledWith('/api/media/uploads/a%20b.png');
		expect(result.data?.deleted).toBe(true);
	});
});
