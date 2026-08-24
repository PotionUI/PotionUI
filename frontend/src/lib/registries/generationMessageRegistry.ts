import { createRegistry } from './registry';
import type { WebSocketMessage } from '$lib/services/websocket';
import type { tabsStore } from '$lib/stores/tabs';

/**
 * Context passed to a generation message handler. `tab` is a snapshot of the
 * tab that owns the generation (resolved once by dispatchGenerationMessage),
 * `generationId` is the id extracted from the message (accounting for the
 * completion/error/cancel `data.id` quirk), and `unsubscribe` closes the
 * WebSocket subscription for that generation id.
 */
export interface MessageContext {
	tabId: string;
	tab: any;
	tabsStore: typeof tabsStore;
	generationId: string | undefined;
	unsubscribe: (generationId: string) => void;
}

export interface GenerationMessageHandler {
	type: string;
	handle(msg: WebSocketMessage, ctx: MessageContext): void;
}

export const generationMessageRegistry = createRegistry<GenerationMessageHandler>('generation-message');
