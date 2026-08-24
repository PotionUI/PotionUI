import type { Writable } from 'svelte/store';
import { logger } from '$lib/utils/logger';
import { BaseWebSocket } from './BaseWebSocket';

export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting';

/**
 * Base for the admin-panel-style sockets (admin, downloader): a connection-state
 * store, a connectAsync() promise that resolves/rejects on open/error, and
 * exponential-backoff reconnection up to maxReconnectAttempts. Subclasses still
 * own buildWsUrl() and onMessage().
 */
export abstract class StatefulWebSocket extends BaseWebSocket {
	protected reconnectAttempts = 0;
	protected readonly maxReconnectAttempts = 5;
	protected intentionalDisconnect = false;

	private connectResolve: (() => void) | null = null;
	private connectReject: ((reason?: unknown) => void) | null = null;

	protected constructor(
		private readonly connectionState: Writable<ConnectionState>,
		/** Only used in log lines ("admin WebSocket connected", "downloader reconnect failed"). */
		private readonly serviceName: string
	) {
		super('', null);
	}

	connectAsync(): Promise<void> {
		return new Promise((resolve, reject) => {
			if (this.ws && this.ws.readyState === WebSocket.OPEN) {
				resolve();
				return;
			}

			this.intentionalDisconnect = false;
			this.connectResolve = resolve;
			this.connectReject = reject;

			this.connectionState.set('connecting');
			this.connect();
		});
	}

	disconnect(): void {
		this.intentionalDisconnect = true;
		super.disconnect();
		this.connectionState.set('disconnected');
	}

	protected override onOpen(): void {
		logger.debug(`${this.serviceName} WebSocket connected`);
		this.connectionState.set('connected');
		this.reconnectAttempts = 0;
		this.currentReconnectDelay = this.reconnectDelay;

		if (this.connectResolve) {
			this.connectResolve();
			this.connectResolve = null;
			this.connectReject = null;
		}
	}

	protected override onError(error: Event): void {
		logger.error(`${this.serviceName} WebSocket error:`, error);
		if (this.connectReject) {
			this.connectReject(error);
			this.connectResolve = null;
			this.connectReject = null;
		}
	}

	protected override onClose(_event: CloseEvent): void {
		logger.debug(`${this.serviceName} WebSocket closed`);
		this.connectionState.set('disconnected');

		if (!this.intentionalDisconnect) {
			this.attemptReconnect();
		}
	}

	isConnected(): boolean {
		return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
	}

	private attemptReconnect(): void {
		if (this.reconnectAttempts >= this.maxReconnectAttempts) {
			logger.error(`Max reconnect attempts reached for ${this.serviceName} WebSocket`);
			return;
		}

		this.reconnectAttempts++;
		const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);
		logger.debug(`Attempting ${this.serviceName} reconnect in ${delay}ms (attempt ${this.reconnectAttempts})`);

		this.connectionState.set('reconnecting');

		setTimeout(() => {
			this.connectAsync().catch((err) => {
				logger.error(`${this.serviceName} reconnect failed:`, err);
			});
		}, delay);
	}
}
