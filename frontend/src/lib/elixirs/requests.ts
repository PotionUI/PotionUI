import { prepareRequestFromSession, type PreparedRequest } from '$lib/generation/prepareRequest';
import { templateSource } from './follow';
import type { ElixirNode, FollowedState, NodeOverrides } from './types';

export interface Randomness {
	random: () => number;
	now: () => number;
}

const FIXED: Randomness = { random: () => 0, now: () => 0 };

const REASONS: Record<string, string> = {
	no_preset: 'Choose a preset for this node first.',
	no_prompt: 'The copied session has no prompt for this mode. Add one in the session and copy it again.',
	director: 'The Video Director settings in this session are not ready to brew.'
};

export type NodeRequestResult =
	| Extract<PreparedRequest, { ok: true }>
	| { ok: false; code: string; message: string; reason?: string | null };

export interface PrepareNodeOptions {
	promptConnected?: boolean;
	followed?: FollowedState | null;
}

export interface RequestSummary {
	prompts: string[];
	negative: string;
	fields: number;
	seed: unknown;
	variables: number;
	segments: number;
	director: string | null;
}

interface SummarySource {
	form_data?: Record<string, unknown> & { video_director?: { mode?: string } | null };
	prompts?: Array<{ positive?: string; negative?: string }>;
	variables?: Record<string, unknown>;
	segments?: unknown[];
}

export function canPrepare(): boolean {
	return true;
}

export const WRITTEN_PROMPT_STANDIN = 'Written prompt';

export function prepareNodeRequest(
	node: ElixirNode,
	presetVars: Record<string, unknown> | null | undefined,
	randomness: Randomness = FIXED,
	{ promptConnected = false, followed = undefined }: PrepareNodeOptions = {}
): NodeRequestResult | null {
	if (!canPrepare()) return null;
	const source = templateSource(node, followed);
	if (!source) return null;
	if ('error' in source) return { ok: false, code: 'session', message: source.error };
	const prepare = (overrides: NodeOverrides) =>
		prepareRequestFromSession({
			presetId: node.preset_id as string,
			mode: node.mode as string,
			session: source.session,
			overrides,
			presetVars: presetVars || {},
			tabId: node.id,
			...randomness
		});
	let result = prepare(source.overrides);
	if (!result.ok && result.code === 'no_prompt' && promptConnected) {
		result = prepare({ ...source.overrides, prompt: WRITTEN_PROMPT_STANDIN, promptSegments: [], promptTabs: undefined });
	}
	if (result.ok) return result;
	return { ...result, message: result.reason || REASONS[result.code] || 'This node cannot be prepared yet.' };
}

export function requestSummary(request: SummarySource): RequestSummary {
	const form = request.form_data || {};
	const prompts = request.prompts || [];
	return {
		prompts: prompts.map((pair) => pair.positive).filter((value): value is string => !!value),
		negative: prompts[0]?.negative || '',
		fields: Object.keys(form).length,
		seed: form.seed ?? null,
		variables: Object.keys(request.variables || {}).length,
		segments: (request.segments || []).length,
		director: form.video_director ? form.video_director.mode ?? 'on' : null
	};
}
