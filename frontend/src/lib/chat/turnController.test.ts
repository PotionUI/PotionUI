import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { chatSession } from '$lib/stores/chatSession';
import {
	sendMessage,
	startNewSession,
	reattachToTurn,
	isRequestNotStartedError,
	type ChatApiLike,
	type TurnControllerDeps,
	type SendMessagePayload,
	type RequestNotStartedError
} from './turnController';
import type { UnifiedChatMessageData } from '$lib/types/chat';

/**
 * Tests the REAL turnController functions the component calls — not a
 * hand-written mirror. Each fixture supplies a fake `ChatApiLike` and drives
 * the actual `chatSession` store through `sendMessage`/`startNewSession`/
 * `reattachToTurn`, asserting on the store afterward.
 */

/** Fills in every `ChatApiLike` method with a default fake that a fixture
 * isn't expected to reach, so a test only has to specify the methods it
 * actually exercises — never a partial object cast to the full interface. */
function makeFakeApi(overrides: Partial<ChatApiLike> = {}): ChatApiLike {
	return {
		createChatSession: async () => ({ success: false, error: 'not used by this fixture' }),
		sendChatMessageStream: async () => {},
		sendChatMessage: async () => ({ success: false, error: 'not used by this fixture' }),
		reattachChatMessageStream: async () => {},
		getChatSession: async () => ({ success: false }),
		...overrides
	};
}

function deps(overrides: Partial<ChatApiLike> = {}): TurnControllerDeps {
	return { store: chatSession, api: makeFakeApi(overrides) };
}

function userMsg(content: string): UnifiedChatMessageData {
	return { role: 'user', content, timestamp: Date.now() };
}

function basicSendParams(instruction: string, payload?: Partial<SendMessagePayload>) {
	return {
		instruction,
		tempUserMessage: userMsg(instruction),
		buildPayload: () => ({ content: instruction, contextMetadata: {}, ...payload }),
		createSessionPayload: { mode: 'generation' }
	};
}

/** Polls `predicate` on real macrotask ticks (never a tight microtask spin,
 * which would hang forever if `predicate` never becomes true because the
 * code under test took a different path than the fixture assumed) and
 * throws instead of hanging once `timeoutMs` elapses. */
async function waitFor(predicate: () => boolean, timeoutMs = 1000): Promise<void> {
	const start = Date.now();
	while (!predicate()) {
		if (Date.now() - start > timeoutMs) {
			throw new Error(`waitFor: condition not met within ${timeoutMs}ms`);
		}
		await new Promise((resolve) => setTimeout(resolve, 0));
	}
}

beforeEach(() => {
	chatSession.reset();
});

describe('sendMessage: control (unchanged turn, real functions)', () => {
	it('behaves normally end to end: session already selected, stream completes', async () => {
		chatSession.patch({ sessionId: 'A' });
		await sendMessage(
			deps({
				sendChatMessageStream: async (_sid, _payload, onEvent) => {
					await onEvent!({ type: 'token', data: { content: 'Hello' } });
					await onEvent!({ type: 'done', data: { assistant_message: { id: 'a1', content: 'Hello' } } });
				}
			}),
			basicSendParams('hi')
		);

		const state = get(chatSession);
		expect(state.isGenerating).toBe(false);
		expect(state.messages).toHaveLength(2);
		expect(state.messages[1]).toMatchObject({ id: 'a1', content: 'Hello', isStreaming: false });
	});

	it('creates a session and uses it normally when nothing switches', async () => {
		await sendMessage(
			deps({
				createChatSession: async () => ({
					success: true,
					data: { id: 'NEW', user_id: 'u', mode: 'generation', title_generated: false, name: null, status: 'active', llm_config_id: null, original_text: null, created_at: null, updated_at: null, closed_at: null, message_count: 0 }
				}),
				sendChatMessageStream: async (_sid, _payload, onEvent) => {
					await onEvent!({ type: 'done', data: { assistant_message: { id: 'a1', content: 'hi there' } } });
				}
			}),
			basicSendParams('first message ever')
		);

		const state = get(chatSession);
		expect(state.sessionId).toBe('NEW');
		expect(state.messages).toHaveLength(2);
		expect(state.messages[1].content).toBe('hi there');
	});
});

describe('sendMessage: continuation after a resolved action', () => {
	it('streams without adding a user message and sends continue_from', async () => {
		chatSession.patch({ sessionId: 'A' });
		chatSession.addMessage({ id: 'a0', role: 'assistant', content: 'proposal', timestamp: 1 });
		const payloads: unknown[] = [];
		await sendMessage(
			deps({
				sendChatMessageStream: async (_sid, payload, onEvent) => {
					payloads.push(payload);
					await onEvent!({ type: 'token', data: { content: 'Done.' } });
					await onEvent!({ type: 'done', data: { assistant_message: { id: 'a1', content: 'Done.' } } });
				}
			}),
			{
				instruction: '',
				recoveryAnchorMessageId: 'a0',
				buildPayload: () => ({
					content: '',
					contextMetadata: {},
					continueFrom: { message_id: 'a0', tool_index: 0 }
				}),
				createSessionPayload: { mode: 'generation' }
			}
		);

		const state = get(chatSession);
		expect(payloads[0]).toMatchObject({ continueFrom: { message_id: 'a0', tool_index: 0 } });
		expect(state.messages.map((m) => [m.id, m.role])).toEqual([
			['a0', 'assistant'],
			['a1', 'assistant']
		]);
		expect(state.isGenerating).toBe(false);
	});

	it('recovers the continuation reply anchored on the resolved message', async () => {
		chatSession.patch({ sessionId: 'A' });
		chatSession.addMessage({ id: 'a0', role: 'assistant', content: 'proposal', timestamp: 1 });
		let fallbackCalls = 0;
		await sendMessage(
			deps({
				sendChatMessageStream: async () => {
					throw Object.assign(new Error('never sent'), { notStarted: true });
				},
				sendChatMessage: async () => {
					fallbackCalls += 1;
					return { success: false, error: 'unused' };
				},
				reattachChatMessageStream: async (_sid, onEvent) => {
					await onEvent!({ type: 'no_active_turn', data: {} });
				},
				getChatSession: async () => ({
					success: true,
					data: {
						messages: [
							{ id: 'a0', role: 'assistant', content: 'proposal' },
							{ id: 'a1', role: 'assistant', content: 'recovered reply' }
						]
					} as never
				})
			}),
			{
				instruction: '',
				recoveryAnchorMessageId: 'a0',
				buildPayload: () => ({ content: '', contextMetadata: {}, continueFrom: { message_id: 'a0', tool_index: 0 } }),
				createSessionPayload: { mode: 'generation' }
			}
		);

		const state = get(chatSession);
		expect(fallbackCalls).toBe(0);
		expect(state.messages.at(-1)).toMatchObject({ id: 'a1', content: 'recovered reply' });
	});
});

