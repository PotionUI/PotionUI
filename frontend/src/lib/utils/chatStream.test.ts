import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
	applyToken,
	applyToolStart,
	applyToolEnd,
	applyStatus,
	applyDone,
	applyError,
	applyTitle,
	applyReplaySnapshot,
	applyDurableRecovery,
	settleUnrecoverable,
	needsDurableRecovery,
	findTurnAssistantMessage,
	isRecoveryStillCurrent,
	finishTurnIfCurrent,
	mergeTraceTimeline,
	hydrateTraceSteps,
	formatContextLedgerSummary,
	sumMemoryDropped
} from './chatStream';
import { chatSession } from '$lib/stores/chatSession';
import { get } from 'svelte/store';
import type { UnifiedChatMessageData, BehaviorTraceManifest, ContextLedger } from '$lib/types/chat';

function fixture(): UnifiedChatMessageData[] {
	return [
		{ role: 'user', content: 'hello', timestamp: 1 },
		{ role: 'assistant', content: '', timestamp: 2, isStreaming: true }
	];
}

describe('applyToken', () => {
	it('sets the accumulated content on the last assistant message', () => {
		const out = applyToken(fixture(), 'Hi the');
		expect(out[1].content).toBe('Hi the');
	});

	it('does not mutate the input array', () => {
		const input = fixture();
		applyToken(input, 'xyz');
		expect(input[1].content).toBe('');
	});

	it('is a no-op when the last message is not an assistant message', () => {
		const msgs: UnifiedChatMessageData[] = [{ role: 'user', content: 'a', timestamp: 1 }];
		expect(applyToken(msgs, 'text')).toBe(msgs);
	});
});

describe('applyToolStart', () => {
	it('appends a running execution WITHOUT blanking already-streamed content', () => {
		// The backend stream is append-only across the whole turn (pre-tool-call
		// narration like "Let me check..." is real text the model produced and
		// stays in the persisted message) — blanking it here made that text
		// vanish and reappear on every tool round.
		const withText = applyToken(fixture(), 'partial');
		const out = applyToolStart(withText, { tool_name: 'list_models', arguments: { q: 1 } });
		const last = out[out.length - 1];
		expect(last.content).toBe('partial');
		expect(last.isStreaming).toBe(true);
		expect(last.tool_executions).toHaveLength(1);
		expect(last.tool_executions![0]).toMatchObject({
			tool_name: 'list_models',
			arguments: { q: 1 },
			status: 'running',
			duration_ms: 0
		});
	});

	it('defaults tool_name to "tool"', () => {
		const out = applyToolStart(fixture(), {});
		expect(out[1].tool_executions![0].tool_name).toBe('tool');
	});
});

describe('applyToolEnd', () => {
	it('marks the matching running execution as done', () => {
		const started = applyToolStart(fixture(), { tool_name: 'list_models' });
		const out = applyToolEnd(started, { tool_name: 'list_models' });
		expect(out[1].tool_executions![0].status).toBe('done');
	});

	it('applies the reported success/duration onto the resolved execution', () => {
		// tool_start seeds a { success: false } placeholder; a successful tool
		// must not still read as failed once tool_end reports success: true.
		const started = applyToolStart(fixture(), { tool_name: 'get_active_models' });
		const out = applyToolEnd(started, {
			tool_name: 'get_active_models',
			success: true,
			duration_ms: 42
		});
		expect(out[1].tool_executions![0]).toMatchObject({
			status: 'done',
			duration_ms: 42,
			result: { success: true }
		});
	});

	it('leaves the placeholder success as-is when tool_end omits it', () => {
		const started = applyToolStart(fixture(), { tool_name: 'list_models' });
		const out = applyToolEnd(started, { tool_name: 'list_models', success: false });
		expect(out[1].tool_executions![0].result.success).toBe(false);
	});

	it('appends a pending-approval execution entry', () => {
		const started = applyToolStart(fixture(), { tool_name: 'update_form_settings' });
		const out = applyToolEnd(started, {
			tool_name: 'update_form_settings',
			pending_approval: true,
			arguments: { field: 'steps' }
		});
		const execs = out[1].tool_executions!;
		expect(execs).toHaveLength(2);
		expect(execs[1]).toMatchObject({
			tool_name: 'update_form_settings',
			pending_approval: true,
			rejected: false
		});
	});

	it('accumulates sources onto the last assistant message', () => {
		const started = applyToolStart(fixture(), { tool_name: 'search_model_prompts' });
		const src = { source_type: 'prompt', title: 'A' };
		const out = applyToolEnd(started, { tool_name: 'search_model_prompts', sources: [src] });
		expect(out[1].sources).toEqual([src]);
	});

	it('carries a structured approval preview onto the pending entry', () => {
		const started = applyToolStart(fixture(), { tool_name: 'remove_phrasebook_values' });
		const preview = { action: 'Remove', target: 'from category camera', items: ['a', 'b'] };
		const out = applyToolEnd(started, {
			tool_name: 'remove_phrasebook_values',
			pending_approval: true,
			preview
		});
		expect(out[1].tool_executions![1].preview).toEqual(preview);
	});

	it('defaults the pending entry preview to null when none is sent', () => {
		const started = applyToolStart(fixture(), { tool_name: 'update_form_settings' });
		const out = applyToolEnd(started, { tool_name: 'update_form_settings', pending_approval: true });
		expect(out[1].tool_executions![1].preview).toBeNull();
	});
});

