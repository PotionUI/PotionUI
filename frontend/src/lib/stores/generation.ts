import { tabsStore } from './tabs';
import type { WebSocketMessage } from '$lib/services/websocket';
import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';
import '$lib/generation/messages';

export interface DispatchDeps {
	/** Unsubscribes the WebSocket from a generation id (complete/error/cancelled). */
	unsubscribe: (generationId: string) => void;
}

const warnedUnknownTypes = new Set<string>();

/**
 * Finds the tab that owns a generation by matching generation_id/id against
 * every tab's currentGeneration, or — for a queued generation that isn't the
 * tab's "current" one (e.g. a second enqueue while another is still running)
 * — against that tab's `generation.queue[]`. Returns null if no tab currently
 * owns it.
 */
export function findTabByGenerationId(generationId: string | undefined): string | null {
	if (!generationId) return null;

	let currentTabs: any[] = [];
	const unsub = tabsStore.subscribe((s) => (currentTabs = s.tabs));
	unsub();

	const tab = currentTabs.find(
		(t) =>
			t.generation.currentGeneration?.generation_id === generationId ||
			t.generation.currentGeneration?.id === generationId ||
			(t.generation.queue || []).some((q: { generation_id: string }) => q.generation_id === generationId)
	);

	return tab?.id || null;
}

/**
 * Dispatches an incoming generation WebSocket message to the registered
 * handler for its type. Owns the id-extraction quirk (completion/error/cancel
 * messages carry the id inside `data.id`, others carry it at the top level)
 * and the tab-resolution lookup, both done once here rather than per handler.
 */
export function dispatchGenerationMessage(message: WebSocketMessage, deps: DispatchDeps): void {
	let generationId = message.generation_id;

	if (
		message.type === 'generation_complete' ||
		message.type === 'generation_error' ||
		message.type === 'generation_cancelled'
	) {
		generationId = (message as any).data?.id || (message as any).data?.generation_id || message.generation_id;
	}

	const targetTabId = findTabByGenerationId(generationId);
	if (!targetTabId) {
		console.error('[WS] Tab not found for generation:', {
			generationId,
			messageType: message.type
		});
		return;
	}

	let currentTabs: any[] = [];
	const unsub = tabsStore.subscribe((s) => (currentTabs = s.tabs));
	unsub();
	const targetTab = currentTabs.find((t) => t.id === targetTabId);
	if (!targetTab) return;

	const handler = generationMessageRegistry.get(message.type);
	if (!handler) {
		if (!warnedUnknownTypes.has(message.type)) {
			warnedUnknownTypes.add(message.type);
			console.warn(`[WS] No generation message handler registered for type "${message.type}"`);
		}
		return;
	}

	handler.handle(message, {
		tabId: targetTabId,
		tab: targetTab,
		tabsStore,
		generationId,
		unsubscribe: deps.unsubscribe
	});
}
