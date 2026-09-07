/**
 * Pure reducers for the chat SSE stream events. Extracted from the inline
 * event switch in UnifiedAIChat's handleSend so the streaming behavior is
 * unit-testable and reusable across send paths.
 *
 * Every reducer takes the current message list and returns a NEW list
 * (never mutates), matching Svelte's reassignment-based reactivity.
 */
import { get, type Readable } from 'svelte/store';
import type {
	UnifiedChatMessageData,
	ToolExecution,
	TraceStep,
	TraceStepName,
	BehaviorTraceManifest,
	ContextLedger
} from '$lib/types/chat';
import type { ChatMessageResponse } from '$lib/types/api';

type Messages = UnifiedChatMessageData[];

/** Next interleaving position for a new trace_steps/tool_executions entry on a message. */
function nextSeq(message: UnifiedChatMessageData): number {
	return (message.trace_steps?.length || 0) + (message.tool_executions?.length || 0);
}

function isLastAssistant(messages: Messages, idx: number): boolean {
	return idx === messages.length - 1 && messages[idx].role === 'assistant';
}

/**
 * `token` event: the caller accumulates streamed content and passes the full
 * accumulated text. The backend's stream is append-only across the whole turn
 * — every `token` event, including any pre-tool-call narration emitted before
 * a `tool_start`, is concatenated into the persisted message (see
 * `full_content` in `src/features/chat/conversation.py` and
 * `execute_with_tools_stream` in `src/features/llm/tools/executor.py`), so
 * `applyToolStart` must NOT blank `content` — narration text stays on screen
 * through the tool round instead of vanishing and reappearing.
 */
export function applyToken(messages: Messages, accumulated: string): Messages {
	const lastIdx = messages.length - 1;
	if (lastIdx < 0 || messages[lastIdx].role !== 'assistant') return messages;
	const msgs = [...messages];
	msgs[lastIdx] = { ...msgs[lastIdx], content: accumulated };
	return msgs;
}

/** `tool_start` event: append a running execution to the streaming assistant message. */
export function applyToolStart(
	messages: Messages,
	data: { tool_name?: string; arguments?: Record<string, unknown> }
): Messages {
	const lastIdx = messages.length - 1;
	if (lastIdx < 0 || messages[lastIdx].role !== 'assistant') return messages;
	const execution: ToolExecution = {
		tool_name: data.tool_name || 'tool',
		arguments: data.arguments || {},
		result: { success: false, data: '' },
		duration_ms: 0,
		status: 'running',
		seq: nextSeq(messages[lastIdx])
	};
	const msgs = [...messages];
	msgs[lastIdx] = {
		...msgs[lastIdx],
		tool_executions: [...(msgs[lastIdx].tool_executions || []), execution],
		isStreaming: true
	};
	return msgs;
}

/**
 * `tool_end` event: mark the matching running execution done; append a
 * pending-approval entry and accumulate sources when present.
 */
export function applyToolEnd(
	messages: Messages,
	data: {
		tool_name?: string;
		arguments?: Record<string, unknown>;
		success?: boolean;
		duration_ms?: number;
		pending_approval?: boolean;
		preview?: ToolExecution['preview'];
		sources?: unknown[];
	}
): Messages {
	let msgs = messages;

	// Mark the last running execution with this tool name as done. `tool_start`
	// seeds `result: { success: false, data: '' }` as a placeholder; the wire
	// event here is the first point the real outcome is known, so apply it —
	// otherwise a successful tool reads as failed (running -> false success)
	// until the final `done` event replaces the whole tool_executions array.
	const lastIdx = msgs.length - 1;
	if (lastIdx >= 0 && msgs[lastIdx].role === 'assistant') {
		const executions = msgs[lastIdx].tool_executions || [];
		for (let i = executions.length - 1; i >= 0; i--) {
			const exec = executions[i];
			if (exec.status === 'running' && (!data.tool_name || exec.tool_name === data.tool_name)) {
				const updated = [...executions];
				updated[i] = {
					...exec,
					status: 'done',
					result: { ...exec.result, success: data.success ?? exec.result.success },
					duration_ms: data.duration_ms ?? exec.duration_ms
				};
				msgs = [...msgs];
				msgs[lastIdx] = { ...msgs[lastIdx], tool_executions: updated };
				break;
			}
		}
	}

	if (data?.pending_approval) {
		msgs = msgs.map((m, idx) => {
			if (!isLastAssistant(msgs, idx)) return m;
			return {
				...m,
				tool_executions: [
					...(m.tool_executions || []),
					{
						tool_name: data.tool_name || '',
						arguments: data.arguments || {},
						result: { success: false, data: '' },
						duration_ms: 0,
						pending_approval: true,
						rejected: false,
						preview: data.preview ?? null,
						seq: nextSeq(m)
					}
				]
			};
		});
	}

	if (data?.sources?.length) {
		msgs = msgs.map((m, idx) => {
			if (!isLastAssistant(msgs, idx)) return m;
			return {
				...m,
				sources: [...(m.sources || []), ...(data.sources as any[])]
			};
		});
	}

	return msgs;
}