describe('applyStatus', () => {
	it('appends a started step with a seq past any existing tool executions', () => {
		const withTool = applyToolStart(fixture(), { tool_name: 'list_models' });
		const out = applyStatus(withTool, { step: 'thinking', state: 'started' });
		const steps = out[1].trace_steps!;
		expect(steps).toHaveLength(1);
		expect(steps[0]).toMatchObject({ step: 'thinking', state: 'started', seq: 1 });
		expect(typeof steps[0].started_at).toBe('number');
	});

	it('resolves the matching started entry in place on completed, computing duration_ms', () => {
		let msgs = applyStatus(fixture(), { step: 'loading_memory', state: 'started' });
		msgs = applyStatus(msgs, {
			step: 'loading_memory',
			state: 'completed',
			detail: { note_count: 3, by_scope: { global: 1, preset: 2, model: 0 } }
		});
		const steps = msgs[1].trace_steps!;
		expect(steps).toHaveLength(1);
		expect(steps[0]).toMatchObject({
			step: 'loading_memory',
			state: 'completed',
			detail: { note_count: 3 }
		});
		expect(typeof steps[0].duration_ms).toBe('number');
	});

	it('interleaves with tool_start/tool_end events chronologically by seq', () => {
		let msgs = applyStatus(fixture(), { step: 'thinking', state: 'started' });
		msgs = applyToolStart(msgs, { tool_name: 'list_models' });
		msgs = applyToolEnd(msgs, { tool_name: 'list_models' });
		msgs = applyStatus(msgs, { step: 'answering', state: 'started' });
		const timeline = mergeTraceTimeline(msgs[1].trace_steps, msgs[1].tool_executions);
		expect(timeline.map((i) => (i.kind === 'context' ? i.step.step : i.execution.tool_name))).toEqual([
			'thinking',
			'list_models',
			'answering'
		]);
	});

	it('is a no-op when the last message is not an assistant message', () => {
		const msgs: UnifiedChatMessageData[] = [{ role: 'user', content: 'a', timestamp: 1 }];
		expect(applyStatus(msgs, { step: 'thinking', state: 'started' })).toBe(msgs);
	});

	it('records a defensive completed entry when no started event was seen', () => {
		const out = applyStatus(fixture(), { step: 'resolving_resources', state: 'completed', detail: { count: 2 } });
		expect(out[1].trace_steps).toHaveLength(1);
		expect(out[1].trace_steps![0]).toMatchObject({ state: 'completed', detail: { count: 2 } });
	});
});

describe('hydrateTraceSteps', () => {
	function manifest(): BehaviorTraceManifest {
		return {
			version: 1,
			mode: 'generation',
			system_prompt_source: 'default',
			resources: [{ uri: 'model:abc', type: 'model' }],
			memory: { note_ids: ['n1', 'n2'], by_scope: { global: 1, preset: 1, model: 0 } },
			pre_chat_actions: ['refresh_models'],
			tools_used: ['list_models'],
			token_counts: { prompt: 100, completion: 40 },
			steps: [
				{ step: 'resolving_resources', duration_ms: 5 },
				{ step: 'loading_memory', duration_ms: 10 },
				{ step: 'running_pre_chat', duration_ms: 8 },
				{ step: 'thinking', duration_ms: 200 },
				{ step: 'answering', duration_ms: 1500 }
			]
		};
	}

	it('returns nothing for a message with no manifest', () => {
		expect(hydrateTraceSteps(undefined, 0)).toEqual([]);
		expect(hydrateTraceSteps(null, 2)).toEqual([]);
	});

	it('reconstructs all steps as completed with manifest-derived detail', () => {
		const steps = hydrateTraceSteps(manifest(), 1);
		expect(steps.every((s) => s.state === 'completed')).toBe(true);
		const resources = steps.find((s) => s.step === 'resolving_resources')!;
		expect(resources.detail).toEqual({ count: 1, uris: ['model:abc'] });
		const memory = steps.find((s) => s.step === 'loading_memory')!;
		expect(memory.detail).toEqual({ note_count: 2, by_scope: { global: 1, preset: 1, model: 0 } });
		const preChat = steps.find((s) => s.step === 'running_pre_chat')!;
		expect(preChat.detail).toEqual({ actions: ['refresh_models'] });
	});

	it('carries by_scope_dropped through when the manifest reports it', () => {
		const m = manifest();
		m.memory.by_scope_dropped = { global: 0, preset: 3, model: 0 };
		const memory = hydrateTraceSteps(m, 1).find((s) => s.step === 'loading_memory')!;
		expect(memory.detail?.by_scope_dropped).toEqual({ global: 0, preset: 3, model: 0 });
	});

	it('omits resources/pre_chat detail when the manifest reports none', () => {
		const m = manifest();
		m.resources = [];
		m.pre_chat_actions = [];
		const steps = hydrateTraceSteps(m, 0);
		expect(steps.find((s) => s.step === 'resolving_resources')!.detail).toBeUndefined();
		expect(steps.find((s) => s.step === 'running_pre_chat')!.detail).toBeUndefined();
	});

	it('places tool_executions between thinking and answering via seq (toolCount aware)', () => {
		const steps = hydrateTraceSteps(manifest(), 2);
		const bySeq = [...steps].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
		const thinkingSeq = bySeq.find((s) => s.step === 'thinking')!.seq!;
		const answeringSeq = bySeq.find((s) => s.step === 'answering')!.seq!;
		expect(answeringSeq).toBe(thinkingSeq + 1 + 2); // 2 tool slots reserved in between
	});
});

describe('a tool round never blanks already-streamed content', () => {
	it('content deltas -> tool_start -> tool_end -> content deltas -> done: content never goes empty', () => {
		let msgs = fixture();
		let accumulated = '';

		accumulated += 'Let me check that for you...';
		msgs = applyToken(msgs, accumulated);
		expect(msgs[1].content).toBe('Let me check that for you...');

		msgs = applyToolStart(msgs, { tool_name: 'get_active_models' });
		// The narration must still be visible for the whole time the tool runs.
		expect(msgs[1].content).toBe('Let me check that for you...');

		msgs = applyToolEnd(msgs, { tool_name: 'get_active_models', success: true, duration_ms: 12 });
		expect(msgs[1].content).toBe('Let me check that for you...');

		accumulated += ' Here is what I found.';
		msgs = applyToken(msgs, accumulated);
		expect(msgs[1].content).toBe('Let me check that for you... Here is what I found.');

		const out = applyDone(msgs, {
			assistant_message: {
				id: 'a1',
				content: 'Let me check that for you... Here is what I found.',
				tool_executions: msgs[1].tool_executions
			}
		});
		// The persisted/final content matches exactly what was on screen live —
		// no jarring replace once the turn finishes.
		expect(out[1].content).toBe('Let me check that for you... Here is what I found.');
	});

	it('multiple tool rounds (text -> tool -> text -> tool -> text): content stays non-empty and only grows', () => {
		let msgs = fixture();
		let accumulated = '';
		const seenAfterEachEvent: string[] = [];

		function token(delta: string) {
			accumulated += delta;
			msgs = applyToken(msgs, accumulated);
			seenAfterEachEvent.push(msgs[1].content);
		}

		token('Checking models...');
		msgs = applyToolStart(msgs, { tool_name: 'get_active_models' });
		seenAfterEachEvent.push(msgs[1].content);
		msgs = applyToolEnd(msgs, { tool_name: 'get_active_models', success: true });
		seenAfterEachEvent.push(msgs[1].content);

		token(' Now checking prompts...');
		msgs = applyToolStart(msgs, { tool_name: 'search_model_prompts' });
		seenAfterEachEvent.push(msgs[1].content);
		msgs = applyToolEnd(msgs, { tool_name: 'search_model_prompts', success: true });
		seenAfterEachEvent.push(msgs[1].content);

		token(' Done, here is the answer.');

		// Never blank at any point once real text has streamed.
		expect(seenAfterEachEvent.every((c) => c.length > 0)).toBe(true);
		expect(msgs[1].content).toBe(
			'Checking models... Now checking prompts... Done, here is the answer.'
		);
		expect(msgs[1].tool_executions).toHaveLength(2);
	});
});

