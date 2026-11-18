import type { GenerationOrigin } from '$lib/types/history';
import type { Tab } from '$lib/types/tabs';
import { findTabFollowing } from '$lib/generation/elsewhere/select';
import { editorOriginOf, type EditorOrigin } from '$lib/tool-tabs/editOrigin';

export type OriginAction =
	| { kind: 'open-in-tab' }
	| { kind: 'go-to-tab'; tabId: string }
	| { kind: 'open-edit'; origin: EditorOrigin };

export const OPEN_IN_TAB_LABEL = 'Open in a tab';
export const GO_TO_TAB_LABEL = 'Go to its tab';
export const OPEN_EDIT_LABEL = 'Open the edit';

export function resolveOriginAction(
	generation: { id: string; origin?: GenerationOrigin | null },
	tabs: readonly Tab[]
): OriginAction | null {
	const kind = generation.origin?.kind;
	if (kind === 'mcp') return { kind: 'open-in-tab' };
	if (kind === 'chat') return null;
	if (kind === 'editor') {
		const origin = editorOriginOf(generation);
		return origin ? { kind: 'open-edit', origin } : null;
	}
	const tabId = findTabFollowing(tabs, generation.id);
	return tabId ? { kind: 'go-to-tab', tabId } : null;
}
