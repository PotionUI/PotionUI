// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { writable, get } from 'svelte/store';
import { StatefulWebSocket, WebSocketConnectError, type ConnectionState } from './StatefulWebSocket';

class MockWebSocket {
	static readonly CONNECTING = 0;
	static readonly OPEN = 1;
	static readonly CLOSING = 2;
	static readonly CLOSED = 3;
	static instances: MockWebSocket[] = [];
	/** When set, the next `new MockWebSocket()` throws this instead of constructing. */
	static nextConstructError: Error | null = null;

	readyState = MockWebSocket.CONNECTING;
	onopen: (() => void) | null = null;
	onmessage: ((event: MessageEvent) => void) | null = null;
	onerror: ((event: Event) => void) | null = null;
	onclose: ((event: CloseEvent) => void) | null = null;
	sent: unknown[] = [];

	constructor(public url: string) {
		if (MockWebSocket.nextConstructError) {
			const error = MockWebSocket.nextConstructError;
			MockWebSocket.nextConstructError = null;
			throw error;
		}
		MockWebSocket.instances.push(this);
	}

	send(data: string): void {
		this.sent.push(JSON.parse(data));
	}

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

	/** Simulates the server starting a close handshake: readyState moves to CLOSING before the eventual onclose. */
	startClosing(): void {
		this.readyState = MockWebSocket.CLOSING;
	}