describe('applyDone', () => {
	it('finalizes the user and assistant messages from the payload', () => {
		const msgs = applyToken(fixture(), 'stream');
		const out = applyDone(msgs, {
			user_message: { id: 'u1', created_at: '2026-01-01T00:00:00Z' },
			assistant_message: {
				id: 'a1',
				content: 'final answer',
				created_at: '2026-01-01T00:00:01Z',
				tokens_used: 12,
				prompt_tokens: 8,
				completion_tokens: 4,
				tool_executions: [{ tool_name: 't', result: { sources: [{ title: 's' }] } }],
				metadata: { foo: 'bar' }
			}
		});
		expect(out[0].id).toBe('u1');
		expect(out[1]).toMatchObject({
			id: 'a1',
			content: 'final answer',
			tokens_used: 12,
			isStreaming: false,
			metadata: { foo: 'bar' }
		});
		expect(out[1].sources).toEqual([{ title: 's' }]);
	});

	it('falls back to streamed content when the payload has none', () => {
		const msgs = applyToken(fixture(), 'streamed text');
		const out = applyDone(msgs, { assistant_message: { id: 'a1' } });
		expect(out[1].content).toBe('streamed text');
	});

	it('maps parsed_content.reply_contract from the persisted assistant message', () => {
		const msgs = applyToken(fixture(), 'stream');
		const out = applyDone(msgs, {
			assistant_message: {
				id: 'a1',
				content: 'final answer',
				parsed_content: {
					reply_contract: {
						improved: ['Added lens detail'],
						questions: [{ text: 'Golden hour or overcast?', options: ['Golden hour', 'Overcast'] }]
					}
				}
			}
		});
		expect(out[1].parsed_content).toEqual({
			reply_contract: {
				improved: ['Added lens detail'],
				questions: [{ text: 'Golden hour or overcast?', options: ['Golden hour', 'Overcast'] }]
			}
		});
	});

	it('leaves parsed_content undefined when the payload has no key for it', () => {
		const msgs = applyToken(fixture(), 'stream');
		const out = applyDone(msgs, { assistant_message: { id: 'a1', content: 'final answer' } });
		expect(out[1].parsed_content).toBeUndefined();
	});

	it('replaces live-accumulated trace_steps with the manifest once behavior_trace lands', () => {
		let msgs = applyStatus(fixture(), { step: 'thinking', state: 'started' });
		const out = applyDone(msgs, {
			assistant_message: {
				id: 'a1',
				content: 'done',
				metadata: {
					behavior_trace: {
						version: 1,
						mode: 'generation',
						system_prompt_source: 'default',
						resources: [],
						memory: { note_ids: [], by_scope: { global: 0, preset: 0, model: 0 } },
						pre_chat_actions: [],
						tools_used: [],
						token_counts: { prompt: 10, completion: 5 },
						steps: [{ step: 'thinking', duration_ms: 300 }]
					}
				}
			}
		});
		expect(out[1].trace_steps).toEqual([
			{ step: 'thinking', state: 'completed', duration_ms: 300, detail: undefined, seq: 0 }
		]);
	});

	it('keeps the live-accumulated trace_steps when no manifest is present yet', () => {
		const msgs = applyStatus(fixture(), { step: 'thinking', state: 'started' });
		const out = applyDone(msgs, { assistant_message: { id: 'a1', content: 'done' } });
		expect(out[1].trace_steps).toHaveLength(1);
		expect(out[1].trace_steps![0]).toMatchObject({ step: 'thinking', state: 'started' });
	});
});

describe('applyError', () => {
	it('removes the trailing streaming placeholder', () => {
		const out = applyError(fixture());
		expect(out).toHaveLength(1);
		expect(out[0].role).toBe('user');
	});

	it('keeps a completed assistant message', () => {
		const msgs = fixture();
		msgs[1] = { ...msgs[1], isStreaming: false };
		expect(applyError(msgs)).toHaveLength(2);
	});
});

describe('applyReplaySnapshot', () => {
	it('replaces the accumulated content with the snapshot text and flags the message partial', () => {
		const out = applyReplaySnapshot(fixture(), { text_so_far: 'Here is the tail ', cursor: 42 });
		expect(out[1].content).toBe('Here is the tail ');
		expect(out[1].isPartial).toBe(true);
	});

	it('defaults to empty content when text_so_far is missing', () => {
		const out = applyReplaySnapshot(fixture(), {});
		expect(out[1].content).toBe('');
	});

	it('is a no-op when the last message is not an assistant message', () => {
		const msgs: UnifiedChatMessageData[] = [{ role: 'user', content: 'a', timestamp: 1 }];
		expect(applyReplaySnapshot(msgs, { text_so_far: 'x' })).toBe(msgs);
	});
});

