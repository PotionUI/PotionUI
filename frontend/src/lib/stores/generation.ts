import { tabsStore } from './tabs';
import { generationTabs, type GenerationTab } from '$lib/types/tabs';
import type { WebSocketMessage } from '$lib/services/websocket';
import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';
import { retireGeneration } from '$lib/generation/messages/generationOutputs';
import { MessageCoalescer } from '$lib/generation/messageCoalescer';
import { observeGenerationMessage } from '$lib/generation/compare/compareStore.svelte';
import { TERMINAL_TYPES, generationIdOf } from '$lib/generation/messages/wire';
import '$lib/generation/messages';

export interface DispatchDeps {
	/** Unsubscribes the WebSocket from a generation id (complete/error/cancelled). */
	unsubscribe: (generationId: string) => void;
}

const warnedUnknownTypes = new Set<string>();

function findTabForGeneration(generationId: string | undefined) {
	if (!generationId) return null;

	let currentTabs: GenerationTab[] = [];
	const unsub = tabsStore.subscribe((s) => (currentTabs = generationTabs(s.tabs)));
	unsub();

	return currentTabs.find(
		(t) =>
			t.generation.currentGeneration?.generation_id === generationId ||
			t.generation.currentGeneration?.id === generationId ||
			t.generation.cancelledGenerationId === generationId ||
			(t.generation.queue || []).some((q: { generation_id: string }) => q.generation_id === generationId)
	) ?? null;
}

export function findTabByGenerationId(generationId: string | undefined): string | null {
	return findTabForGeneration(generationId)?.id || null;
}

export function dispatchGenerationMessage(message: WebSocketMessage, deps: DispatchDeps): void {
	const generationId = generationIdOf(message);

	try {
		observeGenerationMessage(message as { type: string; [key: string]: unknown });
	} catch (error) {
		console.error('[Compare] Failed to apply a generation message to the grid:', error);
	}

	const targetTab = findTabForGeneration(generationId);
	if (!targetTab) {
		if (TERMINAL_TYPES.has(message.type) && generationId) {
			retireGeneration(generationId, deps.unsubscribe);
			return;
		}
		console.error('[WS] Tab not found for generation:', {
			generationId,
			messageType: message.type
		});
		return;
	}

	const handler = generationMessageRegistry.get(message.type);
	if (!handler) {
		if (!warnedUnknownTypes.has(message.type)) {
			warnedUnknownTypes.add(message.type);
			console.warn(`[WS] No generation message handler registered for type "${message.type}"`);
		}
		return;
	}

	handler.handle(message, {
		tabId: targetTab.id,
		tab: targetTab,
		tabsStore,
		generationId,
		unsubscribe: deps.unsubscribe
	});
}

// The backend emits an unthrottled `generation_status` per sampling step,
// which would otherwise drive a tabsStore.updateTab -- and every subscriber's
// reactive graph -- once per step; this buffers the WebSocket entry point's
// traffic through MessageCoalescer and applies it at most once per frame.
let latestDispatchDeps: DispatchDeps | null = null;
const coalescer = new MessageCoalescer<WebSocketMessage>((message) => {
	if (latestDispatchDeps) dispatchGenerationMessage(message, latestDispatchDeps);
});

// The entry point a WebSocket subscription should call for every generation
// message (see handleGenerationMessage in generate/+page.svelte).
export function dispatchGenerationMessageCoalesced(message: WebSocketMessage, deps: DispatchDeps): void {
	latestDispatchDeps = deps;
	coalescer.enqueue(message);
}
