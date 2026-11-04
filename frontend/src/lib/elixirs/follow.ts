import type { VariablesMap } from '$lib/utils/variableDefs';
import { writerTarget } from './model';
import type { ElixirDefinition, ElixirNode, NodeLike, FollowedState, NodeOverrides, SessionSettings, ValidationIssue } from './types';

export interface SessionModeOption {
	id: string;
	label: string;
}

export const SESSION_MODES: SessionModeOption[] = [
	{ id: 'follow', label: 'Follow' },
	{ id: 'copy', label: 'Copy' },
	{ id: 'new', label: 'New' }
];

type MaybeNode = NodeLike | null | undefined;
type MaybeState = FollowedState | null | undefined;
type Issues = Array<Pick<ValidationIssue, 'code'>> | null | undefined;

const MODE_IDS = new Set(SESSION_MODES.map((item) => item.id));

const EXPLANATIONS: Record<string, string> = {
	follow: 'Settings live in the session. Edits there reach this step right away.',
	copy: 'A snapshot with your own edits. Changes to the session are ignored until you update.',
	new: 'This step keeps its own settings and uses no session.'
};

const ISSUE_STATUS: Record<string, string> = {
	followed_session_deleted: 'deleted',
	followed_session_unavailable: 'unavailable',
	followed_session_mismatch: 'mismatch',
	followed_session_mode_missing: 'mode_missing'
};

export const FOLLOW_ISSUE_CODES = new Set(Object.keys(ISSUE_STATUS));

const BROKEN: Record<string, { marker: string; title: string }> = {
	deleted: { marker: 'Session was deleted', title: 'Session was deleted' },
	unavailable: { marker: 'Session not available', title: 'Session is not available' },
	mismatch: { marker: 'Session is for another preset', title: 'Session belongs to another preset' },
	mode_missing: { marker: 'Session has no settings for this mode', title: 'Session has no settings for this mode' }
};

const PROBLEM_MESSAGE = 'This step uses the last settings it saw. Pick another session or keep a copy.';

export interface CardMarker {
	text: string;
	tone: string;
	icon: string | null;
}

export interface SessionProblem {
	status: string;
	title: string;
	message: string;
}

export type TemplateSource =
	| { session: SessionSettings; overrides: NodeOverrides; hash?: string }
	| { error: string };

export function sessionMode(node: MaybeNode): string | null {
	if (node?.kind !== 'preset') return null;
	if (node.session_mode && MODE_IDS.has(node.session_mode)) return node.session_mode;
	return node.session_source ? 'copy' : 'new';
}

export function modeExplanation(mode: string | null | undefined): string {
	return EXPLANATIONS[mode ?? ''] ?? '';
}

export function followedStatus(node: NodeLike, state?: MaybeState, issues: Issues = []): string | null {
	if (sessionMode(node) !== 'follow') return null;
	if (state?.status) return state.status;
	if (node.session_ref?.deleted) return 'deleted';
	for (const issue of issues ?? []) {
		if (ISSUE_STATUS[issue.code]) return ISSUE_STATUS[issue.code];
	}
	return 'ok';
}

export function cardMarker(node: MaybeNode, state?: MaybeState, issues: Issues = []): CardMarker | null {
	if (!node || node.kind !== 'preset' || !node.preset_id) return null;
	const mode = sessionMode(node);
	if (mode === 'follow') {
		const status = followedStatus(node, state, issues);
		if (status && BROKEN[status]) return { text: BROKEN[status].marker, tone: 'warning', icon: 'warning' };
		const name = state?.session?.name ?? node.session_ref?.name;
		return { text: name ? `Following ${name}` : 'Following a session', tone: 'signal', icon: 'plug' };
	}
	if (mode === 'copy') {
		const name = node.session_source?.name;
		return { text: name ? `Copy of ${name}` : 'Copy of a session', tone: 'muted', icon: 'document' };
	}
	return { text: 'Own settings', tone: 'subtle', icon: null };
}

export function sessionProblem(node: NodeLike, state?: MaybeState, issues: Issues = []): SessionProblem | null {
	const status = followedStatus(node, state, issues);
	if (!status || !BROKEN[status]) return null;
	return { status, title: BROKEN[status].title, message: PROBLEM_MESSAGE };
}

