/**
 * Pure derivation for ChatModeSelector.svelte's scope-mismatch notice. A
 * conversation's mode is immutable once it has messages (`modeLocked` in
 * chatSession.ts) — the chip alone doesn't explain to a user who has since
 * navigated elsewhere why the fixed chip disagrees with the page they're on.
 */
import { resolveModeName } from '$lib/stores/chatModes';
import type { ChatMode } from '$lib/types/chat';

export interface ModeScopeMismatch {
	conversationModeName: string;
	pageModeName: string;
}

/**
 * Null when there's nothing to say: the conversation isn't locked yet (the
 * chip itself is still editable), the page's scope hasn't resolved, or the
 * two scopes already agree.
 */
export function deriveModeScopeMismatch(
	conversationModeId: string,
	pageModeId: string | null,
	modes: ChatMode[],
	locked: boolean
): ModeScopeMismatch | null {
	if (!locked || !pageModeId || conversationModeId === pageModeId) return null;
	return {
		conversationModeName: resolveModeName(conversationModeId, modes),
		pageModeName: resolveModeName(pageModeId, modes)
	};
}
