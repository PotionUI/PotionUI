// @vitest-environment jsdom
import { describe, it, expect, vi, beforeAll, afterEach } from 'vitest';
import { get } from 'svelte/store';
import { initHostApi, createGenerationEvents, type PotionUIHostApi, type GenerationEvent } from './host';
import {
	isKeyboardSuppressed,
	suppressKeyboard,
	resumeKeyboard,
	releaseAllKeyboardHolds
} from '$lib/services/keyboard';
import { iconPaths, getIconPath, iconLookup } from '$lib/utils/IconLibrary';
import { chatPanelStore, CHAT_READY_TIMEOUT_MS, type ChatComposer } from '$lib/stores/chatPanel';
import { provideContext, declareMode, onToolApplied } from '$lib/chat/pageContext';
import { activeConfirm, cancelAllConfirms, settleConfirm } from '$lib/stores/confirm';
import type { WebSocketService } from '$lib/services/websocket';

class FakeBrowserSocket {
	static readonly CONNECTING = 0;
	static readonly OPEN = 1;
	static readonly CLOSING = 2;
	static readonly CLOSED = 3;
	static instances: FakeBrowserSocket[] = [];
	readyState = FakeBrowserSocket.CONNECTING;
	sent: Record<string, unknown>[] = [];
	closed = false;
	onopen: (() => void) | null = null;
	onmessage: ((event: { data: string }) => void) | null = null;
	onerror: ((event: Event) => void) | null = null;
	onclose: ((event: CloseEvent) => void) | null = null;
	constructor(readonly url: string) {
		FakeBrowserSocket.instances.push(this);
	}
	open(): void {
		this.readyState = FakeBrowserSocket.OPEN;
		this.onopen?.();
	}
	receive(message: Record<string, unknown>): void {
		this.onmessage?.({ data: JSON.stringify(message) });
	}
	send(data: string): void {
		this.sent.push(JSON.parse(data));
	}
	close(): void {
		this.closed = true;
		this.readyState = FakeBrowserSocket.CLOSED;
	}
}

function host(): PotionUIHostApi {
	return (window as unknown as { __potionui: PotionUIHostApi }).__potionui;
}

beforeAll(() => {
	initHostApi();
});

describe('window.__potionui.confirm', () => {
	afterEach(() => cancelAllConfirms());

	it('passes the button labels to the app dialog and resolves false on cancel', async () => {
		const pending = host().confirm({ message: 'Discard the storyboard?', confirmLabel: 'Discard', cancelLabel: 'Keep it' });
		const request = get(activeConfirm);
		expect(request).toMatchObject({ message: 'Discard the storyboard?', confirmLabel: 'Discard', cancelLabel: 'Keep it' });
		settleConfirm(request!.id, false);
		await expect(pending).resolves.toBe(false);
	});
});

describe('window.__potionui.chat', () => {
	afterEach(() => chatPanelStore.reset());

	it('opens, closes and toggles the global chat panel', () => {
		const chat = host().chat;
		expect(chat.isOpen()).toBe(false);
		chat.open();
		expect(chat.isOpen()).toBe(true);
		expect(get(chatPanelStore).isOpen).toBe(true);
		chat.close();
		expect(chat.isOpen()).toBe(false);
		chat.toggle();
		expect(chat.isOpen()).toBe(true);
		chat.toggle();
		expect(chat.isOpen()).toBe(false);
	});

	it('reflects a panel opened by the host itself', () => {
		chatPanelStore.open();
		expect(host().chat.isOpen()).toBe(true);
	});

	it('keeps the page context hooks', () => {
		const chat = host().chat;
		expect(chat.provideContext).toBe(provideContext);
		expect(chat.declareMode).toBe(declareMode);
		expect(chat.onToolApplied).toBe(onToolApplied);
	});
});

