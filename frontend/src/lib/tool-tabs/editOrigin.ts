import { goto } from '$app/navigation';
import type { GenerationOrigin } from '$lib/types/history';
import { continueEditInNewTab, type ContinueOutcome } from './continueEdit';

export const ORIGIN_LABEL = 'Made in the editor';
export const DELETED_LABEL = 'The edit was deleted';

export interface EditorOrigin {
	editId: string;
	stepId: string | null;
	task: string | null;
	runId: string | null;
}

export function editorOriginOf(generation: { origin?: GenerationOrigin | null }): EditorOrigin | null {
	const origin = generation.origin;
	if (!origin || origin.kind !== 'editor') return null;
	if (typeof origin.edit_id !== 'string' || !origin.edit_id) return null;
	return {
		editId: origin.edit_id,
		stepId: typeof origin.step_id === 'string' ? origin.step_id : null,
		task: typeof origin.task === 'string' ? origin.task : null,
		runId: typeof origin.run_id === 'string' ? origin.run_id : null
	};
}

type Failure = Exclude<ContinueOutcome['kind'], 'opened' | 'focused'>;

export const OUTCOME_COPY: Record<Failure, string> = {
	missing: 'That edit no longer exists.',
	'source-missing': 'The picture for this edit is no longer available.',
	failed: 'Could not open this edit. Try again.'
};

export type OriginOpenResult = { ok: true } | { ok: false; kind: Failure; message: string };

export interface OriginOpenDeps {
	continueEdit: (editId: string) => Promise<ContinueOutcome>;
	goToGenerate: () => void;
}

export function defaultOriginOpenDeps(): OriginOpenDeps {
	return {
		continueEdit: (editId) => continueEditInNewTab(editId, null),
		goToGenerate: () => {
			if (typeof window === 'undefined' || window.location.pathname.startsWith('/generate')) return;
			setTimeout(() => void goto('/generate'), 0);
		}
	};
}

export async function openOriginEdit(origin: EditorOrigin, deps: OriginOpenDeps = defaultOriginOpenDeps()): Promise<OriginOpenResult> {
	const outcome = await deps.continueEdit(origin.editId);
	if (outcome.kind === 'opened' || outcome.kind === 'focused') {
		deps.goToGenerate();
		return { ok: true };
	}
	return { ok: false, kind: outcome.kind, message: OUTCOME_COPY[outcome.kind] };
}
