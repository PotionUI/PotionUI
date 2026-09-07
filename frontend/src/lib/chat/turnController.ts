/**
 * The turn-controller logic for UnifiedAIChat: session creation, sending a
 * message (streaming, the non-streaming fallback, and the ownership rules
 * governing both), reattaching to an in-flight turn, and the SSE event
 * handler shared by both paths.
 *
 * Extracted out of the Svelte component so it can be exercised directly, with
 * the real `chatSession` store and fake transports, rather than only through
 * a hand-written mirror — a mirror can drift from what the component actually
 * does; these are the actual functions the component calls.
 *
 * Every dependency (the store, the API transports, DOM-adjacent effects like
 * scrolling/ticking, and a few UI-only callbacks for state this module has no
 * business owning — recentSessions, selectedImageData, input focus) is taken
 * as an explicit parameter rather than imported, so a test can supply fakes
 * for all of it.
 */
import { get } from 'svelte/store';
import type { UnifiedChatMessageData } from '$lib/types/chat';
import type { APIResponse, ChatSessionResponse, ChatSessionWithMessagesResponse, SendChatMessageResponse } from '$lib/types/api';
import {
	ownSession,
	finishTurnIfCurrent,
	isRecoveryStillCurrent,
	findTurnAssistantMessage,
	applyDurableRecovery,
	settleUnrecoverable,
	needsDurableRecovery,
	mapPersistedMessage,
	type ChatSessionLikeStore,
	type OwnedSessionController
} from '$lib/utils/chatStream';

type Messages = UnifiedChatMessageData[];

/** The transport methods a turn controller needs — the real `api` object
 * (from `$lib/services/api`) satisfies this directly; a test supplies fakes. */
export interface ChatApiLike {
	createChatSession(request: {
		llm_config_id?: string;
		mode?: string;
		tools_enabled?: boolean;
		disabled_tools?: string[];
	}): Promise<APIResponse<ChatSessionResponse>>;
	sendChatMessageStream(
		sessionId: string,
		options: {
			content: string;
			imageData?: string;
			contextMetadata?: Record<string, any>;
			resources?: Array<{ uri: string }>;
		},
		onEvent?: (event: { type: string; data: any }) => void | Promise<void>
	): Promise<void>;
	sendChatMessage(
		sessionId: string,
		options: {
			content: string;
			imageData?: string;
			timeoutSeconds?: number;
			contextMetadata?: Record<string, any>;
			resources?: Array<{ uri: string }>;
		}
	): Promise<APIResponse<SendChatMessageResponse>>;
	reattachChatMessageStream(
		sessionId: string,
		onEvent?: (event: { type: string; data: any }) => void | Promise<void>,
		options?: { afterSeq?: number }
	): Promise<void>;
	getChatSession(sessionId: string): Promise<APIResponse<ChatSessionWithMessagesResponse>>;
}

export interface TurnControllerDeps {
	store: ChatSessionLikeStore;
	api: ChatApiLike;
	/** Defaults to a no-op — a test doesn't need a real scroll. */
	scrollToBottom?: () => void;
	/** Defaults to an immediately-resolved promise — a test doesn't need a real DOM tick. */
	tick?: () => Promise<void>;
	logError?: (message: string, err: unknown) => void;
	/** `title` events land here — recentSessions is component state, not chatSession's. */
	onTitle?: (sessionId: string, name: string) => void;
	/** Fired once per successful (non-truncated) `done`, and once per successful
	 * non-streaming fallback — both are "the turn produced a final answer"
	 * moments where the component clears its selected-image composer state. */
	onAnswered?: () => void;
}

function defaultTick(): Promise<void> {
	return Promise.resolve();
}

function noop(): void {}

function withDefaults(deps: TurnControllerDeps): Required<Pick<TurnControllerDeps, 'scrollToBottom' | 'tick'>> & TurnControllerDeps {
	return {
		...deps,
		scrollToBottom: deps.scrollToBottom ?? noop,
		tick: deps.tick ?? defaultTick
	};
}

// ---------------------------------------------------------------------------
// Session creation
// ---------------------------------------------------------------------------

export type StartNewSessionResult =
	| { status: 'created'; sessionId: string; sessionData: ChatSessionResponse }
	| { status: 'failed' }
	| { status: 'stale' };