describe('window.__potionui.chat composer', () => {
	function fakeComposer(send: (text: string) => Promise<void> = async () => {}): ChatComposer & {
		prefills: Array<[string, boolean]>;
		sends: string[];
	} {
		const prefills: Array<[string, boolean]> = [];
		const sends: string[] = [];
		return {
			prefills,
			sends,
			prefill: (text, replace) => {
				prefills.push([text, replace]);
			},
			send: (text) => {
				sends.push(text);
				return send(text);
			}
		};
	}

	let detach: (() => void) | undefined;

	afterEach(() => {
		detach?.();
		detach = undefined;
		chatPanelStore.reset();
		vi.useRealTimers();
	});

	it('opens the panel on prefill and hands the text over once the chat is ready', () => {
		host().chat.prefill('Plan five shots');
		host().chat.prefill('Rewrite it', { replace: true });
		expect(host().chat.isOpen()).toBe(true);
		const composer = fakeComposer();
		detach = chatPanelStore.attachComposer(composer);
		expect(composer.prefills).toEqual([
			['Plan five shots', false],
			['Rewrite it', true]
		]);
		host().chat.prefill('Again');
		expect(composer.prefills.at(-1)).toEqual(['Again', false]);
	});

	it('ignores a prefill that is not text', () => {
		(host().chat.prefill as (text: unknown) => void)(42);
		expect(host().chat.isOpen()).toBe(false);
	});

	it('drops a prefill when the panel is closed before the chat is ready', () => {
		host().chat.prefill('hello');
		host().chat.close();
		const composer = fakeComposer();
		detach = chatPanelStore.attachComposer(composer);
		expect(composer.prefills).toEqual([]);
	});

	it('drops a prefill when the panel is toggled closed before the chat is ready', () => {
		host().chat.prefill('hello');
		host().chat.toggle();
		const composer = fakeComposer();
		detach = chatPanelStore.attachComposer(composer);
		expect(composer.prefills).toEqual([]);
	});

	it('drops a prefill when the chat never becomes ready', () => {
		vi.useFakeTimers();
		host().chat.prefill('hello');
		vi.advanceTimersByTime(CHAT_READY_TIMEOUT_MS - 1);
		host().chat.prefill('again');
		vi.advanceTimersByTime(1);
		const composer = fakeComposer();
		detach = chatPanelStore.attachComposer(composer);
		expect(composer.prefills).toEqual([]);
	});

	it('opens the panel on send and resolves once the chat has sent the message', async () => {
		const pending = host().chat.send('Make the intro shorter');
		expect(host().chat.isOpen()).toBe(true);
		const composer = fakeComposer();
		detach = chatPanelStore.attachComposer(composer);
		await expect(pending).resolves.toBeUndefined();
		expect(composer.sends).toEqual(['Make the intro shorter']);
	});

	it('passes on the rejection of a chat that cannot send', async () => {
		detach = chatPanelStore.attachComposer(fakeComposer(() => Promise.reject(new Error('No chat model is configured'))));
		await expect(host().chat.send('hello')).rejects.toThrow('No chat model is configured');
	});

	it('turns a chat that throws while sending into a rejection', async () => {
		detach = chatPanelStore.attachComposer(
			fakeComposer(() => {
				throw new Error('The chat broke');
			})
		);
		await expect(host().chat.send('hello')).rejects.toThrow('The chat broke');
	});

	it('rejects a queued send when the chat throws as it takes it', async () => {
		const pending = host().chat.send('hello');
		detach = chatPanelStore.attachComposer(
			fakeComposer(() => {
				throw new Error('The chat broke');
			})
		);
		await expect(pending).rejects.toThrow('The chat broke');
	});

	it('rejects an empty text without opening the panel', async () => {
		await expect(host().chat.send('   ')).rejects.toThrow(TypeError);
		expect(host().chat.isOpen()).toBe(false);
	});

	it('rejects a send when the panel is closed before the chat is ready', async () => {
		const pending = host().chat.send('hello');
		host().chat.close();
		await expect(pending).rejects.toThrow('closed');
		const composer = fakeComposer();
		detach = chatPanelStore.attachComposer(composer);
		expect(composer.sends).toEqual([]);
	});

	it('rejects a send when the chat never becomes ready', async () => {
		vi.useFakeTimers();
		const pending = host().chat.send('hello');
		const settled = expect(pending).rejects.toThrow('The chat is not available');
		vi.advanceTimersByTime(CHAT_READY_TIMEOUT_MS);
		await settled;
	});

	it('sends a message queued after the chat panel detached to the next chat', async () => {
		const first = fakeComposer();
		chatPanelStore.attachComposer(first)();
		const pending = host().chat.send('later');
		expect(first.sends).toEqual([]);
		const second = fakeComposer();
		detach = chatPanelStore.attachComposer(second);
		await pending;
		expect(second.sends).toEqual(['later']);
	});

	it('queues again once the chat panel has detached', () => {
		const first = fakeComposer();
		chatPanelStore.attachComposer(first)();
		host().chat.prefill('later');
		expect(first.prefills).toEqual([]);
		const second = fakeComposer();
		detach = chatPanelStore.attachComposer(second);
		expect(second.prefills).toEqual([['later', false]]);
	});
});