/**
 * `status` event: append or resolve a context step (resolving_resources,
 * tools, loading_memory, running_pre_chat, thinking, answering) on the
 * streaming assistant message's `trace_steps`. `thinking`/`answering` only
 * ever arrive as `started` (no matching `completed`); `tools` only ever
 * arrives as `completed` (its resolution is synchronous, nothing to show
 * "in progress" for — the defensive branch below records it with no prior
 * `started`); the rest arrive as a `started`/`completed` pair — `completed`
 * resolves the most recent unresolved `started` entry for that step name in
 * place, so it keeps its original position (and `seq`) in the interleaved
 * timeline.
 */
export function applyStatus(
	messages: Messages,
	data: { step: TraceStepName; state: 'started' | 'completed'; detail?: Record<string, any> }
): Messages {
	const lastIdx = messages.length - 1;
	if (lastIdx < 0 || messages[lastIdx].role !== 'assistant') return messages;
	const msg = messages[lastIdx];
	const steps = msg.trace_steps || [];

	let updatedSteps: TraceStep[];
	if (data.state === 'started') {
		updatedSteps = [
			...steps,
			{ step: data.step, state: 'started', detail: data.detail, started_at: Date.now(), seq: nextSeq(msg) }
		];
	} else {
		const openIdx = steps
			.map((s, i) => ({ s, i }))
			.reverse()
			.find(({ s }) => s.step === data.step && s.state === 'started')?.i;
		if (openIdx === undefined) {
			// Defensive: a completed event with no matching started event still gets recorded.
			updatedSteps = [
				...steps,
				{ step: data.step, state: 'completed', detail: data.detail, seq: nextSeq(msg) }
			];
		} else {
			updatedSteps = [...steps];
			const prev = updatedSteps[openIdx];
			updatedSteps[openIdx] = {
				...prev,
				state: 'completed',
				detail: data.detail ?? prev.detail,
				duration_ms: prev.started_at ? Date.now() - prev.started_at : undefined
			};
		}
	}

	const msgs = [...messages];
	msgs[lastIdx] = { ...msg, trace_steps: updatedSteps };
	return msgs;
}

export type TraceTimelineItem =
	| { kind: 'context'; step: TraceStep }
	| { kind: 'tool'; index: number; execution: ToolExecution };

/** Interleave context steps and tool executions into one chronological timeline by `seq`. */
export function mergeTraceTimeline(
	traceSteps: TraceStep[] = [],
	toolExecutions: ToolExecution[] = []
): TraceTimelineItem[] {
	const items: TraceTimelineItem[] = [
		...traceSteps.map((step) => ({ kind: 'context' as const, step })),
		...toolExecutions.map((execution, index) => ({ kind: 'tool' as const, index, execution }))
	];
	return items.sort((a, b) => {
		const seqA = a.kind === 'context' ? (a.step.seq ?? 0) : (a.execution.seq ?? 0);
		const seqB = b.kind === 'context' ? (b.step.seq ?? 0) : (b.execution.seq ?? 0);
		return seqA - seqB;
	});
}

/** Per-step detail reconstructed from the persisted manifest (never raw prompts — counts/uris only). */
function manifestStepDetail(
	step: TraceStepName,
	manifest: BehaviorTraceManifest
): Record<string, any> | undefined {
	switch (step) {
		case 'resolving_resources':
			return manifest.resources?.length
				? { count: manifest.resources.length, uris: manifest.resources.map((r) => r.uri) }
				: undefined;
		case 'loading_memory':
			return {
				note_count: manifest.memory?.note_ids?.length || 0,
				by_scope: manifest.memory?.by_scope,
				by_scope_dropped: manifest.memory?.by_scope_dropped
			};
		case 'running_pre_chat':
			return manifest.pre_chat_actions?.length ? { actions: manifest.pre_chat_actions } : undefined;
		case 'tools':
			return { offered: manifest.tools_offered ?? [], withheld: manifest.tools_withheld ?? {} };
		default:
			return undefined;
	}
}

