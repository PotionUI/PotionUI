import { logger, getErrorMessage } from '$lib/utils/logger';
/**
 * Downloads Store
 *
 * Manages download state for the admin panel downloader.
 */

import { writable, derived } from 'svelte/store';
import type { Writable } from 'svelte/store';
import { api } from '$lib/services/api/index';
import { getBackends } from '$lib/services/admin-api';
import {
	downloaderWebSocket,
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
	// WebSocket callbacks cleanup
	let progressUnsubscribe: (() => void) | null = null;
	let statusUnsubscribe: (() => void) | null = null;

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

		// Initialize WebSocket handlers
		initializeWebSocket(): void {
			progressUnsubscribe = downloaderWebSocket.onDownloadProgress((update: DownloadProgressUpdate) => {
				downloads.update((currentDownloads) =>
					currentDownloads.map((d) =>
						d.id === update.download_id
							? {
									...d,
									progress: update.progress,
									downloaded_bytes: update.downloaded_bytes,
									total_bytes: update.total_bytes,
									speed_bytes_per_sec: update.speed_bytes_per_sec
								}
							: d
					)
				);
			});

			statusUnsubscribe = downloaderWebSocket.onDownloadStatus((update: DownloadStatusUpdate) => {
				downloads.update((currentDownloads) =>
					currentDownloads.map((d) =>
						d.id === update.download_id
							? {
									...d,
									status: update.status as DownloadStatus,
									error_message: update.error || null
								}
							: d
					)
				);

				this.loadCounts();
			});

			downloaderWebSocket.subscribeToAllDownloads();
		},

		// Cleanup WebSocket handlers
		cleanupWebSocket(): void {
			if (progressUnsubscribe) {
				progressUnsubscribe();
				progressUnsubscribe = null;
			}
			if (statusUnsubscribe) {
				statusUnsubscribe();
				statusUnsubscribe = null;
			}
		},

		// Load downloads from API
		async loadDownloads(
			status?: DownloadStatus,
			type?: DownloadType,
			limit = 50,
			offset = 0
		): Promise<void> {
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

				if (data.success && data.data) {
					downloads.set(data.data.downloads || []);
					downloadCounts.set(data.data.counts || {});
				} else {
					throw new Error(data.message || 'Failed to load downloads');
				}
			} catch (err: unknown) {
				error.set(getErrorMessage(err));
				logger.error('Failed to load downloads:', err);
			} finally {
				loading.set(false);
			}
		},

		// Load counts only
		async loadCounts(): Promise<void> {
			try {
				const response = await api.getClient().get('/api/downloads?limit=0');
				const data = response.data;
				if (data.success && data.data) {
					downloadCounts.set(data.data.counts || {});
				}
			} catch (err) {
				logger.error('Failed to load download counts:', err);
			}
		},

		// Load settings
		async loadSettings(): Promise<void> {
			try {
				const response = await api.getClient().get('/api/downloads/settings');
				const data = response.data;

				if (data.success && data.data) {
					downloadSettings.set(data.data);
				}
			} catch (err: unknown) {
				error.set(getErrorMessage(err));
				logger.error('Failed to load download settings:', err);
			}
		},

		// Update settings
		async updateSettings(settings: Partial<DownloadSettings>): Promise<boolean> {
			loading.set(true);
			error.set(null);

			try {
				const response = await api.getClient().put('/api/downloads/settings', settings);
				const data = response.data;

				if (data.success && data.data) {
					downloadSettings.set(data.data);
					return true;
				} else {
					throw new Error(data.message || 'Failed to update settings');
				}
			} catch (err: unknown) {
				error.set(getErrorMessage(err));
				logger.error('Failed to update download settings:', err);
				return false;
			} finally {
				loading.set(false);
			}
		},

		// Load configured native.remote backends the Downloader can target
		async loadRemoteBackends(): Promise<void> {
			try {
				const response = await getBackends();
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
			loading.set(true);
			error.set(null);

			try {
				const response = await api.getClient().post('/api/downloads/model', { url, ...options });
				const data = response.data;

				if (data.success && data.data) {
					downloads.update((d) => [data.data, ...d]);
					downloaderWebSocket.subscribeToDownload(data.data.id);
					return data.data;
				} else {
					throw new Error(data.message || 'Failed to queue download');
				}
			} catch (err: unknown) {
				error.set(getErrorMessage(err));
				logger.error('Failed to queue model download:', err);
				return null;
			} finally {
				loading.set(false);
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
			loading.set(true);
			error.set(null);

			try {
				const response = await api.getClient().post('/api/downloads/media', { url, ...options });
				const data = response.data;

				if (data.success && data.data) {
					downloads.update((d) => [data.data, ...d]);
					downloaderWebSocket.subscribeToDownload(data.data.id);
					return data.data;
				} else {
					throw new Error(data.message || 'Failed to queue download');
				}
			} catch (err: unknown) {
				error.set(getErrorMessage(err));
				logger.error('Failed to queue media download:', err);
				return null;
			} finally {
				loading.set(false);
			}
		},

		// Queue a whole Hugging Face repo as one grouped download
		async queueHfRepoDownload(
			repoId: string,
			options?: QueueHfRepoDownloadOptions
		): Promise<Download | null> {
			loading.set(true);
			error.set(null);

			try {
				const response = await api
					.getClient()
					.post('/api/downloads/hf-repo', { repo_id: repoId, ...options });
				const data = response.data;

				if (data.success && data.data) {
					downloads.update((d) => [data.data, ...d]);
					downloaderWebSocket.subscribeToDownload(data.data.id);
					return data.data;
				} else {
					throw new Error(data.message || 'Failed to queue download');
				}
			} catch (err: unknown) {
				error.set(getErrorMessage(err));
				logger.error('Failed to queue HF repo download:', err);
				return null;
			} finally {
				loading.set(false);
			}
		},

		// Pause download
		async pauseDownload(downloadId: string): Promise<boolean> {
			try {
				const response = await api.getClient().post(`/api/downloads/${downloadId}/pause`);
				const data = response.data;

				if (data.success) {
					downloads.update((d) =>
						d.map((dl) => (dl.id === downloadId ? { ...dl, status: 'paused' } : dl))
					);
					return true;
				} else {
					throw new Error(data.message || 'Failed to pause download');
				}
			} catch (err: unknown) {
				error.set(getErrorMessage(err));
				logger.error('Failed to pause download:', err);
				return false;
			}
		},

		// Resume download
		async resumeDownload(downloadId: string): Promise<boolean> {
			try {
				const response = await api.getClient().post(`/api/downloads/${downloadId}/resume`);
				const data = response.data;

				if (data.success) {
					downloads.update((d) =>
						d.map((dl) => (dl.id === downloadId ? { ...dl, status: 'pending' } : dl))
					);
					return true;
				} else {
					throw new Error(data.message || 'Failed to resume download');
				}
			} catch (err: unknown) {
				error.set(getErrorMessage(err));
				logger.error('Failed to resume download:', err);
				return false;
			}
		},

		// Cancel download
		async cancelDownload(downloadId: string): Promise<boolean> {
			try {
				const response = await api.getClient().post(`/api/downloads/${downloadId}/cancel`);
				const data = response.data;

				if (data.success) {
					downloads.update((d) =>
						d.map((dl) => (dl.id === downloadId ? { ...dl, status: 'cancelled' } : dl))
					);
					return true;
				} else {
					throw new Error(data.message || 'Failed to cancel download');
				}
			} catch (err: unknown) {
				error.set(getErrorMessage(err));
				logger.error('Failed to cancel download:', err);
				return false;
			}
		},

		// Retry download
		async retryDownload(downloadId: string): Promise<boolean> {
			try {
				const response = await api.getClient().post(`/api/downloads/${downloadId}/retry`);
				const data = response.data;

				if (data.success) {
					downloads.update((d) =>
						d.map((dl) =>
							dl.id === downloadId ? { ...dl, status: 'pending', error_message: null } : dl
						)
					);
					return true;
				} else {
					throw new Error(data.message || 'Failed to retry download');
				}
			} catch (err: unknown) {
				error.set(getErrorMessage(err));
				logger.error('Failed to retry download:', err);
				return false;
			}
		},

		// Delete download
		async deleteDownload(downloadId: string): Promise<boolean> {
			try {
				const response = await api.getClient().delete(`/api/downloads/${downloadId}`);
				const data = response.data;

				if (data.success) {
					downloads.update((d) => d.filter((dl) => dl.id !== downloadId));
					return true;
				} else {
					throw new Error(data.message || 'Failed to delete download');
				}
			} catch (err: unknown) {
				error.set(getErrorMessage(err));
				logger.error('Failed to delete download:', err);
				return false;
			}
		},

		// Clear completed downloads
		async clearCompleted(): Promise<boolean> {
			try {
				const response = await api.getClient().post('/api/downloads/clear-completed');
				const data = response.data;

				if (data.success) {
					downloads.update((d) => d.filter((dl) => dl.status !== 'completed'));
					this.loadCounts();
					return true;
				} else {
					throw new Error(data.message || 'Failed to clear completed');
				}
			} catch (err: unknown) {
				error.set(getErrorMessage(err));
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
