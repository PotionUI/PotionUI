/**
 * Admin WebSocket Service
 *
 * Manages WebSocket connection for admin panel real-time updates.
 * Separate from generation WebSocket to allow independent lifecycle management.
 */

import { writable, type Writable } from 'svelte/store';
import { getWsUrl } from './wsUrl';
import { api } from '$lib/services/api';
import { logger } from '$lib/utils/logger';
import type { BaseWebSocketMessage } from './BaseWebSocket';
import { StatefulWebSocket, type ConnectionState } from './StatefulWebSocket';

export type { ConnectionState };

// Admin notification interface
export interface AdminNotification {
	type: string;
	category: string;
	level: 'success' | 'info' | 'warning' | 'error';
	title: string;
	message: string;
	timestamp: Date;
}

// Stores for admin WebSocket state
export const adminConnectionState: Writable<ConnectionState> = writable('disconnected');
export const adminClientId: Writable<string | null> = writable(null);
export const adminNotifications: Writable<AdminNotification[]> = writable([]);

// Progress of a pipe requirement install (pip/git), which runs far too long
// for the POST that started it to carry its outcome.
export interface PipeInstallStatus {
	pipe: string;
	status: 'installing' | 'installed' | 'error';
	message: string | null;
}

// Event callbacks
type NotificationCallback = (notification: AdminNotification) => void;
type PipeInstallCallback = (status: PipeInstallStatus) => void;

class AdminWebSocketService extends StatefulWebSocket {
	private clientId: string | null = null;

	// Callbacks
	private notificationCallbacks: Set<NotificationCallback> = new Set();
	private pipeInstallCallbacks: Set<PipeInstallCallback> = new Set();

	constructor() {
		super(adminConnectionState, 'admin');
	}

	protected override buildWsUrl(): string {
		return getWsUrl('/ws/admin', api.getToken());
	}

	override disconnect(): void {
		super.disconnect();
		this.clientId = null;
		adminClientId.set(null);
	}

	protected override onMessage(message: BaseWebSocketMessage): void {
		switch (message.type) {
			case 'connection_established':
				this.clientId = message.client_id as string | null;
				adminClientId.set(message.client_id as string | null);
				logger.debug('Admin WebSocket client ID:', message.client_id);
				break;

			case 'heartbeat':
			case 'pong':
				break;

			case 'notification':
				this.handleNotification(message);
				break;

			case 'pipe_install_status':
				this.pipeInstallCallbacks.forEach((cb) =>
					cb({
						pipe: message.pipe as string,
						status: message.status as PipeInstallStatus['status'],
						message: (message.message as string | null) ?? null
					})
				);
				break;

			default:
				logger.debug('Unknown admin message type:', message.type);
		}
	}

	getClientId(): string | null {
		return this.clientId;
	}

	// ─── Callback registration ────────────────────────────────────────────────

	onNotification(callback: NotificationCallback): () => void {
		this.notificationCallbacks.add(callback);
		return () => this.notificationCallbacks.delete(callback);
	}

	onPipeInstallStatus(callback: PipeInstallCallback): () => void {
		this.pipeInstallCallbacks.add(callback);
		return () => this.pipeInstallCallbacks.delete(callback);
	}

	clearNotifications(): void {
		adminNotifications.set([]);
	}

	// ─── Private message handlers ─────────────────────────────────────────────

	private handleNotification(message: BaseWebSocketMessage): void {
		const notification: AdminNotification = {
			type: 'notification',
			category: message.category as string,
			level: message.level as AdminNotification['level'],
			title: message.title as string,
			message: message.message as string,
			timestamp: new Date()
		};
		this.addNotification(notification);
		this.notificationCallbacks.forEach((cb) => cb(notification));
	}

	private addNotification(notification: AdminNotification): void {
		adminNotifications.update((notifications) => {
			const updated = [notification, ...notifications];
			return updated.slice(0, 50);
		});
	}
}

// Export singleton instance
export const adminWebSocket = new AdminWebSocketService();