/**
 * Reconstruct `trace_steps` from a persisted `metadata.behavior_trace` manifest
 * (reload path — no live `status` events). All steps are exposed as `completed`.
 * `toolCount` is used to interleave the reconstructed steps with the message's
 * existing `tool_executions`: everything up to `thinking` precedes the tool
 * calls, `answering` (and anything else) follows them.
 */
export function hydrateTraceSteps(
	manifest: BehaviorTraceManifest | undefined | null,
	toolCount: number
): TraceStep[] {
	if (!manifest?.steps?.length) return [];
	const AFTER_TOOLS: TraceStepName[] = ['answering'];

	const before: TraceStep[] = [];
	const after: TraceStep[] = [];
	for (const s of manifest.steps) {
		const entry: TraceStep = {
			step: s.step,
			state: 'completed',
			duration_ms: s.duration_ms,
			detail: manifestStepDetail(s.step, manifest)
		};
		(AFTER_TOOLS.includes(s.step) ? after : before).push(entry);
	}
	before.forEach((e, i) => (e.seq = i));
	after.forEach((e, i) => (e.seq = before.length + toolCount + i));
	return [...before, ...after];
}

/** `done` event: finalize the optimistic user message and the streamed assistant message. */
export function applyDone(
	messages: Messages,
	data: { assistant_message?: any; user_message?: any }
): Messages {
	const assistantMsg = data.assistant_message;
	const userMsg = data.user_message;

	const allSources = (assistantMsg?.tool_executions || []).flatMap(
		(te: any) => te.result?.sources || []
	);

	return messages.map((m, idx) => {
		if (idx === messages.length - 2 && m.role === 'user') {
			return {
				...m,
				id: userMsg?.id,
				timestamp: userMsg?.created_at ? new Date(userMsg.created_at).getTime() : m.timestamp
			};
		}
		if (isLastAssistant(messages, idx)) {
			const toolExecutions = assistantMsg?.tool_executions || m.tool_executions || [];
			const manifest = assistantMsg?.metadata?.behavior_trace as BehaviorTraceManifest | undefined;
			// Prefer the persisted manifest (accurate durations) once the backend sends it;
			// fall back to the live-accumulated steps so the trace doesn't disappear if it hasn't.
			const traceSteps = manifest
				? hydrateTraceSteps(manifest, toolExecutions.length)
				: m.trace_steps || [];
			return {
				id: assistantMsg?.id,
				role: 'assistant' as const,
				content: assistantMsg?.content || m.content,
				timestamp: assistantMsg?.created_at
					? new Date(assistantMsg.created_at).getTime()
					: Date.now(),
				tokens_used: assistantMsg?.tokens_used,
				prompt_tokens: assistantMsg?.prompt_tokens,
				completion_tokens: assistantMsg?.completion_tokens,
				tool_executions: toolExecutions,
				trace_steps: traceSteps,
				sources: allSources.length > 0 ? allSources : m.sources || [],
				isStreaming: false,
				metadata: assistantMsg?.metadata || undefined,
				parsed_content: assistantMsg?.parsed_content || undefined
			};
		}
		return m;
	});
}

/** `error` event: drop the streaming assistant placeholder. */
export function applyError(messages: Messages): Messages {
	return messages.filter(
		(m, idx) => !(idx === messages.length - 1 && m.role === 'assistant' && m.isStreaming)
	);
}

/**
 * `replay_snapshot` event: the reconnecting subscriber's expected prefix was
 * compacted away on the backend. Replaces the accumulated streamed text with
 * the snapshot's own (bounded) `text_so_far` and flags the message partial —
 * it is a truncated stand-in, not the full reply, until `done`/`error` or a
 * durable-recovery fetch (`applyDurableRecovery`) replaces it for real.
 */
export function applyReplaySnapshot(
	messages: Messages,
	data: { text_so_far?: string; cursor?: number }
): Messages {
	const lastIdx = messages.length - 1;
	if (lastIdx < 0 || messages[lastIdx].role !== 'assistant') return messages;
	const msgs = [...messages];
	msgs[lastIdx] = { ...msgs[lastIdx], content: data.text_so_far ?? '', isPartial: true };
	return msgs;
}

