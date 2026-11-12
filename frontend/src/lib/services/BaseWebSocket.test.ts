// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/utils/logger', () => ({ logger: { warn: vi.fn(), error: vi.fn(), info: vi.fn(), debug: vi.fn() } }));

const tickets = vi.hoisted(() => ({ issued: [] as string[], failWith: null as unknown, gate: null as Promise<void> | null }));
vi.mock('./wsTicket', () => ({
	ticketedWsUrl: vi.fn(async (path: string) => {
		if (tickets.gate) await tickets.gate;
		if (tickets.failWith) throw tickets.failWith;
		const ticket = `ticket-${tickets.issued.length + 1}`;
		tickets.issued.push(ticket);
		return `ws://host${path}?ticket=${ticket}`;
	}),
	isAuthRefusal: (error: unknown) => {
		const status = (error as { response?: { status?: number } } | null)?.response?.status;
		return status === 401 || status === 403;
	}
}));

import { BaseWebSocket, type BaseWebSocketMessage } from './BaseWebSocket';

class FakeSocket {
	static readonly CONNECTING = 0;
	static readonly OPEN = 1;
	static readonly CLOSING = 2;
	static readonly CLOSED = 3;
	static instances: FakeSocket[] = [];
	readyState = FakeSocket.CONNECTING;
	onopen: (() => void) | null = null;
	onmessage: ((event: MessageEvent) => void) | null = null;
	onerror: ((event: Event) => void) | null = null;
	onclose: ((event: CloseEvent) => void) | null = null;
	constructor(readonly url: string) {
		FakeSocket.instances.push(this);
	}
	send(): void {}
	close(): void {
		this.readyState = FakeSocket.CLOSED;
	}
	open(): void {
		this.readyState = FakeSocket.OPEN;
		this.onopen?.();
	}
	drop(code = 1006): void {
		this.readyState = FakeSocket.CLOSED;
		this.onclose?.({ code } as CloseEvent);
	}
}

class TestSocket extends BaseWebSocket {
	refused: unknown[] = [];
	constructor() {
		super('/ws/test');
	}
	protected onMessage(_message: BaseWebSocketMessage): void {}
	protected override onAuthRefused(error: unknown): void {
		this.refused.push(error);
	}
}

const flush = () => vi.advanceTimersByTimeAsync(0);

describe('BaseWebSocket tickets', () => {
	beforeEach(() => {
		vi.useFakeTimers();
		vi.stubGlobal('WebSocket', FakeSocket);
		FakeSocket.instances = [];
		tickets.issued = [];
		tickets.failWith = null;
		tickets.gate = null;
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.unstubAllGlobals();
	});

	it('fetches a ticket for the first connect and puts it in the URL', async () => {
		const socket = new TestSocket();

		socket.connect();
		await flush();

		expect(FakeSocket.instances).toHaveLength(1);
		expect(FakeSocket.instances[0].url).toBe('ws://host/ws/test?ticket=ticket-1');
		expect(FakeSocket.instances[0].url).not.toContain('token=');
		socket.disconnect();
	});

	it('fetches a new ticket for every reconnect, never reusing a spent one', async () => {
		const socket = new TestSocket();
		socket.connect();
		await flush();

		for (let drop = 1; drop <= 3; drop++) {
			FakeSocket.instances.at(-1)!.drop();
			await vi.advanceTimersByTimeAsync(40000);
			expect(FakeSocket.instances).toHaveLength(drop + 1);
		}

		expect(tickets.issued).toEqual(['ticket-1', 'ticket-2', 'ticket-3', 'ticket-4']);
		expect(FakeSocket.instances.map((instance) => instance.url.split('ticket=')[1])).toEqual(tickets.issued);
		socket.disconnect();
	});

	it('asks for one ticket however many times connect() is called while it waits', async () => {
		let release!: () => void;
		tickets.gate = new Promise<void>((resolve) => (release = resolve));
		const socket = new TestSocket();

		socket.connect();
		socket.connect();
		socket.connect();
		release();
		await flush();

		expect(tickets.issued).toHaveLength(1);
		expect(FakeSocket.instances).toHaveLength(1);
		socket.disconnect();
	});

	it('opens nothing when disconnect() arrives before the ticket does', async () => {
		let release!: () => void;
		tickets.gate = new Promise<void>((resolve) => (release = resolve));
		const socket = new TestSocket();

		socket.connect();
		socket.disconnect();
		release();
		await flush();

		expect(FakeSocket.instances).toHaveLength(0);
	});

	it('can connect again after a disconnect that landed during the ticket fetch', async () => {
		let release!: () => void;
		tickets.gate = new Promise<void>((resolve) => (release = resolve));
		const socket = new TestSocket();
		socket.connect();
		socket.disconnect();
		socket.connect();
		release();
		await flush();

		expect(FakeSocket.instances).toHaveLength(1);
		socket.disconnect();
	});

	it('keeps the reconnect backoff when the ticket fetch itself fails', async () => {
		tickets.failWith = new Error('offline');
		const socket = new TestSocket();

		socket.connect();
		await flush();
		const { ticketedWsUrl } = await import('./wsTicket');
		const calls = () => vi.mocked(ticketedWsUrl).mock.calls.length;
		const first = calls();
		await vi.advanceTimersByTimeAsync(999);
		expect(calls()).toBe(first);
		await vi.advanceTimersByTimeAsync(1);
		expect(calls()).toBe(first + 1);
		await vi.advanceTimersByTimeAsync(1999);
		expect(calls()).toBe(first + 1);
		await vi.advanceTimersByTimeAsync(1);
		expect(calls()).toBe(first + 2);
		expect(FakeSocket.instances).toHaveLength(0);
		socket.disconnect();
	});

	it('does not retry when the ticket route refuses the credential', async () => {
		tickets.failWith = { response: { status: 403 } };
		const socket = new TestSocket();

		socket.connect();
		await vi.advanceTimersByTimeAsync(120000);

		expect(socket.refused).toHaveLength(1);
		expect(FakeSocket.instances).toHaveLength(0);
	});
});