/**
 * Create a new chat session for a send that has no session yet.
 *
 * `pending` is the send's ownership token captured BEFORE this call: its
 * `turnSeq` (allocated before any await at all) and the store's `sessionId`
 * at that moment (always `null` here — this is only the "no session exists
 * yet" path). Both the success and the failure outcome are only published to
 * the store if `pending` is still what the store shows once the request
 * resolves — otherwise the result is discarded entirely (`{status:'stale'}`):
 * a newer turn may have started, or the user may have navigated to a
 * different EXISTING session, while this request was in flight, and this
 * call must not overwrite either. The remote session, if one was actually
 * created, is simply left alone in that case — not deleted, just not adopted
 * by a controller that no longer owns anything.
 */
export async function startNewSession(
	deps: TurnControllerDeps,
	pending: { sessionId: string | null; turnSeq: number },
	payload: { llm_config_id?: string; mode?: string; tools_enabled?: boolean; disabled_tools?: string[] }
): Promise<StartNewSessionResult> {
	let response: APIResponse<ChatSessionResponse>;
	try {
		response = await deps.api.createChatSession(payload);
	} catch (err) {
		if (!isRecoveryStillCurrent(get(deps.store), pending)) return { status: 'stale' };
		deps.logError?.('Failed to create session:', err);
		deps.store.patch({ error: 'Failed to create chat session', isGenerating: false });
		return { status: 'failed' };
	}

	if (!isRecoveryStillCurrent(get(deps.store), pending)) return { status: 'stale' };

	if (response.success && response.data) {
		deps.store.patch({ sessionId: response.data.id });
		return { status: 'created', sessionId: response.data.id, sessionData: response.data };
	}
	deps.store.patch({ error: 'Failed to create chat session', isGenerating: false });
	return { status: 'failed' };
}

// ---------------------------------------------------------------------------
// The shared SSE event handler
// ---------------------------------------------------------------------------

export interface StreamEventHandler {
	handleEvent: (event: { type: string; data: any }) => Promise<void>;
	getLastSeq: () => number | undefined;
	recoverDurableMessage: () => Promise<void>;
}

/**
 * One SSE event handler, shared by the live send stream and the reattach
 * stream so a reloaded, resumed turn drives the exact same reducers. Closes
 * over its own token accumulator; a reattach replays tokens from the start,
 * so accumulating from '' reconstructs the same content the live path built.
 *
 * Every publication goes through `owned` (see `ownSession`), never the raw
 * store directly: `owned`'s captured {sessionId, turnSeq} identity was fixed
 * when the controller (sendMessage/reattachToTurn) claimed this turn, and
 * every one of `owned`'s methods re-checks that identity against the store's
 * CURRENT state before applying anything — so a stream event that arrives
 * after the user switched sessions, or after a newer turn started in the
 * same one, is silently dropped instead of corrupting whatever now owns the
 * UI.
 *
 * `overflow`/`replay_snapshot`/`no_active_turn` mean the locally accumulated
 * text can no longer be trusted to be the whole reply: either events were
 * dropped in transit, or this reconnect's expected prefix was compacted away
 * on the backend. `partial` tracks that state per streamed message so
 * `done`/`error` know whether to trust their own payload or reconcile
 * against the durable persisted message once the turn is no longer live.
 */