	message(data: unknown): void {
		this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
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

	it('a pending retry cancelled by disconnect() before it fires never creates a socket', async () => {
		const state = writable<ConnectionState>('disconnected');
		const socket = new TestSocket(state);

		const connectPromise = socket.connectAsync();
		MockWebSocket.instances[0].open();
		await connectPromise;

		// Unsolicited close schedules a retry ...
		MockWebSocket.instances[0].fail(1006);
		expect(get(state)).toBe('reconnecting');

		// ... but the caller disconnects before the backoff elapses.
		socket.disconnect();
		expect(get(state)).toBe('disconnected');

		const countBefore = MockWebSocket.instances.length;
		await vi.advanceTimersByTimeAsync(30000);
		expect(MockWebSocket.instances.length).toBe(countBefore);
		expect(get(state)).toBe('disconnected');
	});

	it('coalesces concurrent connectAsync() callers onto one handshake and settles both on open', async () => {
		const socket = new TestSocket();

		const first = socket.connectAsync();
		const second = socket.connectAsync();

		expect(MockWebSocket.instances.length).toBe(1);

		MockWebSocket.instances[0].open();

		await expect(first).resolves.toBeUndefined();
		await expect(second).resolves.toBeUndefined();
	});

	it('disconnect() during a pending handshake rejects the waiter instead of hanging forever', async () => {
		const socket = new TestSocket();

		const pending = socket.connectAsync();
		socket.disconnect();

		await expect(pending).rejects.toBeInstanceOf(WebSocketConnectError);
	});

	it('a close before the socket ever opened does not reject immediately - it keeps retrying until one succeeds', async () => {
		const state = writable<ConnectionState>('disconnected');
		const socket = new TestSocket(state);

		const pending = socket.connectAsync();
		MockWebSocket.instances[0].fail(1006);
		expect(get(state)).toBe('reconnecting');

		await vi.advanceTimersByTimeAsync(1000);
		expect(MockWebSocket.instances.length).toBe(2);
		MockWebSocket.instances[1].open();

		await expect(pending).resolves.toBeUndefined();
		expect(get(state)).toBe('connected');
	});

	it('the WebSocket constructor throwing rejects the waiter immediately and does not retry', async () => {
		const state = writable<ConnectionState>('disconnected');
		const socket = new TestSocket(state);

		MockWebSocket.nextConstructError = new Error('blocked by browser policy');
		const pending = socket.connectAsync();

		await expect(pending).rejects.toThrow('blocked by browser policy');
		expect(get(state)).toBe('disconnected');

		await vi.advanceTimersByTimeAsync(60000);
		expect(MockWebSocket.instances.length).toBe(0);
	});

	it('retry exhaustion rejects a waiter that never saw a successful open', async () => {
		const socket = new TestSocket();

		const pending = socket.connectAsync();
		// Attach the rejection expectation now: `pending` rejects mid-loop
		// (before the final await below runs), which would otherwise surface
		// as an unhandled promise rejection.
		const settled = expect(pending).rejects.toBeInstanceOf(WebSocketConnectError);

		for (let i = 0; i < 6; i++) {
			const last = MockWebSocket.instances[MockWebSocket.instances.length - 1];
			last.fail(1006);
			await vi.advanceTimersByTimeAsync(60000);
		}

		await settled;
	});

	it('reconnects cleanly after disconnect(), ignoring the abandoned socket', async () => {
		const state = writable<ConnectionState>('disconnected');
		const socket = new TestSocket(state);

		const first = socket.connectAsync();
		const abandoned = MockWebSocket.instances[0];
		socket.disconnect();
		await expect(first).rejects.toBeInstanceOf(WebSocketConnectError);

		const second = socket.connectAsync();
		expect(MockWebSocket.instances.length).toBe(2);

		// The abandoned socket's handlers were detached by disconnect() - this is a no-op.
		abandoned.open();
		expect(get(state)).toBe('connecting');

		MockWebSocket.instances[1].open();
		await expect(second).resolves.toBeUndefined();
		expect(get(state)).toBe('connected');
	});

	it('a lingering CLOSING socket cannot touch state once a replacement is pending', async () => {
		const state = writable<ConnectionState>('disconnected');
		const socket = new TestSocket(state);

		const first = socket.connectAsync();
		const a = MockWebSocket.instances[0];
		a.open();
		await first;
		expect(get(state)).toBe('connected');

		// Server starts closing A, but the close event hasn't landed yet.
		a.startClosing();
		const second = socket.connectAsync();
		const b = MockWebSocket.instances[1];
		expect(get(state)).toBe('connecting');

		// A's belated events must not touch state, messages, or B's waiter.
		a.message({ type: 'stale-from-a' });
		a.error();
		a.fail(1006);

		expect(get(state)).toBe('connecting');
		expect(socket.messages).toEqual([]);
		expect(MockWebSocket.instances.length).toBe(2); // no reconnect scheduled off A's close

		b.open();
		await expect(second).resolves.toBeUndefined();
		expect(get(state)).toBe('connected');
	});

	it('a lingering CLOSING socket cannot touch state, heartbeat, or messages once the replacement is connected', async () => {
		const state = writable<ConnectionState>('disconnected');
		const socket = new TestSocket(state);

		const first = socket.connectAsync();
		const a = MockWebSocket.instances[0];
		a.open();
		await first;

		a.startClosing();
		socket.connectAsync();
		const b = MockWebSocket.instances[1];
		b.open();
		expect(get(state)).toBe('connected');

		// B's heartbeat is running.
		await vi.advanceTimersByTimeAsync(30000);
		expect(b.sent.length).toBe(1);

		// A's belated open/message/error/close must all be ignored now that B is current.
		a.open();
		a.message({ type: 'stale-from-a' });
		a.error();
		a.fail(1006);

		expect(get(state)).toBe('connected');
		expect(socket.messages).toEqual([]);
		expect(MockWebSocket.instances.length).toBe(2); // no reconnect scheduled off A's close

		// B's heartbeat must still be alive - A's close must not have stopped it.
		await vi.advanceTimersByTimeAsync(30000);
		expect(b.sent.length).toBe(2);
	});

	it('a failed replacement construction still retires the old attempt - its late close cannot resurrect a retry', async () => {
		const state = writable<ConnectionState>('disconnected');
		const socket = new TestSocket(state);

		const first = socket.connectAsync();
		const a = MockWebSocket.instances[0];
		a.open();
		await first;

		a.startClosing();
		MockWebSocket.nextConstructError = new Error('blocked by browser policy');
		const second = socket.connectAsync();

		await expect(second).rejects.toThrow('blocked by browser policy');
		expect(get(state)).toBe('disconnected');

		// The failed replacement was never pushed to instances - only A exists.
		expect(MockWebSocket.instances.length).toBe(1);

		// A's belated close must be a no-op now: no reconnecting state, no retry.
		a.fail(1006);
		await vi.advanceTimersByTimeAsync(60000);

		expect(get(state)).toBe('disconnected');
		expect(MockWebSocket.instances.length).toBe(1);
	});
});
