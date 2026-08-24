import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
	applyToken,
	applyToolStart,
	applyToolEnd,
	applyStatus,
	applyDone,
	applyError,
	applyTitle,
	mergeTraceTimeline,
	hydrateTraceSteps,
	formatContextLedgerSummary,
	sumMemoryDropped
} from './chatStream';
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
