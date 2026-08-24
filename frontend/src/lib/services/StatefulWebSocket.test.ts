// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { writable, get } from 'svelte/store';
import { StatefulWebSocket, type ConnectionState } from './StatefulWebSocket';

class MockWebSocket {
	static readonly CONNECTING = 0;
	static readonly OPEN = 1;
	static readonly CLOSING = 2;
	static readonly CLOSED = 3;
	static instances: MockWebSocket[] = [];

	readyState = MockWebSocket.CONNECTING;
	onopen: (() => void) | null = null;
	onmessage: ((event: MessageEvent) => void) | null = null;
	onerror: ((event: Event) => void) | null = null;
	onclose: ((event: CloseEvent) => void) | null = null;

	constructor(public url: string) {
		MockWebSocket.instances.push(this);
	}

	send(): void {}

	close(): void {
		this.readyState = MockWebSocket.CLOSED;
	}

	open(): void {
		this.readyState = MockWebSocket.OPEN;
		this.onopen?.();
	}

	error(): void {
		this.onerror?.(new Event('error'));
	}

	fail(code: number): void {
		this.readyState = MockWebSocket.CLOSED;
		this.onclose?.({ code } as CloseEvent);
	}
}

class TestSocket extends StatefulWebSocket {
	messages: unknown[] = [];

	constructor(state = writable<ConnectionState>('disconnected')) {
		super(state, 'test');
	}

	protected override buildWsUrl(): string {
		return 'ws://test/socket';
	}

	protected override onMessage(message: unknown): void {
		this.messages.push(message);
	}
}

describe('StatefulWebSocket', () => {
	beforeEach(() => {
		MockWebSocket.instances = [];
		vi.stubGlobal('WebSocket', MockWebSocket);
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.unstubAllGlobals();
	});

	it('connectAsync() sets connecting then connected, and resolves on open', async () => {
		const state = writable<ConnectionState>('disconnected');
		const socket = new TestSocket(state);

		const pending = socket.connectAsync();
		expect(get(state)).toBe('connecting');

		const ws = MockWebSocket.instances[0];
		ws.open();

		await expect(pending).resolves.toBeUndefined();
		expect(get(state)).toBe('connected');
		expect(socket.isConnected()).toBe(true);
	});

	it('connectAsync() rejects when the socket errors before opening', async () => {
		const socket = new TestSocket();
		const pending = socket.connectAsync();

		const ws = MockWebSocket.instances[0];
		ws.error();

		await expect(pending).rejects.toBeInstanceOf(Event);
	});

	it('disconnect() is intentional: onClose() after disconnect() does not schedule a reconnect', async () => {
		const state = writable<ConnectionState>('disconnected');
		const socket = new TestSocket(state);

		const connectPromise = socket.connectAsync();
		MockWebSocket.instances[0].open();
		await connectPromise;

		socket.disconnect();
		expect(get(state)).toBe('disconnected');

		// BaseWebSocket.disconnect() detaches the real onclose handler before
		// close() to stop the browser event reaching this class at all - so the
		// only way to pin the intentionalDisconnect guard itself (defense in
		// depth for whatever calls onClose after a disconnect) is to invoke it
		// directly, the way the base class would.
		(socket as unknown as { onClose(event: CloseEvent): void }).onClose({ code: 1006 } as CloseEvent);

		const countBefore = MockWebSocket.instances.length;
		await vi.advanceTimersByTimeAsync(30000);
		expect(MockWebSocket.instances.length).toBe(countBefore);
		expect(get(state)).toBe('disconnected');
	});

	it('an unsolicited close schedules a reconnect with exponential backoff', async () => {
		const state = writable<ConnectionState>('disconnected');
		const socket = new TestSocket(state);

		const connectPromise = socket.connectAsync();
		MockWebSocket.instances[0].open();
		await connectPromise;

		// Server drops the connection without socket.disconnect() being called.
		MockWebSocket.instances[0].fail(1006);
		expect(get(state)).toBe('reconnecting');
		expect(MockWebSocket.instances.length).toBe(1);

		// First backoff: reconnectDelay (1000ms).
		await vi.advanceTimersByTimeAsync(1000);
		expect(MockWebSocket.instances.length).toBe(2);
	});

	it('gives up after maxReconnectAttempts and stops scheduling new sockets', async () => {
		const state = writable<ConnectionState>('disconnected');
		const socket = new TestSocket(state);

		const connectPromise = socket.connectAsync();
		MockWebSocket.instances[0].open();
		await connectPromise;

		// Fail every reconnect attempt in turn, advancing past each backoff delay.
		for (let i = 0; i < 5; i++) {
			const last = MockWebSocket.instances[MockWebSocket.instances.length - 1];
			last.fail(1006);
			await vi.advanceTimersByTimeAsync(60000);
		}

		// 1 initial + 5 reconnect attempts = 6 sockets, then it stops.
		expect(MockWebSocket.instances.length).toBe(6);

		const last = MockWebSocket.instances[MockWebSocket.instances.length - 1];
		last.fail(1006);
		await vi.advanceTimersByTimeAsync(60000);
		expect(MockWebSocket.instances.length).toBe(6);
	});
});
