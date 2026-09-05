import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { chatSession } from '$lib/stores/chatSession';
import {
	sendMessage,
	startNewSession,
	reattachToTurn,
	type ChatApiLike,
	type TurnControllerDeps,
	type SendMessagePayload
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
		while (!rejectStream) await Promise.resolve();

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
		while (!deliverEvent) await Promise.resolve();

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

describe('item 3: no duplicate invocation — accepted evidence gates the fallback', () => {
	it('allows exactly one non-streaming fallback when the transport fails before any event ever arrived', async () => {
		chatSession.patch({ sessionId: 'A' });
		let fallbackCalls = 0;
		await sendMessage(
			deps({
				sendChatMessageStream: async () => {
					throw new Error('never even connected');
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

describe('existing controls: late fallback after a newer turn in the same session', () => {
	it('drops a fallback SUCCESS response arriving after a newer turn started in the same session', async () => {
		chatSession.patch({ sessionId: 'A' });
		let rejectStream: ((err: Error) => void) | undefined;
		let resolveFallback: ((v: any) => void) | undefined;
		const sendPromise = sendMessage(
			deps({
				sendChatMessageStream: () => new Promise<void>((_r, reject) => (rejectStream = reject)),
				sendChatMessage: () => new Promise((resolve) => (resolveFallback = resolve))
			}),
			basicSendParams('hello')
		);
		while (!rejectStream) await Promise.resolve();

		rejectStream!(new Error('boom'));
		while (!resolveFallback) await Promise.resolve();

		chatSession.beginTurn();
		chatSession.patch({ isGenerating: true, error: '' });
		chatSession.addMessage({ role: 'assistant', content: '', timestamp: Date.now(), isStreaming: true });

		resolveFallback!({
			success: true,
			data: {
				user_message: { id: 'u1', session_id: 's', role: 'user', content: 'hello', created_at: null },
				assistant_message: { id: 'late-fallback', session_id: 's', role: 'assistant', content: 'should never appear', created_at: null },
				modified_prompt: null
			}
		});
		await sendPromise;

		const state = get(chatSession);
		expect(state.messages).toHaveLength(2); // A's user message (never touched) + the newer turn's placeholder
		expect(state.messages[0].content).toBe('hello');
		expect(state.messages[1].content).toBe('');
		expect(state.messages.some((m) => m.id === 'late-fallback')).toBe(false);
	});

	it('drops a fallback FAILURE arriving after a newer turn started in the same session', async () => {
		chatSession.patch({ sessionId: 'A' });
		let rejectStream: ((err: Error) => void) | undefined;
		let rejectFallback: ((err: Error) => void) | undefined;
		const sendPromise = sendMessage(
			deps({
				sendChatMessageStream: () => new Promise<void>((_r, reject) => (rejectStream = reject)),
				sendChatMessage: () => new Promise((_resolve, reject) => (rejectFallback = reject))
			}),
			basicSendParams('hello')
		);
		while (!rejectStream) await Promise.resolve();

		rejectStream!(new Error('boom'));
		while (!rejectFallback) await Promise.resolve();

		chatSession.beginTurn();
		chatSession.patch({ isGenerating: true, error: '' });

		rejectFallback!(new Error('also boom'));
		await sendPromise;

		expect(get(chatSession).error).toBe(''); // not overwritten by the retired turn's failure
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
