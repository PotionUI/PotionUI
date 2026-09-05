import { logger, getErrorMessage } from '$lib/utils/logger';
/**
 * Downloads Store
 *
 * Manages download state for the admin panel downloader.
 */

import { writable, derived, get } from 'svelte/store';
import type { Writable } from 'svelte/store';
import { api } from '$lib/services/api/index';
import { getBackends } from '$lib/services/admin-api';
import {
	downloaderWebSocket,
	downloaderConnectionState,
	type DownloadProgressUpdate,
	type DownloadStatusUpdate
} from '$lib/services/downloaderWebsocket';

const NATIVE_REMOTE_DRIVER = 'native.remote';

// Types
export type DownloadStatus =
	| 'pending'
	| 'downloading'
	| 'paused'
	| 'completed'
	| 'failed'
	| 'cancelled';
export type DownloadType = 'model' | 'media' | 'hf_repo';

export interface Download {
	id: string;
	type: DownloadType;
	url: string;
	destination_path: string;
	filename: string;
	status: DownloadStatus;
	progress: number;
	total_bytes: number | null;
	downloaded_bytes: number;
	speed_bytes_per_sec: number | null;
	error_message: string | null;
	provider_id: string | null;
	tags: string[];
	checksum_sha256: string | null;
	retry_count: number;
	group_id?: string | null;
	repo_id?: string | null;
	created_at: string | null;
	started_at: string | null;
	completed_at: string | null;
	created_by: string | null;
	/** Set when this model was fetched straight onto a `native.remote`
	 * backend's worker depot instead of local disk. */
	destination_backend_id?: string | null;
}

export interface RemoteDestinationBackend {
	id: string;
	name: string;
}

export interface QueueHfRepoDownloadOptions {
	destination_dir?: string;
	revision?: string;
	allow_patterns?: string[];
}

export interface DownloadSettings {
	max_concurrent_downloads: number;
	auto_retry_failed: boolean;
	max_retries: number;
	chunk_size_kb: number;
	verify_checksum: boolean;
	default_model_directory: string;
	default_media_directory: string;
}

export interface DownloadCounts {
	[status: string]: number;
}

export type DownloadBadgeVariant = 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'signal';

const STATUS_BADGE_VARIANTS: Record<DownloadStatus, DownloadBadgeVariant> = {
	pending: 'warning',
	downloading: 'signal',
	paused: 'neutral',
	completed: 'success',
	failed: 'danger',
	cancelled: 'neutral'
};

export function statusBadgeVariant(status: DownloadStatus): DownloadBadgeVariant {
	return STATUS_BADGE_VARIANTS[status] ?? 'neutral';
}

export function statusLabel(status: DownloadStatus): string {
	return status.charAt(0).toUpperCase() + status.slice(1);
}

/** Best-effort model type extracted from `destination_path` (e.g.
 * `.../models/checkpoint/x.safetensors` -> "checkpoint") - the Download
 * record itself carries no `model_type` field, only where it landed. */