describe('sendMessage: continuation refused by the server', () => {
	function continuationParams(onRejected: (r: { message: string; code?: string }) => void) {
		return {
			instruction: '',
			recoveryAnchorMessageId: 'a0',
			onRejected,
			buildPayload: () => ({ content: '', contextMetadata: {}, continueFrom: { message_id: 'a0', tool_index: 0 } }),
			createSessionPayload: { mode: 'generation' }
		};
	}

	it('hands a 409 to onRejected without reattaching or raising the session banner', async () => {
		chatSession.patch({ sessionId: 'A' });
		chatSession.addMessage({ id: 'a0', role: 'assistant', content: 'proposal', timestamp: 1 });
		const rejections: unknown[] = [];
		let reattached = 0;
		await sendMessage(
			deps({
				sendChatMessageStream: async () => {
					throw Object.assign(new Error('Message has already been continued'), {
						httpStatus: 409,
						code: 'continuation_conflict'
					});
				},
				reattachChatMessageStream: async () => {
					reattached += 1;
				}
			}),
			continuationParams((r) => rejections.push(r))
		);

		const state = get(chatSession);
		expect(rejections).toEqual([{ message: 'Message has already been continued', code: 'continuation_conflict' }]);
		expect(reattached).toBe(0);
		expect(state.error).toBe('');
		expect(state.isGenerating).toBe(false);
		expect(state.messages.map((m) => m.id)).toEqual(['a0']);
	});

	it('routes a stream error event to onRejected instead of the banner', async () => {
		chatSession.patch({ sessionId: 'A' });
		chatSession.addMessage({ id: 'a0', role: 'assistant', content: 'proposal', timestamp: 1 });
		const rejections: unknown[] = [];
		await sendMessage(
			deps({
				sendChatMessageStream: async (_sid, _payload, onEvent) => {
					await onEvent!({ type: 'error', data: { error: 'tool_execution_pending', message: 'still pending' } });
				}
			}),
			continuationParams((r) => rejections.push(r))
		);

		const state = get(chatSession);
		expect(rejections).toEqual([{ message: 'still pending', code: 'tool_execution_pending' }]);
		expect(state.error).toBe('');
		expect(state.messages.map((m) => m.id)).toEqual(['a0']);
	});
});

describe('item 1: startNewSession ownership', () => {
	it('publishes success and adopts the created session when nothing switched', async () => {
		const turnSeq = chatSession.beginTurn();
		const result = await startNewSession(
			deps({
				createChatSession: async () => ({
					success: true,
					data: { id: 'NEW', user_id: 'u', mode: 'generation', title_generated: false, name: null, status: 'active', llm_config_id: null, original_text: null, created_at: null, updated_at: null, closed_at: null, message_count: 0 }
				})
			}),
			{ sessionId: null, turnSeq },
			{ mode: 'generation' }
		);
		expect(result.status).toBe('created');
		expect(get(chatSession).sessionId).toBe('NEW');
	});

	it('discards a SUCCESS created while a newer turn had already started (turnSeq changed)', async () => {
		const turnSeq = chatSession.beginTurn();
		let resolveCreate: ((r: any) => void) | undefined;
		const resultPromise = startNewSession(
			deps({ createChatSession: () => new Promise((resolve) => (resolveCreate = resolve)) }),
			{ sessionId: null, turnSeq },
			{ mode: 'generation' }
		);
		// A newer turn begins before creation resolves.
		chatSession.beginTurn();
		resolveCreate!({ success: true, data: { id: 'ORPHANED', user_id: 'u', mode: 'generation', title_generated: false, name: null, status: 'active', llm_config_id: null, original_text: null, created_at: null, updated_at: null, closed_at: null, message_count: 0 } });

		const result = await resultPromise;
		expect(result.status).toBe('stale');
		// The store was never touched — no sessionId, no orphaned adoption.
		expect(get(chatSession).sessionId).toBeNull();
	});

	it('discards a SUCCESS created while the user selected a different existing session (turnSeq unchanged)', async () => {
		const turnSeq = chatSession.beginTurn();
		let resolveCreate: ((r: any) => void) | undefined;
		const resultPromise = startNewSession(
			deps({ createChatSession: () => new Promise((resolve) => (resolveCreate = resolve)) }),
			{ sessionId: null, turnSeq },
			{ mode: 'generation' }
		);
		// The user browses to an existing session C — no new turn, just a
		// different sessionId now showing.
		chatSession.patch({ sessionId: 'C', messages: [userMsg('C question')] });
		resolveCreate!({ success: true, data: { id: 'ORPHANED', user_id: 'u', mode: 'generation', title_generated: false, name: null, status: 'active', llm_config_id: null, original_text: null, created_at: null, updated_at: null, closed_at: null, message_count: 0 } });

		const result = await resultPromise;
		expect(result.status).toBe('stale');
		const state = get(chatSession);
		expect(state.sessionId).toBe('C');
		expect(state.messages).toHaveLength(1);
		expect(state.messages[0].content).toBe('C question');
	});

	it('discards a FAILURE while a newer turn started — never resets that turn\'s isGenerating or error', async () => {
		const turnSeq = chatSession.beginTurn();
		let resolveCreate: ((r: any) => void) | undefined;
		const resultPromise = startNewSession(
			deps({ createChatSession: () => new Promise((resolve) => (resolveCreate = resolve)) }),
			{ sessionId: null, turnSeq },
			{ mode: 'generation' }
		);
		chatSession.beginTurn();
		chatSession.patch({ isGenerating: true, error: '' });
		resolveCreate!({ success: false, error: 'boom' });

		const result = await resultPromise;
		expect(result.status).toBe('stale');
		const state = get(chatSession);
		expect(state.isGenerating).toBe(true);
		expect(state.error).toBe('');
	});

	it('publishes a genuine failure (still current) as an error and clears isGenerating', async () => {
		const turnSeq = chatSession.beginTurn();
		const result = await startNewSession(
			deps({ createChatSession: async () => ({ success: false, error: 'nope' }) }),
			{ sessionId: null, turnSeq },
			{ mode: 'generation' }
		);
		expect(result.status).toBe('failed');
		const state = get(chatSession);
		expect(state.isGenerating).toBe(false);
		expect(state.error).toBe('Failed to create chat session');
	});

	it('end to end through sendMessage: a delayed creation SUCCESS never overwrites a session the user switched to', async () => {
		let resolveCreate: ((r: any) => void) | undefined;
		const sendPromise = sendMessage(
			deps({ createChatSession: () => new Promise((resolve) => (resolveCreate = resolve)) }),
			basicSendParams('first message ever')
		);
		chatSession.patch({ sessionId: 'C', messages: [userMsg('C question')] });
		resolveCreate!({ success: true, data: { id: 'NEW', user_id: 'u', mode: 'generation', title_generated: false, name: null, status: 'active', llm_config_id: null, original_text: null, created_at: null, updated_at: null, closed_at: null, message_count: 0 } });
		await sendPromise;

		const state = get(chatSession);
		expect(state.sessionId).toBe('C');
		expect(state.messages).toHaveLength(1);
		expect(state.messages[0].content).toBe('C question');
	});
});

