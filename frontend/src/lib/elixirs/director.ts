import { nodeLabel, nodePorts } from './model';
import type { ElixirDefinition, ElixirNode, PresetIndex, ValidationIssue } from './types';

const SINGLE_SHOT = new Set(['t2v', 'i2v', 'flf']);

export interface DirectorDoc {
	mode?: string;
	segments?: unknown[];
}

export interface DirectorTarget {
	node: ElixirNode;
	role: string;
	label: string;
}

export interface PreparedLike {
	error?: string;
	request?: { form_data?: { video_director?: DirectorDoc | null } & Record<string, unknown> };
}

export function directorProblem(doc: DirectorDoc | null | undefined, role: string, subject: string): string | null {
	if (!doc || typeof doc !== 'object') return `${subject} has no Video Director to put the image into.`;
	const segments = (doc.segments || []).filter((segment) => segment && typeof segment === 'object');
	if (segments.length > 1) {
		return `${subject} has ${segments.length} shots. Elixirs can feed an image into a single-shot session only.`;
	}
	if (!SINGLE_SHOT.has(doc.mode as string)) {
		return `${subject} uses keyframes, audio or other timed parts. Elixirs can feed an image into a plain single-shot session only.`;
	}
	if (role === 'last' && doc.mode === 't2v') {
		return `${subject} has no first frame. A last frame needs one, so add a first frame in the session.`;
	}
	return null;
}

export function directorTargets(definition: ElixirDefinition | null | undefined, presetIndex: PresetIndex): DirectorTarget[] {
	const targets: DirectorTarget[] = [];
	for (const edge of definition?.edges ?? []) {
		const node = definition?.nodes.find((item) => item.id === edge.target);
		if (!node || node.kind !== 'preset' || !node.session_snapshot) continue;
		const input = nodePorts(node, presetIndex).inputs[edge.target_handle];
		if (input?.target !== 'video_director_role' || !input.role) continue;
		targets.push({ node, role: input.role, label: nodeLabel(node, presetIndex) });
	}
	return targets;
}

export async function directorIssues(
	definition: ElixirDefinition | null | undefined,
	presetIndex: PresetIndex,
	prepare: (node: ElixirNode) => PreparedLike | null | undefined | Promise<PreparedLike | null | undefined>
): Promise<ValidationIssue[]> {
	const issues: ValidationIssue[] = [];
	for (const { node, role, label } of directorTargets(definition, presetIndex)) {
		const prepared = await prepare(node);
		if (!prepared?.request) continue;
		const message = directorProblem(prepared.request.form_data?.video_director, role, `${label}'s session`);
		if (message) issues.push({ code: 'director_unsupported', message, severity: 'error', node_id: node.id });
	}
	return issues;
}