export function conversions(node: NodeLike): string[] {
	const mode = sessionMode(node);
	if (mode === 'follow') return ['copy'];
	const canSave = mode === 'copy' ? !!node.session_snapshot : Object.keys(node?.overrides ?? {}).length > 0;
	const actions: string[] = [];
	if (mode === 'copy' && node.session_source?.id) actions.push('update', 'follow');
	if (canSave) actions.push('save');
	return actions;
}

export function sessionRow(node: NodeLike, state?: MaybeState): { name: string; version: number | string | null } | null {
	const mode = sessionMode(node);
	if (mode === 'follow') {
		return { name: state?.session?.name ?? node.session_ref?.name ?? 'A session', version: state?.session?.version ?? node.session_ref?.version ?? null };
	}
	if (mode === 'copy' && node.session_source) {
		return { name: node.session_source.name ?? 'A session', version: node.session_source.version ?? null };
	}
	return null;
}

export function templateSource(node: MaybeNode, state?: MaybeState): TemplateSource | null {
	if (!node || node.kind !== 'preset' || !node.preset_id || !node.mode) return null;
	const mode = sessionMode(node);
	if (mode === 'follow') {
		if (state?.status === 'ok') return { session: state.settings as SessionSettings, overrides: {}, hash: state.session_hash };
		const problem = sessionProblem(node, state);
		if (problem) return { error: `${problem.title}. Pick another session or keep a copy.` };
		return { error: 'Its followed session could not be read. Try again.' };
	}
	if (mode === 'copy') return node.session_snapshot ? { session: node.session_snapshot, overrides: node.overrides || {} } : null;
	return { session: {}, overrides: node.overrides || {} };
}

export function sessionHashes(definition: Pick<ElixirDefinition, 'nodes'> | null | undefined, followed: Record<string, FollowedState> | null | undefined): Record<string, string> {
	const hashes: Record<string, string> = {};
	for (const node of definition?.nodes ?? []) {
		const state = followed?.[node.id];
		if (sessionMode(node) === 'follow' && state?.status === 'ok' && state.session_hash) hashes[node.id] = state.session_hash;
	}
	return hashes;
}

function withOverrides(snapshot: SessionSettings | null | undefined, overrides: NodeOverrides | null | undefined): SessionSettings {
	const effective: SessionSettings = { ...(snapshot || {}) };
	for (const [key, value] of Object.entries(overrides || {})) {
		effective[key] = key === 'formData' ? { ...(effective.formData || {}), ...((value as Record<string, unknown>) || {}) } : value;
	}
	return effective;
}

export function effectiveSettings(node: NodeLike, state?: MaybeState): SessionSettings | null {
	const mode = sessionMode(node);
	if (mode === 'follow') return state?.status === 'ok' ? state.settings ?? null : node.session_snapshot ?? null;
	if (mode === 'copy') return node.session_snapshot ? withOverrides(node.session_snapshot, node.overrides) : null;
	if (mode === 'new') return Object.keys(node.overrides ?? {}).length > 0 ? withOverrides({}, node.overrides) : null;
	return null;
}

export function staleReasonText(
	result: { stale?: boolean; stale_reason?: string | null; stale_node_id?: string | null } | null | undefined,
	nodeId: string,
	labelOf: (id: string) => string
): string | null {
	if (!result?.stale) return null;
	if (result.stale_reason !== 'session_changed') return 'An earlier step has newer outputs.';
	if (!result.stale_node_id || result.stale_node_id === nodeId) return 'Session changed';
	return `${labelOf(result.stale_node_id)} changed`;
}

export function uniqueSessionName(base: string | null | undefined, taken: Iterable<unknown> | null | undefined): string {
	const name = (base || '').trim() || 'Elixir step';
	const used = new Set([...(taken || [])].map((item) => String(item).trim().toLowerCase()));
	if (!used.has(name.toLowerCase())) return name;
	for (let index = 2; ; index += 1) {
		const candidate = `${name} ${index}`;
		if (!used.has(candidate.toLowerCase())) return candidate;
	}
}

export function writerVariables(definition: ElixirDefinition, node: ElixirNode, followed: Record<string, FollowedState | undefined>): VariablesMap {
	const targetId = writerTarget(definition, node);
	const target = targetId ? definition.nodes.find((item) => item.id === targetId) : null;
	const variables = target ? effectiveSettings(target, followed[target.id])?.variables : null;
	return variables && typeof variables === 'object' && !Array.isArray(variables) ? (variables as VariablesMap) : {};
}