describe('item 2: ownership re-checked before every outbound call and after preparatory steps', () => {
	it('issues zero outbound requests when a switch happens during "context preparation" (buildPayload)', async () => {
		chatSession.patch({ sessionId: 'A' });
		let streamCalls = 0;
		const sendPromise = sendMessage(
			deps({ sendChatMessageStream: async () => { streamCalls += 1; } }),
			{
				instruction: 'hi',
				tempUserMessage: userMsg('hi'),
				buildPayload: () => {
					// Simulate the switch happening right after the optimistic
					// user message landed but before the stream call.
					chatSession.beginTurn();
					chatSession.patch({ sessionId: 'B', messages: [] });
					return { content: 'hi', contextMetadata: {} };
				},
				createSessionPayload: { mode: 'generation' }
			}
		);
		await sendPromise;
		expect(streamCalls).toBe(0);
		expect(get(chatSession).sessionId).toBe('B');
		expect(get(chatSession).messages).toHaveLength(0);
	});

	it('issues zero outbound requests when a switch happens in the gap between the placeholder tick and the stream call', async () => {
		// Distinct from the buildPayload fixture above: this switch happens
		// AFTER the placeholder was already added (guarded, so it correctly
		// lands on A) and its own `await tick()` — the one remaining
		// unguarded gap before the stream call, if the pre-call check were
		// ever removed.
		chatSession.patch({ sessionId: 'A' });
		let tickCalls = 0;
		let streamCalls = 0;
		await sendMessage(
			{
				store: chatSession,
				api: makeFakeApi({ sendChatMessageStream: async () => { streamCalls += 1; } }),
				tick: async () => {
					tickCalls += 1;
					if (tickCalls === 2) {
						// The user switches to B right after the placeholder's
						// own tick, before this turn ever reaches the stream call.
						chatSession.beginTurn();
						chatSession.patch({ sessionId: 'B', messages: [] });
					}
				}
			},
			basicSendParams('hi')
		);
		expect(streamCalls).toBe(0);
		expect(get(chatSession).sessionId).toBe('B');
		expect(get(chatSession).messages).toHaveLength(0);
	});

	it('never calls the fallback network request when the stream transport rejects after a session switch', async () => {
		chatSession.patch({ sessionId: 'A' });
		let rejectStream: ((err: Error) => void) | undefined;
		let fallbackCalls = 0;
		const sendPromise = sendMessage(
			deps({
				sendChatMessageStream: () => new Promise<void>((_r, reject) => (rejectStream = reject)),
				sendChatMessage: async () => {
					fallbackCalls += 1;
					return { success: true, data: undefined } as any;
				}
			}),
			basicSendParams('hello from A')
		);
		// sendMessage awaits `tick()` twice before the stream call — poll
		// rather than assume it reaches that call synchronously.
		await waitFor(() => !!rejectStream);

		chatSession.beginTurn();
		chatSession.patch({ sessionId: 'B', isGenerating: true, error: '', messages: [] });
		chatSession.addMessage(userMsg('B question'));
		chatSession.addMessage({ role: 'assistant', content: '', timestamp: Date.now(), isStreaming: true });

		rejectStream!(new Error('network down'));
		await sendPromise;

		expect(fallbackCalls).toBe(0);
		const state = get(chatSession);
		expect(state.sessionId).toBe('B');
		expect(state.messages).toHaveLength(2);
		expect(state.messages[0].content).toBe('B question');
		expect(state.messages[1].isStreaming).toBe(true);
	});

	it('drops a late token arriving after the user switched to a different session', async () => {
		chatSession.patch({ sessionId: 'A' });
		let deliverEvent: ((e: { type: string; data: any }) => void) | undefined;
		void sendMessage(
			deps({
				sendChatMessageStream: (_sid, _payload, onEvent) => {
					deliverEvent = onEvent;
					return new Promise<void>(() => {});
				}
			}),
			basicSendParams('hello from A')
		);
		// sendMessage awaits `tick()` twice before the stream call — poll
		// rather than assume it reaches that call synchronously.
		await waitFor(() => !!deliverEvent);

		chatSession.beginTurn();
		chatSession.patch({ sessionId: 'B', isGenerating: true, error: '', messages: [] });
		chatSession.addMessage({ role: 'assistant', content: '', timestamp: Date.now(), isStreaming: true });

		deliverEvent!({ type: 'token', data: { content: 'A late token' } });

		const state = get(chatSession);
		expect(state.sessionId).toBe('B');
		expect(state.messages).toHaveLength(1);
		expect(state.messages[0].content).toBe('');
	});
});

describe('isRequestNotStartedError', () => {
	it('is true only for an object literally carrying notStarted: true', () => {
		expect(isRequestNotStartedError({ notStarted: true })).toBe(true);
		expect(isRequestNotStartedError(new Error('boom'))).toBe(false);
		expect(isRequestNotStartedError({ notStarted: false })).toBe(false);
		expect(isRequestNotStartedError(null)).toBe(false);
		expect(isRequestNotStartedError(undefined)).toBe(false);
		expect(isRequestNotStartedError('a string error')).toBe(false);
		expect(isRequestNotStartedError({ message: 'network dropped' })).toBe(false);
	});
});

