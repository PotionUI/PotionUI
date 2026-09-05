import type { Writable } from 'svelte/store';
import { logger } from '$lib/utils/logger';
import { BaseWebSocket } from './BaseWebSocket';

export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'reconnecting';

/** Typed rejection reason for a connectAsync() waiter that will never see an open socket. */
export class WebSocketConnectError extends Error {
	constructor(message: string) {
		super(message);
		this.name = 'WebSocketConnectError';
	}
}

interface ConnectWaiter {
	resolve: () => void;
	reject: (reason: unknown) => void;
}

/**
 * Base for the admin-panel-style sockets (admin, downloader): a connection-state
 * store, a connectAsync() promise that resolves/rejects on open/error, and
 * exponential-backoff reconnection up to maxReconnectAttempts. Subclasses still
 * own buildWsUrl() and onMessage().
 *
 * connectAsync() contract: a call made while a handshake is already CONNECTING
 * coalesces onto that same handshake instead of opening a second socket, and
 * every coalesced caller settles together. All outstanding callers resolve
 * once, on open. A close before ever opening does not reject by itself - it
 * keeps retrying (matching pre-existing behaviour) and the waiter resolves
 * on a later open. Callers reject once (with a typed reason where this class
 * controls it) on: explicit disconnect(), the WebSocket constructor throwing
 * (treated as non-retryable - rejected immediately with no backoff attempt,
 * since the same construction is expected to throw again), or reconnect
 * attempts being exhausted. No caller is left pending once the underlying
 * attempt is decided.
 *
 * Every scheduled reconnect is tagged with the (inherited) generation live
 * when it was scheduled; disconnect() bumps it and cancels the pending
 * timer, so a retry already in flight cannot create a socket for a lifetime
 * the caller has since abandoned.
 */
export abstract class StatefulWebSocket extends BaseWebSocket {
	protected reconnectAttempts = 0;
	protected readonly maxReconnectAttempts = 5;
	protected intentionalDisconnect = false;

	private waiters: ConnectWaiter[] = [];

	protected constructor(
		private readonly connectionState: Writable<ConnectionState>,
		/** Only used in log lines ("admin WebSocket connected", "downloader reconnect failed"). */
		private readonly serviceName: string
	) {
		super('', null);
	}

	connectAsync(): Promise<void> {
		if (this.ws && this.ws.readyState === WebSocket.OPEN) {
			return Promise.resolve();
		}

		const alreadyConnecting = this.ws !== null && this.ws.readyState === WebSocket.CONNECTING;

		return new Promise<void>((resolve, reject) => {
			this.waiters.push({ resolve, reject });
			if (alreadyConnecting) return; // coalesce onto the in-flight handshake

			this.cancelReconnect();
			this.intentionalDisconnect = false;
			this.connectionState.set('connecting');
			this.connect(); // bumps the inherited generation for the new attempt
		});
	}

	disconnect(): void {
		this.intentionalDisconnect = true;
		this.reconnectAttempts = 0;
		this.generation++;
		super.disconnect();
		this.connectionState.set('disconnected');
		this.settleWaiters('reject', new WebSocketConnectError(`${this.serviceName}: disconnected`));
	}

	protected override onOpen(): void {
		logger.debug(`${this.serviceName} WebSocket connected`);
		this.connectionState.set('connected');
		this.reconnectAttempts = 0;
		this.currentReconnectDelay = this.reconnectDelay;
		this.settleWaiters('resolve');
	}

	protected override onError(error: Event): void {
		logger.error(`${this.serviceName} WebSocket error:`, error);
		this.settleWaiters('reject', error);
	}

	protected override onClose(event: CloseEvent): void {
		logger.debug(`${this.serviceName} WebSocket closed`);
		this.connectionState.set('disconnected');

		if (this.intentionalDisconnect) return;

		// Whether or not this attempt ever opened, a pending waiter stays
		// pending through the retry chain - it settles on the next open, or on
		// scheduleReconnect() rejecting it once attempts are exhausted.
		super.onClose(event); // BaseWebSocket.onClose() -> this.scheduleReconnect()
	}

	protected override onConstructError(error: unknown): void {
		logger.error(`${this.serviceName} WebSocket construction failed:`, error);
		// Non-retryable: the same URL/environment is expected to throw again, so
		// don't burn an attempt slot waiting out a backoff for it.
		this.connectionState.set('disconnected');
		this.settleWaiters('reject', error);
	}

	/**
	 * Replaces BaseWebSocket's reconnect scheduling: attempt-counted with a
	 * cap, drives connectionState, and rejects any still-pending waiters once
	 * attempts are exhausted. Reuses the inherited reconnectTimer field so
	 * disconnect() (via BaseWebSocket.cancelReconnect()) clears it for free.
	 */
	protected override scheduleReconnect(): void {
		if (this.reconnectTimer) return;

		if (this.reconnectAttempts >= this.maxReconnectAttempts) {
			logger.error(`Max reconnect attempts reached for ${this.serviceName} WebSocket`);
			this.settleWaiters('reject', new WebSocketConnectError(`${this.serviceName}: max reconnect attempts reached`));
			return;
		}

		this.reconnectAttempts++;
		const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);
		logger.debug(`Attempting ${this.serviceName} reconnect in ${delay}ms (attempt ${this.reconnectAttempts})`);

		this.connectionState.set('reconnecting');

		const gen = this.generation;
		this.reconnectTimer = setTimeout(() => {
			this.reconnectTimer = null;
			if (gen !== this.generation) return; // superseded by a disconnect()/fresh connectAsync() meanwhile
			this.connectionState.set('connecting');
			this.connect();
		}, delay);
	}

	isConnected(): boolean {
		return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
	}

	private settleWaiters(outcome: 'resolve' | 'reject', reason?: unknown): void {
		const pending = this.waiters;
		this.waiters = [];
		for (const waiter of pending) {
			if (outcome === 'resolve') waiter.resolve();
			else waiter.reject(reason);
		}
	}
}