export function createStreamEventHandler(
	deps: TurnControllerDeps,
	owned: OwnedSessionController,
	initialUserMessageId?: string
): StreamEventHandler {
	const d = withDefaults(deps);
	const sessionId = owned.captured.sessionId;
	let streamedContent = '';
	let lastSeq: number | undefined;
	let partial = false;
	let recovered = false;
	// The identity of the turn we're recovering FOR — the user message it
	// answers. Seeded when already known (reattaching to a turn whose user
	// message was already visible in the loaded session); otherwise learned
	// from this turn's own `message_created` event (the live-send path).
	let userMessageId = initialUserMessageId;

	// Fetches the persisted assistant message that answers THIS turn's user
	// message (never "the last assistant message in the session" — see
	// findTurnAssistantMessage) and, if `owned` is still current, replaces
	// the streaming/partial placeholder with it. The only way to recover the
	// real reply once no more stream events are coming (a turn that finished
	// and was evicted) or once a gap or a truncated reference makes the
	// accumulated text unreliable. Read-only (a GET through the existing
	// session/messages path) — never re-runs a tool or re-invokes the
	// stream, and skips the fetch entirely once already known stale.
	// Idempotent per handler instance (`recovered` guards a second call).
	async function recoverDurableMessage(): Promise<void> {
		if (recovered) return;
		recovered = true;
		if (!owned.isCurrent()) return; // don't even bother with a doomed fetch

		let matched: ChatSessionWithMessagesResponse['messages'][number] | null = null;
		try {
			const response = await d.api.getChatSession(sessionId);
			if (response.success) {
				matched = findTurnAssistantMessage(response.data?.messages, userMessageId);
			}
		} catch (err) {
			d.logError?.('Failed to recover the persisted reply after a stream gap:', err);
		}

		// Re-check after the await: staleness can newly occur during the
		// fetch itself, not just before it.
		if (!owned.isCurrent()) return;

		if (matched) {
			owned.updateMessages((msgs) => applyDurableRecovery(msgs, mapPersistedMessage(matched!)));
			return;
		}

		// No durable answer exists for THIS turn (a genuinely failed turn, a
		// dropped connection whose backend turn also never finished, or the
		// fetch itself failed) — never substitute an unrelated message.
		// Settle it as a STOPPED, visibly incomplete, explicitly retryable
		// response: whatever content is already showing (a partial snapshot,
		// a truncated preview, or nothing) is retained, never deleted; it
		// stops reading as still live (isStreaming: false) and stays flagged
		// partial so the incomplete-reply affordance doesn't silently
		// vanish. Preserve a more specific error already set by the
		// triggering event over this generic one.
		const currentError = get(d.store).error;
		owned.updateMessages((msgs) => settleUnrecoverable(msgs));
		owned.patch({ error: currentError || 'The reply may be incomplete — you can try again.' });
	}

	const handleEvent = async (event: { type: string; data: any }) => {
		if (typeof event.data?.seq === 'number') lastSeq = event.data.seq;
		// An essential event (done/error/tool result/message_created) over
		// the per-event byte cap arrives as a bounded reference, not its
		// full body — the message it belongs to can't be trusted as
		// complete even with no replay_snapshot/overflow ever seen.
		if (event.data?.truncated) partial = true;

		// Drop every event outright once this turn is no longer current —
		// applying/scrolling for a retired turn, or spending a recovery
		// fetch on one, would only corrupt or waste effort on whatever now
		// owns the UI. `owned`'s own methods would no-op anyway, but this
		// also skips e.g. an unnecessary recoverDurableMessage() GET.
		if (!owned.isCurrent()) return;

		if (event.type === 'message_created') {
			userMessageId = event.data?.user_message_id || userMessageId;
		} else if (event.type === 'token') {
			streamedContent += event.data.content;
			if (owned.applyStreamEvent(event, { accumulated: streamedContent })) d.scrollToBottom();
		} else if (event.type === 'tool_start') {
			if (owned.applyStreamEvent(event)) d.scrollToBottom();
		} else if (event.type === 'tool_end') {
			owned.applyStreamEvent(event);
		} else if (event.type === 'status') {
			if (owned.applyStreamEvent(event)) d.scrollToBottom();
		} else if (event.type === 'replay_snapshot') {
			// This reconnect's expected prefix was compacted away; the
			// snapshot's own bounded text replaces our accumulator so later
			// token deltas append onto the right base, not a gap — the
			// message is flagged partial until done/error/recovery settles it.
			streamedContent = event.data?.text_so_far || '';
			partial = true;
			if (owned.applyStreamEvent(event)) d.scrollToBottom();
		} else if (event.type === 'overflow') {
			// Some live events were dropped for this connection. Nothing is
			// durably persisted yet for a still-running turn, so recovery
			// happens at done/error below rather than here — fetching now
			// would find no new message (or, worse, a stale one from a
			// previous turn) and overwrite live content with it.
			partial = true;
		} else if (event.type === 'done') {
			// A truncated done's own payload has no assistant_message to
			// finalize from at all (see turns.py's per-event bounding) —
			// applying it would "finalize" the message on stale/partial
			// local content right before recovery replaces it anyway.
			// Skip straight to recovery instead of flashing that state.
			if (!event.data?.truncated) {
				owned.applyStreamEvent(event);
			}
			d.onAnswered?.();
			if (needsDurableRecovery('done', partial)) await recoverDurableMessage();
		} else if (event.type === 'title') {
			const titled = event.data || {};
			if (titled.session_id && titled.name) d.onTitle?.(titled.session_id, titled.name);
		} else if (event.type === 'generation_cancelled') {
			// The turn was stopped; drop the streaming placeholder like an error.
			owned.applyStreamEvent({ type: 'error', data: {} });
		} else if (event.type === 'error') {
			owned.patch({ error: event.data.message || 'Streaming error' });
			if (needsDurableRecovery('error', partial)) {
				await recoverDurableMessage();
			} else {
				owned.applyStreamEvent(event);
			}
		} else if (event.type === 'no_active_turn') {
			// Reattached to a turn that already finished and was evicted from
			// the backend's retained buffer — no more events are coming;
			// recover the final reply from the durable path once, and stop.
			if (needsDurableRecovery('no_active_turn', partial)) await recoverDurableMessage();
		}
	};

	return { handleEvent, getLastSeq: () => lastSeq, recoverDurableMessage };
}