describe('item 3: no duplicate invocation — only affirmative not-started evidence gates the fallback', () => {
	it('treats a transport failure with zero delivered events as AMBIGUOUS, not "never started" — reattaches instead of falling back', async () => {
		// The exact case Codex found broken: the server accepts the streaming
		// POST and genuinely starts the turn, but the connection drops before
		// any event is ever delivered to this browser. "No event observed"
		// must never be read as "the invocation never started" — the only
		// safe move is the read-only path (reattach, then durable recovery).
		chatSession.patch({ sessionId: 'A' });
		let serverInvocations = 0;
		let fallbackCalls = 0;
		let reattachCalls = 0;
		await sendMessage(
			deps({
				sendChatMessageStream: async () => {
					serverInvocations += 1;
					throw new Error('connection dropped before any event arrived');
				},
				reattachChatMessageStream: async () => {
					reattachCalls += 1;
					throw new Error('reattach also failed');
				},
				sendChatMessage: async () => {
					fallbackCalls += 1;
					return { success: true, data: undefined } as any;
				},
				getChatSession: async () => ({ success: true, data: { messages: [] } as any })
			}),
			basicSendParams('hello')
		);

		expect(serverInvocations).toBe(1); // the streaming request was issued exactly once
		expect(fallbackCalls).toBe(0); // never re-invoked the model
		expect(reattachCalls).toBe(1); // resumed watching the (possibly still-running) backend turn instead

		const state = get(chatSession);
		const last = state.messages[state.messages.length - 1];
		expect(last.isStreaming).toBe(false);
		expect(last.isPartial).toBe(true);
		expect(state.error.toLowerCase()).toContain('incomplete');
	});

	it('allows exactly one non-streaming fallback ONLY when the transport affirmatively proves the request was never sent', async () => {
		// The one narrow case where an automatic resend is safe — encoded as
		// a typed marker (RequestNotStartedError) rather than inferred from
		// the absence of events. No real transport in this app produces this
		// today; this proves the mechanism itself works when something does.
		chatSession.patch({ sessionId: 'A' });
		let fallbackCalls = 0;
		await sendMessage(
			deps({
				sendChatMessageStream: async () => {
					const notStartedError: RequestNotStartedError & Error = Object.assign(
						new Error('DNS resolution failed before the request was ever sent'),
						{ notStarted: true as const }
					);
					throw notStartedError;
				},
				sendChatMessage: async () => {
					fallbackCalls += 1;
					return {
						success: true,
						data: {
							user_message: { id: 'u1', session_id: 's', role: 'user', content: 'hello', created_at: null },
							assistant_message: { id: 'a1', session_id: 's', role: 'assistant', content: 'fallback answer', created_at: null },
							modified_prompt: null
						}
					};
				}
			}),
			basicSendParams('hello')
		);
		expect(fallbackCalls).toBe(1);
		const state = get(chatSession);
		expect(state.messages[state.messages.length - 1].content).toBe('fallback answer');
	});

	it('never falls back once the stream was accepted (message_created + token arrived) — reattaches, then settles as a retryable error', async () => {
		chatSession.patch({ sessionId: 'A' });
		let fallbackCalls = 0;
		let reattachCalls = 0;
		await sendMessage(
			deps({
				sendChatMessageStream: async (_sid, _payload, onEvent) => {
					await onEvent!({ type: 'message_created', data: { user_message_id: 'u1' } });
					await onEvent!({ type: 'token', data: { content: 'partial ' } });
					throw new Error('network dropped');
				},
				reattachChatMessageStream: async () => {
					reattachCalls += 1;
					throw new Error('reattach also failed');
				},
				sendChatMessage: async () => {
					fallbackCalls += 1;
					return { success: true, data: undefined } as any;
				},
				getChatSession: async () => ({
					success: true,
					data: { messages: [{ id: 'u1', session_id: 's', role: 'user', content: 'hello', created_at: null }] } as any
				})
			}),
			basicSendParams('hello')
		);

		expect(fallbackCalls).toBe(0); // never re-ran the model
		expect(reattachCalls).toBe(1); // resumed watching the backend turn instead

		const state = get(chatSession);
		const last = state.messages[state.messages.length - 1];
		// Content is retained (never deleted), settled as stopped-but-visible
		// and explicitly retryable — never silently resent.
		expect(last.content).toBe('partial ');
		expect(last.isStreaming).toBe(false);
		expect(last.isPartial).toBe(true);
		expect(state.error.toLowerCase()).toContain('incomplete');
	});

	it('recovers the durable answer via reattach when the backend turn already finished', async () => {
		chatSession.patch({ sessionId: 'A' });
		let fallbackCalls = 0;
		await sendMessage(
			deps({
				sendChatMessageStream: async (_sid, _payload, onEvent) => {
					await onEvent!({ type: 'message_created', data: { user_message_id: 'u1' } });
					await onEvent!({ type: 'token', data: { content: 'partial ' } });
					throw new Error('network dropped');
				},
				reattachChatMessageStream: async (_sid, onEvent) => {
					// The turn already finished server-side and was evicted.
					await onEvent!({ type: 'no_active_turn', data: {} });
				},
				sendChatMessage: async () => {
					fallbackCalls += 1;
					return { success: true, data: undefined } as any;
				},
				getChatSession: async () => ({
					success: true,
					data: {
						messages: [
							{ id: 'u1', session_id: 's', role: 'user', content: 'hello', created_at: null },
							{ id: 'a1', session_id: 's', role: 'assistant', content: 'the real complete answer', created_at: null }
						]
					} as any
				})
			}),
			basicSendParams('hello')
		);

		expect(fallbackCalls).toBe(0);
		const state = get(chatSession);
		const last = state.messages[state.messages.length - 1];
		expect(last.id).toBe('a1');
		expect(last.content).toBe('the real complete answer');
		expect(last.isStreaming).toBe(false);
	});
});

describe('existing controls: late read-only recovery after a newer turn in the same session', () => {
	// A generic (unmarked) stream failure never triggers the fallback — see
	// `item 3` above — so these two fixtures instead drive the ambiguous
	// path's own delayed step (reattach fails -> recoverDurableMessage's GET)
	// and prove a LATE result from that GET can't corrupt whatever a newer
	// turn already put in the store, whether the GET eventually finds a
	// match or not.
	it('drops a stale recovery MATCH arriving after a newer turn started in the same session', async () => {
		chatSession.patch({ sessionId: 'A' });
		let streamInvocations = 0;
		let fallbackCalls = 0;
		let reattachCalls = 0;
		let rejectStream: ((err: Error) => void) | undefined;
		let rejectReattach: ((err: Error) => void) | undefined;
		let resolveGetSession: ((v: any) => void) | undefined;
		const sendPromise = sendMessage(
			deps({
				// message_created arrives (so recoverDurableMessage later has a
				// user-message id to match against) before the connection dies —
				// the exact "accepted, then dropped" shape this whole rework is
				// about.
				sendChatMessageStream: (_sid, _payload, onEvent) => {
					streamInvocations += 1;
					return (async () => {
						await onEvent!({ type: 'message_created', data: { user_message_id: 'u1' } });
						return new Promise<void>((_r, reject) => (rejectStream = reject));
					})();
				},
				reattachChatMessageStream: () => {
					reattachCalls += 1;
					return new Promise<void>((_r, reject) => (rejectReattach = reject));
				},
				sendChatMessage: async () => {
					fallbackCalls += 1;
					return { success: false, error: 'must never be called' };
				},
				getChatSession: () => new Promise((resolve) => (resolveGetSession = resolve))
			}),
			basicSendParams('hello')
		);
		await waitFor(() => !!rejectStream);
		rejectStream!(new Error('connection dropped'));

		await waitFor(() => !!rejectReattach);
		rejectReattach!(new Error('reattach also failed'));

		// The recovery GET is now in flight for the retired turn. A newer
		// turn starts in the same session before it resolves.
		await waitFor(() => !!resolveGetSession);
		chatSession.beginTurn();
		chatSession.patch({ isGenerating: true, error: '' });
		chatSession.addMessage({ role: 'assistant', content: '', timestamp: Date.now(), isStreaming: true });

		resolveGetSession!({
			success: true,
			data: {
				messages: [
					{ id: 'u1', session_id: 's', role: 'user', content: 'hello', created_at: null },
					{ id: 'late-recovery', session_id: 's', role: 'assistant', content: 'should never appear', created_at: null }
				]
			}
		});
		await sendPromise;

		expect(streamInvocations).toBe(1); // the model was invoked exactly once
		expect(fallbackCalls).toBe(0); // never re-invoked
		expect(reattachCalls).toBe(1);

		const state = get(chatSession);
		// The read-only path never deletes a retired turn's placeholder (only
		// the fallback branch's pre-resend cleanup does that, and this path
		// never reaches it) — so both the orphaned first placeholder and the
		// newer turn's placeholder are present, and neither was overwritten
		// by the late, dropped recovery result.
		expect(state.messages).toHaveLength(3);
		expect(state.messages[0].content).toBe('hello');
		expect(state.messages[1].isStreaming).toBe(true);
		expect(state.messages[1].content).toBe('');
		expect(state.messages[2].isStreaming).toBe(true);
		expect(state.messages[2].content).toBe('');
		expect(state.messages.some((m) => m.id === 'late-recovery')).toBe(false);
	});

	it('drops a stale retryable-settle (no durable match found) arriving after a newer turn started in the same session', async () => {
		chatSession.patch({ sessionId: 'A' });
		let streamInvocations = 0;
		let fallbackCalls = 0;
		let rejectStream: ((err: Error) => void) | undefined;
		let rejectReattach: ((err: Error) => void) | undefined;
		let resolveGetSession: ((v: any) => void) | undefined;
		const sendPromise = sendMessage(
			deps({
				sendChatMessageStream: () => {
					streamInvocations += 1;
					return new Promise<void>((_r, reject) => (rejectStream = reject));
				},
				reattachChatMessageStream: () => new Promise<void>((_r, reject) => (rejectReattach = reject)),
				sendChatMessage: async () => {
					fallbackCalls += 1;
					return { success: false, error: 'must never be called' };
				},
				getChatSession: () => new Promise((resolve) => (resolveGetSession = resolve))
			}),
			basicSendParams('hello')
		);
		await waitFor(() => !!rejectStream);
		rejectStream!(new Error('connection dropped'));

		await waitFor(() => !!rejectReattach);
		rejectReattach!(new Error('reattach also failed'));

		await waitFor(() => !!resolveGetSession);
		chatSession.beginTurn();
		chatSession.patch({ isGenerating: true, error: '' });

		// No durable message for this (retired) turn — would normally settle
		// it as retryable and set an error, but the newer turn now owns the
		// store.
		resolveGetSession!({ success: true, data: { messages: [] } });
		await sendPromise;

		expect(streamInvocations).toBe(1);
		expect(fallbackCalls).toBe(0);
		expect(get(chatSession).error).toBe(''); // not overwritten by the retired turn's settle
	});
});

