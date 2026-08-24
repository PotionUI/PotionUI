/**
 * Pure reducers for the chat SSE stream events. Extracted from the inline
 * event switch in UnifiedAIChat's handleSend so the streaming behavior is
 * unit-testable and reusable across send paths.
 *
 * Every reducer takes the current message list and returns a NEW list
 * (never mutates), matching Svelte's reassignment-based reactivity.
 */
import type {
	UnifiedChatMessageData,
	ToolExecution,
	TraceStep,
	TraceStepName,
	BehaviorTraceManifest,
	ContextLedger
} from '$lib/types/chat';

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
 * loading_memory, running_pre_chat, thinking, answering) on the streaming
 * assistant message's `trace_steps`. `thinking`/`answering` only ever arrive
 * as `started` (no matching `completed`); the rest arrive as a `started`/
 * `completed` pair — `completed` resolves the most recent unresolved
 * `started` entry for that step name in place, so it keeps its original
 * position (and `seq`) in the interleaved timeline.
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
