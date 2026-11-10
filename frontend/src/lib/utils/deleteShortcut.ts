import { tabDeleteTarget, type TabDeleteTarget } from '$lib/generation/deleteTabGeneration';
import type { GenerationTab } from '$lib/types/tabs';

export type DeleteKeyEvent = Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'altKey' | 'shiftKey' | 'defaultPrevented' | 'repeat'>;

export type SelectionDeleteIntent = { kind: 'single'; id: string } | { kind: 'bulk'; ids: string[] };

export function isDeleteKey(event: DeleteKeyEvent): boolean {
	if (event.defaultPrevented || event.repeat) return false;
	if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return false;
	return event.key === 'Delete';
}

export function selectionDeleteIntent(
	event: DeleteKeyEvent,
	ownsKeys: boolean,
	selectedIds: readonly string[]
): SelectionDeleteIntent | null {
	if (!isDeleteKey(event) || !ownsKeys) return null;
	const ids = [...new Set(selectedIds)];
	if (ids.length === 0) return null;
	if (ids.length === 1) return { kind: 'single', id: ids[0] };
	return { kind: 'bulk', ids };
}

export interface GenerateDeleteState {
	ownsKeys: boolean;
	repeat?: boolean;
	toolTabActive: boolean;
	showsGrid: boolean;
	deleteInFlight: boolean;
	tab: GenerationTab | null | undefined;
}

export function generateDeleteIntent(state: GenerateDeleteState): TabDeleteTarget | null {
	if (!state.ownsKeys || state.repeat || state.toolTabActive || state.showsGrid || state.deleteInFlight) return null;
	return tabDeleteTarget(state.tab);
}
