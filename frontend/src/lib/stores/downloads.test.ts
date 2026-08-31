import { describe, it, expect, vi, beforeEach } from 'vitest';

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPut = vi.fn();
const mockDelete = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: () => ({
			get: (...args: unknown[]) => mockGet(...args),
			post: (...args: unknown[]) => mockPost(...args),
			put: (...args: unknown[]) => mockPut(...args),
			delete: (...args: unknown[]) => mockDelete(...args)
		})
	}
}));

vi.mock('$lib/services/downloaderWebsocket', () => ({
	downloaderWebSocket: {
		onDownloadProgress: () => () => {},
		onDownloadStatus: () => () => {},
		subscribeToAllDownloads: () => {},
		subscribeToDownload: () => {}
	}
}));

import { downloadStore } from './downloads';

describe('stores/downloads REST paths', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		mockGet.mockResolvedValue({ data: { success: true, data: { downloads: [], counts: {} } } });
		mockPost.mockResolvedValue({ data: { success: true, data: { id: 'd1' } } });
		mockPut.mockResolvedValue({ data: { success: true, data: {} } });
		mockDelete.mockResolvedValue({ data: { success: true } });
	});

	it('loadDownloads hits /api/downloads', async () => {
		await downloadStore.loadDownloads();
		expect(mockGet).toHaveBeenCalledWith(expect.stringMatching(/^\/api\/downloads\?/));
	});

	it('loadCounts hits /api/downloads', async () => {
		await downloadStore.loadCounts();
		expect(mockGet).toHaveBeenCalledWith('/api/downloads?limit=0');
	});

	it('loadSettings hits /api/downloads/settings', async () => {
		await downloadStore.loadSettings();
		expect(mockGet).toHaveBeenCalledWith('/api/downloads/settings');
	});

	it('updateSettings hits /api/downloads/settings', async () => {
		await downloadStore.updateSettings({ max_concurrent_downloads: 3 });
		expect(mockPut).toHaveBeenCalledWith('/api/downloads/settings', {
			max_concurrent_downloads: 3
		});
	});

	it('queueModelDownload hits /api/downloads/model', async () => {
		await downloadStore.queueModelDownload('https://example.com/model.safetensors');
		expect(mockPost).toHaveBeenCalledWith(
			'/api/downloads/model',
			expect.objectContaining({ url: 'https://example.com/model.safetensors' })
		);
	});

	it('loadRemoteBackends hits /api/backends and keeps only configured native.remote rows', async () => {
		mockGet.mockResolvedValueOnce({
			data: {
				success: true,
				data: [
					{ id: 'r1', name: 'Remote One', driver: 'native.remote', configured: true },
					{ id: 'r2', name: 'Remote Two', driver: 'native.remote', configured: false },
					{ id: 'l1', name: 'Local', driver: 'native.local', configured: true }
				]
			}
		});

		const { get } = await import('svelte/store');
		const { remoteBackends } = await import('./downloads');
		await downloadStore.loadRemoteBackends();

		expect(mockGet).toHaveBeenCalledWith('/api/backends');
		expect(get(remoteBackends)).toEqual([{ id: 'r1', name: 'Remote One' }]);
	});

	it('queueModelDownload with a destination backend sends destination_backend_id', async () => {
		await downloadStore.queueModelDownload('https://example.com/model.safetensors', {
			destination_backend_id: 'r1'
		});
		expect(mockPost).toHaveBeenCalledWith(
			'/api/downloads/model',
			expect.objectContaining({
				url: 'https://example.com/model.safetensors',
				destination_backend_id: 'r1'
			})
		);
	});

	it('queueMediaDownload hits /api/downloads/media', async () => {
		await downloadStore.queueMediaDownload('https://example.com/img.png');
		expect(mockPost).toHaveBeenCalledWith(
			'/api/downloads/media',
			expect.objectContaining({ url: 'https://example.com/img.png' })
		);
	});

	it('queueHfRepoDownload hits /api/downloads/hf-repo with repo_id and destination_dir', async () => {
		await downloadStore.queueHfRepoDownload('BAAI/bge-small-en-v1.5', {
			destination_dir: 'models/text_embeddings/baai-bge-small-en-v1-5'
		});
		expect(mockPost).toHaveBeenCalledWith('/api/downloads/hf-repo', {
			repo_id: 'BAAI/bge-small-en-v1.5',
			destination_dir: 'models/text_embeddings/baai-bge-small-en-v1-5'
		});
	});

	it('pauseDownload/resumeDownload/cancelDownload/retryDownload hit /api/downloads/{id}/...', async () => {
		await downloadStore.pauseDownload('d1');
		expect(mockPost).toHaveBeenCalledWith('/api/downloads/d1/pause');

		await downloadStore.resumeDownload('d1');
		expect(mockPost).toHaveBeenCalledWith('/api/downloads/d1/resume');

		await downloadStore.cancelDownload('d1');
		expect(mockPost).toHaveBeenCalledWith('/api/downloads/d1/cancel');

		await downloadStore.retryDownload('d1');
		expect(mockPost).toHaveBeenCalledWith('/api/downloads/d1/retry');
	});

	it('deleteDownload hits /api/downloads/{id}', async () => {
		await downloadStore.deleteDownload('d1');
		expect(mockDelete).toHaveBeenCalledWith('/api/downloads/d1');
	});

	it('clearCompleted hits /api/downloads/clear-completed', async () => {
		await downloadStore.clearCompleted();
		expect(mockPost).toHaveBeenCalledWith('/api/downloads/clear-completed');
	});
});