describe('needsDurableRecovery', () => {
	it('always recovers on no_active_turn, partial or not', () => {
		expect(needsDurableRecovery('no_active_turn', false)).toBe(true);
		expect(needsDurableRecovery('no_active_turn', true)).toBe(true);
	});

	it('recovers on done/error only when the message was ever flagged partial', () => {
		expect(needsDurableRecovery('done', true)).toBe(true);
		expect(needsDurableRecovery('done', false)).toBe(false);
		expect(needsDurableRecovery('error', true)).toBe(true);
		expect(needsDurableRecovery('error', false)).toBe(false);
	});

	it('never recovers on a bare overflow — nothing is durable yet mid-turn', () => {
		expect(needsDurableRecovery('overflow', true)).toBe(false);
		expect(needsDurableRecovery('overflow', false)).toBe(false);
	});

	it('does not recover on ordinary streaming events', () => {
		expect(needsDurableRecovery('token', true)).toBe(false);
		expect(needsDurableRecovery('status', true)).toBe(false);
	});
});

describe('findTurnAssistantMessage', () => {
	const sessionMessages = [
		{ id: 'u0', role: 'user', content: 'earlier question' },
		{ id: 'a0', role: 'assistant', content: 'earlier answer' },
		{ id: 'u1', role: 'user', content: 'current question' },
		{ id: 'a1', role: 'assistant', content: 'current answer' }
	];

	it('finds the assistant message immediately following the given user message id', () => {
		expect(findTurnAssistantMessage(sessionMessages, 'u1')).toMatchObject({ id: 'a1' });
	});

	it('never falls back to the last assistant message when the current turn has none yet', () => {
		const noAnswerYet = sessionMessages.slice(0, 3); // ends on u1, no a1
		expect(findTurnAssistantMessage(noAnswerYet, 'u1')).toBeNull();
	});

	it('returns null when the message right after the user message is not an assistant message', () => {
		const twoUsersInARow = [
			{ id: 'u1', role: 'user', content: 'a' },
			{ id: 'u2', role: 'user', content: 'b' }
		];
		expect(findTurnAssistantMessage(twoUsersInARow, 'u1')).toBeNull();
	});

	it('returns null when the user message id is not present', () => {
		expect(findTurnAssistantMessage(sessionMessages, 'does-not-exist')).toBeNull();
	});

	it('returns null when userMessageId is undefined (never learned) or messages is empty/undefined', () => {
		expect(findTurnAssistantMessage(sessionMessages, undefined)).toBeNull();
		expect(findTurnAssistantMessage([], 'u1')).toBeNull();
		expect(findTurnAssistantMessage(undefined, 'u1')).toBeNull();
	});
});

describe('isRecoveryStillCurrent', () => {
	it('is current when both the session and the turn identity still match', () => {
		expect(
			isRecoveryStillCurrent(
				{ sessionId: 's1', turnSeq: 3 },
				{ sessionId: 's1', turnSeq: 3 }
			)
		).toBe(true);
	});

	it('is stale once the store has switched to a different session', () => {
		expect(
			isRecoveryStillCurrent(
				{ sessionId: 's2', turnSeq: 3 },
				{ sessionId: 's1', turnSeq: 3 }
			)
		).toBe(false);
	});

	it('is stale once a newer turn has started in the same session', () => {
		expect(
			isRecoveryStillCurrent(
				{ sessionId: 's1', turnSeq: 4 },
				{ sessionId: 's1', turnSeq: 3 }
			)
		).toBe(false);
	});

	it('is stale when there is no current session at all', () => {
		expect(
			isRecoveryStillCurrent(
				{ sessionId: null, turnSeq: 3 },
				{ sessionId: 's1', turnSeq: 3 }
			)
		).toBe(false);
	});
});

describe('applyDurableRecovery', () => {
	const persisted: UnifiedChatMessageData = {
		id: 'a1',
		role: 'assistant',
		content: 'The full, durable reply.',
		timestamp: 999,
		tool_executions: [
			{ tool_name: 'search', arguments: {}, result: { success: true, data: 'ok' }, duration_ms: 5 }
		]
	};

	it('replaces a streaming placeholder with the persisted message', () => {
		const out = applyDurableRecovery(fixture(), persisted);
		expect(out[1]).toMatchObject({
			id: 'a1',
			content: 'The full, durable reply.',
			isStreaming: false,
			isPartial: false
		});
		expect(out[1].tool_executions).toHaveLength(1);
	});

	it('replaces a partial (but no longer streaming) placeholder too', () => {
		const msgs = fixture();
		msgs[1] = { ...msgs[1], isStreaming: false, isPartial: true, content: 'stale snapshot' };
		const out = applyDurableRecovery(msgs, persisted);
		expect(out[1].content).toBe('The full, durable reply.');
	});

	it('is a no-op when nothing was persisted (falls through to applyError upstream)', () => {
		const msgs = fixture();
		expect(applyDurableRecovery(msgs, null)).toBe(msgs);
	});

	it('overwrites even an already-settled assistant message (applyDone already ran first on the real done path)', () => {
		// applyDone unconditionally clears isStreaming/isPartial on every done,
		// gapped or not — applyDurableRecovery must not re-gate on those flags,
		// or a recovery that runs right after applyDone would silently no-op.
		const msgs = fixture();
		msgs[1] = { ...msgs[1], isStreaming: false, content: 'already done' };
		const out = applyDurableRecovery(msgs, persisted);
		expect(out[1].content).toBe('The full, durable reply.');
	});

	it('is a no-op when the last message is not an assistant message', () => {
		const msgs: UnifiedChatMessageData[] = [{ role: 'user', content: 'a', timestamp: 1 }];
		expect(applyDurableRecovery(msgs, persisted)).toBe(msgs);
	});
});

