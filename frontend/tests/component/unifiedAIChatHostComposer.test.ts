import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getMyLLMConfigurations: vi.fn().mockResolvedValue({
			success: true,
			data: { llm_configs: [{ id: 'cfg-1', name: 'Test config' }] }
		}),
		getChatModes: vi.fn().mockResolvedValue({ success: true, data: { modes: [] } }),
		listChatTools: vi.fn().mockResolvedValue({ success: true, data: { tools: [] } }),
		getChatSessions: vi.fn().mockResolvedValue({ success: true, data: { sessions: [] } }),
		getMyToolsetPreferences: vi.fn().mockResolvedValue({ success: false }),
		listPresets: vi.fn().mockResolvedValue({ success: false }),
		setOnAuthExpired: vi.fn(),
		setOnTokenRefreshed: vi.fn(),
		clearAuth: vi.fn(),
		listMemory: vi.fn().mockResolvedValue({
			success: true,
			data: { notes: [], injection: { cap_per_group: 0, max_content_len: 0 } }
		}),
		createChatSession: vi.fn(),
		sendChatMessageStream: vi.fn(),
		reattachChatMessageStream: vi.fn(),
		getChatSession: vi.fn().mockResolvedValue({ success: false })
	}
}));

vi.mock('$app/stores', () => {
	const { writable } = require('svelte/store');
	return { page: writable({ url: new URL('http://localhost/plugins/my-plugin') }) };
});

class StubResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}

class StubIntersectionObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}

const { api } = await import('$lib/services/api/index');
const { chatSession } = await import('$lib/stores/chatSession');
const { chatComposerDrafts } = await import('$lib/stores/chatComposerDrafts');
const { chatPanelStore } = await import('$lib/stores/chatPanel');
const { declareMode } = await import('$lib/chat/pageContext');
const { default: UnifiedAIChat } = await import('$lib/components/UnifiedAIChat.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { get } = await import('svelte/store');

async function settle() {
	for (let i = 0; i < 12; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let originalIO: unknown;
let originalRO: unknown;
let target: HTMLElement;
let component: { $destroy: () => void } | undefined;
let undeclare: (() => void) | undefined;

async function mountChat() {
	originalIO = (globalThis as any).IntersectionObserver;
	originalRO = (globalThis as any).ResizeObserver;
	(globalThis as any).IntersectionObserver = StubIntersectionObserver;
	(globalThis as any).ResizeObserver = StubResizeObserver;
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({ component: UnifiedAIChat as never, target, props: {} });
	await settle();
}

function composer(): HTMLElement {
	return target.querySelector('[role="textbox"][aria-label="Message PotionAI"]') as HTMLElement;
}

afterEach(() => {
	component?.$destroy();
	component = undefined;
	target?.remove();
	undeclare?.();
	undeclare = undefined;
	chatSession.reset();
	chatPanelStore.reset();
	chatComposerDrafts.reset();
	(globalThis as any).IntersectionObserver = originalIO;
	(globalThis as any).ResizeObserver = originalRO;
	vi.clearAllMocks();
});

describe('UnifiedAIChat composer driven by window.__potionui.chat', () => {
	it('prefill puts the text in an empty composer, unsent, and focuses it', async () => {
		await mountChat();
		chatPanelStore.prefill('Plan five shots for a rainy night');
		await settle();

		expect(composer().textContent).toBe('Plan five shots for a rainy night');
		expect(document.activeElement).toBe(composer());
		expect(api.sendChatMessageStream).not.toHaveBeenCalled();
		expect(get(chatSession).messages).toHaveLength(0);
	});

	it('prefill keeps a draft the user typed unless replace is set', async () => {
		chatComposerDrafts.save(null, { text: 'my own draft', resources: {} });
		await mountChat();

		chatPanelStore.prefill('Plan five shots');
		await settle();
		expect(composer().textContent).toBe('my own draft');

		chatPanelStore.prefill('Plan five shots', { replace: true });
		await settle();
		expect(composer().textContent).toBe('Plan five shots');
	});

	it('a prefill made before the panel mounted lands once the chat is ready', async () => {
		chatPanelStore.prefill('Queued before mount');
		expect(get(chatPanelStore).isOpen).toBe(true);
		await mountChat();
		expect(composer().textContent).toBe('Queued before mount');
	});

	it('send resolves once the message is on its way, without touching the draft', async () => {
		chatComposerDrafts.save('sess-1', { text: 'unrelated draft', resources: {} });
		chatSession.loadedSession({ id: 'sess-1', mode: 'generation' }, []);
		let finishStream: () => void = () => {};
		vi.mocked(api.sendChatMessageStream).mockImplementation(
			() => new Promise<void>((resolve) => (finishStream = resolve))
		);
		await mountChat();

		await chatPanelStore.send('Make the intro shorter');

		expect(api.sendChatMessageStream).toHaveBeenCalledWith(
			'sess-1',
			expect.objectContaining({ content: 'Make the intro shorter' }),
			expect.any(Function)
		);
		expect(get(chatSession).messages[0]).toMatchObject({ role: 'user', content: 'Make the intro shorter' });
		expect(get(chatSession).isGenerating).toBe(true);
		expect(composer().textContent).toBe('unrelated draft');
		finishStream();
		await settle();
	});

	it('send starts a conversation in the declared mode when the open one is in another mode', async () => {
		chatSession.loadedSession({ id: 'sess-1', mode: 'generation' }, [
			{ id: 'u1', role: 'user', content: 'earlier', timestamp: 1 },
			{ id: 'a1', role: 'assistant', content: 'reply', timestamp: 2 }
		] as never);
		vi.mocked(api.createChatSession).mockResolvedValue({
			success: true,
			data: { id: 'sess-2', mode: 'my-plugin', status: 'active' }
		} as never);
		vi.mocked(api.sendChatMessageStream).mockResolvedValue(undefined);
		await mountChat();
		undeclare = declareMode('my-plugin');

		await chatPanelStore.send('Plan the shots');

		expect(api.createChatSession).toHaveBeenCalledWith(expect.objectContaining({ mode: 'my-plugin' }));
		expect(api.sendChatMessageStream).toHaveBeenCalledWith('sess-2', expect.any(Object), expect.any(Function));
		expect(get(chatSession).mode).toBe('my-plugin');
		await settle();
	});

	it('send rejects while the assistant is still answering', async () => {
		await mountChat();
		chatSession.patch({ isGenerating: true });
		await expect(chatPanelStore.send('hello')).rejects.toThrow('still answering');
		expect(api.sendChatMessageStream).not.toHaveBeenCalled();
	});

	it('send rejects when the session cannot be created', async () => {
		vi.mocked(api.createChatSession).mockResolvedValue({ success: false } as never);
		await mountChat();
		await expect(chatPanelStore.send('hello')).rejects.toThrow('Failed to create chat session');
		expect(api.sendChatMessageStream).not.toHaveBeenCalled();
	});
});
