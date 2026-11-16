import { canContinueEdit, type MadeInEditor } from '$lib/library/libraryItemMeta';

export const CONTINUE_ACTION = 'continue-edit';

export function continuableLink(items: readonly { madeInEditor?: MadeInEditor | null }[]): MadeInEditor | null {
	if (items.length !== 1) return null;
	const link = items[0].madeInEditor;
	return canContinueEdit(link) ? link : null;
}
