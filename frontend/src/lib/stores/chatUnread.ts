import { writable, get } from 'svelte/store';
import { isChatPanelOpen } from '$lib/stores/chatPanel';

export const chatUnread = writable(false);

export function markAssistantReplied(): void {
	if (!get(isChatPanelOpen)) chatUnread.set(true);
}

export function clearChatUnread(): void {
	chatUnread.set(false);
}

isChatPanelOpen.subscribe((open) => {
	if (open) clearChatUnread();
});
