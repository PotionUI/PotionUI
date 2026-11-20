import { confirmDialog } from '$lib/stores/confirm';
import { appliedCount, clearAll, type EditStackState } from './editStack';

export async function confirmRemoveAll(stack: EditStackState<unknown>): Promise<EditStackState<unknown> | null> {
	const count = appliedCount(stack);
	if (count === 0) return null;
	const confirmed = await confirmDialog({
		title: 'Remove all edits?',
		message: `${count} ${count === 1 ? 'edit' : 'edits'} will be removed from this picture. Undo brings ${count === 1 ? 'it' : 'them'} back while this editor stays open.`,
		variant: 'danger',
		confirmLabel: 'Remove all edits',
		cancelLabel: 'Keep them'
	});
	return confirmed ? clearAll(stack) : null;
}