describe('window.__potionui.keyboard', () => {
	afterEach(() => {
		releaseAllKeyboardHolds();
		resumeKeyboard();
	});

	it('silences the host shortcuts between suppress and resume', () => {
		const keyboard = host().keyboard;
		expect(isKeyboardSuppressed()).toBe(false);
		keyboard.suppress();
		expect(isKeyboardSuppressed()).toBe(true);
		keyboard.resume();
		expect(isKeyboardSuppressed()).toBe(false);
	});

	it('counts suppressions so one resume does not cancel another', () => {
		const keyboard = host().keyboard;
		keyboard.suppress();
		keyboard.suppress();
		keyboard.resume();
		expect(isKeyboardSuppressed()).toBe(true);
		keyboard.resume();
		expect(isKeyboardSuppressed()).toBe(false);
		keyboard.resume();
		keyboard.suppress();
		expect(isKeyboardSuppressed()).toBe(true);
	});

	it('returns a release that only drops its own suppression, once', () => {
		const keyboard = host().keyboard;
		const releaseFirst = keyboard.suppress();
		keyboard.suppress();
		releaseFirst();
		releaseFirst();
		expect(isKeyboardSuppressed()).toBe(true);
		keyboard.resume();
		expect(isKeyboardSuppressed()).toBe(false);
	});

	it('balances suppressions released by resume and by their own release', () => {
		const keyboard = host().keyboard;
		keyboard.suppress();
		const releaseSecond = keyboard.suppress();
		keyboard.resume();
		releaseSecond();
		expect(isKeyboardSuppressed()).toBe(false);
	});

	it('does not interfere with the core suppression flag', () => {
		const keyboard = host().keyboard;
		keyboard.suppress();
		resumeKeyboard();
		expect(isKeyboardSuppressed()).toBe(true);
		keyboard.resume();
		suppressKeyboard();
		keyboard.resume();
		expect(isKeyboardSuppressed()).toBe(true);
		resumeKeyboard();
		expect(isKeyboardSuppressed()).toBe(false);
	});

	it('is released when the plugin page host clears every hold', () => {
		host().keyboard.suppress();
		host().keyboard.suppress();
		releaseAllKeyboardHolds();
		expect(isKeyboardSuppressed()).toBe(false);
	});

	it('ignores a release left over from a page that was already cleared', () => {
		const stale = host().keyboard.suppress();
		releaseAllKeyboardHolds();
		host().keyboard.suppress();
		stale();
		expect(isKeyboardSuppressed()).toBe(true);
	});
});