// ---------------------------------------------------------------------------
// Reattach
// ---------------------------------------------------------------------------

export interface ReattachParams {
	sessionId: string;
	afterSeq?: number;
	initialUserMessageId?: string;
	/** e.g. focusing the composer input once settled. */
	onSettled?: () => void;
}

/**
 * Reattach to a turn still running on the backend (page reload mid-response,
 * or a live send whose transport failed ambiguously after the request was
 * already issued — see `sendMessage`'s use of this for that case). The
 * persisted messages already include the user message; this adds a
 * streaming assistant placeholder and replays the turn's events into it.
 */
export async function reattachToTurn(deps: TurnControllerDeps, params: ReattachParams): Promise<void> {
	const d = withDefaults(deps);
	const turnSeq = d.store.beginTurn();
	const owned = ownSession(d.store, { sessionId: params.sessionId, turnSeq });
	d.store.patch({ isGenerating: true, error: '' });
	owned.addMessage({
		role: 'assistant',
		content: '',
		timestamp: Date.now(),
		isStreaming: true
	});
	await d.tick();
	d.scrollToBottom();

	try {
		const handler = createStreamEventHandler(d, owned, params.initialUserMessageId);
		if (!owned.isCurrent()) return; // re-check immediately before the outbound call
		await d.api.reattachChatMessageStream(params.sessionId, handler.handleEvent, {
			afterSeq: params.afterSeq
		});
	} catch (err) {
		d.logError?.('Failed to reattach to in-flight turn:', err);
	} finally {
		// Only if THIS reattach's session/turn is still what the store is
		// showing — a delayed recovery elsewhere, or a fresh send that
		// started meanwhile, must not have this stale controller drop its
		// (unrelated) streaming placeholder or clear its isGenerating.
		const applied = finishTurnIfCurrent(d.store, owned.captured, (msgs) =>
			// Drop a still-empty placeholder (e.g. the turn had already
			// finished and was evicted, so nothing was replayed).
			msgs.filter(
				(m, idx) =>
					!(
						idx === msgs.length - 1 &&
						m.role === 'assistant' &&
						m.isStreaming &&
						!m.content &&
						!m.tool_executions?.length &&
						!m.trace_steps?.length
					)
			)
		);
		if (applied) {
			await d.tick();
			d.scrollToBottom();
			params.onSettled?.();
		}
	}
}

// ---------------------------------------------------------------------------
// Send
// ---------------------------------------------------------------------------

export interface SendMessagePayload {
	content: string;
	imageData?: string;
	contextMetadata: Record<string, any>;
	resources?: Array<{ uri: string }>;
	timeoutSeconds?: number;
}

export interface SendMessageParams {
	instruction: string;
	tempUserMessage: UnifiedChatMessageData;
	/** Builds the request payload — called AFTER the optimistic user message
	 * is published, so it can read whatever UI/segment/context state it
	 * needs at send time. Ownership is re-checked immediately after this
	 * returns, before it's used for anything. */
	buildPayload: () => SendMessagePayload;
	/** Only used when no session exists yet. */
	createSessionPayload: { llm_config_id?: string; mode?: string; tools_enabled?: boolean; disabled_tools?: string[] };
	/** Fired once a session was actually created and adopted (never on a
	 * discarded/stale creation) — e.g. updating recentSessions. */
	onSessionCreated?: (sessionData: ChatSessionResponse) => void;
	/** e.g. focusing the composer input once settled. */
	onSettled?: () => void;
}