/**
 * Whether a stream event should trigger a durable-message recovery fetch, for
 * a message that has (`wasPartial`) or hasn't been flagged partial so far.
 *
 * `no_active_turn` (reattaching to a turn that already finished and was
 * evicted from the backend's retained buffer) always recovers — no further
 * events are coming, so a REST fetch is the only way to get the real reply.
 * `done`/`error` recover only when the message was ever flagged partial
 * (a `replay_snapshot` or `overflow` happened earlier): a `done` not preceded
 * by either already carries the authoritative content in its own payload, and
 * recovering unconditionally would risk fetching mid-turn (nothing durable
 * exists yet) or doing pointless extra requests on the common, ungapped path.
 * A plain `overflow` while the turn is still running never recovers by
 * itself — nothing is durably persisted for an in-progress turn yet.
 *
 * The caller also flips `wasPartial` to true the moment ANY event (not just
 * `replay_snapshot`/`overflow`) arrives carrying `data.truncated: true` — a
 * `done`, `error`, tool result, or `message_created` can itself be a bounded
 * reference (see turns.py's per-event byte cap) with no preceding gap
 * signal, and a truncated `done` must never be trusted as the final content
 * either.
 */
export function needsDurableRecovery(eventType: string, wasPartial: boolean): boolean {
	if (eventType === 'no_active_turn') return true;
	if (eventType === 'done' || eventType === 'error') return wasPartial;
	return false;
}

/**
 * Find the persisted message that answers a SPECIFIC user message, by
 * identity — never "the last assistant message in the session". A session
 * can have an earlier turn's answer as its most recent persisted message
 * while the CURRENT turn's is still missing (it failed before persisting, or
 * simply hasn't landed yet); substituting that older answer would silently
 * show the wrong reply. The current turn's user message is always persisted
 * before its assistant reply, so the match is simply "the assistant message
 * immediately following the user message this turn started with" — if that
 * next message doesn't exist yet, or isn't role `assistant`, there is no
 * durable answer for this turn yet.
 */
export function findTurnAssistantMessage<T extends { id?: string | null; role: string }>(
	messages: T[] | undefined,
	userMessageId: string | undefined
): T | null {
	if (!messages?.length || !userMessageId) return null;
	const idx = messages.findIndex((m) => m.id === userMessageId);
	if (idx === -1) return null;
	const next = messages[idx + 1];
	return next && next.role === 'assistant' ? next : null;
}

/** Map one persisted `ChatMessageResponse` into the shape the message list
 * renders. Shared by the session-load path (UnifiedAIChat's `loadSession`)
 * and the stream-recovery path (`recoverDurableMessage` in
 * turnController.ts) so both produce an identical message from the same
 * backend record. */
export function mapPersistedMessage(msg: ChatMessageResponse): UnifiedChatMessageData {
	const metadata = (msg as any).metadata || {};
	const toolExecs = metadata.tool_executions || (msg as any).tool_executions || [];
	return {
		id: msg.id,
		role: msg.role,
		content: msg.content,
		timestamp: msg.created_at ? new Date(msg.created_at).getTime() : Date.now(),
		imageUrl: metadata.image_url || null,
		tokens_used: msg.tokens_used,
		prompt_tokens: msg.prompt_tokens,
		completion_tokens: msg.completion_tokens,
		tool_executions: toolExecs,
		sources: toolExecs.flatMap((te: any) => te.result?.sources || []),
		metadata
	};
}

/**
 * Whether a recovery fetch that was started for `expected` (a session id +
 * turn identity, captured when the streaming handler was created) is still
 * the thing the store should show, now that its async GET has resolved.
 *
 * A recovery fetch can take a while; in that time the user can switch to a
 * different session, or — within the same session — a brand new turn can
 * start (send another message once this one settles or errors). Publishing
 * a stale recovery over either would silently show a past turn's content
 * layered onto the current one. `turnSeq` is a monotonic id the store hands
 * out per streamed turn (`chatSession.beginTurn()`), so this is a simple
 * identity comparison, not a heuristic.
 */
export function isRecoveryStillCurrent(
	current: { sessionId: string | null; turnSeq: number },
	expected: { sessionId: string | null; turnSeq: number }
): boolean {
	return current.sessionId === expected.sessionId && current.turnSeq === expected.turnSeq;
}

/** The subset of the chatSession store's own interface a controller's
 * terminal cleanup needs — kept minimal so this is testable against a fake
 * store as well as the real `chatSession` singleton. */