export function modelTypeFromPath(destinationPath: string): string | null {
	const match = destinationPath.replace(/\\/g, '/').match(/\/models\/([^/]+)\//);
	return match ? match[1] : null;
}

export interface QueueModelDownloadOptions {
	destination_dir?: string;
	model_type?: string;
	filename?: string;
	tags?: string[];
	checksum_sha256?: string;
	provider_id?: string;
	destination_backend_id?: string;
}

// Stores
export const downloads: Writable<Download[]> = writable([]);
export const downloadCounts: Writable<DownloadCounts> = writable({});
export const downloadSettings: Writable<DownloadSettings | null> = writable(null);
export const loading: Writable<boolean> = writable(false);
export const error: Writable<string | null> = writable(null);
/** Configured `native.remote` backends the Downloader can target as a
 * destination - empty when none are configured, in which case the
 * destination picker stays hidden and every download is local (unchanged). */
export const remoteBackends: Writable<RemoteDestinationBackend[]> = writable([]);

// Derived stores
export const activeDownloads = derived(downloads, ($downloads) =>
	$downloads.filter((d) => d.status === 'downloading')
);

export const pendingDownloads = derived(downloads, ($downloads) =>
	$downloads.filter((d) => d.status === 'pending')
);

export const completedDownloads = derived(downloads, ($downloads) =>
	$downloads.filter((d) => d.status === 'completed')
);

export const failedDownloads = derived(downloads, ($downloads) =>
	$downloads.filter((d) => d.status === 'failed')
);

export const pausedDownloads = derived(downloads, ($downloads) =>
	$downloads.filter((d) => d.status === 'paused')
);

export const cancelledDownloads = derived(downloads, ($downloads) =>
	$downloads.filter((d) => d.status === 'cancelled')
);

// Helper functions
function formatBytes(bytes: number): string {
	if (bytes === 0) return '0 B';
	const k = 1024;
	const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
	const i = Math.floor(Math.log(bytes) / Math.log(k));
	return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function formatSpeed(bytesPerSec: number | null): string {
	if (!bytesPerSec) return '-';
	return formatBytes(bytesPerSec) + '/s';
}

function formatTimestamp(value: string | null | undefined): string {
	if (!value) return '-';
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return '-';
	return date.toLocaleTimeString(undefined, { hour12: false });
}

function formatEta(download: Download): string {
	if (!download.speed_bytes_per_sec || download.speed_bytes_per_sec <= 0) return '-';
	if (!download.total_bytes) return '-';
	const remaining = download.total_bytes - download.downloaded_bytes;
	if (remaining <= 0) return '0s';
	const seconds = Math.ceil(remaining / download.speed_bytes_per_sec);
	if (seconds < 60) return `${seconds}s`;
	if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
	const hours = Math.floor(seconds / 3600);
	const mins = Math.floor((seconds % 3600) / 60);
	return `${hours}h ${mins}m`;
}

// Store implementation
function createDownloadStore() {
	// WebSocket callback cleanup. Arrays (not single fields) so a second
	// initializeWebSocket() before a cleanup keeps every prior handle instead
	// of leaking the ones a reassignment would have dropped.
	let progressUnsubscribes: Array<() => void> = [];
	let statusUnsubscribes: Array<() => void> = [];
	let connectionUnsubscribes: Array<() => void> = [];

	// Bumped on every initializeWebSocket()/cleanupWebSocket() so an in-flight
	// read or a callback registered under a retired mount can tell it no
	// longer owns the store and must not publish.
	let sessionToken = 0;

	// Monotonic clock recording every local mutation to a download (WS event
	// or a queue/pause/resume/cancel/retry/delete command), keyed by id, so a
	// delayed list snapshot can tell whether its own view of an id is stale.
	let seqCounter = 0;
	interface Touch {
		seq: number;
		deleted: boolean;
		/** Fields to reapply on top of whatever row a merge finds - carries a
		 * WS-delivered field update through even when it arrives before the id
		 * has ever appeared in a list response. */
		patch?: Partial<Download>;
	}
	const perIdSeq = new Map<string, Touch>();
	let lastListSeqApplied = -1;
	let lastAppliedCountsSeq = -1;
	let countsInFlight: Promise<void> | null = null;

	function touchId(id: string, opts: { deleted?: boolean; patch?: Partial<Download> } = {}): void {
		perIdSeq.set(id, { seq: ++seqCounter, deleted: !!opts.deleted, patch: opts.patch });
	}

	function computeCountsFrom(list: Download[]): DownloadCounts {
		const counts: DownloadCounts = {};
		for (const d of list) {
			counts[d.status] = (counts[d.status] ?? 0) + 1;
		}
		return counts;
	}

	// Reconciles a list response issued at `seqAtIssue` against everything
	// that has happened locally since: an id touched after issue keeps its
	// current (already-newer) row instead of the snapshot's, a row the
	// snapshot doesn't know about yet but that was queued/touched since issue
	// is kept, and one deleted since issue is dropped.
	function mergeListSnapshot(incoming: Download[], seqAtIssue: number): Download[] {
		const current = get(downloads);
		const currentById = new Map(current.map((d) => [d.id, d] as const));
		const seen = new Set<string>();
		const merged: Download[] = [];

		for (const row of incoming) {
			seen.add(row.id);
			const touch = perIdSeq.get(row.id);
			if (touch && touch.seq > seqAtIssue) {
				if (touch.deleted) continue;
				const base = currentById.get(row.id) ?? row;
				merged.push(touch.patch ? { ...base, ...touch.patch } : base);
			} else {
				merged.push(row);
			}
		}

		for (const row of current) {
			if (seen.has(row.id)) continue;
			const touch = perIdSeq.get(row.id);
			if (touch && touch.seq > seqAtIssue && !touch.deleted) {
				merged.unshift(touch.patch ? { ...row, ...touch.patch } : row);
			}
		}

		return merged;
	}

	return {
		// Expose stores
		downloads,
		downloadCounts,
		downloadSettings,
		loading,
		error,
		activeDownloads,
		pendingDownloads,
		completedDownloads,
		failedDownloads,
		pausedDownloads,
		cancelledDownloads,
		remoteBackends,

		// Helpers
		formatBytes,
		formatSpeed,
		formatEta,
		formatTimestamp,

		// Initialize WebSocket handlers. Idempotent: a second call before a
		// cleanup bumps the session so the first call's callbacks and any
		// reads it started stop publishing, while every unsubscribe handle
		// (old and new) is kept so cleanupWebSocket() still removes them all.
		initializeWebSocket(): number {
			sessionToken += 1;
			const token = sessionToken;

			progressUnsubscribes.push(
				downloaderWebSocket.onDownloadProgress((update: DownloadProgressUpdate) => {
					if (token !== sessionToken) return;
					const patch: Partial<Download> = {
						progress: update.progress,
						downloaded_bytes: update.downloaded_bytes,
						total_bytes: update.total_bytes,
						speed_bytes_per_sec: update.speed_bytes_per_sec
					};
					touchId(update.download_id, { patch });
					downloads.update((currentDownloads) =>
						currentDownloads.map((d) => (d.id === update.download_id ? { ...d, ...patch } : d))
					);
				})
			);

			statusUnsubscribes.push(
				downloaderWebSocket.onDownloadStatus((update: DownloadStatusUpdate) => {
					if (token !== sessionToken) return;
					const patch: Partial<Download> = {
						status: update.status as DownloadStatus,
						error_message: update.error || null
					};
					touchId(update.download_id, { patch });
					downloads.update((currentDownloads) =>
						currentDownloads.map((d) => (d.id === update.download_id ? { ...d, ...patch } : d))
					);

					void this.loadCounts();
				})
			);

			// Re-subscribes on every transition to 'connected', including the
			// first - a reconnect drops the server-side subscription, so this
			// re-issues it and refreshes the list once to pick up whatever
			// happened while disconnected (the very first connect skips the
			// refresh; the view's own onMount already loads the list).
			let sawConnected = false;
			connectionUnsubscribes.push(
				downloaderConnectionState.subscribe((state) => {
					if (token !== sessionToken) return;
					if (state !== 'connected') return;
					downloaderWebSocket.subscribeToAllDownloads();
					if (sawConnected) {
						void this.loadDownloads();
					}
					sawConnected = true;
				})
			);

			return token;
		},

		// Cleanup WebSocket handlers
		cleanupWebSocket(): void {
			sessionToken += 1;
			for (const unsubscribe of progressUnsubscribes) unsubscribe();
			for (const unsubscribe of statusUnsubscribes) unsubscribe();
			for (const unsubscribe of connectionUnsubscribes) unsubscribe();
			progressUnsubscribes = [];
			statusUnsubscribes = [];
			connectionUnsubscribes = [];
			// Drop the in-flight counts request's slot (its own token check
			// still stops it from publishing) so a fresh mount's loadCounts()
			// starts its own fetch instead of silently coalescing onto - and
			// getting no publication from - a retired session's abandoned one.
			countsInFlight = null;
		},

		// Load downloads from API
		async loadDownloads(
			status?: DownloadStatus,
			type?: DownloadType,
			limit = 50,
			offset = 0
		): Promise<void> {
			const token = sessionToken;
			const seqAtIssue = seqCounter;
			loading.set(true);
			error.set(null);

			try {
				const params: Record<string, string> = {
					limit: limit.toString(),
					offset: offset.toString()
				};
				if (status) params['status'] = status;
				if (type) params['type'] = type;

				const searchParams = new URLSearchParams(params);
				const response = await api.getClient().get(`/api/downloads?${searchParams}`);
				const data = response.data;

				if (token !== sessionToken) return; // retired: view was torn down or replaced meanwhile

				if (data.success && data.data) {
					if (seqAtIssue >= lastListSeqApplied) {
						const incoming: Download[] = data.data.downloads || [];
						const merged = mergeListSnapshot(incoming, seqAtIssue);
						const changedSince = Array.from(perIdSeq.values()).some((t) => t.seq > seqAtIssue);
						downloads.set(merged);
						downloadCounts.set(changedSince ? computeCountsFrom(merged) : data.data.counts || {});
						lastListSeqApplied = seqAtIssue;
					}
				} else {
					throw new Error(data.message || 'Failed to load downloads');
				}
			} catch (err: unknown) {
				if (token === sessionToken) {
					error.set(getErrorMessage(err));
				}
				logger.error('Failed to load downloads:', err);
			} finally {
				if (token === sessionToken) {
					loading.set(false);
				}
			}
		},

		// Load counts only. Concurrent calls coalesce onto one in-flight
		// request/publication; a response older than the newest one already
		// applied is dropped instead of overwriting it.
		async loadCounts(): Promise<void> {
			if (countsInFlight) return countsInFlight;

			const token = sessionToken;
			const seqAtIssue = seqCounter;

			countsInFlight = (async () => {
				try {
					const response = await api.getClient().get('/api/downloads?limit=0');
					const data = response.data;
					if (token !== sessionToken) return;
					if (seqAtIssue < lastAppliedCountsSeq) return;
					if (data.success && data.data) {
						downloadCounts.set(data.data.counts || {});
						lastAppliedCountsSeq = seqAtIssue;
					}
				} catch (err) {
					logger.error('Failed to load download counts:', err);
				} finally {
					countsInFlight = null;
				}
			})();

			return countsInFlight;
		},

		// Load settings
		async loadSettings(): Promise<void> {
			const token = sessionToken;
			try {
				const response = await api.getClient().get('/api/downloads/settings');
				const data = response.data;
				if (token !== sessionToken) return;

				if (data.success && data.data) {
					downloadSettings.set(data.data);
				}
			} catch (err: unknown) {
				if (token === sessionToken) error.set(getErrorMessage(err));
				logger.error('Failed to load download settings:', err);
			}
		},

		// Update settings
		async updateSettings(settings: Partial<DownloadSettings>): Promise<boolean> {
			const token = sessionToken;
			loading.set(true);
			error.set(null);

			try {
				const response = await api.getClient().put('/api/downloads/settings', settings);
				const data = response.data;

				if (data.success && data.data) {
					if (token === sessionToken) downloadSettings.set(data.data);
					return true;
				} else {
					throw new Error(data.message || 'Failed to update settings');
				}
			} catch (err: unknown) {
				if (token === sessionToken) error.set(getErrorMessage(err));
				logger.error('Failed to update download settings:', err);
				return false;
			} finally {
				if (token === sessionToken) loading.set(false);
			}
		},

		// Load configured native.remote backends the Downloader can target
		async loadRemoteBackends(): Promise<void> {
			const token = sessionToken;
			try {
				const response = await getBackends();
				if (token !== sessionToken) return;
				if (response.success && response.data) {
					remoteBackends.set(
						response.data
							.filter((b) => b.driver === NATIVE_REMOTE_DRIVER && b.configured)
							.map((b) => ({ id: b.id, name: b.name }))
					);
				}
			} catch (err) {
				logger.error('Failed to load remote destination backends:', err);
			}
		},

		// Queue model download
		async queueModelDownload(
			url: string,
			options?: QueueModelDownloadOptions
		): Promise<Download | null> {
			const token = sessionToken;
			loading.set(true);
			error.set(null);

			try {
				const response = await api.getClient().post('/api/downloads/model', { url, ...options });
				const data = response.data;

				if (data.success && data.data) {
					if (token === sessionToken) {
						touchId(data.data.id);
						downloads.update((d) => [data.data, ...d]);
					}
					downloaderWebSocket.subscribeToDownload(data.data.id);
					return data.data;
				} else {
					throw new Error(data.message || 'Failed to queue download');
				}
			} catch (err: unknown) {
				if (token === sessionToken) error.set(getErrorMessage(err));
				logger.error('Failed to queue model download:', err);
				return null;
			} finally {
				if (token === sessionToken) loading.set(false);
			}
		},

		// Queue media download
		async queueMediaDownload(
			url: string,
			options?: {
				destination_dir?: string;
				filename?: string;
			}
		): Promise<Download | null> {
			const token = sessionToken;
			loading.set(true);
			error.set(null);

			try {
				const response = await api.getClient().post('/api/downloads/media', { url, ...options });
				const data = response.data;

				if (data.success && data.data) {
					if (token === sessionToken) {
						touchId(data.data.id);
						downloads.update((d) => [data.data, ...d]);
					}
					downloaderWebSocket.subscribeToDownload(data.data.id);
					return data.data;
				} else {
					throw new Error(data.message || 'Failed to queue download');
				}
			} catch (err: unknown) {
				if (token === sessionToken) error.set(getErrorMessage(err));
				logger.error('Failed to queue media download:', err);
				return null;
			} finally {
				if (token === sessionToken) loading.set(false);
			}
		},

		// Queue a whole Hugging Face repo as one grouped download
		async queueHfRepoDownload(
			repoId: string,
			options?: QueueHfRepoDownloadOptions
		): Promise<Download | null> {
			const token = sessionToken;
			loading.set(true);
			error.set(null);

			try {
				const response = await api
					.getClient()
					.post('/api/downloads/hf-repo', { repo_id: repoId, ...options });
				const data = response.data;

				if (data.success && data.data) {
					if (token === sessionToken) {
						touchId(data.data.id);
						downloads.update((d) => [data.data, ...d]);
					}
					downloaderWebSocket.subscribeToDownload(data.data.id);
					return data.data;
				} else {
					throw new Error(data.message || 'Failed to queue download');
				}
			} catch (err: unknown) {
				if (token === sessionToken) error.set(getErrorMessage(err));
				logger.error('Failed to queue HF repo download:', err);
				return null;
			} finally {
				if (token === sessionToken) loading.set(false);
			}
		},

		// Pause download
		async pauseDownload(downloadId: string): Promise<boolean> {
			const token = sessionToken;
			try {
				const response = await api.getClient().post(`/api/downloads/${downloadId}/pause`);
				const data = response.data;

				if (data.success) {
					if (token === sessionToken) {
						touchId(downloadId, { patch: { status: 'paused' } });
						downloads.update((d) =>
							d.map((dl) => (dl.id === downloadId ? { ...dl, status: 'paused' } : dl))
						);
					}
					return true;
				} else {
					throw new Error(data.message || 'Failed to pause download');
				}
			} catch (err: unknown) {
				if (token === sessionToken) error.set(getErrorMessage(err));
				logger.error('Failed to pause download:', err);
				return false;
			}
		},

		// Resume download
		async resumeDownload(downloadId: string): Promise<boolean> {
			const token = sessionToken;
			try {
				const response = await api.getClient().post(`/api/downloads/${downloadId}/resume`);
				const data = response.data;

				if (data.success) {
					if (token === sessionToken) {
						touchId(downloadId, { patch: { status: 'pending' } });
						downloads.update((d) =>
							d.map((dl) => (dl.id === downloadId ? { ...dl, status: 'pending' } : dl))
						);
					}
					return true;
				} else {
					throw new Error(data.message || 'Failed to resume download');
				}
			} catch (err: unknown) {
				if (token === sessionToken) error.set(getErrorMessage(err));
				logger.error('Failed to resume download:', err);
				return false;
			}
		},

		// Cancel download
		async cancelDownload(downloadId: string): Promise<boolean> {
			const token = sessionToken;
			try {
				const response = await api.getClient().post(`/api/downloads/${downloadId}/cancel`);
				const data = response.data;

				if (data.success) {
					if (token === sessionToken) {
						touchId(downloadId, { patch: { status: 'cancelled' } });
						downloads.update((d) =>
							d.map((dl) => (dl.id === downloadId ? { ...dl, status: 'cancelled' } : dl))
						);
					}
					return true;
				} else {
					throw new Error(data.message || 'Failed to cancel download');
				}
			} catch (err: unknown) {
				if (token === sessionToken) error.set(getErrorMessage(err));
				logger.error('Failed to cancel download:', err);
				return false;
			}
		},

		// Retry download
		async retryDownload(downloadId: string): Promise<boolean> {
			const token = sessionToken;
			try {
				const response = await api.getClient().post(`/api/downloads/${downloadId}/retry`);
				const data = response.data;

				if (data.success) {
					if (token === sessionToken) {
						touchId(downloadId, { patch: { status: 'pending', error_message: null } });
						downloads.update((d) =>
							d.map((dl) =>
								dl.id === downloadId ? { ...dl, status: 'pending', error_message: null } : dl
							)
						);
					}
					return true;
				} else {
					throw new Error(data.message || 'Failed to retry download');
				}
			} catch (err: unknown) {
				if (token === sessionToken) error.set(getErrorMessage(err));
				logger.error('Failed to retry download:', err);
				return false;
			}
		},

		// Delete download
		async deleteDownload(downloadId: string): Promise<boolean> {
			const token = sessionToken;
			try {
				const response = await api.getClient().delete(`/api/downloads/${downloadId}`);
				const data = response.data;

				if (data.success) {
					if (token === sessionToken) {
						touchId(downloadId, { deleted: true });
						downloads.update((d) => d.filter((dl) => dl.id !== downloadId));
					}
					return true;
				} else {
					throw new Error(data.message || 'Failed to delete download');
				}
			} catch (err: unknown) {
				if (token === sessionToken) error.set(getErrorMessage(err));
				logger.error('Failed to delete download:', err);
				return false;
			}
		},

		// Clear completed downloads
		async clearCompleted(): Promise<boolean> {
			const token = sessionToken;
			try {
				const response = await api.getClient().post('/api/downloads/clear-completed');
				const data = response.data;

				if (data.success) {
					if (token === sessionToken) {
						for (const dl of get(downloads)) {
							if (dl.status === 'completed') touchId(dl.id, { deleted: true });
						}
						downloads.update((d) => d.filter((dl) => dl.status !== 'completed'));
						void this.loadCounts();
					}
					return true;
				} else {
					throw new Error(data.message || 'Failed to clear completed');
				}
			} catch (err: unknown) {
				if (token === sessionToken) error.set(getErrorMessage(err));
				logger.error('Failed to clear completed downloads:', err);
				return false;
			}
		},

		// Clear error
		clearError(): void {
			error.set(null);
		},

		// Reset store
		reset(): void {
			this.cleanupWebSocket();
			perIdSeq.clear();
			lastListSeqApplied = -1;
			lastAppliedCountsSeq = -1;
			countsInFlight = null;
			downloads.set([]);
			downloadCounts.set({});
			downloadSettings.set(null);
			remoteBackends.set([]);
			loading.set(false);
			error.set(null);
		}
	};
}

export const downloadStore = createDownloadStore();