/**
 * Affirmative, typed evidence that a streaming request never actually left
 * the client — e.g. a network-level failure (DNS resolution, connection
 * refused) that happened before any bytes of the request were sent. This is
 * the ONLY basis on which `sendMessage` may automatically retry via the
 * non-streaming fallback: "no event was ever observed by this browser" is
 * NOT the same claim and must never be treated as one. Once
 * `sendChatMessageStream` has been called, the backend may have accepted and
 * started the turn regardless of what happens to this specific connection
 * afterward (see turns.py: a turn runs to completion independently of any
 * one subscriber) — a dropped connection, a timeout, a proxy hiccup after
 * the request landed all look identical to "never started" from here, and
 * re-invoking the model for any of them would duplicate a real generation.
 *
 * No transport in this codebase produces this evidence today — `fetch`
 * rejections and stream-read errors are generic `Error`s with no way to
 * distinguish "never sent" from "sent, then the connection died" — so this
 * mechanism is currently dormant (see `isRequestNotStartedError`'s doc). Note
 * that a `fetch()` call REJECTING is not, by itself, that proof either: the
 * browser can have already written the entire request to the wire before
 * `fetch()`'s own promise settles, so an error caught around a pending
 * `fetch()` call is exactly as ambiguous as any other transport failure
 * here. This marker exists for a transport that can prove failure with an
 * affirmative LOCAL preflight check that fails BEFORE `fetch()` is ever
 * called at all (e.g. a connectivity check, or request validation) — that
 * has somewhere to plug in without reintroducing an ambiguous "maybe it
 * started" fallback.
 */
export interface RequestNotStartedError {
	notStarted: true;
}

/**
 * Whether `err` carries `RequestNotStartedError`'s marker. Currently always
 * `false` in practice — see `RequestNotStartedError`'s doc comment — which
 * means `sendMessage`'s automatic non-streaming fallback is effectively
 * dormant until a transport is built that can set this affirmatively.
 */
export function isRequestNotStartedError(err: unknown): boolean {
	return (
		typeof err === 'object' &&
		err !== null &&
		'notStarted' in err &&
		(err as Record<'notStarted', unknown>).notStarted === true
	);
}

/**
 * Send `instruction` as a new turn: creates a session first if none exists,
 * streams the response, and treats any streaming transport failure as
 * AMBIGUOUS by default — once `sendChatMessageStream` has been called, a
 * failure could mean the request never reached the server, or that it did
 * and the backend turn is still running there; this module cannot tell the
 * difference (see `RequestNotStartedError`), and "zero events observed"
 * proves nothing either way. The safe default is always the read-only path:
 * reattach to watch the (possibly still-running, backend-owned) turn resume,
 * then durable recovery if that also fails — never re-sending the
 * instruction, which would duplicate the model/tool invocation. The
 * non-streaming fallback fires automatically ONLY when the caught error
 * carries `RequestNotStartedError`'s affirmative evidence — today, never.
 *
 * Ownership ({sessionId, turnSeq}) is established before any await at all
 * (turnSeq) or taken directly from session creation's own return value
 * (sessionId, never a later re-read of the store), then wrapped once via
 * `ownSession` into `owned` — every publication and every outbound request's
 * session id go through `owned` from that point on, re-checked immediately
 * before each one and after each preparatory await.
 */