export interface TurnFinishableStore
	extends Readable<{ sessionId: string | null; turnSeq: number; error: string }> {
	updateMessages(fn: (messages: UnifiedChatMessageData[]) => UnifiedChatMessageData[]): void;
	/** Matches the fields a turn controller actually patches (including
	 * `sessionId`, for `startNewSession` adopting a newly created session) —
	 * a subset of the real chatSession store's own `Partial<ChatConversationState>`. */
	patch(partial: { isGenerating?: boolean; error?: string; sessionId?: string | null }): void;
}

/**
 * The subset of the chatSession store's interface a turn controller
 * publishes through — everything `ownSession` wraps with the ownership
 * check, plus `beginTurn` for allocating a new turn's identity.
 */
export interface ChatSessionLikeStore extends TurnFinishableStore {
	addMessage(message: UnifiedChatMessageData): void;
	applyStreamEvent(event: { type: string; data: any }, opts?: { accumulated?: string }): void;
	beginTurn(): number;
}

/** A `ChatSessionLikeStore` facade whose every mutating call is a no-op once
 * its captured turn is no longer current — see `ownSession`. */
export interface OwnedSessionController {
	readonly captured: { sessionId: string; turnSeq: number };
	/** Re-checked fresh on every call — never cached. */
	isCurrent(): boolean;
	patch(partial: { isGenerating?: boolean; error?: string }): boolean;
	addMessage(message: UnifiedChatMessageData): boolean;
	updateMessages(fn: (messages: Messages) => Messages): boolean;
	applyStreamEvent(event: { type: string; data: any }, opts?: { accumulated?: string }): boolean;
}

/**
 * Scope a chat session store to one turn's ownership: every mutating call
 * becomes a no-op — returning `false` instead of applying — once
 * `isRecoveryStillCurrent(get(store), captured)` is false.
 *
 * A turn controller (the live-send path, `reattachToTurn`) makes MANY
 * publications over its lifetime: the optimistic user message, the streaming
 * placeholder, every SSE event's reducer application, the transport-failure
 * fallback's own updates, the terminal cleanup. By the time any one of these
 * runs, the user could have switched sessions or started a newer turn in the
 * same one — checking this once at the top and trusting it for everything
 * that follows is exactly the bug this closes (Codex found the INNER
 * catch's fallback path and individual stream-event application both
 * skipping the check entirely). Wrapping the store once, here, means every
 * call site gets the guard automatically instead of one being missed.
 *
 * Returns `false` from a call instead of throwing — the caller can use that
 * to skip its own dependent effects (e.g. not scrolling to a message that
 * was never actually applied) without a try/catch.
 */
export function ownSession(
	store: ChatSessionLikeStore,
	captured: { sessionId: string; turnSeq: number }
): OwnedSessionController {
	const isCurrent = () => isRecoveryStillCurrent(get(store), captured);
	return {
		captured,
		isCurrent,
		patch(partial) {
			if (!isCurrent()) return false;
			store.patch(partial);
			return true;
		},
		addMessage(message) {
			if (!isCurrent()) return false;
			store.addMessage(message);
			return true;
		},
		updateMessages(fn) {
			if (!isCurrent()) return false;
			store.updateMessages(fn);
			return true;
		},
		applyStreamEvent(event, opts) {
			if (!isCurrent()) return false;
			store.applyStreamEvent(event, opts);
			return true;
		}
	};
}

/**
 * A controller's (live-send or reattach) terminal cleanup — but only if the
 * turn it was controlling is still the one the store is showing.
 *
 * Every controller runs at least one `await` (the whole streamed request);
 * by the time it settles, the user can have switched sessions or started a
 * newer turn in the SAME session (reattachToTurn's own placeholder cleanup
 * racing a fresh send is the concrete case this closes). Running the cleanup
 * unconditionally would then retire state that belongs to whatever now owns
 * the UI — clearing `isGenerating` for a turn that isn't running anymore, or
 * dropping a message that isn't the stale controller's placeholder at all.
 *
 * `cleanupMessages`, when given, runs via `updateMessages` (e.g. dropping a
 * still-empty reattach placeholder) before `isGenerating` is cleared. Returns
 * whether the cleanup actually ran, so a caller can skip its own trailing
 * effects (scroll, focus) too when it didn't.
 */