describe('reattachToTurn: real function against the real store', () => {
	it('settles the placeholder as a retryable error when no_active_turn finds nothing durable', async () => {
		chatSession.patch({ sessionId: 'A', messages: [userMsg('question')] });
		await reattachToTurn(
			deps({
				reattachChatMessageStream: async (_sid, onEvent) => {
					await onEvent!({ type: 'no_active_turn', data: {} });
				},
				getChatSession: async () => ({ success: true, data: { messages: [] } as any })
			}),
			{ sessionId: 'A' }
		);
		const state = get(chatSession);
		// recoverDurableMessage's settleUnrecoverable never deletes the
		// message — it clears isStreaming and flags isPartial instead, so
		// reattachToTurn's own "drop a still-empty streaming placeholder"
		// filter (which only matches isStreaming: true) leaves it in place.
		expect(state.messages).toHaveLength(2);
		const placeholder = state.messages[1];
		expect(placeholder.isStreaming).toBe(false);
		expect(placeholder.isPartial).toBe(true);
		expect(state.error.toLowerCase()).toContain('incomplete');
		expect(state.isGenerating).toBe(false);
	});

	it('drops a genuinely still-empty placeholder when reattaching fails outright before recovery could run', async () => {
		chatSession.patch({ sessionId: 'A', messages: [userMsg('question')] });
		await reattachToTurn(
			deps({
				reattachChatMessageStream: async () => {
					throw new Error('network unreachable');
				}
			}),
			{ sessionId: 'A' }
		);
		const state = get(chatSession);
		// No event ever arrived, so recovery never ran; the placeholder is
		// still isStreaming/empty and reattachToTurn's finally cleanup drops it.
		expect(state.messages).toHaveLength(1);
		expect(state.isGenerating).toBe(false);
	});
});

