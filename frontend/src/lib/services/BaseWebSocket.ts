import { logger } from '$lib/utils/logger';

export type BaseWebSocketMessage = {
	type: string;
	[key: string]: unknown;
};

/**
 * Abstract base class for WebSocket services.
 *
 * Provides shared logic for:
 * - Connection management (connect, disconnect)
 * - Heartbeat / ping-pong
 * - Exponential backoff reconnection
 * - Auth token injection via query param
 */
export abstract class BaseWebSocket {
	protected ws: WebSocket | null = null;
	protected readonly reconnectDelay: number = 1000;
	protected readonly maxReconnectDelay: number = 30000;
	protected currentReconnectDelay: number = 1000;
	protected reconnectTimer: ReturnType<typeof setTimeout> | null = null;
	protected heartbeatInterval: ReturnType<typeof setInterval> | null = null;
	/**
	 * Bumped every time connect() actually constructs a socket. A closing/closed
	 * socket can linger with its handlers still attached while a replacement is
	 * created (connect()'s guard only blocks OPEN/CONNECTING) - each handler
	 * captures the socket and token it was registered for and no-ops once
	 * either has been superseded, so a stale socket's events can never touch
	 * this.ws, the heartbeat, or connection state again.
	 */
	protected generation = 0;

	constructor(
		protected readonly url: string,
		protected token: string | null
	) {}

	// ─── Public API ──────────────────────────────────────────────────────────

	/**
	 * Open the WebSocket connection. Subclasses call super.connect() or use
	 * the buildWsUrl() helper directly.
	 */
	connect(): void {
		// CONNECTING counts too: a second connect() while the handshake is in
		// flight would orphan the first socket with its handlers still attached.
		if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) return;

		const wsUrl = this.buildWsUrl();

		try {
			const socket = new WebSocket(wsUrl);
			const token = ++this.generation;
			this.ws = socket;
			// True once this specific socket+token is still the active attempt -
			// every handler below checks this before touching shared state.
			const isCurrent = () => socket === this.ws && token === this.generation;

			socket.onopen = () => {
				if (!isCurrent()) return;
				logger.debug(`[${this.constructor.name}] connected`);
				this.currentReconnectDelay = this.reconnectDelay;
				this.onOpen();
				this.startHeartbeat();
			};

			socket.onmessage = (event) => {
				if (!isCurrent()) return;
				try {
					const message = JSON.parse(event.data) as BaseWebSocketMessage;
					this.onMessage(message);
				} catch (error) {
					logger.error(`[${this.constructor.name}] Failed to parse message:`, error);
				}
			};

			socket.onerror = (error) => {
				if (!isCurrent()) return;
				logger.error(`[${this.constructor.name}] error:`, error);
				this.onError(error);
			};

			socket.onclose = (event) => {
				if (!isCurrent()) return;
				logger.debug(`[${this.constructor.name}] disconnected, code:`, event.code);
				this.stopHeartbeat();
				this.onClose(event);
			};
		} catch (error) {
			logger.error(`[${this.constructor.name}] Failed to create WebSocket:`, error);
			this.onConstructError(error);
		}
	}

	disconnect(): void {
		this.cancelReconnect();
		this.stopHeartbeat();
		if (this.ws) {
			// Detach handlers BEFORE closing: close() fires onclose
			// asynchronously, and the base onClose schedules a reconnect —
			// which would resurrect this deliberately-disconnected service as
			// a background orphan (one per page mount/unmount cycle; Svelte
			// hydration destroys and remounts the page, so this happened on
			// every load). Closing a CONNECTING socket also logs the browser's
			// "closed before the connection is established" — expected here.
			this.ws.onopen = null;
			this.ws.onmessage = null;
			this.ws.onerror = null;
			this.ws.onclose = null;
			this.ws.close();
			this.ws = null;
		}
	}

	isConnected(): boolean {
		return this.ws !== null && this.ws.readyState === WebSocket.OPEN;
	}

	// ─── Protected hooks (override in subclasses) ────────────────────────────

	/** Called after the connection opens. */
	protected onOpen(): void {}

	/** Called with every parsed incoming message. */
	protected abstract onMessage(message: BaseWebSocketMessage): void;

	/** Called on a socket error event. */
	protected onError(_error: Event): void {}

	/** Called when the socket closes. Base implementation schedules reconnect. */
	protected onClose(_event: CloseEvent): void {
		this.scheduleReconnect();
	}

	/** Called when `new WebSocket()` itself throws. Base implementation schedules reconnect. */
	protected onConstructError(_error: unknown): void {
		this.scheduleReconnect();
	}

	// ─── Helpers ─────────────────────────────────────────────────────────────

	protected buildWsUrl(): string {
		return this.token
			? `${this.url}?token=${encodeURIComponent(this.token)}`
			: this.url;
	}

	protected send(data: unknown): void {
		if (this.ws?.readyState === WebSocket.OPEN) {
			this.ws.send(JSON.stringify(data));
		}
	}

	protected scheduleReconnect(): void {
		if (this.reconnectTimer) return;

		this.reconnectTimer = setTimeout(() => {
			this.reconnectTimer = null;
			this.currentReconnectDelay = Math.min(
				this.currentReconnectDelay * 2,
				this.maxReconnectDelay
			);
			this.connect();
		}, this.currentReconnectDelay);
	}

	protected cancelReconnect(): void {
		if (this.reconnectTimer) {
			clearTimeout(this.reconnectTimer);
			this.reconnectTimer = null;
		}
	}

	protected startHeartbeat(): void {
		this.stopHeartbeat();
		this.heartbeatInterval = setInterval(() => {
			this.send({ type: 'ping', timestamp: Date.now() });
		}, 30000);
	}

	protected stopHeartbeat(): void {
		if (this.heartbeatInterval) {
			clearInterval(this.heartbeatInterval);
			this.heartbeatInterval = null;
		}
	}
}