describe('window.__potionui.registerIcon', () => {
	const added: string[] = [];

	afterEach(() => {
		for (const name of added.splice(0)) delete iconPaths[name];
	});

	it('adds an icon that the icon lookup resolves', () => {
		added.push('my-plugin-clapper');
		expect(host().registerIcon('my-plugin-clapper', 'M4 4h16v16H4z')).toBe(true);
		expect(getIconPath('my-plugin-clapper')).toBe('M4 4h16v16H4z');
		expect(get(iconLookup)('my-plugin-clapper')).toBe('M4 4h16v16H4z');
	});

	it('copies a multi-path icon', () => {
		const paths = ['M1 1h2', 'M3 3h4'];
		added.push('my-plugin-timeline');
		expect(host().registerIcon('my-plugin-timeline', paths)).toBe(true);
		paths.push('M9 9h9');
		expect(getIconPath('my-plugin-timeline')).toEqual(['M1 1h2', 'M3 3h4']);
	});

	it('notifies mounted icons when a new icon arrives', () => {
		const seen: (string | string[] | null)[] = [];
		const stop = iconLookup.subscribe((lookup) => seen.push(lookup('my-plugin-late')));
		added.push('my-plugin-late');
		host().registerIcon('my-plugin-late', 'M2 2h20');
		stop();
		expect(seen).toEqual([null, 'M2 2h20']);
	});

	it('never replaces a core icon', () => {
		const original = iconPaths.plus;
		expect(host().registerIcon('plus', 'M0 0h1')).toBe(false);
		expect(iconPaths.plus).toBe(original);
	});

	it('keeps the first registration of a plugin icon', () => {
		added.push('my-plugin-render');
		expect(host().registerIcon('my-plugin-render', 'M1 1h1')).toBe(true);
		expect(host().registerIcon('my-plugin-render', 'M2 2h2')).toBe(false);
		expect(getIconPath('my-plugin-render')).toBe('M1 1h1');
	});

	it('refuses an empty name or empty paths', () => {
		expect(host().registerIcon('', 'M1 1h1')).toBe(false);
		expect(host().registerIcon('my-plugin-empty', '')).toBe(false);
		expect(host().registerIcon('my-plugin-empty', [])).toBe(false);
		expect(host().registerIcon('my-plugin-empty', ['M1 1h1', ''])).toBe(false);
		expect('my-plugin-empty' in iconPaths).toBe(false);
	});
});

