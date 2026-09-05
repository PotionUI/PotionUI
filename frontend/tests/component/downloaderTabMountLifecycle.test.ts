// @vitest-environment jsdom
//
// DownloaderTab's onMount awaited connectAsync() (and the serial
// list/settings/backends loads after it) with no destroyed check, so a
// destroy while any of that was still pending let the stale continuation
// register WebSocket handlers, issue HTTP requests, and publish into the
// (singleton) downloads store well after the component was gone. Drives the
// real component with a controllable deferred connectAsync() across destroy.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPut = vi.fn();
const mockDelete = vi.fn();
const mockGetProviders = vi.fn();
const mockGetBackends = vi.fn();
const mockConnectAsync = vi.fn();
const mockDisconnect = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: () => ({
			get: (...args: unknown[]) => mockGet(...args),
			post: (...args: unknown[]) => mockPost(...args),
			put: (...args: unknown[]) => mockPut(...args),
			delete: (...args: unknown[]) => mockDelete(...args)
		}),
		getProviders: (...args: unknown[]) => mockGetProviders(...args),
		getToken: () => null
	}
}));

vi.mock('$lib/services/admin-api', () => ({
	getBackends: (...args: unknown[]) => mockGetBackends(...args)
}));

const wsMocks = vi.hoisted(() => {
	const progressCallbacks = new Set<(update: unknown) => void>();
	const statusCallbacks = new Set<(update: unknown) => void>();
	const connectionSubscribers = new Set<(state: string) => void>();
	return {
		progressCallbacks,
		statusCallbacks,
		connectionSubscribers,
		reset() {
			progressCallbacks.clear();
			statusCallbacks.clear();
			connectionSubscribers.clear();
		}
	};
});

vi.mock('$lib/services/downloaderWebsocket', () => ({
	downloaderConnectionState: {
		subscribe: (run: (state: string) => void) => {
			wsMocks.connectionSubscribers.add(run);
			run('connected');
			return () => wsMocks.connectionSubscribers.delete(run);
		}
	},
	downloaderWebSocket: {
		connectAsync: (...args: unknown[]) => mockConnectAsync(...args),
		disconnect: (...args: unknown[]) => mockDisconnect(...args),
		onDownloadProgress: (cb: (update: unknown) => void) => {
			wsMocks.progressCallbacks.add(cb);
			return () => wsMocks.progressCallbacks.delete(cb);
		},
		onDownloadStatus: (cb: (update: unknown) => void) => {
			wsMocks.statusCallbacks.add(cb);
			return () => wsMocks.statusCallbacks.delete(cb);
		},
		subscribeToAllDownloads: () => {},
		subscribeToDownload: () => {}
	}
}));

const { downloadStore, downloads, downloadCounts } = await import('$lib/stores/downloads');
const { default: DownloaderTab } = await import(
	'../../src/routes/admin/components/DownloaderTab.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function deferred<T>() {
	let resolve!: (value: T) => void;
	let reject!: (reason: unknown) => void;
	const promise = new Promise<T>((res, rej) => {
		resolve = res;
		reject = rej;
	});
	return { promise, resolve, reject };
}

function download(overrides: Record<string, unknown> = {}) {
	return {
		id: 'd1',
		type: 'model',
		url: 'https://example.com/model.safetensors',
		destination_path: '/models/checkpoint/x.safetensors',
		filename: 'x.safetensors',
		status: 'downloading',
		progress: 0.5,
		total_bytes: 100,
		downloaded_bytes: 50,
		speed_bytes_per_sec: 10,
		error_message: null,
		provider_id: null,
		tags: [],
		checksum_sha256: null,
		retry_count: 0,
		created_at: '2026-01-01T00:00:00Z',
		started_at: null,
		completed_at: null,
		created_by: null,
		...overrides
	};
}

function flush(times = 3) {
	let p: Promise<void> = Promise.resolve();
	for (let i = 0; i < times; i++) p = p.then(() => Promise.resolve());
	return p;
}

