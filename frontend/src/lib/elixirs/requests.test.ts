import { beforeEach, expect, test, vi } from 'vitest';
import { canPrepare, prepareNodeRequest as prepareNode, requestSummary } from './requests';
import type { ElixirNode } from './types';

interface LooseResult {
	ok: boolean;
	request: any;
	message: string;
	code: string;
}

function prepareNodeRequest(...args: Parameters<typeof prepareNode>): LooseResult | null {
	return prepareNode(...args) as unknown as LooseResult | null;
}

const calls = vi.hoisted(() => [] as any[]);

vi.mock('$lib/generation/prepareRequest', () => ({
	prepareRequestFromSession: (input: any) => {
		calls.push(input);
		if (!input.session.prompt) return { ok: false, code: 'no_prompt', reason: null };
		return {
			ok: true,
			request: {
				prompts: [{ positive: input.session.prompt, negative: 'blur' }],
				form_data: { ...input.session.formData, ...(input.overrides.formData || {}) },
				variables: { mood: 'calm' },
				segments: [{}, {}]
			}
		};
	}
}));

beforeEach(() => {
	calls.length = 0;
});

const node: ElixirNode = {
	id: 'n1',
	kind: 'preset',
	preset_id: 'p1',
	mode: 'txt2img',
	session_mode: 'copy',
	session_snapshot: { prompt: 'a harbour', formData: { seed: 42, steps: 8 } },
	overrides: { formData: { seed: 777 } }
};

test('a node is prepared from its copied session, its local edits and the preset vars', () => {
	const result = prepareNodeRequest(node, { num_prompts: 1 })!;

	expect(result.ok).toBe(true);
	expect(calls.length).toBe(1);
	expect(calls[0].session).toStrictEqual(node.session_snapshot);
	expect(calls[0].overrides).toStrictEqual(node.overrides);
	expect(calls[0].presetVars).toStrictEqual({ num_prompts: 1 });
	expect(calls[0].tabId).toBe('n1');
	expect(calls[0].random()).toBe(0);
	expect(result.request.form_data).toStrictEqual({ seed: 777, steps: 8 });
});

test('a node that cannot be prepared explains why in plain words', () => {
	const result = prepareNodeRequest({ ...node, session_snapshot: { formData: {} } }, {})!;

	expect(result.ok).toBe(false);
	expect(result.message).toMatch(/no prompt for this mode/);
});

test('nothing is prepared for media sources or nodes without a session or preset, and preparing is always available', () => {
	expect(prepareNodeRequest({ ...node, kind: 'media' }, {})).toBe(null);
	expect(prepareNodeRequest({ ...node, session_mode: 'copy', session_snapshot: null }, {})).toBe(null);
	expect(prepareNodeRequest({ ...node, preset_id: null }, {})).toBe(null);
	expect(canPrepare()).toBe(true);
	expect(prepareNodeRequest(node, {})).not.toBe(null);
});

test('a followed node is prepared from the followed session settings, never from its stored snapshot', () => {
	const followed = { ...node, session_mode: 'follow', session_ref: { id: 's1', name: 'Orbit' }, overrides: {} };
	const state = { status: 'ok', settings: { prompt: 'fresh', formData: { steps: 30 } }, session_hash: 'h1' };
	const result = prepareNodeRequest(followed, {}, undefined, { followed: state })!;

	expect(result.ok).toBe(true);
	expect(calls[0].session).toStrictEqual(state.settings);
	expect(calls[0].overrides).toStrictEqual({});
});

test('a followed node whose session is gone or unread cannot be prepared', () => {
	const followed = { ...node, session_mode: 'follow', session_ref: { id: 's1', name: 'Orbit' } };

	const deleted = prepareNodeRequest(followed, {}, undefined, { followed: { status: 'deleted' } })!;
	expect(deleted.ok).toBe(false);
	expect(deleted.message).toMatch(/Session was deleted/);
	expect(prepareNodeRequest(followed, {})!.ok).toBe(false);
	expect(calls.length).toBe(0);
});

test('a node with its own settings is prepared from an empty session and its edits', () => {
	const own = { ...node, session_mode: 'new', session_snapshot: null, overrides: { prompt: 'own', formData: { steps: 4 } } };
	const result = prepareNodeRequest(own, {})!;

	expect(calls[0].session).toStrictEqual({});
	expect(calls[0].overrides).toStrictEqual(own.overrides);
	expect(result.ok).toBe(false);
	expect(result.message).toMatch(/no prompt/);
});

test('the summary counts what the request carries', () => {
	const summary = requestSummary({
		prompts: [{ positive: 'one', negative: 'blur' }, { positive: 'two', negative: '' }],
		form_data: { seed: 5, steps: 8, video_director: { mode: 'i2v' } },
		variables: { a: '1' },
		segments: [{}]
	});

	expect(summary).toStrictEqual({ prompts: ['one', 'two'], negative: 'blur', fields: 3, seed: 5, variables: 1, segments: 1, director: 'i2v' });
});
