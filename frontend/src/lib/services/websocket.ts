import { goto } from '$app/navigation';
import { logger } from '$lib/utils/logger';
import { api } from '$lib/services/api/index';
import { authStore } from '$lib/stores/auth';
import { getWsUrl } from './wsUrl';
import { BaseWebSocket, type BaseWebSocketMessage } from './BaseWebSocket';

export type WebSocketMessage = BaseWebSocketMessage & {
	generation_id?: string;
};

export type MessageHandler = (message: WebSocketMessage) => void;

export class WebSocketService extends BaseWebSocket {
	private subscriptions: Map<string, Set<MessageHandler>> = new Map();
	private isConnectedValue: boolean = false;
	private connectionCallbacks: Set<(connected: boolean) => void> = new Set();
	private onAuthFailedCallback: (() => void) | null = null;

	constructor(url: string, token: string | null) {
		super(url, token);
	}

	// ─── Overrides ────────────────────────────────────────────────────────────

	connect(): void {
		if (this.ws?.readyState === WebSocket.OPEN) return;
		super.connect();
	}

	disconnect(): void {
		super.disconnect();
		this.isConnectedValue = false;
		this.notifyConnectionChange(false);
	}

	protected override onOpen(): void {
		this.isConnectedValue = true;
		this.notifyConnectionChange(true);
		// Re-subscribe all pending subscriptions
		for (const generationId of this.subscriptions.keys()) {
			this.sendSubscription(generationId);
		}
	}

	protected override onMessage(message: BaseWebSocketMessage): void {
		const wsMessage = message as WebSocketMessage;

		if (
			wsMessage.type === 'connection_established' ||
			wsMessage.type === 'heartbeat' ||
			wsMessage.type === 'pong'
		) {
			return;
		}

		// Extract generation ID - completion messages have it in data.id, others at top level
		let generationId = wsMessage.generation_id;
		if (
			wsMessage.type === 'generation_complete' ||
			wsMessage.type === 'generation_error' ||
			wsMessage.type === 'generation_cancelled'
		) {
			const data = wsMessage.data as Record<string, unknown> | undefined;
			generationId =
				(data?.id as string) ||
				(data?.generation_id as string) ||
				wsMessage.generation_id;
		}

		if (generationId) {
			const handlers = this.subscriptions.get(generationId);
			if (handlers) {
				handlers.forEach((handler) => handler(wsMessage));
			} else {
				logger.warn(`No handlers found for generation_id: ${generationId}`);
			}
		} else {
			logger.warn(`Message ${wsMessage.type} has no generation_id`);
		}
	}

	protected override onClose(event: CloseEvent): void {
		this.isConnectedValue = false;
		this.notifyConnectionChange(false);

		if (event.code === 4001) {
			logger.warn('WebSocket auth failed (4001), not reconnecting');
			this.onAuthFailedCallback?.();
			return;
		}

		this.scheduleReconnect();
	}

	// ─── Public API ──────────────────────────────────────────────────────────

	subscribe(generationId: string, handler: MessageHandler): void {
		if (!this.subscriptions.has(generationId)) {
			this.subscriptions.set(generationId, new Set());
		}
		this.subscriptions.get(generationId)!.add(handler);

		if (this.ws?.readyState === WebSocket.OPEN) {
			this.sendSubscription(generationId);
		}
	}

	unsubscribe(generationId: string, handler?: MessageHandler): void {
		if (handler) {
			this.subscriptions.get(generationId)?.delete(handler);
		} else {
			this.subscriptions.delete(generationId);
		}
	}

	isConnected(): boolean {
		return this.isConnectedValue;
	}

	onConnectionChange(callback: (connected: boolean) => void): () => void {
		this.connectionCallbacks.add(callback);
		return () => this.connectionCallbacks.delete(callback);
	}

	onAuthFailed(callback: () => void): void {
		this.onAuthFailedCallback = callback;
	}

	// ─── Private helpers ─────────────────────────────────────────────────────

	private sendSubscription(generationId: string): void {
		this.send({ type: 'subscribe_generation', generation_id: generationId });
	}

	private notifyConnectionChange(connected: boolean): void {
		this.connectionCallbacks.forEach((cb) => cb(connected));
	}
}

// Builds a generation WebSocket with the standard auth-failure handling
// (logout + redirect to login) already wired, so every call site reacts the
// same way to an expired/invalid token instead of going silent.
export function createGenerationSocket(): WebSocketService {
	const ws = new WebSocketService(getWsUrl('/ws/generation'), api.getToken());
	ws.onAuthFailed(() => {
		authStore.logout();
		goto('/login?expired=1');
	});
	return ws;
}
