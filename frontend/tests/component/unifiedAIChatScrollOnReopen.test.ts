// @vitest-environment jsdom
//
// Regression: GlobalChatPanel wraps UnifiedAIChat in {#if isOpen}, so closing
// the chat UNMOUNTS the component and reopening MOUNTS IT FRESH — it never
// goes through display:none. preserveScrollAcrossHiding's restore branch
// (`!collapsed && wasCollapsed`) only fires when a later ResizeObserver
// callback sees the node go hidden->visible; on a brand-new mount the node is
// visible from its very first observed layout, and `wasCollapsed` starts
// `false`, so the branch never ran and the browser's native scrollTop (0) won
// by default.
//
// jsdom does no layout, so real scrollHeight/clientHeight are always 0 and it
// has no ResizeObserver implementation at all. This test stubs both: a fake
// ResizeObserver captures the callback the component registers so the test
// can fire it on demand, and the container's clientHeight/scrollHeight are
// overridden via defineProperty to stand in for "the message list has
// rendered with real content". The assertion is purely mechanical — did the
// component drive scrollTop to scrollHeight once its container is observed
// as visible — not a real-browser pixel check.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getMyLLMConfigurations: vi.fn().mockResolvedValue({ success: true, data: { llm_configs: [] } }),
		getChatModes: vi.fn().mockResolvedValue({ success: true, data: { modes: [] } }),
		listChatTools: vi.fn().mockResolvedValue({ success: true, data: { tools: [] } }),
		getChatSessions: vi.fn().mockResolvedValue({ success: true, data: { sessions: [] } })
	}
}));

vi.mock('$app/stores', () => {
	const { writable } = require('svelte/store');
	return { page: writable({ url: new URL('http://localhost/generate') }) };
});

type FakeEntry = { node: Element; cb: ResizeObserverCallback };

class FakeResizeObserver {
	static instances: FakeEntry[] = [];
	private cb: ResizeObserverCallback;
	constructor(cb: ResizeObserverCallback) {
		this.cb = cb;
	}
	observe(node: Element) {
		FakeResizeObserver.instances.push({ node, cb: this.cb });
	}
	unobserve() {}
	disconnect() {
		FakeResizeObserver.instances = FakeResizeObserver.instances.filter((e) => e.cb !== this.cb);
	}
}

const { api } = await import('$lib/services/api/index');
const { chatSession } = await import('$lib/stores/chatSession');
const { default: UnifiedAIChat } = await import('$lib/components/UnifiedAIChat.svelte');
const { createClassComponent } = await import('svelte/legacy');

function setBoxSize(node: Element, clientHeight: number, scrollHeight: number) {
	Object.defineProperty(node, 'clientHeight', { value: clientHeight, configurable: true });
	Object.defineProperty(node, 'scrollHeight', { value: scrollHeight, configurable: true });
}

function fireResizeCallbackFor(node: Element) {
	const entry = FakeResizeObserver.instances.find((e) => e.node === node);
	expect(entry, 'component never registered a ResizeObserver on the messages container').toBeTruthy();
	entry!.cb([] as never, undefined as never);
}

function findMessagesContainer(target: Element): HTMLElement {
	const el = target.querySelector<HTMLElement>('.scrollbar-thin');
	expect(el, 'messages container (.scrollbar-thin) not found in the rendered tree').toBeTruthy();
	return el!;
}

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let originalRO: unknown;
let target: HTMLElement;
let component: { $destroy: () => void } | undefined;

beforeEach(() => {
	originalRO = (globalThis as any).ResizeObserver;
	(globalThis as any).ResizeObserver = FakeResizeObserver;
	FakeResizeObserver.instances = [];

	target = document.createElement('div');
	document.body.appendChild(target);

	// Simulate a conversation that was already loaded during a PREVIOUS open
	// of the panel. chatSession is a module-level singleton store, so it
	// really does carry this across GlobalChatPanel's unmount/remount, same
	// as production.
	chatSession.loadedSession(
		{ id: 'sess-1', mode: 'generation' },
		[
			{ role: 'user', content: 'first message', timestamp: 1 } as never,
			{ role: 'assistant', content: 'reply', timestamp: 2 } as never
		]
	);
});

afterEach(() => {
	component?.$destroy();
	component = undefined;
	target.remove();
	chatSession.reset();
	(globalThis as any).ResizeObserver = originalRO;
	vi.clearAllMocks();
});

describe('UnifiedAIChat scroll position on reopen', () => {
	it('pins the messages container to the bottom on a fresh mount with a restored conversation', async () => {
		component = createClassComponent({ component: UnifiedAIChat as never, target, props: {} });
		await settle();

		const container = findMessagesContainer(target);
		// Stand in for "the message list has rendered with real content" —
		// jsdom reports 0 for both, always.
		setBoxSize(container, 400, 5000);
		container.scrollTop = 0;

		fireResizeCallbackFor(container);

		expect(container.scrollTop).toBe(5000);
	});
});
