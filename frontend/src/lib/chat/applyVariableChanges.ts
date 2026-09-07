// Applies a validated `manage_prompt_variables` tool result to a tab's
// VariablesMap. Operation shape mirrors the `apply_variable_changes` payload
// built by src/features/llm/tools/builtin/prompt_variables_tool.py's
// execute_confirmed (snake_case keys, wire-crossing JSON) — same idiom as
// applyMusicDirectorOperations in $lib/utils/musicDirector.ts.

import {
	createChoiceVariable,
	createTextVariable,
	type ChoiceVariableMode,
	type VariablesMap
} from '$lib/utils/variableDefs';

export interface PromptVariableOperation {
	op: 'set' | 'remove';
	name: string;
	type?: 'text' | 'choice';
	value?: string;
	options?: string[];
	mode?: ChoiceVariableMode;
	pinned_index?: number | null;
}

/**
 * Pure reducer: `variables` plus a list of already-validated operations ->
 * the next `VariablesMap`. The backend is the source of truth for validity —
 * an operation this can't make sense of (missing name, unrecognized `op`) is
 * skipped rather than thrown on, since a plugin's `onToolApplied` handler or
 * a future op kind should degrade quietly here, not crash the chat.
 */
export function applyVariableChanges(
	variables: VariablesMap,
	operations: PromptVariableOperation[] | undefined
): VariablesMap {
	if (!operations || operations.length === 0) return variables;
	const next: VariablesMap = { ...variables };

	for (const op of operations) {
		if (!op || typeof op.name !== 'string' || !op.name) continue;

		if (op.op === 'remove') {
			delete next[op.name];
			continue;
		}

		if (op.op !== 'set') continue;

		if (op.type === 'choice') {
			const def = createChoiceVariable(op.options && op.options.length > 0 ? op.options : ['', '']);
			def.mode = op.mode ?? 'shuffle';
			def.pinnedIndex = def.mode === 'pin' ? (op.pinned_index ?? null) : null;
			next[op.name] = def;
		} else {
			next[op.name] = createTextVariable(op.value ?? '');
		}
	}

	return next;
}
