import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { AxiosInstance } from 'axios';
import { get } from 'svelte/store';
import { createChatApi } from './chat';
import { sendMessage, reattachToTurn } from '$lib/chat/turnController';
import { chatSession } from '$lib/stores/chatSession';
import type { UnifiedChatMessageData } from '$lib/types/chat';

/**
 * Exercises the real SSE parsing/termination boundary between `chat.ts`'s
 * `createChatApi` (real `readSseStream` reading a real fetch `Response`
 * body) and `turnController.ts`'s real `sendMessage`/`reattachToTurn`
 * against the real `chatSession` store — deliberately NOT the fake
 * `ChatApiLike` stubs used in turnController.test.ts, which can't reproduce
 * a bug that lives in how `chat.ts` decides a stream ended.
 */

function sseFrame(type: string, data: unknown): string {
	return `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** A fetch Response whose body is a real ReadableStream emitting `frames`
 * and then closing cleanly — the "proxy closed the connection" shape, not a
 * network failure (which would reject the fetch promise instead). */
function sseResponse(frames: string[]): Response {
	const encoder = new TextEncoder();
	const stream = new ReadableStream<Uint8Array>({
		start(controller) {
			for (const frame of frames) controller.enqueue(encoder.encode(frame));
			controller.close();
		}
	});
	return new Response(stream, { status: 200 });
}

function userMsg(content: string): UnifiedChatMessageData {
	return { role: 'user', content, timestamp: Date.now() };
}

function basicSendParams(instruction: string) {
	return {
		instruction,
		tempUserMessage: userMsg(instruction),
		buildPayload: () => ({ content: instruction, contextMetadata: {} }),
		createSessionPayload: { mode: 'generation' }
	};
}

/** Polls on real macrotask ticks and throws instead of hanging if a fixture's
 * assumed code path is never reached. */
async function waitFor(predicate: () => boolean, timeoutMs = 1000): Promise<void> {
	const start = Date.now();
	while (!predicate()) {
		if (Date.now() - start > timeoutMs) throw new Error(`waitFor: condition not met within ${timeoutMs}ms`);
		await new Promise((resolve) => setTimeout(resolve, 0));
	}
}

beforeEach(() => {
	chatSession.reset();
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('a stream that closes without a terminal event is treated as incomplete, not success', () => {
	it('a direct send whose POST stream closes without a terminal event reattaches, then settles as retryable (no durable match)', async () => {
		chatSession.patch({ sessionId: 'A' });
		const client = {
			get: vi.fn(async () => ({
				data: {
					success: true,
					data: { messages: [{ id: 'u1', session_id: 'A', role: 'user', content: 'hello', created_at: null }] }
				}
			}))
		} as unknown as AxiosInstance;
		const api = createChatApi(client, () => null, () => 'http://test');

		const fetchMock = vi.fn();
		// POST: message_created + a token arrive, then the body closes — no
		// done/error/no_active_turn ever seen.
		fetchMock.mockResolvedValueOnce(
			sseResponse([
				sseFrame('message_created', { user_message_id: 'u1' }),
				sseFrame('token', { content: 'partial answer' })
			])
		);
		// The reattach GET: closes immediately, zero events, no terminal either.
		fetchMock.mockResolvedValueOnce(sseResponse([]));
		vi.stubGlobal('fetch', fetchMock);

		await sendMessage({ store: chatSession, api }, basicSendParams('hello'));

		expect(fetchMock).toHaveBeenCalledTimes(2); // one POST, one reattach attempt
		expect(client.get).toHaveBeenCalledTimes(1); // one durable-recovery GET

		const state = get(chatSession);
		const last = state.messages[state.messages.length - 1];
		expect(last.content).toBe('partial answer'); // retained, never deleted
		expect(last.isStreaming).toBe(false);
		expect(last.isPartial).toBe(true);
		expect(state.error.toLowerCase()).toContain('incomplete');
	});

	it('a rejected POST connection followed by a reattach stream that ALSO closes without a terminal event settles the same way', async () => {
		chatSession.patch({ sessionId: 'A' });
		const client = {
			get: vi.fn(async () => ({ data: { success: true, data: { messages: [] } } }))
		} as unknown as AxiosInstance;
		const api = createChatApi(client, () => null, () => 'http://test');

		const fetchMock = vi.fn();
		fetchMock.mockRejectedValueOnce(new Error('network down')); // the POST connection itself fails
		fetchMock.mockResolvedValueOnce(sseResponse([])); // the reattach GET closes without a terminal event
		vi.stubGlobal('fetch', fetchMock);

		await sendMessage({ store: chatSession, api }, basicSendParams('hello'));

		expect(fetchMock).toHaveBeenCalledTimes(2);
		expect(client.get).toHaveBeenCalledTimes(1);

		const state = get(chatSession);
		const last = state.messages[state.messages.length - 1];
		expect(last.isStreaming).toBe(false);
		expect(last.isPartial).toBe(true);
		expect(state.error.toLowerCase()).toContain('incomplete');
	});

	it('control: a normal "done" terminal still resolves as a success — no reattach, no recovery GET', async () => {
		chatSession.patch({ sessionId: 'A' });
		const client = { get: vi.fn() } as unknown as AxiosInstance;
		const api = createChatApi(client, () => null, () => 'http://test');

		const fetchMock = vi.fn();
		fetchMock.mockResolvedValueOnce(
			sseResponse([
				sseFrame('message_created', { user_message_id: 'u1' }),
				sseFrame('token', { content: 'the full answer' }),
				sseFrame('done', {
					assistant_message: {
						id: 'a1',
						session_id: 'A',
						role: 'assistant',
						content: 'the full answer',
						created_at: null
					}
				})
			])
		);
		vi.stubGlobal('fetch', fetchMock);

		await sendMessage({ store: chatSession, api }, basicSendParams('hello'));

		expect(fetchMock).toHaveBeenCalledTimes(1); // the stream completed normally — no reattach
		expect(client.get).not.toHaveBeenCalled();

		const state = get(chatSession);
		const last = state.messages[state.messages.length - 1];
		expect(last.isStreaming).toBe(false);
		expect(last.id).toBe('a1');
	});

	it('control: a normal "error" terminal resolves without triggering the incomplete-stream recovery path', async () => {
		chatSession.patch({ sessionId: 'A' });
		const client = { get: vi.fn() } as unknown as AxiosInstance;
		const api = createChatApi(client, () => null, () => 'http://test');

		const fetchMock = vi.fn();
		fetchMock.mockResolvedValueOnce(
			sseResponse([sseFrame('message_created', { user_message_id: 'u1' }), sseFrame('error', { message: 'boom' })])
		);
		vi.stubGlobal('fetch', fetchMock);

		await sendMessage({ store: chatSession, api }, basicSendParams('hello'));

		expect(fetchMock).toHaveBeenCalledTimes(1); // "error" is a real terminal event — no reattach
		expect(client.get).not.toHaveBeenCalled();
		expect(get(chatSession).error).toBe('boom');
	});

	it('control: a normal "no_active_turn" terminal on reattach resolves via the existing recovery path, not the incomplete-stream one', async () => {
		chatSession.patch({ sessionId: 'A', messages: [userMsg('question')] });
		const client = {
			get: vi.fn(async () => ({
				data: { success: true, data: { messages: [{ id: 'a1', session_id: 'A', role: 'assistant', content: 'recovered', created_at: null }] } }
			}))
		} as unknown as AxiosInstance;
		const api = createChatApi(client, () => null, () => 'http://test');

		const fetchMock = vi.fn();
		fetchMock.mockResolvedValueOnce(sseResponse([sseFrame('no_active_turn', {})]));
		vi.stubGlobal('fetch', fetchMock);

		await reattachToTurn({ store: chatSession, api }, { sessionId: 'A', initialUserMessageId: 'u1' });

		expect(fetchMock).toHaveBeenCalledTimes(1); // "no_active_turn" is a real terminal event — no second reattach
		expect(client.get).toHaveBeenCalledTimes(1); // recovery still runs, via the pre-existing needsDurableRecovery path

		const state = get(chatSession);
		expect(state.isGenerating).toBe(false);
	});

	it('drops a stale recovery result when a newer turn started before the durable GET resolved', async () => {
		chatSession.patch({ sessionId: 'A' });
		let resolveGet: ((v: any) => void) | undefined;
		const client = {
			get: vi.fn(() => new Promise((resolve) => (resolveGet = resolve)))
		} as unknown as AxiosInstance;
		const api = createChatApi(client, () => null, () => 'http://test');

		const fetchMock = vi.fn();
		fetchMock.mockResolvedValueOnce(sseResponse([])); // POST closes without a terminal event
		fetchMock.mockResolvedValueOnce(sseResponse([])); // reattach GET closes without a terminal event too
		vi.stubGlobal('fetch', fetchMock);

		const sendPromise = sendMessage({ store: chatSession, api }, basicSendParams('hello'));

		await waitFor(() => !!resolveGet);
		chatSession.beginTurn();
		chatSession.patch({ isGenerating: true, error: '' });
		chatSession.addMessage({ role: 'assistant', content: '', timestamp: Date.now(), isStreaming: true });

		resolveGet!({
			data: {
				success: true,
				data: {
					messages: [
						{ id: 'u1', session_id: 'A', role: 'user', content: 'hello', created_at: null },
						{ id: 'late', session_id: 'A', role: 'assistant', content: 'should never appear', created_at: null }
					]
				}
			}
		});
		await sendPromise;

		const state = get(chatSession);
		expect(state.messages.some((m) => m.id === 'late')).toBe(false);
	});
});