describe('item 4: streamed token coalescing and scroll-pin (createStreamEventHandler)', () => {
	it('coalesces rapid token events into one deferred store update (last-wins), applied only once a frame elapses', async () => {
		chatSession.patch({ sessionId: 'A' });
		let deliverEvent: ((e: { type: string; data: any }) => void | Promise<void>) | undefined;
		void sendMessage(
			{
				store: chatSession,
				api: makeFakeApi({
					sendChatMessageStream: (_sid, _payload, onEvent) => {
						deliverEvent = onEvent;
						return new Promise<void>(() => {});
					}
				})
			},
			basicSendParams('hi')
		);
		await waitFor(() => !!deliverEvent);

		await deliverEvent!({ type: 'token', data: { content: 'He' } });
		await deliverEvent!({ type: 'token', data: { content: 'y' } });

		// Still queued — not applied synchronously per token.
		expect(get(chatSession).messages[1].content).toBe('');

		// Real macrotask delay past the setTimeout(16) fallback (no
		// requestAnimationFrame in this test environment).
		await new Promise((resolve) => setTimeout(resolve, 30));

		expect(get(chatSession).messages[1].content).toBe('Hey');
	});

	it('coalesces the follow-along scroll to once per batch, not once per token, and only via the pinned variant', async () => {
		chatSession.patch({ sessionId: 'A' });
		let deliverEvent: ((e: { type: string; data: any }) => void | Promise<void>) | undefined;
		let pinnedCalls = 0;
		let jumpCalls = 0;
		void sendMessage(
			{
				store: chatSession,
				api: makeFakeApi({
					sendChatMessageStream: (_sid, _payload, onEvent) => {
						deliverEvent = onEvent;
						return new Promise<void>(() => {});
					}
				}),
				scrollToBottom: () => {
					jumpCalls += 1;
				},
				scrollToBottomIfPinned: () => {
					pinnedCalls += 1;
				}
			},
			basicSendParams('hi')
		);
		await waitFor(() => !!deliverEvent);
		const jumpCallsAfterSend = jumpCalls; // the explicit send-time jumps, unrelated to this assertion

		await deliverEvent!({ type: 'token', data: { content: 'a' } });
		await deliverEvent!({ type: 'token', data: { content: 'ab' } });
		await deliverEvent!({ type: 'token', data: { content: 'abc' } });
		expect(pinnedCalls).toBe(0); // nothing yet — still batched

		await new Promise((resolve) => setTimeout(resolve, 30));

		expect(pinnedCalls).toBe(1); // one scroll for the whole batch of three tokens
		expect(jumpCalls).toBe(jumpCallsAfterSend); // the unconditional jump was never used for stream progress
	});

	it('flushes a pending token before applying a subsequent non-token event, so ordering holds', async () => {
		chatSession.patch({ sessionId: 'A' });
		let deliverEvent: ((e: { type: string; data: any }) => void | Promise<void>) | undefined;
		void sendMessage(
			{
				store: chatSession,
				api: makeFakeApi({
					sendChatMessageStream: (_sid, _payload, onEvent) => {
						deliverEvent = onEvent;
						return new Promise<void>(() => {});
					}
				})
			},
			basicSendParams('hi')
		);
		await waitFor(() => !!deliverEvent);

		await deliverEvent!({ type: 'token', data: { content: 'narration before the tool call' } });
		// No real delay at all — the very next event must still see the
		// token's content already applied, not racing a still-pending frame.
		await deliverEvent!({ type: 'tool_start', data: { tool_name: 'list_models' } });

		const msg = get(chatSession).messages[get(chatSession).messages.length - 1];
		expect(msg.content).toBe('narration before the tool call');
		expect(msg.tool_executions).toHaveLength(1);
	});

	it('nothing is applied after done: a token scheduled just before it never resurrects after the message is finalized', async () => {
		chatSession.patch({ sessionId: 'A' });
		await sendMessage(
			{
				store: chatSession,
				api: makeFakeApi({
					sendChatMessageStream: async (_sid, _payload, onEvent) => {
						await onEvent!({ type: 'token', data: { content: 'partial' } });
						await onEvent!({
							type: 'done',
							data: { assistant_message: { id: 'a1', content: 'final answer' } }
						});
					}
				})
			},
			basicSendParams('hi')
		);

		expect(get(chatSession).messages[1].content).toBe('final answer');

		// Even if a real browser's rAF for the token's push() were still
		// pending, letting real time pass a frame must not resurrect the
		// pre-done content over the finalized message.
		await new Promise((resolve) => setTimeout(resolve, 30));
		expect(get(chatSession).messages[1].content).toBe('final answer');
	});

	it('a pending token is flushed into recovery instead of being silently lost when the connection dies before another event arrives', async () => {
		// Regression: recoverDurableMessage's settleUnrecoverable retains
		// "whatever content is already showing" — that must be the token that
		// really did arrive, not '' because it was still batched up when the
		// connection broke with no further event to trigger a flush.
		chatSession.patch({ sessionId: 'A' });
		await sendMessage(
			deps({
				sendChatMessageStream: async (_sid, _payload, onEvent) => {
					await onEvent!({ type: 'message_created', data: { user_message_id: 'u1' } });
					await onEvent!({ type: 'token', data: { content: 'partial ' } });
					throw new Error('network dropped');
				},
				reattachChatMessageStream: async () => {
					throw new Error('reattach also failed');
				},
				getChatSession: async () => ({
					success: true,
					data: { messages: [{ id: 'u1', session_id: 's', role: 'user', content: 'hello', created_at: null }] } as any
				})
			}),
			basicSendParams('hello')
		);

		const last = get(chatSession).messages[get(chatSession).messages.length - 1];
		expect(last.content).toBe('partial ');
		expect(last.isPartial).toBe(true);
	});

	it('sendMessage: the terminal settle scroll (after done) is pinned-only, not the unconditional jump — only the send-time placeholder adds use scrollToBottom', async () => {
		chatSession.patch({ sessionId: 'A' });
		let jumpCalls = 0;
		let pinnedCalls = 0;
		await sendMessage(
			{
				store: chatSession,
				api: makeFakeApi({
					sendChatMessageStream: async (_sid, _payload, onEvent) => {
						await onEvent!({ type: 'done', data: { assistant_message: { id: 'a1', content: 'answer' } } });
					}
				}),
				scrollToBottom: () => {
					jumpCalls += 1;
				},
				scrollToBottomIfPinned: () => {
					pinnedCalls += 1;
				}
			},
			basicSendParams('hi')
		);

		// The two explicit send-time adds (user message, streaming placeholder).
		expect(jumpCalls).toBe(2);
		// The settle-after-done scroll went through the pinned variant.
		expect(pinnedCalls).toBe(1);
	});

	it('reattachToTurn: the terminal settle scroll is pinned-only — the initial reattach jump still uses the unconditional scroll', async () => {
		chatSession.patch({ sessionId: 'A', messages: [userMsg('question')] });
		let jumpCalls = 0;
		let pinnedCalls = 0;
		await reattachToTurn(
			{
				store: chatSession,
				api: makeFakeApi({
					reattachChatMessageStream: async (_sid, onEvent) => {
						await onEvent!({ type: 'done', data: { assistant_message: { id: 'a1', content: 'answer' } } });
					}
				}),
				scrollToBottom: () => {
					jumpCalls += 1;
				},
				scrollToBottomIfPinned: () => {
					pinnedCalls += 1;
				}
			},
			{ sessionId: 'A' }
		);

		// The one explicit jump right after mounting the reattach placeholder.
		expect(jumpCalls).toBe(1);
		// The settle-after-done scroll went through the pinned variant.
		expect(pinnedCalls).toBe(1);
	});
});

describe('onAssistantFinalized: every live arrival of a final assistant message', () => {
	const toolExecutions = [
		{ tool_name: 'save_shot', arguments: {}, result: { success: true, data: '{"shot":1}' }, duration_ms: 3 }
	];

	function finalizedDeps(overrides: Partial<ChatApiLike>, seen: UnifiedChatMessageData[]): TurnControllerDeps {
		return { ...deps(overrides), onAssistantFinalized: (message) => seen.push(message) };
	}

	it('reports the message a done event finalized, with its tool executions', async () => {
		chatSession.patch({ sessionId: 'A' });
		const seen: UnifiedChatMessageData[] = [];
		await sendMessage(
			finalizedDeps(
				{
					sendChatMessageStream: async (_sid, _payload, onEvent) => {
						await onEvent!({
							type: 'done',
							data: {
								assistant_message: { id: 'a1', role: 'assistant', content: 'Saved.', tool_executions: toolExecutions }
							}
						});
					}
				},
				seen
			),
			basicSendParams('save it')
		);

		expect(seen).toHaveLength(1);
		expect(seen[0]).toMatchObject({ id: 'a1', role: 'assistant', tool_executions: toolExecutions });
	});

	it('reports the recovered message once when a truncated done is replaced by durable recovery', async () => {
		chatSession.patch({ sessionId: 'A' });
		const seen: UnifiedChatMessageData[] = [];
		await sendMessage(
			finalizedDeps(
				{
					sendChatMessageStream: async (_sid, _payload, onEvent) => {
						await onEvent!({ type: 'message_created', data: { user_message_id: 'u1' } });
						await onEvent!({ type: 'done', data: { truncated: true } });
					},
					getChatSession: async () => ({
						success: true,
						data: {
							messages: [
								{ id: 'u1', session_id: 's', role: 'user', content: 'save it', created_at: null },
								{
									id: 'a1',
									session_id: 's',
									role: 'assistant',
									content: 'Saved.',
									created_at: null,
									metadata: { tool_executions: toolExecutions }
								}
							]
						}
					} as any)
				},
				seen
			),
			basicSendParams('save it')
		);

		expect(seen.map((message) => message.id)).toEqual(['a1']);
		expect(seen[0].tool_executions).toEqual(toolExecutions);
	});

	it('reports the same message twice when a done after a gap is followed by recovery', async () => {
		chatSession.patch({ sessionId: 'A' });
		const seen: UnifiedChatMessageData[] = [];
		await sendMessage(
			finalizedDeps(
				{
					sendChatMessageStream: async (_sid, _payload, onEvent) => {
						await onEvent!({ type: 'message_created', data: { user_message_id: 'u1' } });
						await onEvent!({ type: 'overflow', data: {} });
						await onEvent!({
							type: 'done',
							data: { assistant_message: { id: 'a1', content: 'Saved.', tool_executions: toolExecutions } }
						});
					},
					getChatSession: async () => ({
						success: true,
						data: {
							messages: [
								{ id: 'u1', session_id: 's', role: 'user', content: 'save it', created_at: null },
								{
									id: 'a1',
									session_id: 's',
									role: 'assistant',
									content: 'Saved.',
									created_at: null,
									metadata: { tool_executions: toolExecutions }
								}
							]
						}
					} as any)
				},
				seen
			),
			basicSendParams('save it')
		);

		expect(seen.map((message) => message.id)).toEqual(['a1', 'a1']);
	});

	it('reports the answer of the non-streaming fallback', async () => {
		chatSession.patch({ sessionId: 'A' });
		const seen: UnifiedChatMessageData[] = [];
		await sendMessage(
			finalizedDeps(
				{
					sendChatMessageStream: async () => {
						throw Object.assign(new Error('never sent'), { notStarted: true as const });
					},
					sendChatMessage: async () => ({
						success: true,
						data: {
							user_message: { id: 'u1', session_id: 's', role: 'user', content: 'save it', created_at: null },
							assistant_message: {
								id: 'a1',
								session_id: 's',
								role: 'assistant',
								content: 'Saved.',
								created_at: null,
								metadata: { tool_executions: toolExecutions }
							},
							modified_prompt: null
						}
					} as any)
				},
				seen
			),
			basicSendParams('save it')
		);

		expect(seen).toHaveLength(1);
		expect(seen[0]).toMatchObject({ id: 'a1', content: 'Saved.', tool_executions: toolExecutions });
	});

	it('reports nothing for a turn that only streamed an error', async () => {
		chatSession.patch({ sessionId: 'A' });
		const seen: UnifiedChatMessageData[] = [];
		await sendMessage(
			finalizedDeps(
				{
					sendChatMessageStream: async (_sid, _payload, onEvent) => {
						await onEvent!({ type: 'error', data: { message: 'boom' } });
					}
				},
				seen
			),
			basicSendParams('save it')
		);

		expect(seen).toEqual([]);
	});
});

