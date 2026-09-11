// @vitest-environment jsdom
//
// Windowed transcript, UI half: a session loaded with `hasEarlier: true`
// shows a "Load earlier messages" row; clicking it fetches the preceding
// page (`api.getChatMessagesBefore`) and prepends it in front of what was
// already loaded, without disturbing the messages already on screen.
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
		clearAuth: vi.fn(),
		listMemory: vi.fn().mockResolvedValue({ success: true, data: { notes: [], injection: { cap_per_group: 0, max_content_len: 0 } } }),
		getChatMessagesBefore: vi.fn()
	}
}));

vi.mock('$app/stores', () => {
	const { writable } = require('svelte/store');
	return { page: writable({ url: new URL('http://localhost/generate') }) };
});

// jsdom has no IntersectionObserver, and no ResizeObserver either (the
// messages container's own scroll-restore action registers one on mount) —
// these tests drive `loadEarlier()` via the button's click, not either
// observer, so both stubs just need to not throw.
class StubIntersectionObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}

class StubResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}

const { api } = await import('$lib/services/api/index');
const { chatSession } = await import('$lib/stores/chatSession');
const { default: UnifiedAIChat } = await import('$lib/components/UnifiedAIChat.svelte');
const { createClassComponent } = await import('svelte/legacy');

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let originalIO: unknown;
let originalRO: unknown;
let target: HTMLElement;
let component: { $destroy: () => void } | undefined;

function setup() {
	originalIO = (globalThis as any).IntersectionObserver;
	originalRO = (globalThis as any).ResizeObserver;
	(globalThis as any).IntersectionObserver = StubIntersectionObserver;
	(globalThis as any).ResizeObserver = StubResizeObserver;
	target = document.createElement('div');
	document.body.appendChild(target);
}

afterEach(() => {
	component?.$destroy();
	component = undefined;
	target?.remove();
	chatSession.reset();
	(globalThis as any).IntersectionObserver = originalIO;
	(globalThis as any).ResizeObserver = originalRO;
	vi.clearAllMocks();
});

describe('UnifiedAIChat "Load earlier messages"', () => {
	it('shows the row only when the loaded session reports more history, and hides it once there is none left', async () => {
		setup();
		chatSession.loadedSession(
			{ id: 'sess-1', mode: 'generation' },
			[{ id: 'm2', role: 'assistant', content: 'recent reply', timestamp: 2 } as never],
			{ messageCount: 5, hasEarlier: true }
		);
		component = createClassComponent({ component: UnifiedAIChat as never, target, props: {} });
		await settle();

		const button = Array.from(target.querySelectorAll('button')).find((b) =>
			b.textContent?.includes('Load earlier messages')
		);
		expect(button, 'Load earlier messages row not rendered for hasEarlier: true').toBeTruthy();
	});

	it('does not render the row for a fully-loaded conversation', async () => {
		setup();
		chatSession.loadedSession(
			{ id: 'sess-1', mode: 'generation' },
			[{ id: 'm1', role: 'user', content: 'hi', timestamp: 1 } as never],
			{ messageCount: 1, hasEarlier: false }
		);
		component = createClassComponent({ component: UnifiedAIChat as never, target, props: {} });
		await settle();

		const button = Array.from(target.querySelectorAll('button')).find((b) =>
			b.textContent?.includes('Load earlier messages')
		);
		expect(button).toBeFalsy();
	});

	it('clicking the row prepends the older page in front of what is already loaded', async () => {
		setup();
		(api.getChatMessagesBefore as ReturnType<typeof vi.fn>).mockResolvedValue({
			success: true,
			data: {
				messages: [
					{ id: 'm1', session_id: 'sess-1', role: 'user', content: 'first message', created_at: null },
					{ id: 'm2', session_id: 'sess-1', role: 'assistant', content: 'first reply', created_at: null }
				],
				has_earlier: false
			}
		});
		chatSession.loadedSession(
			{ id: 'sess-1', mode: 'generation' },
			[{ id: 'm3', role: 'user', content: 'third message', timestamp: 3 } as never],
			{ messageCount: 3, hasEarlier: true }
		);
		component = createClassComponent({ component: UnifiedAIChat as never, target, props: {} });
		await settle();

		const button = Array.from(target.querySelectorAll('button')).find((b) =>
			b.textContent?.includes('Load earlier messages')
		);
		expect(button).toBeTruthy();
		button!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		await settle();

		expect(api.getChatMessagesBefore).toHaveBeenCalledWith('sess-1', 'm3', 60);

		const { get } = await import('svelte/store');
		const state = get(chatSession);
		expect(state.messages.map((m) => m.id)).toEqual(['m1', 'm2', 'm3']);
		expect(state.hasEarlier).toBe(false);
		expect(state.loadingEarlier).toBe(false);

		// The row is gone now that there's nothing earlier left.
		const buttonAfter = Array.from(target.querySelectorAll('button')).find((b) =>
			b.textContent?.includes('Load earlier messages')
		);
		expect(buttonAfter).toBeFalsy();
	});

	it('a second click while a fetch is already in flight does not issue a second request', async () => {
		setup();
		let resolveFetch!: (v: unknown) => void;
		(api.getChatMessagesBefore as ReturnType<typeof vi.fn>).mockReturnValue(
			new Promise((resolve) => (resolveFetch = resolve))
		);
		chatSession.loadedSession(
			{ id: 'sess-1', mode: 'generation' },
			[{ id: 'm3', role: 'user', content: 'third message', timestamp: 3 } as never],
			{ messageCount: 3, hasEarlier: true }
		);
		component = createClassComponent({ component: UnifiedAIChat as never, target, props: {} });
		await settle();

		const button = () =>
			Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.includes('Load earlier messages'));

		button()!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		await settle();
		button()?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		await settle();

		expect(api.getChatMessagesBefore).toHaveBeenCalledTimes(1);

		resolveFetch({ success: true, data: { messages: [], has_earlier: false } });
		await settle();
	});

	it('a backend envelope failure (e.g. message_not_found) leaves the loaded window untouched, clears loadingEarlier, and shows no error banner', async () => {
		setup();
		(api.getChatMessagesBefore as ReturnType<typeof vi.fn>).mockResolvedValue({
			success: false,
			error: 'message_not_found'
		});
		chatSession.loadedSession(
			{ id: 'sess-1', mode: 'generation' },
			[{ id: 'm3', role: 'user', content: 'third message', timestamp: 3 } as never],
			{ messageCount: 3, hasEarlier: true }
		);
		component = createClassComponent({ component: UnifiedAIChat as never, target, props: {} });
		await settle();

		const button = Array.from(target.querySelectorAll('button')).find((b) =>
			b.textContent?.includes('Load earlier messages')
		);
		button!.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		await settle();

		const { get } = await import('svelte/store');
		const state = get(chatSession);
		expect(state.messages.map((m) => m.id)).toEqual(['m3']); // untouched
		expect(state.loadingEarlier).toBe(false);
		expect(state.error).toBe('');
		expect(target.querySelector('.chat-error-banner')).toBeFalsy();
		// hasEarlier stays true — the row is still there to retry.
		expect(state.hasEarlier).toBe(true);
	});
});
