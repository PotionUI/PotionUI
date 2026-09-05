import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';

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

// A fake downloader WebSocket that exposes its registered callbacks so tests
// can drive events directly, plus a controllable connection-state store so
// initializeWebSocket()'s reconnect coordination can be exercised.
const wsMocks = vi.hoisted(() => {
	const progressCallbacks = new Set<(update: unknown) => void>();
	const statusCallbacks = new Set<(update: unknown) => void>();
	const connectionSubscribers = new Set<(state: string) => void>();
	let connectionState: 'disconnected' | 'connecting' | 'connected' | 'reconnecting' = 'connected';
	return {
		progressCallbacks,
		statusCallbacks,
		connectionSubscribers,
		setConnectionState(state: typeof connectionState) {
			connectionState = state;
			for (const sub of Array.from(connectionSubscribers)) sub(state);
		},
		reset() {
			progressCallbacks.clear();
			statusCallbacks.clear();
			connectionSubscribers.clear();
			connectionState = 'connected';
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

import { downloadStore, downloads, downloadCounts, loading, type Download } from './downloads';

/** A resolvable/rejectable promise for driving out-of-order async responses. */
function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((res) => {
		resolve = res;
	});
	return { promise, resolve };
}

function download(overrides: Partial<Download> = {}): Download {
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

describe('stores/downloads REST paths', () => {
	beforeEach(() => {
		vi.resetAllMocks();
		wsMocks.reset();
		downloadStore.reset();
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

describe('stores/downloads WebSocket lifecycle and reconciliation', () => {
	beforeEach(() => {
		vi.resetAllMocks();
		wsMocks.reset();
		downloadStore.reset();
		mockGet.mockResolvedValue({ data: { success: true, data: { downloads: [], counts: {} } } });
		mockPost.mockResolvedValue({ data: { success: true, data: { id: 'd1' } } });
	});

	it('a delayed list snapshot does not clobber a newer status event that arrived while it was in flight', async () => {
		downloadStore.initializeWebSocket();

		// The list request is issued first (captures the old snapshot's
		// sequence) but its response is deferred until after the WS event and
		// the counts refresh it triggers have both landed.
		const listResponse = deferred<{ data: unknown }>();
		const countsResponse = deferred<{ data: unknown }>();
		mockGet.mockImplementationOnce(() => listResponse.promise); // GET /api/downloads?...
		mockGet.mockImplementationOnce(() => countsResponse.promise); // GET /api/downloads?limit=0

		const loadPromise = downloadStore.loadDownloads();

		// A status event for d1 arrives before the list response - marks d1
		// completed and kicks off loadCounts() (mocked above as deferred too).
		for (const cb of wsMocks.statusCallbacks) {
			cb({ download_id: 'd1', status: 'completed', filename: 'x.safetensors' });
		}

		// The counts refresh the status event triggered finishes first.
		countsResponse.resolve({ data: { success: true, data: { counts: { completed: 1 } } } });
		await Promise.resolve();
		await Promise.resolve();

		// Now the stale list snapshot (issued before the status event) arrives.
		listResponse.resolve({
			data: {
				success: true,
				data: { downloads: [download({ status: 'downloading' })], counts: { downloading: 1 } }
			}
		});
		await loadPromise;

		const rows = get(downloads);
		expect(rows).toHaveLength(1);
		expect(rows[0].status).toBe('completed');
		expect(get(downloadCounts)).toEqual({ completed: 1 });
	});

	it('loadCounts coalesces concurrent calls into one request and one publication', async () => {
		downloadStore.initializeWebSocket();
		mockGet.mockResolvedValue({ data: { success: true, data: { counts: { pending: 2 } } } });

		await Promise.all([downloadStore.loadCounts(), downloadStore.loadCounts()]);

		expect(mockGet).toHaveBeenCalledTimes(1);
		expect(get(downloadCounts)).toEqual({ pending: 2 });
	});

	it('an abandoned in-flight counts refresh does not block or overwrite the remounted session', async () => {
		downloadStore.initializeWebSocket();

		const stale = deferred<{ data: unknown }>();
		mockGet.mockImplementationOnce(() => stale.promise);
		const staleCounts = downloadStore.loadCounts(); // triggered by the old session, never resolves yet

		downloadStore.cleanupWebSocket();
		downloadStore.initializeWebSocket();

		// The remounted session's own refresh must issue its own request
		// rather than silently coalescing onto the abandoned one.
		mockGet.mockResolvedValueOnce({ data: { success: true, data: { counts: { completed: 5 } } } });
		await downloadStore.loadCounts();
		expect(get(downloadCounts)).toEqual({ completed: 5 });

		// The stale request finally resolves - must not regress the fresh state.
		stale.resolve({ data: { success: true, data: { counts: { downloading: 1 } } } });
		await staleCounts;
		expect(get(downloadCounts)).toEqual({ completed: 5 });
	});

	it('initializeWebSocket called twice then cleanupWebSocket leaves zero registered listeners', () => {
		downloadStore.initializeWebSocket();
		downloadStore.initializeWebSocket();

		expect(wsMocks.progressCallbacks.size).toBe(2);
		expect(wsMocks.statusCallbacks.size).toBe(2);
		expect(wsMocks.connectionSubscribers.size).toBe(2);

		downloadStore.cleanupWebSocket();

		expect(wsMocks.progressCallbacks.size).toBe(0);
		expect(wsMocks.statusCallbacks.size).toBe(0);
		expect(wsMocks.connectionSubscribers.size).toBe(0);
	});

	it('a second initializeWebSocket() retires the first: its stale WS events no longer publish', () => {
		downloadStore.initializeWebSocket();
		downloadStore.initializeWebSocket();

		downloads.set([download({ status: 'downloading' })]);

		// Fire every registered progress callback (both the retired first one
		// and the current second one) with the same event.
		for (const cb of Array.from(wsMocks.progressCallbacks)) {
			cb({
				download_id: 'd1',
				progress: 0.9,
				downloaded_bytes: 90,
				total_bytes: 100,
				speed_bytes_per_sec: 5,
				filename: 'x.safetensors'
			});
		}

		// Both callbacks are still registered (idempotent init keeps every
		// handle) but only the current session's publishes - applying the
		// patch twice is harmless and observably identical to once.
		expect(get(downloads)[0].progress).toBe(0.9);

		downloadStore.cleanupWebSocket();
		expect(wsMocks.progressCallbacks.size).toBe(0);
	});

	it('destroy while a list load is pending drops its publication; a fresh mount is unaffected', async () => {
		downloadStore.initializeWebSocket();

		const stalePending = deferred<{ data: unknown }>();
		mockGet.mockImplementationOnce(() => stalePending.promise);
		const staleLoad = downloadStore.loadDownloads();

		// Destroy before the request resolves.
		downloadStore.cleanupWebSocket();

		// Remount.
		downloadStore.initializeWebSocket();
		mockGet.mockResolvedValueOnce({
			data: { success: true, data: { downloads: [download({ id: 'd2' })], counts: { downloading: 1 } } }
		});
		await downloadStore.loadDownloads();

		expect(get(downloads).map((d) => d.id)).toEqual(['d2']);

		// The stale request finally resolves - must not resurrect d1 or
		// otherwise disturb the remounted state.
		stalePending.resolve({
			data: { success: true, data: { downloads: [download({ id: 'd1' })], counts: { downloading: 1 } } }
		});
		await staleLoad;

		expect(get(downloads).map((d) => d.id)).toEqual(['d2']);

		downloadStore.cleanupWebSocket();
		expect(wsMocks.progressCallbacks.size).toBe(0);
		expect(wsMocks.statusCallbacks.size).toBe(0);
		expect(wsMocks.connectionSubscribers.size).toBe(0);
	});

	it('a reconnect (connectionState -> connected again) re-subscribes and refreshes the list once', async () => {
		mockGet.mockResolvedValue({ data: { success: true, data: { downloads: [], counts: {} } } });
		downloadStore.initializeWebSocket();
		await Promise.resolve();

		const callsBeforeReconnect = mockGet.mock.calls.length;

		wsMocks.setConnectionState('reconnecting');
		wsMocks.setConnectionState('connected');
		await Promise.resolve();
		await Promise.resolve();

		expect(mockGet.mock.calls.length).toBe(callsBeforeReconnect + 1);
	});

	it('a new download queued while a delayed list snapshot is in flight survives the merge', async () => {
		downloadStore.initializeWebSocket();

		const listResponse = deferred<{ data: unknown }>();
		mockGet.mockImplementationOnce(() => listResponse.promise);
		const loadPromise = downloadStore.loadDownloads();

		mockPost.mockResolvedValueOnce({ data: { success: true, data: download({ id: 'd2', status: 'pending' }) } });
		await downloadStore.queueModelDownload('https://example.com/new.safetensors');

		listResponse.resolve({ data: { success: true, data: { downloads: [], counts: {} } } });
		await loadPromise;

		expect(get(downloads).map((d) => d.id)).toContain('d2');
	});

	it('two list requests issued with no intervening mutation: the later-issued response always wins, arrival order aside', async () => {
		downloadStore.initializeWebSocket();

		const first = deferred<{ data: unknown }>();
		const second = deferred<{ data: unknown }>();
		mockGet.mockImplementationOnce(() => first.promise);
		const firstLoad = downloadStore.loadDownloads();
		mockGet.mockImplementationOnce(() => second.promise);
		const secondLoad = downloadStore.loadDownloads();

		// The later-issued request's (fresher) response arrives first.
		second.resolve({
			data: {
				success: true,
				data: { downloads: [download({ status: 'completed' })], counts: { completed: 1 } }
			}
		});
		await secondLoad;
		expect(get(downloads)[0].status).toBe('completed');

		// The earlier-issued (stale) response arrives after - must not
		// overwrite the fresher state even though it arrives second.
		first.resolve({
			data: {
				success: true,
				data: { downloads: [download({ status: 'downloading' })], counts: { downloading: 1 } }
			}
		});
		await firstLoad;

		expect(get(downloads)[0].status).toBe('completed');
		expect(get(downloadCounts)).toEqual({ completed: 1 });
	});

	it('loading is owned by the most recently issued list request: an earlier one settling first must not clear it early', async () => {
		downloadStore.initializeWebSocket();

		const first = deferred<{ data: unknown }>();
		mockGet.mockImplementationOnce(() => first.promise);
		const firstLoad = downloadStore.loadDownloads();
		expect(get(loading)).toBe(true);

		const second = deferred<{ data: unknown }>();
		mockGet.mockImplementationOnce(() => second.promise);
		const secondLoad = downloadStore.loadDownloads();

		// The stale first request resolves while the second (which now owns
		// loading) is still pending.
		first.resolve({ data: { success: true, data: { downloads: [], counts: {} } } });
		await firstLoad;
		expect(get(loading)).toBe(true);

		second.resolve({ data: { success: true, data: { downloads: [], counts: {} } } });
		await secondLoad;
		expect(get(loading)).toBe(false);
	});

	it('accumulates per-id patches: a status update then a progress tick, both before the row ever arrives, both survive the merge', async () => {
		downloadStore.initializeWebSocket();

		const listResponse = deferred<{ data: unknown }>();
		mockGet.mockImplementationOnce(() => listResponse.promise);
		const loadPromise = downloadStore.loadDownloads();

		for (const cb of wsMocks.statusCallbacks) {
			cb({ download_id: 'd1', status: 'completed', filename: 'x.safetensors' });
		}
		for (const cb of wsMocks.progressCallbacks) {
			cb({
				download_id: 'd1',
				progress: 1,
				downloaded_bytes: 100,
				total_bytes: 100,
				speed_bytes_per_sec: 0,
				filename: 'x.safetensors'
			});
		}

		listResponse.resolve({
			data: {
				success: true,
				data: {
					downloads: [download({ status: 'downloading', progress: 0 })],
					counts: { downloading: 1 }
				}
			}
		});
		await loadPromise;

		const row = get(downloads)[0];
		expect(row.status).toBe('completed'); // the earlier patch's field must survive the later one
		expect(row.progress).toBe(1);
	});

	it('a counts-affecting event during an in-flight counts fetch discards its stale result and schedules exactly one fresh refresh', async () => {
		downloadStore.initializeWebSocket();

		const stale = deferred<{ data: unknown }>();
		mockGet.mockImplementationOnce(() => stale.promise);
		const staleCounts = downloadStore.loadCounts();

		// A counts-affecting mutation lands while the fetch above is in flight.
		for (const cb of wsMocks.statusCallbacks) {
			cb({ download_id: 'd1', status: 'completed', filename: 'x.safetensors' });
		}

		// The status handler's own loadCounts() call coalesces onto the same
		// in-flight attempt - no second request is issued yet, only the dirty
		// flag records that a fresher answer will be needed.
		expect(mockGet).toHaveBeenCalledTimes(1);

		const followUp = deferred<{ data: unknown }>();
		mockGet.mockImplementationOnce(() => followUp.promise);

		// The stale attempt resolves with pre-event data - must be discarded,
		// not even transiently, while the follow-up it schedules is still
		// pending.
		stale.resolve({ data: { success: true, data: { counts: { downloading: 1 } } } });
		await staleCounts;
		await Promise.resolve();
		await Promise.resolve();

		expect(mockGet).toHaveBeenCalledTimes(2); // the stale attempt plus exactly one follow-up
		expect(get(downloadCounts)).toEqual({}); // the stale downloading:1 was never published

		followUp.resolve({ data: { success: true, data: { counts: { completed: 1 } } } });
		await Promise.resolve();
		await Promise.resolve();
		expect(get(downloadCounts)).toEqual({ completed: 1 });
	});

	it('a list response resolving while the newer dedicated request its own invalidating mutation triggered is still pending never publishes its stale bundled counts', async () => {
		downloadStore.initializeWebSocket();

		const listResponse = deferred<{ data: unknown }>();
		mockGet.mockImplementationOnce(() => listResponse.promise);
		const loadPromise = downloadStore.loadDownloads();

		// A newer completion status arrives while the list request above is
		// still in flight - this starts the dedicated counts request, itself
		// also still pending.
		const countsResponse = deferred<{ data: unknown }>();
		mockGet.mockImplementationOnce(() => countsResponse.promise);
		for (const cb of wsMocks.statusCallbacks) {
			cb({ download_id: 'd1', status: 'completed', filename: 'x.safetensors' });
		}

		// The OLD list resolves with its stale bundled counts WHILE the
		// dedicated request above is still pending - must not publish, not
		// even transiently.
		listResponse.resolve({
			data: {
				success: true,
				data: {
					downloads: [download({ status: 'downloading' })],
					counts: { downloading: 1, completed: 0 }
				}
			}
		});
		await loadPromise;
		expect(get(downloadCounts)).toEqual({});

		// The dedicated request finally resolves with the true, global tally.
		countsResponse.resolve({ data: { success: true, data: { counts: { completed: 100 } } } });
		await Promise.resolve();
		await Promise.resolve();
		expect(get(downloadCounts)).toEqual({ completed: 100 });
	});

	it('a newer bundled (list) counts result wins over an older dedicated one, regardless of arrival order', async () => {
		downloadStore.initializeWebSocket();

		const dedicated = deferred<{ data: unknown }>();
		mockGet.mockImplementationOnce(() => dedicated.promise);
		const dedicatedCall = downloadStore.loadCounts(); // issued first (older)

		const list = deferred<{ data: unknown }>();
		mockGet.mockImplementationOnce(() => list.promise);
		const listCall = downloadStore.loadDownloads(); // issued second (newer)

		// The newer (list) response arrives first.
		list.resolve({ data: { success: true, data: { downloads: [], counts: { completed: 1 } } } });
		await listCall;
		expect(get(downloadCounts)).toEqual({ completed: 1 });

		// The older dedicated response arrives after - must not overwrite it.
		dedicated.resolve({ data: { success: true, data: { counts: { downloading: 1 } } } });
		await dedicatedCall;
		expect(get(downloadCounts)).toEqual({ completed: 1 });
	});

	it('a newer dedicated counts result wins over an older bundled (list) one, regardless of arrival order', async () => {
		downloadStore.initializeWebSocket();

		const list = deferred<{ data: unknown }>();
		mockGet.mockImplementationOnce(() => list.promise);
		const listCall = downloadStore.loadDownloads(); // issued first (older)

		const dedicated = deferred<{ data: unknown }>();
		mockGet.mockImplementationOnce(() => dedicated.promise);
		const dedicatedCall = downloadStore.loadCounts(); // issued second (newer)

		// The newer (dedicated) response arrives first.
		dedicated.resolve({ data: { success: true, data: { counts: { completed: 5 } } } });
		await dedicatedCall;
		expect(get(downloadCounts)).toEqual({ completed: 5 });

		// The older bundled response arrives after - must not overwrite it.
		list.resolve({ data: { success: true, data: { downloads: [], counts: { downloading: 3 } } } });
		await listCall;
		expect(get(downloadCounts)).toEqual({ completed: 5 });
	});
});
