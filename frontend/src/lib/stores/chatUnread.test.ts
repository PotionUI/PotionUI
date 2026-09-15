import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { chatUnread, markAssistantReplied, clearChatUnread } from './chatUnread';
import { chatPanelStore } from './chatPanel';

describe('chatUnread store', () => {
	beforeEach(() => {
		chatPanelStore.reset();
		clearChatUnread();
	});

	it('starts cleared', () => {
		expect(get(chatUnread)).toBe(false);
	});

	it('markAssistantReplied sets unread when the panel is closed', () => {
		markAssistantReplied();
		expect(get(chatUnread)).toBe(true);
	});

	it('does not set unread while the panel is open', () => {
		chatPanelStore.open();
		markAssistantReplied();
		expect(get(chatUnread)).toBe(false);
	});

	it('clearChatUnread clears an existing flag', () => {
		markAssistantReplied();
		clearChatUnread();
		expect(get(chatUnread)).toBe(false);
	});

	it('opening the panel clears an existing unread flag', () => {
		markAssistantReplied();
		expect(get(chatUnread)).toBe(true);

		chatPanelStore.open();
		expect(get(chatUnread)).toBe(false);
	});

	it('toggling the panel open also clears the flag', () => {
		markAssistantReplied();
		chatPanelStore.toggle();
		expect(get(chatUnread)).toBe(false);
	});
});