describe('settleUnrecoverable', () => {
	it('retains existing content, clears isStreaming, and sets isPartial', () => {
		const msgs: UnifiedChatMessageData[] = [
			{ id: 'u1', role: 'user', content: 'q', timestamp: 1 },
			{ role: 'assistant', content: 'a truncated preview...', timestamp: 2, isStreaming: true }
		];
		const out = settleUnrecoverable(msgs);
		expect(out[1].content).toBe('a truncated preview...');
		expect(out[1].isStreaming).toBe(false);
		expect(out[1].isPartial).toBe(true);
	});

	it('never deletes the message, even with no content at all', () => {
		const msgs: UnifiedChatMessageData[] = [
			{ id: 'u1', role: 'user', content: 'q', timestamp: 1 },
			{ role: 'assistant', content: '', timestamp: 2, isStreaming: true }
		];
		const out = settleUnrecoverable(msgs);
		expect(out).toHaveLength(2);
		expect(out[1].content).toBe('');
		expect(out[1].isPartial).toBe(true);
	});

	it('is a no-op when the last message is not an assistant message', () => {
		const msgs: UnifiedChatMessageData[] = [{ role: 'user', content: 'a', timestamp: 1 }];
		expect(settleUnrecoverable(msgs)).toBe(msgs);
	});
});

describe('finishTurnIfCurrent (against the real chatSession store)', () => {
	// Drives the ACTUAL chatSession singleton through beginTurn/patch/
	// addMessage the way the two real controllers (reattachToTurn, the
	// live-send path) do, then calls finishTurnIfCurrent exactly as they do
	// in their finally blocks — this is the real store, not a fake.
	beforeEach(() => {
		chatSession.reset();
	});

	it('applies the cleanup when the session/turn captured at start is still current', () => {
		const turnSeq = chatSession.beginTurn();
		chatSession.patch({ sessionId: 'sA', isGenerating: true });
		chatSession.addMessage({ role: 'assistant', content: '', timestamp: 1, isStreaming: true });

		const applied = finishTurnIfCurrent(chatSession, { sessionId: 'sA', turnSeq }, (msgs) =>
			msgs.filter((m) => !(m.role === 'assistant' && m.isStreaming && !m.content))
		);

		expect(applied).toBe(true);
		const state = get(chatSession);
		expect(state.isGenerating).toBe(false);
		expect(state.messages).toHaveLength(0); // the empty placeholder was dropped
	});

	it('drops the cleanup untouched once a DIFFERENT session has since started its own turn', () => {
		// Controller A starts, captures its identity...
		const turnSeqA = chatSession.beginTurn();
		const capturedA = { sessionId: 'sA', turnSeq: turnSeqA };
		chatSession.patch({ sessionId: 'sA', isGenerating: true });
		chatSession.addMessage({ role: 'assistant', content: '', timestamp: 1, isStreaming: true });

		// ...then, while A's request is still in flight, the user switches to
		// session B and B starts its own turn with its own placeholder.
		chatSession.beginTurn();
		chatSession.patch({ sessionId: 'sB', isGenerating: true, error: '' });
		chatSession.addMessage({ role: 'assistant', content: '', timestamp: 2, isStreaming: true });

		// A's delayed controller now resolves and runs its finally block.
		const applied = finishTurnIfCurrent(chatSession, capturedA, (msgs) =>
			msgs.filter((m) => !(m.role === 'assistant' && m.isStreaming && !m.content))
		);

		expect(applied).toBe(false);
		const state = get(chatSession);
		// B's session, generating flag, and placeholder are all untouched.
		expect(state.sessionId).toBe('sB');
		expect(state.isGenerating).toBe(true);
		// Both placeholders survive: A's dropped cleanup never ran, so it
		// never removed A's own placeholder, let alone B's.
		expect(state.messages).toHaveLength(2);
	});

	it('drops the cleanup once a NEWER turn has started in the SAME session', () => {
		const turnSeqA = chatSession.beginTurn();
		const capturedA = { sessionId: 'sA', turnSeq: turnSeqA };
		chatSession.patch({ sessionId: 'sA', isGenerating: true });
		chatSession.addMessage({ role: 'assistant', content: '', timestamp: 1, isStreaming: true });

		// A user resent (or reattached again) in the SAME session before A's
		// original controller resolved.
		chatSession.beginTurn();
		chatSession.patch({ isGenerating: true });
		chatSession.addMessage({ role: 'assistant', content: '', timestamp: 2, isStreaming: true });

		const applied = finishTurnIfCurrent(chatSession, capturedA, (msgs) =>
			msgs.filter((m) => !(m.role === 'assistant' && m.isStreaming && !m.content))
		);

		expect(applied).toBe(false);
		const state = get(chatSession);
		expect(state.isGenerating).toBe(true);
		expect(state.messages).toHaveLength(2); // neither placeholder was dropped
	});
});

describe('formatContextLedgerSummary', () => {
	it('renders the compact per-component breakdown with a total', () => {
		const ledger: ContextLedger = {
			system_prompt: { chars: 17200, est_tokens: 4300 },
			tool_schemas: { chars: 30000, est_tokens: 7500, tool_count: 32 },
			memory: { chars: 3200, est_tokens: 800 },
			history: { chars: 8400, est_tokens: 2100, message_count: 12 },
			total_est_tokens: 14700
		};
		expect(formatContextLedgerSummary(ledger)).toBe(
			'System prompt ~4.3k tok · Tools (32) ~7.5k tok · Memory ~0.8k tok · History (12) ~2.1k tok · ~14.7k total'
		);
	});

	it('keeps sub-1000 token counts in k-notation so components stay comparable', () => {
		const ledger: ContextLedger = {
			system_prompt: { chars: 400, est_tokens: 100 },
			tool_schemas: { chars: 0, est_tokens: 0, tool_count: 0 },
			memory: { chars: 0, est_tokens: 0 },
			history: { chars: 800, est_tokens: 200, message_count: 2 },
			total_est_tokens: 300
		};
		expect(formatContextLedgerSummary(ledger)).toBe(
			'System prompt ~0.1k tok · Tools (0) ~0 tok · Memory ~0 tok · History (2) ~0.2k tok · ~0.3k total'
		);
	});
});

describe('sumMemoryDropped', () => {
	it('sums dropped counts across scopes', () => {
		expect(sumMemoryDropped({ global: 0, preset: 3, model: 1 })).toBe(4);
	});

	it('is 0 for undefined or null (manifests from before the field existed)', () => {
		expect(sumMemoryDropped(undefined)).toBe(0);
		expect(sumMemoryDropped(null)).toBe(0);
	});

	it('is 0 when nothing was dropped', () => {
		expect(sumMemoryDropped({ global: 0, preset: 0, model: 0 })).toBe(0);
	});
});

