// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';

const warn = vi.hoisted(() => vi.fn());
vi.mock('$lib/utils/logger', () => ({ logger: { warn, error: vi.fn(), info: vi.fn(), debug: vi.fn() } }));
vi.mock('$lib/stores/auth', () => ({ authStore: { expireSession: vi.fn() } }));
const ticketRef = vi.hoisted(() => ({ issued: 0, fail: null as unknown }));
vi.mock('$lib/services/api/index', () => ({ api: { getToken: () => 'session-token' } }));
vi.mock('./wsTicket', () => ({
	ticketedWsUrl: vi.fn(async (path: string) => {
		if (ticketRef.fail) throw ticketRef.fail;
		ticketRef.issued += 1;
		return `ws://test.invalid${path}?ticket=ticket-${ticketRef.issued}`;
	}),
	isAuthRefusal: (error: unknown) => (error as { response?: { status?: number } })?.response?.status === 401
}));

import { authStore } from '$lib/stores/auth';
import { createGenerationSocket } from './websocket';

describe('createGenerationSocket', () => {
	it('expires the session on an auth-failed close (4001), same as every other generation socket', () => {
		const ws = createGenerationSocket();

		// Simulate the server closing the connection with the auth-failure code,
		// which WebSocketService.onClose() turns into the onAuthFailed callback.
		(ws as unknown as { onClose(event: CloseEvent): void }).onClose({ code: 4001 } as CloseEvent);

		expect(authStore.expireSession).toHaveBeenCalledTimes(1);
	});
});

describe('generation socket reconnect', () => {
	class RecordingSocket {
		static readonly CONNECTING = 0;
		static readonly OPEN = 1;
		static readonly CLOSING = 2;
		static readonly CLOSED = 3;
		static urls: string[] = [];
		readyState = RecordingSocket.CONNECTING;
		onopen: (() => void) | null = null;
		onmessage: ((event: MessageEvent) => void) | null = null;
		onerror: ((event: Event) => void) | null = null;
		onclose: ((event: CloseEvent) => void) | null = null;
		constructor(url: string) {
			RecordingSocket.urls.push(url);
		}
		close(): void {
			this.readyState = RecordingSocket.CLOSED;
		}
		send(): void {}
	}

	afterEach(() => {
		vi.useRealTimers();
		vi.unstubAllGlobals();
		ticketRef.issued = 0;
		ticketRef.fail = null;
		RecordingSocket.urls = [];
	});

	it('connects with a ticket and never puts a token in the URL', async () => {
		vi.stubGlobal('WebSocket', RecordingSocket);
		const ws = createGenerationSocket();

		ws.connect();
		await vi.waitFor(() => expect(RecordingSocket.urls).toHaveLength(1));

		expect(RecordingSocket.urls[0]).toContain('ticket=ticket-1');
		expect(RecordingSocket.urls[0]).not.toContain('token=');
		ws.disconnect();
	});

	it('fetches a fresh ticket for every reconnect', async () => {
		vi.useFakeTimers();
		vi.stubGlobal('WebSocket', RecordingSocket);
		const ws = createGenerationSocket();

		ws.connect();
		await vi.advanceTimersByTimeAsync(0);
		expect(RecordingSocket.urls[0]).toContain('ticket=ticket-1');

		const socket = (ws as unknown as { ws: RecordingSocket }).ws;
		socket.readyState = RecordingSocket.CLOSED;
		socket.onclose?.({ code: 1006 } as CloseEvent);
		await vi.advanceTimersByTimeAsync(5000);

		expect(RecordingSocket.urls).toHaveLength(2);
		expect(RecordingSocket.urls[1]).toContain('ticket=ticket-2');
		expect(ticketRef.issued).toBe(2);
		ws.disconnect();
	});

	it('does not fetch a second ticket while the first connect is still waiting for one', async () => {
		vi.stubGlobal('WebSocket', RecordingSocket);
		const ws = createGenerationSocket();

		ws.connect();
		ws.connect();
		ws.connect();
		await vi.waitFor(() => expect(RecordingSocket.urls).toHaveLength(1));

		expect(ticketRef.issued).toBe(1);
		ws.disconnect();
	});

	it('opens no socket when disconnect() lands while the ticket is in flight', async () => {
		vi.stubGlobal('WebSocket', RecordingSocket);
		const ws = createGenerationSocket();

		ws.connect();
		ws.disconnect();
		await new Promise((resolve) => setTimeout(resolve, 0));

		expect(RecordingSocket.urls).toHaveLength(0);
	});

	it('backs off between attempts when tickets cannot be fetched instead of hammering the route', async () => {
		vi.useFakeTimers();
		vi.stubGlobal('WebSocket', RecordingSocket);
		ticketRef.fail = new Error('network down');
		const ws = createGenerationSocket();

		ws.connect();
		await vi.advanceTimersByTimeAsync(0);
		const afterFirst = vi.mocked((await import('./wsTicket')).ticketedWsUrl).mock.calls.length;
		await vi.advanceTimersByTimeAsync(999);
		const beforeSecondDelay = vi.mocked((await import('./wsTicket')).ticketedWsUrl).mock.calls.length;
		await vi.advanceTimersByTimeAsync(1);
		const afterSecondDelay = vi.mocked((await import('./wsTicket')).ticketedWsUrl).mock.calls.length;

		expect(beforeSecondDelay).toBe(afterFirst);
		expect(afterSecondDelay).toBe(afterFirst + 1);
		expect(RecordingSocket.urls).toHaveLength(0);
		ws.disconnect();
	});

	it('expires the session and stops retrying when the ticket route refuses the credential', async () => {
		vi.useFakeTimers();
		vi.stubGlobal('WebSocket', RecordingSocket);
		ticketRef.fail = { response: { status: 401 } };
		vi.mocked(authStore.expireSession).mockClear();
		const ws = createGenerationSocket();

		ws.connect();
		await vi.advanceTimersByTimeAsync(60000);

		expect(authStore.expireSession).toHaveBeenCalledTimes(1);
		expect(ticketRef.issued).toBe(0);
		expect(RecordingSocket.urls).toHaveLength(0);
		ws.disconnect();
	});
});


describe('generation socket message routing', () => {
	function deliver(message: Record<string, unknown>): void {
		const ws = createGenerationSocket();
		(ws as unknown as { onMessage(m: unknown): void }).onMessage(message);
	}

	beforeEach(() => warn.mockClear());

	it('ignores the id-less status snapshot without warning', () => {
		deliver({ type: 'status_update', data: { id: 'g1', status: 'running' } });
		expect(warn).not.toHaveBeenCalled();
	});

	it('still warns about other messages that carry no generation id', () => {
		deliver({ type: 'gallery_update', data: {} });
		expect(warn).toHaveBeenCalledWith('Message gallery_update has no generation_id');
	});
});
