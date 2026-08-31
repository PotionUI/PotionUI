/**
 * Downloader WebSocket Service
 *
 * The core download queue owns its own WebSocket endpoint and message
 * protocol (`/ws/downloads`) - the core admin socket (`/ws/admin`)
 * never relays download events. Separate lifecycle so the admin Downloads
 * tab can connect/disconnect independently of the core admin socket.
 */

import { writable, type Writable } from 'svelte/store';
import { getWsUrl } from './wsUrl';
import { api } from '$lib/services/api';
import { logger } from '$lib/utils/logger';
import type { BaseWebSocketMessage } from './BaseWebSocket';
import { StatefulWebSocket, type ConnectionState } from './StatefulWebSocket';

export type { ConnectionState };

export interface DownloadProgressUpdate {
	download_id: string;
	progress: number;
	downloaded_bytes: number;
	total_bytes: number | null;
	speed_bytes_per_sec: number | null;
	filename: string;
}

export interface DownloadStatusUpdate {
	download_id: string;
	status: string;
	filename: string;
	error?: string;
	path?: string;
}

export const downloaderConnectionState: Writable<ConnectionState> = writable('disconnected');

type DownloadProgressCallback = (update: DownloadProgressUpdate) => void;
type DownloadStatusCallback = (update: DownloadStatusUpdate) => void;

class DownloaderWebSocketService extends StatefulWebSocket {
	private downloadProgressCallbacks: Set<DownloadProgressCallback> = new Set();
	private downloadStatusCallbacks: Set<DownloadStatusCallback> = new Set();

	constructor() {
		super(downloaderConnectionState, 'downloader');
	}

	protected override buildWsUrl(): string {
		return getWsUrl('/ws/downloads', api.getToken());
	}

	protected override onMessage(message: BaseWebSocketMessage): void {
		switch (message.type) {
			case 'connection_established':
			case 'heartbeat':
			case 'pong':
			case 'subscribed':
			case 'unsubscribed':
			case 'subscribed_all':
				break;

			case 'download_progress':
				this.handleDownloadProgress(message);
				break;

			// The server derives these from DownloadStatus: 'download_' + enum value
			// (download_connection_hub.py broadcast_status). Keep in lockstep.
			case 'download_pending':
			case 'download_downloading':
			case 'download_paused':
			case 'download_completed':
			case 'download_failed':
			case 'download_cancelled':
				this.handleDownloadStatus(message);
				break;

			default:
				logger.debug('Unknown downloader message type:', message.type);
		}
	}

	subscribeToDownload(downloadId: string): void {
		this.send({ type: 'subscribe_download', download_id: downloadId });
	}

	unsubscribeFromDownload(downloadId: string): void {
		this.send({ type: 'unsubscribe_download', download_id: downloadId });
	}

	subscribeToAllDownloads(): void {
		this.send({ type: 'subscribe_all_downloads' });
	}

	onDownloadProgress(callback: DownloadProgressCallback): () => void {
		this.downloadProgressCallbacks.add(callback);
		return () => this.downloadProgressCallbacks.delete(callback);
	}

	onDownloadStatus(callback: DownloadStatusCallback): () => void {
		this.downloadStatusCallbacks.add(callback);
		return () => this.downloadStatusCallbacks.delete(callback);
	}

	private handleDownloadProgress(message: BaseWebSocketMessage): void {
		const update: DownloadProgressUpdate = {
			download_id: message.download_id as string,
			progress: message.progress as number,
			downloaded_bytes: message.downloaded_bytes as number,
			total_bytes: message.total_bytes as number | null,
			speed_bytes_per_sec: message.speed_bytes_per_sec as number | null,
			filename: message.filename as string
		};
		this.downloadProgressCallbacks.forEach((cb) => cb(update));
	}

	private handleDownloadStatus(message: BaseWebSocketMessage): void {
		const update: DownloadStatusUpdate = {
			download_id: message.download_id as string,
			status: (message.status as string) || message.type.replace('download_', ''),
			filename: message.filename as string,
			error: message.error as string | undefined,
			path: message.path as string | undefined
		};
		this.downloadStatusCallbacks.forEach((cb) => cb(update));
	}
}

export const downloaderWebSocket = new DownloaderWebSocketService();