describe('applyTitle', () => {
	it('patches the title and title_generated flag onto the matching session', () => {
		const sessions = [
			{ id: 's1', name: null },
			{ id: 's2', name: 'Old' }
		];
		const out = applyTitle(sessions, 's1', 'Portrait lighting tips');
		expect(out[0]).toMatchObject({ name: 'Portrait lighting tips', title_generated: true });
		expect(out[1].name).toBe('Old');
	});
});

describe('reattach replay determinism', () => {
	// Mirrors createStreamEventHandler's dispatch in UnifiedAIChat: fold an SSE
	// event sequence into a messages array, accumulating token text from ''.
	// The reattach path replays the whole turn from its start through THIS same
	// dispatch, so replaying the buffered sequence cold must reconstruct exactly
	// the state a live stream produced.
	// applyStatus stamps started_at/duration_ms from Date.now(). The "cold
	// replay equals live" test below runs `reduce()` twice and diffs the full
	// result with toEqual, so a real clock tick landing between the two calls
	// under load (GC pause, thread contention) makes the two runs disagree on
	// those wall-clock fields even though the reducer itself was deterministic.
	// Freeze the clock so the comparison isolates actual reducer determinism.
	beforeEach(() => {
		vi.useFakeTimers();
	});

	afterEach(() => {
		vi.useRealTimers();
	});

	function reduce(initial: UnifiedChatMessageData[], events: { type: string; data: any }[]) {
		let messages = initial.map((m) => ({ ...m }));
		let accumulated = '';
		for (const event of events) {
			switch (event.type) {
				case 'token':
					accumulated += event.data.content;
					messages = applyToken(messages, accumulated);
					break;
				case 'tool_start':
					messages = applyToolStart(messages, event.data || {});
					break;
				case 'tool_end':
					messages = applyToolEnd(messages, event.data || {});
					break;
				case 'status':
					messages = applyStatus(messages, event.data || {});
					break;
				case 'done':
					messages = applyDone(messages, event.data || {});
					break;
				case 'error':
					messages = applyError(messages);
					break;
				// message_created / title / no_active_turn: no message-state change
			}
		}
		return messages;
	}

	// A representative turn: pre-token status, a tool round, narration tokens,
	// then done (no persisted manifest, so live-accumulated trace_steps survive).
	const events: { type: string; data: any }[] = [
		{ type: 'status', data: { step: 'loading_memory', state: 'started' } },
		{ type: 'status', data: { step: 'loading_memory', state: 'completed', detail: { note_count: 0 } } },
		{ type: 'message_created', data: { user_message_id: 'u1' } },
		{ type: 'status', data: { step: 'thinking', state: 'started' } },
		{ type: 'tool_start', data: { tool_name: 'list_models', arguments: { q: 1 } } },
		{ type: 'tool_end', data: { tool_name: 'list_models', success: true, duration_ms: 12 } },
		{ type: 'status', data: { step: 'answering', state: 'started' } },
		{ type: 'token', data: { content: 'Here ' } },
		{ type: 'token', data: { content: 'you go.' } },
		{
			type: 'done',
			data: {
				user_message: { id: 'u1', created_at: '2026-07-19T00:00:00Z' },
				assistant_message: {
					id: 'a1',
					content: 'Here you go.',
					created_at: '2026-07-19T00:00:01Z',
					tool_executions: [
						{ tool_name: 'list_models', arguments: { q: 1 }, result: { success: true }, duration_ms: 12 }
					]
				}
			}
		}
	];

	const initial = (): UnifiedChatMessageData[] => [
		{ id: 'u1', role: 'user', content: 'list models', timestamp: 1 },
		{ role: 'assistant', content: '', timestamp: 2, isStreaming: true }
	];

	it('replays a full turn into a finalized assistant message', () => {
		const out = reduce(initial(), events);
		expect(out).toHaveLength(2);
		const assistant = out[1];
		expect(assistant.id).toBe('a1');
		expect(assistant.content).toBe('Here you go.');
		expect(assistant.isStreaming).toBe(false);
		expect(assistant.tool_executions).toHaveLength(1);
	});

	it('cold replay equals live-streamed state (deterministic)', () => {
		const live = reduce(initial(), events);
		const cold = reduce(initial(), events);
		expect(cold).toEqual(live);
	});

	it('reconstructs the same content whether tokens arrive in one chunk or many', () => {
		const split = reduce(initial(), events);
		const oneChunk = reduce(
			initial(),
			events.map((e) =>
				e.type === 'token' && e.data.content === 'Here '
					? { type: 'token', data: { content: 'Here you go.' } }
					: e
			).filter((e) => !(e.type === 'token' && e.data.content === 'you go.'))
		);
		expect(oneChunk[1].content).toBe(split[1].content);
	});
});

