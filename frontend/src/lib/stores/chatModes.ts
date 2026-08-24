/**
 * Chat modes + tool catalog, fetched once from the backend:
 *   GET /api/chat/modes  — modes registered by core + plugins
 *   GET /api/chat/tools  — tool metadata (mode scoping, icon, label)
 *
 * Also exports the pure route→mode resolver used when opening the panel or
 * starting a new conversation.
 */
import { writable } from 'svelte/store';
import { logger } from '$lib/utils/logger';
import { api } from '$lib/services/api/index';
import type { ChatMode, ChatToolInfo } from '$lib/types/chat';
import { DEFAULT_CHAT_MODE } from '$lib/stores/chatSession';

export interface ChatModesState {
	modes: ChatMode[];
	toolsCatalog: ChatToolInfo[];
	loaded: boolean;
}

const store = writable<ChatModesState>({ modes: [], toolsCatalog: [], loaded: false });

let loadPromise: Promise<void> | null = null;

export const chatModes = {
	subscribe: store.subscribe,

	/** Fetch modes + tool catalog once (idempotent; pass force to refetch). */
	load(force = false): Promise<void> {
		if (loadPromise && !force) return loadPromise;
		loadPromise = (async () => {
			try {
				const [modesResp, toolsResp] = await Promise.all([
					api.getChatModes(),
					api.listChatTools()
				]);
				store.set({
					modes: modesResp.data?.modes || [],
					toolsCatalog: toolsResp.data?.tools || [],
					loaded: true
				});
			} catch (err) {
				logger.error('Failed to load chat modes:', err);
				store.update((s) => ({ ...s, loaded: true }));
			}
		})();
		return loadPromise;
	}
};

/**
 * Resolve the chat mode for a route: across all modes' default_route_prefixes,
 * the longest prefix that matches the pathname wins (so a plugin page like
 * /plugins/dataset-generator/... beats a generic '/' prefix). Matching is
 * path-segment aware ('/generate' matches '/generate' and '/generate/x',
 * not '/generatex'). Falls back to the default 'generation' mode.
 */
export function resolveModeForRoute(pathname: string, modes: ChatMode[]): string {
	const path = pathname.replace(/\/+$/, '') || '/';
	let best = '';
	let bestLen = -1;
	for (const mode of modes) {
		for (const raw of mode.default_route_prefixes || []) {
			const prefix = raw.replace(/\/+$/, '') || '/';
			const matches = prefix === '/' || path === prefix || path.startsWith(prefix + '/');
			if (matches && prefix.length > bestLen) {
				bestLen = prefix.length;
				best = mode.id;
			}
		}
	}
	return best || DEFAULT_CHAT_MODE;
}

/** Tools visible in a mode: the mode's own tools plus global (modeless) tools. */
export function toolsForMode(tools: ChatToolInfo[], modeId: string): ChatToolInfo[] {
	return tools.filter((t) => !t.mode || t.mode === modeId);
}

/** Display name for a mode id, falling back to the id itself if unknown. */
export function resolveModeName(modeId: string, modes: ChatMode[]): string {
	return modes.find((m) => m.id === modeId)?.name || modeId;
}
