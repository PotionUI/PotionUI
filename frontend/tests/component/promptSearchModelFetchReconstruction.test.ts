// @vitest-environment jsdom
//
// The admin Prompt Search settings panel used to track "a fetch is running"
// only in a page-local downloadId->kind map, so a reload or reconnect
// mid-download read back as idle/not-present and an admin could fire a
// second fetch. This mounts the real panel fresh (it never queues anything
// itself in this test) against a status response that already reports an
// in-flight job, proving the "downloading" state is reconstructed from the
// backend response alone.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/admin-api', () => ({
	getPromptEmbeddingStatus: vi.fn(),
	getMediaModelsStatus: vi.fn()
}));

vi.mock('$lib/services/downloaderWebsocket', () => ({
	downloaderWebSocket: {
		connectAsync: vi.fn().mockResolvedValue(undefined),
		onDownloadProgress: vi.fn(() => () => {}),
		onDownloadStatus: vi.fn(() => () => {}),
		subscribeToDownload: vi.fn(),
		disconnect: vi.fn()
	}
}));

vi.mock('$lib/stores/downloads', () => ({
	downloadStore: {
		formatBytes: (n: number) => `${n} B`,
		queueHfRepoDownload: vi.fn()
	}
}));

const adminApi = await import('$lib/services/admin-api');
const { downloaderWebSocket } = await import('$lib/services/downloaderWebsocket');
const { default: PromptSearchPanel } = await import(
	'../../src/routes/admin/components/settings/PromptSearchPanel.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

const SETTINGS = {
	prompt_embedding_provider: 'local',
	prompt_embedding_model: 'BAAI/bge-small-en-v1.5'
};

function mountPanel() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: PromptSearchPanel as never,
		target,
		props: { settings: SETTINGS, onSettingChange: () => {} }
	});
	return { target, destroy: () => component.$destroy() };
}

async function settle() {
	for (let i = 0; i < 6; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let mounted: ReturnType<typeof mountPanel> | undefined;

beforeEach(() => {
	vi.mocked(adminApi.getMediaModelsStatus).mockResolvedValue({
		success: true,
		data: {
			tagger: { present: false, path: null, size: null, loaded: false, active_download: null },
			vision: { present: false, path: null, size: null, loaded: false, active_download: null }
		}
	} as never);
});

afterEach(() => {
	mounted?.target.remove();
	vi.clearAllMocks();
});

describe('Prompt Search panel reconstructs an in-flight fetch on mount', () => {
	it('reads "downloading" straight off the status response, with no download queued by this page', async () => {
		vi.mocked(adminApi.getPromptEmbeddingStatus).mockResolvedValue({
			success: true,
			data: {
				present: false,
				path: '/models/text_embeddings/baai-bge-small-en-v1-5',
				size: null,
				loaded: false,
				active_download: {
					id: 'dl-1',
					status: 'downloading',
					progress: 0.42,
					downloaded_bytes: 420,
					total_bytes: 1000,
					speed_bytes_per_sec: 100
				}
			}
		} as never);

		mounted = mountPanel();
		await settle();

		expect(mounted.target.textContent).toContain('Downloading');
		expect(mounted.target.textContent).toContain('42%');
		expect(vi.mocked(adminApi.getPromptEmbeddingStatus)).toHaveBeenCalled();
		expect(vi.mocked(downloaderWebSocket.subscribeToDownload)).toHaveBeenCalledWith('dl-1');
	});

	it('reads a pending job as queued rather than idle', async () => {
		vi.mocked(adminApi.getPromptEmbeddingStatus).mockResolvedValue({
			success: true,
			data: {
				present: false,
				path: '/models/text_embeddings/baai-bge-small-en-v1-5',
				size: null,
				loaded: false,
				active_download: {
					id: 'dl-2',
					status: 'pending',
					progress: 0,
					downloaded_bytes: 0,
					total_bytes: null,
					speed_bytes_per_sec: null
				}
			}
		} as never);

		mounted = mountPanel();
		await settle();

		expect(mounted.target.textContent).toContain('queued');
		expect(vi.mocked(downloaderWebSocket.subscribeToDownload)).toHaveBeenCalledWith('dl-2');
	});

	it('shows idle/no download when the status response reports none in flight', async () => {
		vi.mocked(adminApi.getPromptEmbeddingStatus).mockResolvedValue({
			success: true,
			data: {
				present: false,
				path: '/models/text_embeddings/baai-bge-small-en-v1-5',
				size: null,
				loaded: false,
				active_download: null
			}
		} as never);

		mounted = mountPanel();
		await settle();

		expect(mounted.target.textContent).not.toContain('Downloading');
		expect(vi.mocked(downloaderWebSocket.subscribeToDownload)).not.toHaveBeenCalled();
	});
});

describe('Prompt Search panel surfaces in-memory residency separately from on-disk presence', () => {
	it('shows "In memory" only when the status response reports it loaded', async () => {
		vi.mocked(adminApi.getPromptEmbeddingStatus).mockResolvedValue({
			success: true,
			data: {
				present: true,
				path: '/models/text_embeddings/baai-bge-small-en-v1-5',
				size: 1234,
				loaded: true,
				active_download: null
			}
		} as never);

		mounted = mountPanel();
		await settle();

		expect(mounted.target.textContent).toContain('Ready');
		expect(mounted.target.textContent).toContain('In memory');
	});

	it('reads "Ready" with no "In memory" badge when present but evicted', async () => {
		vi.mocked(adminApi.getPromptEmbeddingStatus).mockResolvedValue({
			success: true,
			data: {
				present: true,
				path: '/models/text_embeddings/baai-bge-small-en-v1-5',
				size: 1234,
				loaded: false,
				active_download: null
			}
		} as never);

		mounted = mountPanel();
		await settle();

		expect(mounted.target.textContent).toContain('Ready');
		expect(mounted.target.textContent).not.toContain('In memory');
	});
});