export function finishTurnIfCurrent(
	store: TurnFinishableStore,
	captured: { sessionId: string; turnSeq: number },
	cleanupMessages?: (messages: Messages) => Messages
): boolean {
	if (!isRecoveryStillCurrent(get(store), captured)) return false;
	if (cleanupMessages) store.updateMessages(cleanupMessages);
	store.patch({ isGenerating: false });
	return true;
}

/**
 * Replace the trailing assistant message with the durable persisted one —
 * used after an error that followed a partial replay, after a `done` whose
 * own payload can't be trusted because the stream had a gap or was itself
 * truncated, and when reattaching to a turn that already finished and was
 * evicted (no more stream events are coming, so this is the only way to
 * recover it).
 *
 * Trusts the caller's `needsDurableRecovery` decision rather than re-checking
 * `isStreaming`/`isPartial` here: `applyDone` already unconditionally clears
 * both flags on the message this runs right after (it finalizes every done,
 * gapped or not), so gating on them would silently no-op exactly when
 * recovery matters most. No-op if there's no trailing assistant message, or
 * nothing was persisted (`persisted` is null) — the caller is responsible for
 * deciding what a failed recovery means (see `findTurnAssistantMessage` and
 * `isRecoveryStillCurrent`): never substitute an unrelated message, and never
 * publish a recovery for a turn/session that's no longer current.
 */
export function applyDurableRecovery(
	messages: Messages,
	persisted: UnifiedChatMessageData | null | undefined
): Messages {
	const lastIdx = messages.length - 1;
	if (lastIdx < 0 || messages[lastIdx].role !== 'assistant') return messages;
	if (!persisted) return messages;
	const msgs = [...messages];
	msgs[lastIdx] = { ...persisted, isStreaming: false, isPartial: false };
	return msgs;
}

/**
 * Settle the trailing assistant message as a stopped, visibly incomplete
 * response — used when a recovery attempt found no durable match for this
 * turn (a genuinely unrecoverable gap, not a "nothing was ever wrong" case):
 * whatever content is already showing (partial snapshot text, or a truncated
 * reference's preview, or nothing at all) is RETAINED, never deleted;
 * `isStreaming` is cleared so it stops reading as still live, and `isPartial`
 * is set so the UI's incomplete-reply affordance stays visible instead of
 * silently vanishing. Pair with a `chatSession.patch({ error })` call — this
 * only touches the message, not the session-level error string.
 */
export function settleUnrecoverable(messages: Messages): Messages {
	const lastIdx = messages.length - 1;
	if (lastIdx < 0 || messages[lastIdx].role !== 'assistant') return messages;
	const msgs = [...messages];
	msgs[lastIdx] = { ...msgs[lastIdx], isStreaming: false, isPartial: true };
	return msgs;
}

/** `1234` -> `1.2k`, `800` -> `0.8k`; kept in one k-scaled unit throughout so the components in a ledger line stay comparable at a glance. */
function formatTokenCount(n: number): string {
	return n === 0 ? '0' : `${(n / 1000).toFixed(1)}k`;
}

/**
 * One compact line summarizing a turn's `context_ledger` — the size (in
 * estimated tokens) of each component that reached the LLM, plus the total.
 */
export function formatContextLedgerSummary(ledger: ContextLedger): string {
	const parts = [
		`System prompt ~${formatTokenCount(ledger.system_prompt.est_tokens)} tok`,
		`Tools (${ledger.tool_schemas.tool_count}) ~${formatTokenCount(ledger.tool_schemas.est_tokens)} tok`,
		`Memory ~${formatTokenCount(ledger.memory.est_tokens)} tok`,
		`History (${ledger.history.message_count}) ~${formatTokenCount(ledger.history.est_tokens)} tok`
	];
	return `${parts.join(' · ')} · ~${formatTokenCount(ledger.total_est_tokens)} total`;
}

/** Total memory notes left out of the injected block across all scopes. */
export function sumMemoryDropped(
	byScopeDropped: { global: number; preset: number; model: number } | undefined | null
): number {
	if (!byScopeDropped) return 0;
	return (byScopeDropped.global ?? 0) + (byScopeDropped.preset ?? 0) + (byScopeDropped.model ?? 0);
}

/** `title` event: patch the generated title onto a session in a session list. */
export function applyTitle<T extends { id: string; name?: string | null }>(
	sessions: T[],
	sessionId: string,
	title: string
): T[] {
	return sessions.map((s) =>
		s.id === sessionId ? { ...s, name: title, title_generated: true } : s
	);
}
