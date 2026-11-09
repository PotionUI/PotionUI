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
		resolveToolExecution: vi.fn(),
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
const { onToolApplied, resetPageContext } = await import('$lib/chat/pageContext');
const { default: UnifiedAIChat } = await import('$lib/components/UnifiedAIChat.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { get } = await import('svelte/store');

const savedShot = {
	tool_name: 'save_shot',
	arguments: {},
	result: { success: true, data: JSON.stringify({ shot: 3 }) },
	duration_ms: 2
};

const pendingShot = {
	...savedShot,
	result: { success: true, data: JSON.stringify({ status: 'pending_approval', shot: 4 }) },
	approval_status: 'pending' as const
};

async function settle() {
	for (let i = 0; i < 12; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let originalIO: unknown;
let originalRO: unknown;
let target: HTMLElement;
let component: { $destroy: () => void } | undefined;

async function mountChat(messages: unknown[]) {
	originalIO = (globalThis as any).IntersectionObserver;
	originalRO = (globalThis as any).ResizeObserver;
	(globalThis as any).IntersectionObserver = StubIntersectionObserver;
	(globalThis as any).ResizeObserver = StubResizeObserver;
	chatSession.loadedSession({ id: 'sess-1', mode: 'generation' }, messages as never, {
		messageCount: messages.length,
		hasEarlier: false
	});
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({ component: UnifiedAIChat as never, target, props: {} });
	await settle();
}

function replyWith(assistantMessage: Record<string, unknown>) {
	vi.mocked(api.sendChatMessageStream).mockImplementation(async (_sid, _payload, onEvent) => {
		await onEvent!({ type: 'done', data: { user_message: { id: 'u2' }, assistant_message: assistantMessage } });
	});
}

async function send(text: string) {
	chatComposerDrafts.save('sess-1', { text, resources: {} });
	await mountChat([]);
	(target.querySelector('button[aria-label="Send message"]') as HTMLButtonElement).click();
	await settle();
}

afterEach(() => {
	component?.$destroy();
	component = undefined;
	target?.remove();
	chatSession.reset();
	chatComposerDrafts.clear('sess-1');
	resetPageContext();
	(globalThis as any).IntersectionObserver = originalIO;
	(globalThis as any).ResizeObserver = originalRO;
	vi.clearAllMocks();
});

describe('UnifiedAIChat delivers tool results to onToolApplied', () => {
	it('runs the handler of a tool that ran without approval once the reply is final', async () => {
		const handler = vi.fn();
		onToolApplied('save_shot', handler);
		replyWith({ id: 'a2', content: 'Saved shot 3.', tool_executions: [savedShot, pendingShot] });

		await send('save shot 3');

		expect(api.sendChatMessageStream).toHaveBeenCalledTimes(1);
		expect(handler).toHaveBeenCalledTimes(1);
		expect(handler).toHaveBeenCalledWith({ shot: 3 });
	});

	it('shows the message of a stale outcome in place of the narration', async () => {
		onToolApplied('save_shot', () => ({ status: 'stale', message: 'That shot belongs to another project.' }));
		replyWith({ id: 'a2', content: 'Saved shot 3.', tool_executions: [savedShot] });

		await send('save shot 3');

		const reply = get(chatSession).messages.find((message) => message.id === 'a2');
		expect(reply?.content).toBe('That shot belongs to another project.');
		expect(target.textContent).toContain('That shot belongs to another project.');
	});

	it('does not replay the tool results of a conversation it opens', async () => {
		const handler = vi.fn();
		onToolApplied('save_shot', handler);

		await mountChat([
			{ id: 'u1', role: 'user', content: 'save shot 3', timestamp: 1 },
			{ id: 'a1', role: 'assistant', content: 'Saved shot 3.', timestamp: 2, tool_executions: [savedShot] }
		]);

		expect(handler).not.toHaveBeenCalled();
	});

	it('runs the handler of an approved tool once, and not again when the continuation is final', async () => {
		const handler = vi.fn();
		onToolApplied('save_shot', handler);
		await mountChat([
			{ id: 'u1', role: 'user', content: 'save shot 4', timestamp: 1 },
			{ id: 'a1', role: 'assistant', content: 'Approve saving shot 4?', timestamp: 2, tool_executions: [pendingShot] }
		]);
		const approved = { ...savedShot, result: { success: true, data: JSON.stringify({ shot: 4 }) }, approval_status: 'approved' };
		vi.mocked(api.resolveToolExecution).mockResolvedValue({
			success: true,
			data: { status: 'approved', message_id: 'a1', tool_index: 0, execution: approved }
		} as never);
		vi.mocked(api.sendChatMessageStream).mockImplementation(async (_sid, _payload, onEvent) => {
			await onEvent!({
				type: 'done',
				data: { assistant_message: { id: 'a2', content: 'Saved shot 4.', tool_executions: [] } }
			});
		});

		(target.querySelector('[data-testid="action-card"] .action-btn.is-approve') as HTMLButtonElement).click();
		await settle();

		expect(api.sendChatMessageStream).toHaveBeenCalledTimes(1);
		expect(handler).toHaveBeenCalledTimes(1);
		expect(handler).toHaveBeenCalledWith({ shot: 4 });
	});
});