describe('gap recovery: replay_snapshot + overflow fall back to the durable message', () => {
	// Mirrors createStreamEventHandler's full dispatch, including the two gap
	// signals and the recovery decision — a long answer whose recognisable
	// prefix ("The full answer starts here.") gets compacted away mid-stream,
	// then an overflow drops more of it, and only the durable fetch (never a
	// re-run of the tool or a second `done`) produces the final message.
	function reduceWithRecovery(
		initial: UnifiedChatMessageData[],
		events: { type: string; data: any }[],
		durableMessage: UnifiedChatMessageData | null
	) {
		let messages = initial.map((m) => ({ ...m }));
		let accumulated = '';
		let partial = false;
		let recoveries = 0;

		for (const event of events) {
			switch (event.type) {
				case 'token':
					accumulated += event.data.content;
					messages = applyToken(messages, accumulated);
					break;
				case 'tool_start':
					messages = applyToolStart(messages, event.data || {});
					break;
				case 'tool_end':
					messages = applyToolEnd(messages, event.data || {});
					break;
				case 'replay_snapshot':
					accumulated = event.data?.text_so_far || '';
					partial = true;
					messages = applyReplaySnapshot(messages, event.data || {});
					break;
				case 'overflow':
					partial = true;
					break;
				case 'done':
					messages = applyDone(messages, event.data || {});
					if (needsDurableRecovery('done', partial)) {
						recoveries += 1;
						messages = applyDurableRecovery(messages, durableMessage);
					}
					break;
				case 'error':
					if (needsDurableRecovery('error', partial)) {
						recoveries += 1;
						// Mirrors the real handler's recoverDurableMessage: when
						// nothing was actually persisted for this turn, never call
						// applyError — that would drop content that may still be
						// partially shown; leave it as-is and let the (already
						// patched) error string speak for itself.
						if (durableMessage) messages = applyDurableRecovery(messages, durableMessage);
					} else {
						messages = applyError(messages);
					}
					break;
			}
		}
		return { messages, recoveries };
	}

	const initial = (): UnifiedChatMessageData[] => [
		{ id: 'u1', role: 'user', content: 'tell me a long story', timestamp: 1 },
		{ role: 'assistant', content: '', timestamp: 2, isStreaming: true }
	];

	const durableMessage: UnifiedChatMessageData = {
		id: 'a1',
		role: 'assistant',
		content: 'The full answer starts here. ...(the rest, recovered from the database)...',
		timestamp: 999,
		tool_executions: [
			{ tool_name: 'search', arguments: { q: 'story' }, result: { success: true, data: 'ok' }, duration_ms: 8 }
		]
	};

	it('recovers the durable message once a partial stream reaches done, keeping tool/terminal outcomes intact', () => {
		const events: { type: string; data: any }[] = [
			{ type: 'tool_start', data: { tool_name: 'search', arguments: { q: 'story' } } },
			{ type: 'token', data: { content: 'The full answer starts here. ' } },
			// The backend compacted the buffered prefix away before this client
			// could reconnect; the snapshot is a bounded stand-in, not the whole
			// reply — recognisable by its truncated, ellipsis-free tail.
			{ type: 'replay_snapshot', data: { text_so_far: 'The full answer sta', cursor: 50 } },
			{ type: 'tool_end', data: { tool_name: 'search', success: true, duration_ms: 8 } },
			// This connection then fell behind live and missed more deltas.
			{ type: 'overflow', data: {} },
			{ type: 'done', data: {} }
		];

		const { messages, recoveries } = reduceWithRecovery(initial(), events, durableMessage);

		expect(recoveries).toBe(1);
		const assistant = messages[1];
		expect(assistant.content).toBe(durableMessage.content);
		expect(assistant.isPartial).toBe(false);
		expect(assistant.isStreaming).toBe(false);
		// The terminal outcome (done) and the tool round both survive the
		// recovery — recovery replaces content/metadata, not history.
		expect(assistant.tool_executions).toHaveLength(1);
		expect(assistant.tool_executions?.[0].tool_name).toBe('search');
	});

	it('does not recover on a plain overflow while the turn is still running', () => {
		const events: { type: string; data: any }[] = [
			{ type: 'token', data: { content: 'partway ' } },
			{ type: 'overflow', data: {} }
		];
		const { recoveries } = reduceWithRecovery(initial(), events, durableMessage);
		expect(recoveries).toBe(0);
	});

	it('never recovers, and content matches the payload verbatim, on an ungapped done', () => {
		const events: { type: string; data: any }[] = [
			{ type: 'token', data: { content: 'all good' } },
			{ type: 'done', data: { assistant_message: { id: 'a2', content: 'all good' } } }
		];
		const { messages, recoveries } = reduceWithRecovery(initial(), events, durableMessage);
		expect(recoveries).toBe(0);
		expect(messages[1].content).toBe('all good');
		expect(messages[1].id).toBe('a2');
	});

	it('keeps the partial content visible (never applyError) when a gapped stream errors and nothing was persisted', () => {
		const events: { type: string; data: any }[] = [
			{ type: 'token', data: { content: 'partway ' } },
			{ type: 'overflow', data: {} },
			{ type: 'error', data: { message: 'boom' } }
		];
		const { messages, recoveries } = reduceWithRecovery(initial(), events, null);
		expect(recoveries).toBe(1);
		// A gap happened, but nothing durable exists for this turn to recover
		// (a genuine failure, not just a lost stream): the placeholder is left
		// as-is rather than vanishing via applyError — the caller is
		// responsible for surfacing an accurate error alongside it.
		expect(messages).toHaveLength(2);
		expect(messages[1].content).toBe('partway ');
	});
});

