export type StackFoldName = 'layers' | 'edits';
export type StackFolds = Record<StackFoldName, boolean>;

export const STACK_FOLDS_KEY = 'potionui:image-editor:stack-folds';

export function readStackFolds(): StackFolds {
	const open: StackFolds = { layers: false, edits: false };
	try {
		const parsed = JSON.parse(localStorage.getItem(STACK_FOLDS_KEY) ?? 'null');
		if (!parsed || typeof parsed !== 'object') return open;
		return { layers: parsed.layers === true, edits: parsed.edits === true };
	} catch {
		return open;
	}
}

export function writeStackFolds(folds: StackFolds): void {
	try {
		localStorage.setItem(STACK_FOLDS_KEY, JSON.stringify(folds));
	} catch {
		return;
	}
}