// api.getClient().get(...) returns an axios-style response (`.data` wraps the
// APIResponse); api.getProviders()/getBackends() return the APIResponse itself.
const EMPTY_LIST = { data: { success: true, data: { downloads: [], counts: {} } } };
const EMPTY_PROVIDERS = { success: true, data: [] };
const EMPTY_BACKENDS = { success: true, data: [] };

function mountTab() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: DownloaderTab as never, target, props: {} });
	return { target, destroy: () => (component.$destroy(), target.remove()) };
}

let mounted: ReturnType<typeof mountTab> | undefined;

beforeEach(() => {
	vi.resetAllMocks();
	wsMocks.reset();
	downloadStore.reset();
	mockGet.mockResolvedValue(EMPTY_LIST);
	mockGetProviders.mockResolvedValue(EMPTY_PROVIDERS);
	mockGetBackends.mockResolvedValue(EMPTY_BACKENDS);
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('DownloaderTab mount lifecycle', () => {
	it('destroy while connectAsync is pending (resolves later): no handlers, no request, no publication', async () => {
		const connect = deferred<void>();
		mockConnectAsync.mockReturnValue(connect.promise);

		mounted = mountTab();
		await flush();

		mounted.destroy();
		mounted = undefined;

		connect.resolve();
		await flush();

		expect(wsMocks.progressCallbacks.size).toBe(0);
		expect(wsMocks.statusCallbacks.size).toBe(0);
		expect(mockGet).not.toHaveBeenCalled();
		expect(get(downloads)).toEqual([]);
		expect(get(downloadCounts)).toEqual({});
	});

	it('destroy while connectAsync is pending (rejects later): no handlers, no request, no publication', async () => {
		const connect = deferred<void>();
		mockConnectAsync.mockReturnValue(connect.promise);

		mounted = mountTab();
		await flush();

		mounted.destroy();
		mounted = undefined;

		connect.reject(new Error('socket blocked'));
		await flush();

		expect(wsMocks.progressCallbacks.size).toBe(0);
		expect(wsMocks.statusCallbacks.size).toBe(0);
		expect(mockGet).not.toHaveBeenCalled();
		expect(get(downloads)).toEqual([]);
		expect(get(downloadCounts)).toEqual({});
	});

	it('destroy while the serial list load is pending: no publication after teardown', async () => {
		mockConnectAsync.mockResolvedValue(undefined);
		const list = deferred<{ data: unknown }>();
		mockGet.mockImplementationOnce(() => list.promise);

		mounted = mountTab();
		await flush();

		mounted.destroy();
		mounted = undefined;

		list.resolve({
			data: {
				success: true,
				data: { downloads: [download({ id: 'ghost' })], counts: { pending: 1 } }
			}
		});
		await flush();

		expect(get(downloads)).toEqual([]);
		expect(get(downloadCounts)).toEqual({});
	});

	it('a fresh mount after a destroyed one is unaffected and completes normally', async () => {
		const staleConnect = deferred<void>();
		mockConnectAsync.mockReturnValueOnce(staleConnect.promise);

		mounted = mountTab();
		await flush();
		mounted.destroy();
		mounted = undefined;

		mockConnectAsync.mockResolvedValueOnce(undefined);
		mockGet.mockResolvedValue({
			data: {
				success: true,
				data: { downloads: [download({ id: 'd1' })], counts: { downloading: 1 } }
			}
		});

		mounted = mountTab();
		await flush(6);

		expect(get(downloads).map((d) => d.id)).toEqual(['d1']);
		expect(get(downloadCounts)).toEqual({ downloading: 1 });
		expect(wsMocks.progressCallbacks.size).toBe(1);
		expect(wsMocks.statusCallbacks.size).toBe(1);

		// The stale first mount's connect finally resolving must not disturb
		// the now-live remounted session.
		staleConnect.resolve();
		await flush();
		expect(get(downloads).map((d) => d.id)).toEqual(['d1']);
		expect(wsMocks.progressCallbacks.size).toBe(1);
	});
});
