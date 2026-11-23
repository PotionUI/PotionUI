// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { createClassComponent } from 'svelte/legacy';
import { settle } from './helpers/settle';

const factoryRuns = vi.hoisted(() => ({ count: 0 }));

vi.mock('$lib/components/UnifiedAIChat.svelte', async () => {
	factoryRuns.count += 1;
	return { default: (await import('./stubs/ChatProbe.svelte')).default };
});

import GlobalChatPanel from '$lib/components/GlobalChatPanel.svelte';
import { chatPanelStore } from '$lib/stores/chatPanel';

let component: { $destroy: () => void } | undefined;

beforeEach(() => {
	chatPanelStore.reset();
	const target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({ component: GlobalChatPanel as never, target });
});

afterEach(() => {
	component?.$destroy();
	component = undefined;
	chatPanelStore.reset();
	document.body.innerHTML = '';
});

describe('GlobalChatPanel lazy chat', () => {
	it('does not import the chat UI until the panel is opened, then mounts it inside the shell', async () => {
		await settle(3);
		expect(factoryRuns.count).toBe(0);
		expect(document.querySelector('.chat-shell')).toBeNull();

		chatPanelStore.open();

		await vi.waitFor(() => expect(document.querySelector('.chat-shell [data-chat-probe]')).not.toBeNull());
		expect(factoryRuns.count).toBe(1);
	});
});
