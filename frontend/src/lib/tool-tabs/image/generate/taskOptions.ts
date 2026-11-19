import type { TaskOffer } from '$lib/services/api/editorTasks';

export interface TaskHint {
	name: string;
	label: string;
}

export const INCLUDE_SHADOW = 'include_shadow';

export const TASK_HINTS: Readonly<Record<string, readonly TaskHint[]>> = {
	remove_object: [{ name: INCLUDE_SHADOW, label: 'Include its shadow' }]
};

export type TaskOptions = Record<string, unknown>;

export function hintsOf(taskId: string | null | undefined): readonly TaskHint[] {
	return (taskId && TASK_HINTS[taskId]) || [];
}

export function withOption(options: TaskOptions | undefined, name: string, on: boolean): TaskOptions {
	const next = { ...(options ?? {}) };
	if (on) next[name] = true;
	else delete next[name];
	return next;
}

export function runOptionsOf(options: TaskOptions | undefined): TaskOptions | undefined {
	const kept = Object.entries(options ?? {}).filter(([, value]) => value === true);
	return kept.length ? Object.fromEntries(kept) : undefined;
}

export function readOptions(raw: unknown): TaskOptions | undefined {
	if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return undefined;
	return runOptionsOf(raw as TaskOptions);
}

const VIA_LABELS: Readonly<Record<string, string>> = { edit_region: 'Edit a region' };

export function viaNote(offer: Pick<TaskOffer, 'via'> | null | undefined, labelOf?: (task: string) => string | null | undefined): string | null {
	const via = offer?.via;
	if (!via) return null;
	const label = labelOf?.(via) ?? VIA_LABELS[via];
	return label ? `Uses ${label} with a built-in instruction.` : null;
}

export function maskWhatOf(task: string | null | undefined): string | null {
	if (task === 'remove_object') return 'object you want removed';
	if (task === 'replace_background') return 'background you want replaced';
	return null;
}