describe('reasoning events (createStreamEventHandler)', () => {
	async function openStream() {
		chatSession.patch({ sessionId: 'A' });
		let deliverEvent: ((e: { type: string; data: any }) => void | Promise<void>) | undefined;
		void sendMessage(
			{
				store: chatSession,
				api: makeFakeApi({
					sendChatMessageStream: (_sid, _payload, onEvent) => {
						deliverEvent = onEvent;
						return new Promise<void>(() => {});
					}
				})
			},
			basicSendParams('hi')
		);
		await waitFor(() => !!deliverEvent);
		return deliverEvent!;
	}

	const assistant = () => {
		const messages = get(chatSession).messages;
		return messages[messages.length - 1];
	};

	const flushFrame = () => new Promise((resolve) => setTimeout(resolve, 30));

	it('accumulates reasoning deltas into one open block, applied once per frame', async () => {
		const deliver = await openStream();

		await deliver({ type: 'reasoning', data: { block: 0, kind: 'raw', text: 'let ' } });
		await deliver({ type: 'reasoning', data: { block: 0, kind: 'raw', text: 'me think' } });
		expect(assistant().reasoning).toBeUndefined();

		await flushFrame();

		expect(assistant().reasoning).toHaveLength(1);
		expect(assistant().reasoning![0]).toMatchObject({ kind: 'raw', text: 'let me think', open: true });
	});

	it('keeps the kind the server reported for a block', async () => {
		const deliver = await openStream();

		await deliver({ type: 'reasoning', data: { block: 0, kind: 'summary', text: 'Checked both tabs.' } });
		await flushFrame();

		expect(assistant().reasoning![0].kind).toBe('summary');
	});

	it('an answer token folds the block, even with no frame in between', async () => {
		const deliver = await openStream();

		await deliver({ type: 'reasoning', data: { block: 0, kind: 'raw', text: 'plan' } });
		await deliver({ type: 'token', data: { content: 'Answer' } });
		await flushFrame();

		expect(assistant().reasoning![0].text).toBe('plan');
		expect(assistant().reasoning![0].open).toBeUndefined();
		expect(assistant().content).toBe('Answer');
	});

	it('flushes pending reasoning before a tool call so the order holds', async () => {
		const deliver = await openStream();

		await deliver({ type: 'reasoning', data: { block: 0, kind: 'raw', text: 'need a tool' } });
		await deliver({ type: 'tool_start', data: { tool_name: 'list_models' } });

		expect(assistant().reasoning![0].text).toBe('need a tool');
		expect(assistant().reasoning![0].open).toBeUndefined();
		expect(assistant().tool_executions).toHaveLength(1);
	});

	it('reasoning_end applies the pending text, then closes the block with the reported duration', async () => {
		const deliver = await openStream();

		await deliver({ type: 'reasoning', data: { block: 0, kind: 'raw', text: 'weighing' } });
		await deliver({ type: 'reasoning_end', data: { block: 0, duration_ms: 1840 } });

		expect(assistant().reasoning).toEqual([
			expect.objectContaining({ kind: 'raw', text: 'weighing', duration_ms: 1840 })
		]);
		expect(assistant().reasoning![0].open).toBeUndefined();
	});

	it('a reasoning_end with no streamed text records an omitted block with its duration', async () => {
		const deliver = await openStream();

		await deliver({ type: 'reasoning_end', data: { block: 0, duration_ms: 3100 } });

		expect(assistant().reasoning).toEqual([{ kind: 'omitted', text: '', duration_ms: 3100 }]);
	});

	it('keeps each model turn of a tool round as its own block when the server numbers them globally', async () => {
		const deliver = await openStream();

		await deliver({ type: 'reasoning', data: { block: 0, kind: 'raw', text: 'first' } });
		await deliver({ type: 'reasoning_end', data: { block: 0, duration_ms: 100 } });
		await deliver({ type: 'step', data: { seq: 1, finish: 'tool_calls' } });
		await deliver({ type: 'reasoning', data: { block: 1, kind: 'raw', text: 'second' } });
		await deliver({ type: 'reasoning', data: { block: 1, kind: 'raw', text: ' look' } });
		await flushFrame();

		expect(assistant().reasoning!.map((block) => block.text)).toEqual(['first', 'second look']);
		expect(assistant().reasoning![1].open).toBe(true);
	});

	it('keeps each model turn of a tool round as its own block when the server restarts numbering per step', async () => {
		const deliver = await openStream();

		await deliver({ type: 'reasoning', data: { block: 0, kind: 'raw', text: 'first' } });
		await deliver({ type: 'reasoning_end', data: { block: 0, duration_ms: 100 } });
		await deliver({ type: 'step', data: { seq: 1, finish: 'tool_calls' } });
		await deliver({ type: 'reasoning', data: { block: 0, kind: 'summary', text: 'second' } });
		await deliver({ type: 'reasoning_end', data: { block: 0, duration_ms: 200 } });
		await flushFrame();

		expect(assistant().reasoning!.map((block) => [block.kind, block.text, block.duration_ms])).toEqual([
			['raw', 'first', 100],
			['summary', 'second', 200]
		]);
	});

	it('a new block index applies the old block text before starting the next', async () => {
		const deliver = await openStream();

		await deliver({ type: 'reasoning', data: { block: 0, kind: 'raw', text: 'first turn' } });
		await deliver({ type: 'reasoning', data: { block: 1, kind: 'raw', text: 'second turn' } });

		expect(assistant().reasoning!.map((block) => block.text)).toEqual(['first turn']);

		await flushFrame();

		expect(assistant().reasoning!.map((block) => block.text)).toEqual(['first turn', 'second turn']);
		expect(assistant().reasoning![0].open).toBeUndefined();
	});

	it('step and notice events land on the streaming message', async () => {
		const deliver = await openStream();

		await deliver({
			type: 'notice',
			data: { kind: 'retrying', message: 'Rate limited; retrying in 4 s.', detail: { retry_after: 4 } }
		});
		await deliver({ type: 'step', data: { seq: 2, finish: 'stop', usage: { input_tokens: 12, output_tokens: 3 } } });

		expect(assistant().notices!.map((notice) => notice.message)).toEqual(['Rate limited; retrying in 4 s.']);
		expect(assistant().usage).toEqual({ input_tokens: 12, output_tokens: 3 });
	});

	it('done without reasoning in its payload keeps what streamed', async () => {
		const deliver = await openStream();

		await deliver({ type: 'reasoning', data: { block: 0, kind: 'raw', text: 'streamed reasoning' } });
		await deliver({ type: 'token', data: { content: 'answer' } });
		await deliver({
			type: 'done',
			data: {
				assistant_message: {
					id: 'a1',
					content: 'answer',
					metadata: { behavior_trace: { steps: [], thinking_visible: true } }
				}
			}
		});

		expect(assistant().reasoning!.map((block) => block.text)).toEqual(['streamed reasoning']);
		expect(assistant().reasoning![0].open).toBeUndefined();
		expect(assistant().isStreaming).toBe(false);
	});

	it('done replaces the live blocks with the persisted ones', async () => {
		const deliver = await openStream();

		await deliver({ type: 'reasoning', data: { block: 0, kind: 'raw', text: 'live text' } });
		await deliver({
			type: 'done',
			data: {
				assistant_message: {
					id: 'a1',
					content: 'answer',
					reasoning: [{ kind: 'summary', text: 'stored text', redacted: false, duration_ms: 7000 }]
				}
			}
		});

		expect(assistant().reasoning).toEqual([{ kind: 'summary', text: 'stored text', duration_ms: 7000 }]);
		expect(assistant().isStreaming).toBe(false);
	});

	it('a truncated reasoning reference does not break the stream', async () => {
		const deliver = await openStream();

		await deliver({ type: 'reasoning', data: { truncated: true, preview: 'xx', content_bytes: 9000 } });
		await deliver({ type: 'token', data: { content: 'ok' } });
		await flushFrame();

		expect(assistant().content).toBe('ok');
	});

	it('step_discarded removes the discarded step text so the retried step continues from the boundary', async () => {
		const deliver = await openStream();

		await deliver({ type: 'token', data: { content: 'First step. ' } });
		await deliver({ type: 'step', data: { seq: 1, finish: 'tool_calls' } });
		await deliver({ type: 'token', data: { content: 'Second step, cut' } });
		await deliver({ type: 'step_discarded', data: { seq: 2 } });
		expect(assistant().content).toBe('First step. ');

		await deliver({ type: 'token', data: { content: 'Retried.' } });
		await flushFrame();

		expect(assistant().content).toBe('First step. Retried.');
	});

	it('step_discarded drops text that was still waiting for the next frame', async () => {
		const deliver = await openStream();

		await deliver({ type: 'token', data: { content: 'never applied' } });
		await deliver({ type: 'step_discarded', data: { seq: 1 } });
		await flushFrame();

		expect(assistant().content).toBe('');
	});

	it('step_discarded removes the reasoning of the discarded step and lets the retry reuse its block numbers', async () => {
		const deliver = await openStream();

		await deliver({ type: 'reasoning', data: { block: 0, kind: 'raw', text: 'first' } });
		await deliver({ type: 'reasoning_end', data: { block: 0, duration_ms: 100 } });
		await deliver({ type: 'step', data: { seq: 1, finish: 'tool_calls' } });
		await deliver({ type: 'reasoning', data: { block: 0, kind: 'raw', text: 'doomed' } });
		await deliver({ type: 'step_discarded', data: { seq: 2 } });
		expect(assistant().reasoning!.map((block) => block.text)).toEqual(['first']);

		await deliver({ type: 'reasoning', data: { block: 0, kind: 'raw', text: 'retry' } });
		await deliver({ type: 'reasoning_end', data: { block: 0, duration_ms: 50 } });
		await flushFrame();

		expect(assistant().reasoning!.map((block) => [block.text, block.duration_ms])).toEqual([
			['first', 100],
			['retry', 50]
		]);
	});

	it('step_discarded keeps block indexes exact when the retry numbers its blocks past the discarded ones', async () => {
		const deliver = await openStream();

		await deliver({ type: 'reasoning', data: { block: 0, kind: 'raw', text: 'first' } });
		await deliver({ type: 'reasoning_end', data: { block: 0, duration_ms: 100 } });
		await deliver({ type: 'step', data: { seq: 1, finish: 'tool_calls' } });
		await deliver({ type: 'reasoning', data: { block: 1, kind: 'raw', text: 'doomed one' } });
		await deliver({ type: 'reasoning', data: { block: 2, kind: 'raw', text: 'doomed two' } });
		await deliver({ type: 'step_discarded', data: { seq: 2 } });

		await deliver({ type: 'reasoning', data: { block: 3, kind: 'raw', text: 'retry one' } });
		await deliver({ type: 'reasoning', data: { block: 4, kind: 'raw', text: 'retry two' } });
		await deliver({ type: 'reasoning_end', data: { block: 3, duration_ms: 30 } });
		await deliver({ type: 'reasoning_end', data: { block: 4, duration_ms: 40 } });
		await flushFrame();

		expect(assistant().reasoning!.map((block) => [block.text, block.duration_ms])).toEqual([
			['first', 100],
			['retry one', 30],
			['retry two', 40]
		]);
	});

	it('step_discarded before any step clears the whole attempt', async () => {
		const deliver = await openStream();

		await deliver({ type: 'reasoning', data: { block: 0, kind: 'raw', text: 'doomed' } });
		await deliver({ type: 'token', data: { content: 'cut' } });
		await deliver({ type: 'step_discarded', data: { seq: 1 } });
		await deliver({ type: 'reasoning', data: { block: 0, kind: 'raw', text: 'fresh' } });
		await flushFrame();

		expect(assistant().content).toBe('');
		expect(assistant().reasoning!.map((block) => block.text)).toEqual(['fresh']);
	});
});