describe('window.__potionui.generation.subscribe', () => {
	afterEach(() => {
		vi.unstubAllGlobals();
		FakeBrowserSocket.instances = [];
	});

	function stubSocket(): void {
		vi.stubGlobal('WebSocket', FakeBrowserSocket);
	}

	it('keeps prepareRequest', () => {
		expect(typeof host().generation.prepareRequest).toBe('function');
	});

	it('opens one generation socket for every subscription and subscribes each id', () => {
		stubSocket();
		const generation = host().generation;
		const stopFirst = generation.subscribe('gen-1', () => {});
		const stopSecond = generation.subscribe('gen-2', () => {});
		expect(FakeBrowserSocket.instances).toHaveLength(1);
		const socket = FakeBrowserSocket.instances[0];
		expect(socket.url).toContain('/ws/generation');
		socket.open();
		expect(socket.sent).toEqual([
			{ type: 'subscribe_generation', generation_id: 'gen-1' },
			{ type: 'subscribe_generation', generation_id: 'gen-2' }
		]);
		const stopThird = generation.subscribe('gen-3', () => {});
		expect(socket.sent.at(-1)).toEqual({ type: 'subscribe_generation', generation_id: 'gen-3' });
		expect(FakeBrowserSocket.instances).toHaveLength(1);
		stopFirst();
		stopSecond();
		stopThird();
	});

	it('delivers only the four generation events of the subscribed id', () => {
		stubSocket();
		const first: GenerationEvent[] = [];
		const second: GenerationEvent[] = [];
		const stopFirst = host().generation.subscribe('gen-1', (event) => first.push(event));
		const stopSecond = host().generation.subscribe('gen-2', (event) => second.push(event));
		const socket = FakeBrowserSocket.instances[0];
		socket.open();
		socket.receive({ type: 'subscribed', generation_id: 'gen-1' });
		socket.receive({ type: 'generation_status', generation_id: 'gen-1', status: 'running', progress: 40 });
		socket.receive({ type: 'generation_status', generation_id: 'gen-2', status: 'queued' });
		socket.receive({ type: 'generation_complete', data: { id: 'gen-1' } });
		socket.receive({ type: 'generation_error', data: { generation_id: 'gen-2', error: 'boom' } });
		socket.receive({ type: 'generation_cancelled', data: { id: 'gen-1' } });
		expect(first.map((event) => event.type)).toEqual([
			'generation_status',
			'generation_complete',
			'generation_cancelled'
		]);
		expect(first[0]).toMatchObject({ status: 'running', progress: 40 });
		expect(second.map((event) => event.type)).toEqual(['generation_status', 'generation_error']);
		stopFirst();
		stopSecond();
	});

	it('stops a handler on unsubscribe and closes the socket after the last one', () => {
		stubSocket();
		const kept: GenerationEvent[] = [];
		const dropped: GenerationEvent[] = [];
		const stopDropped = host().generation.subscribe('gen-1', (event) => dropped.push(event));
		const stopKept = host().generation.subscribe('gen-1', (event) => kept.push(event));
		const socket = FakeBrowserSocket.instances[0];
		socket.open();
		expect(socket.sent).toHaveLength(1);
		stopDropped();
		stopDropped();
		socket.receive({ type: 'generation_status', generation_id: 'gen-1', status: 'running' });
		expect(dropped).toEqual([]);
		expect(kept).toHaveLength(1);
		expect(socket.closed).toBe(false);
		stopKept();
		expect(socket.closed).toBe(true);
		const stopAgain = host().generation.subscribe('gen-1', () => {});
		expect(FakeBrowserSocket.instances).toHaveLength(2);
		stopAgain();
	});

	it('keeps other handlers running when one throws', () => {
		stubSocket();
		const received: GenerationEvent[] = [];
		const stopThrowing = host().generation.subscribe('gen-1', () => {
			throw new Error('plugin bug');
		});
		const stopReceiving = host().generation.subscribe('gen-1', (event) => received.push(event));
		const socket = FakeBrowserSocket.instances[0];
		socket.open();
		socket.receive({ type: 'generation_complete', data: { id: 'gen-1' } });
		expect(received).toHaveLength(1);
		stopThrowing();
		stopReceiving();
	});

	it('revives a dropped socket when a new generation is followed', () => {
		stubSocket();
		const received: GenerationEvent[] = [];
		const stopFirst = host().generation.subscribe('gen-1', (event) => received.push(event));
		const dropped = FakeBrowserSocket.instances[0];
		dropped.open();
		dropped.readyState = FakeBrowserSocket.CLOSED;
		dropped.onclose?.({ code: 1006 } as CloseEvent);
		const stopSecond = host().generation.subscribe('gen-2', () => {});
		expect(FakeBrowserSocket.instances).toHaveLength(2);
		const revived = FakeBrowserSocket.instances[1];
		revived.open();
		expect(revived.sent).toEqual([
			{ type: 'subscribe_generation', generation_id: 'gen-1' },
			{ type: 'subscribe_generation', generation_id: 'gen-2' }
		]);
		revived.receive({ type: 'generation_complete', data: { id: 'gen-1' } });
		expect(received).toHaveLength(1);
		stopFirst();
		stopSecond();
	});

	it('leaves a newer subscription alone when an old unsubscribe runs again', () => {
		stubSocket();
		const received: GenerationEvent[] = [];
		const stopOld = host().generation.subscribe('gen-1', () => {});
		stopOld();
		const stopNew = host().generation.subscribe('gen-1', (event) => received.push(event));
		stopOld();
		const socket = FakeBrowserSocket.instances.at(-1)!;
		socket.open();
		socket.receive({ type: 'generation_complete', data: { id: 'gen-1' } });
		expect(socket.closed).toBe(false);
		expect(received).toHaveLength(1);
		stopNew();
	});

	it('ignores a subscription without an id or a handler', () => {
		stubSocket();
		const generation = host().generation as unknown as {
			subscribe(id: unknown, handler: unknown): () => void;
		};
		generation.subscribe('', () => {})();
		generation.subscribe('gen-1', null)();
		expect(FakeBrowserSocket.instances).toHaveLength(0);
	});
});

describe('createGenerationEvents', () => {
	it('forgets an id on the socket once its last handler leaves', () => {
		const socket = {
			connect: vi.fn(),
			disconnect: vi.fn(),
			subscribe: vi.fn(),
			unsubscribe: vi.fn()
		};
		const createSocket = vi.fn(() => socket as unknown as WebSocketService);
		const events = createGenerationEvents(createSocket);
		const stopFirst = events.subscribe('gen-1', () => {});
		const stopSecond = events.subscribe('gen-2', () => {});
		expect(createSocket).toHaveBeenCalledTimes(1);
		expect(socket.subscribe.mock.calls.map(([id]) => id)).toEqual(['gen-1', 'gen-2']);
		stopFirst();
		expect(socket.unsubscribe).toHaveBeenCalledWith('gen-1');
		expect(socket.disconnect).not.toHaveBeenCalled();
		stopSecond();
		expect(socket.disconnect).toHaveBeenCalledTimes(1);
	});
});