export async function sendMessage(deps: TurnControllerDeps, params: SendMessageParams): Promise<void> {
	const d = withDefaults(deps);
	const turnSeq = d.store.beginTurn();
	d.store.patch({ error: '', isGenerating: true });

	let ownedSessionId = get(d.store).sessionId;
	if (!ownedSessionId) {
		const result = await startNewSession(d, { sessionId: null, turnSeq }, params.createSessionPayload);
		if (result.status !== 'created') return; // stale: nothing to do; failed: startNewSession already reset state
		ownedSessionId = result.sessionId;
		params.onSessionCreated?.(result.sessionData);
	}
	const owned = ownSession(d.store, { sessionId: ownedSessionId, turnSeq });

	try {
		if (owned.addMessage(params.tempUserMessage)) {
			await d.tick();
			d.scrollToBottom();
		}

		const payload = params.buildPayload();
		// Re-check after this preparatory step: it may itself touch plugin
		// hooks, or simply enough wall-clock time may have passed for a
		// switch, between the user message landing and here.
		if (!owned.isCurrent()) return;

		if (
			owned.addMessage({ role: 'assistant' as const, content: '', timestamp: Date.now(), isStreaming: true })
		) {
			await d.tick();
			d.scrollToBottom();
		}

		const handler = createStreamEventHandler(d, owned);

		try {
			if (!owned.isCurrent()) return; // re-check immediately before the outbound call
			await d.api.sendChatMessageStream(owned.captured.sessionId, payload, handler.handleEvent);
		} catch (err: any) {
			if (!owned.isCurrent()) return;

			if (!isRequestNotStartedError(err)) {
				// The default, ambiguous case: the request was issued and we
				// have no affirmative proof it never reached the server — a
				// dropped connection before the first event is not evidence
				// of "never started" (see this function's own doc comment).
				// Never re-send; always go read-only: re-attach to resume
				// watching the (possibly still-running, backend-owned) turn;
				// if that also fails, or the turn already finished and was
				// evicted, handleEvent's own recovery settles the message
				// from the durable record, or as an explicit, visibly
				// incomplete, retryable state if nothing durable exists.
				d.logError?.('Stream transport failed ambiguously; reattaching rather than resending:', err);
				try {
					if (!owned.isCurrent()) return;
					await d.api.reattachChatMessageStream(owned.captured.sessionId, handler.handleEvent, {
						afterSeq: handler.getLastSeq()
					});
				} catch (reattachErr) {
					if (owned.isCurrent()) await handler.recoverDurableMessage();
				}
				return;
			}

			// Affirmative not-started evidence (see RequestNotStartedError) —
			// a non-streaming resend is safe exactly once. No transport in
			// this codebase produces this today; this branch is dormant
			// until one does.
			d.logError?.('Stream request never reached the server, falling back to non-streaming:', err);
			owned.updateMessages((msgs) =>
				msgs.filter((m, idx) => !(idx === msgs.length - 1 && m.role === 'assistant' && m.isStreaming))
			);

			try {
				if (!owned.isCurrent()) return; // re-check immediately before the outbound call
				const response = await d.api.sendChatMessage(owned.captured.sessionId, {
					content: payload.content,
					imageData: payload.imageData,
					timeoutSeconds: payload.timeoutSeconds,
					contextMetadata: payload.contextMetadata,
					resources: payload.resources
				});
				if (!owned.isCurrent()) return; // staleness can newly occur during this await too

				if (response.success && response.data) {
					const userMsg = response.data.user_message;
					const assistantMsg = response.data.assistant_message;
					owned.updateMessages((msgs) => {
						const updatedMessages = msgs.map((m, idx) => {
							if (idx === msgs.length - 1 && m.role === 'user') {
								return {
									id: userMsg.id,
									role: userMsg.role as 'user',
									content: params.instruction,
									timestamp: userMsg.created_at ? new Date(userMsg.created_at).getTime() : Date.now(),
									metadata: (userMsg as any).metadata || m.metadata
								};
							}
							return m;
						});
						return [
							...updatedMessages,
							{
								id: assistantMsg.id,
								role: assistantMsg.role as 'assistant',
								content: assistantMsg.content,
								timestamp: assistantMsg.created_at
									? new Date(assistantMsg.created_at).getTime()
									: Date.now(),
								tokens_used: assistantMsg.tokens_used,
								prompt_tokens: assistantMsg.prompt_tokens,
								completion_tokens: assistantMsg.completion_tokens,
								tool_executions:
									(assistantMsg as any).metadata?.tool_executions ||
									(assistantMsg as any).tool_executions ||
									[],
								metadata: (assistantMsg as any).metadata || undefined
							}
						];
					});
					d.onAnswered?.();
				} else {
					owned.patch({ error: response.error || 'Failed to send message' });
				}
			} catch (fallbackErr: any) {
				if (owned.isCurrent()) {
					owned.patch({ error: fallbackErr.message || 'Failed to send message' });
				}
			}
		}
	} catch (err: any) {
		d.logError?.('Error sending message:', err);
		owned.patch({ error: err.message || 'Failed to send message' });
	} finally {
		// Only if THIS send's session/turn is still current — a stale
		// controller must not clear isGenerating or scroll/focus for
		// whatever now owns the UI.
		const applied = finishTurnIfCurrent(d.store, owned.captured);
		if (applied) {
			await d.tick();
			d.scrollToBottom();
			params.onSettled?.();
		}
	}
}