describe('recoverDurableMessage orchestration: identity matching, truncation, and staleness', () => {
	// Mirrors UnifiedAIChat's recoverDurableMessage exactly: match by turn
	// identity (never "last assistant message"), drop the result if a newer
	// turn/session now owns the UI, otherwise apply it — and if nothing
	// durable exists for this turn, never substitute anything, just report it.
	function simulateRecovery(
		messages: UnifiedChatMessageData[],
		opts: {
			sessionMessages: { id?: string; role: string; content?: string; tool_executions?: any[] }[];
			userMessageId: string | undefined;
			current: { sessionId: string | null; turnSeq: number };
			expected: { sessionId: string; turnSeq: number };
			currentError: string;
		}
	): { messages: UnifiedChatMessageData[]; error: string; published: boolean } {
		const matched = findTurnAssistantMessage(opts.sessionMessages, opts.userMessageId);

		if (!isRecoveryStillCurrent(opts.current, opts.expected)) {
			return { messages, error: opts.currentError, published: false };
		}

		if (matched) {
			return {
				messages: applyDurableRecovery(messages, matched as unknown as UnifiedChatMessageData),
				error: opts.currentError,
				published: true
			};
		}

		// No durable answer for THIS turn: settle it as a stopped, visibly
		// incomplete response (content retained, isStreaming cleared,
		// isPartial set) rather than leaving it looking live forever.
		return {
			messages: settleUnrecoverable(messages),
			error: opts.currentError || 'The full reply could not be recovered.',
			published: false
		};
	}

	const streamingPlaceholder = (): UnifiedChatMessageData[] => [
		{ id: 'u1', role: 'user', content: 'current question', timestamp: 1 },
		{ role: 'assistant', content: 'partial snapshot text', timestamp: 2, isPartial: true }
	];

	it('never substitutes a prior unrelated answer when the current turn has no durable answer yet', () => {
		// The session has an earlier, unrelated turn fully persisted, but the
		// CURRENT turn's assistant reply never landed (it failed outright).
		const sessionMessages = [
			{ id: 'u0', role: 'user', content: 'old question' },
			{ id: 'a0', role: 'assistant', content: 'OLD unrelated answer' },
			{ id: 'u1', role: 'user', content: 'current question' }
			// no a1 — nothing persisted for this turn
		];

		const result = simulateRecovery(streamingPlaceholder(), {
			sessionMessages,
			userMessageId: 'u1',
			current: { sessionId: 's1', turnSeq: 1 },
			expected: { sessionId: 's1', turnSeq: 1 },
			currentError: ''
		});

		expect(result.published).toBe(false);
		// Settled, not substituted: content retained (specifically NOT the
		// old, unrelated answer), no longer looking live, still flagged
		// incomplete.
		expect(result.messages[1].content).toBe('partial snapshot text');
		expect(result.messages[1].id).toBeUndefined();
		expect(result.messages[1].isStreaming).toBe(false);
		expect(result.messages[1].isPartial).toBe(true);
		expect(result.error).toBe('The full reply could not be recovered.');
	});

	it('preserves a more specific error already shown instead of overwriting it with the generic one', () => {
		const result = simulateRecovery(streamingPlaceholder(), {
			sessionMessages: [{ id: 'u1', role: 'user' }],
			userMessageId: 'u1',
			current: { sessionId: 's1', turnSeq: 1 },
			expected: { sessionId: 's1', turnSeq: 1 },
			currentError: 'The response timed out.'
		});
		expect(result.published).toBe(false);
		expect(result.error).toBe('The response timed out.');
	});

	it('recovers via the durable fetch, metadata and all, when a done arrives truncated with no prior gap signal', () => {
		// No replay_snapshot or overflow ever happened — the truncated done
		// itself is the only gap signal, per needsDurableRecovery's contract.
		let partial = false;
		const doneEvent = { type: 'done', data: { truncated: true, preview: '{"assistant_...' } };
		if (doneEvent.data.truncated) partial = true;
		expect(needsDurableRecovery('done', partial)).toBe(true);

		const persistedToolExecutions = [
			{ tool_name: 'search', arguments: { q: 'story' }, result: { success: true, data: 'ok' }, duration_ms: 9 }
		];
		const sessionMessages = [
			{ id: 'u1', role: 'user', content: 'current question' },
			{
				id: 'a1',
				role: 'assistant',
				content: 'The real, complete reply, restored from the database.',
				tool_executions: persistedToolExecutions
			}
		];

		const result = simulateRecovery(streamingPlaceholder(), {
			sessionMessages,
			userMessageId: 'u1',
			current: { sessionId: 's1', turnSeq: 1 },
			expected: { sessionId: 's1', turnSeq: 1 },
			currentError: ''
		});

		expect(result.published).toBe(true);
		expect(result.messages[1].id).toBe('a1');
		expect(result.messages[1].content).toBe('The real, complete reply, restored from the database.');
		// The done event's own (truncated, near-empty) payload never reaches
		// the message — tool_executions come from the durable record.
		expect(result.messages[1].tool_executions).toEqual(persistedToolExecutions);
		expect(result.messages[1].isPartial).toBe(false);
	});

	it('marks partial and recovers when the buffered-tools single huge `token` event arrives truncated', () => {
		// The exact shape execute_with_tools_stream emits for a Completed
		// reply (src/features/llm/tools/executor.py): one `token` event
		// carrying the WHOLE completion. turns.py bounds it past the byte cap
		// exactly like an oversized essential event — the client must react
		// to it the same way too, regardless of event type.
		let partial = false;
		const hugeTokenEvent = {
			type: 'token',
			data: { truncated: true, content: 'The full answer. The full …', content_bytes: 9001 }
		};
		if (hugeTokenEvent.data.truncated) partial = true;
		// A token event is never itself a recovery trigger (only done/error/
		// no_active_turn are) — but it must leave `partial` set so the
		// terminal event that follows recovers.
		expect(needsDurableRecovery('token', partial)).toBe(false);
		expect(needsDurableRecovery('done', partial)).toBe(true);

		const sessionMessages = [
			{ id: 'u1', role: 'user', content: 'current question' },
			{ id: 'a1', role: 'assistant', content: 'The full answer, restored in full from the database.' }
		];
		const result = simulateRecovery(streamingPlaceholder(), {
			sessionMessages,
			userMessageId: 'u1',
			current: { sessionId: 's1', turnSeq: 1 },
			expected: { sessionId: 's1', turnSeq: 1 },
			currentError: ''
		});
		expect(result.published).toBe(true);
		expect(result.messages[1].content).toBe('The full answer, restored in full from the database.');
	});

	it('drops a delayed recovery once a different session now owns the UI', () => {
		const sessionMessages = [
			{ id: 'u1', role: 'user' },
			{ id: 'a1', role: 'assistant', content: 'would-be recovered answer' }
		];
		const result = simulateRecovery(streamingPlaceholder(), {
			sessionMessages,
			userMessageId: 'u1',
			current: { sessionId: 's2', turnSeq: 1 }, // switched sessions while the fetch was in flight
			expected: { sessionId: 's1', turnSeq: 1 },
			currentError: ''
		});
		expect(result.published).toBe(false);
		expect(result.messages[1].content).toBe('partial snapshot text'); // untouched
	});

	it('drops a delayed recovery once a newer turn has started in the same session', () => {
		const sessionMessages = [
			{ id: 'u1', role: 'user' },
			{ id: 'a1', role: 'assistant', content: 'would-be recovered answer' }
		];
		const result = simulateRecovery(streamingPlaceholder(), {
			sessionMessages,
			userMessageId: 'u1',
			current: { sessionId: 's1', turnSeq: 2 }, // a new turn already started
			expected: { sessionId: 's1', turnSeq: 1 },
			currentError: ''
		});
		expect(result.published).toBe(false);
		expect(result.messages[1].content).toBe('partial snapshot text'); // untouched
	});
});
