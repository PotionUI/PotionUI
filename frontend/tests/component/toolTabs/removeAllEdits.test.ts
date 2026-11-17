import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apply, appliedCount, beginEdit, canUndo, createStack, undo } from '$lib/tool-tabs/frame/editStack';

const confirmDialog = vi.hoisted(() => vi.fn());
vi.mock('$lib/stores/confirm', async (importActual) => ({ ...(await importActual<object>()), confirmDialog }));

const { confirmRemoveAll } = await import('$lib/tool-tabs/frame/removeAll');

function stackOfTwo() {
	let stack = createStack<unknown>();
	for (const label of ['Crop', 'Light']) stack = apply(beginEdit(stack, { tool: 'crop', icon: '', label, summary: '', params: {} }));
	return stack;
}

beforeEach(() => confirmDialog.mockReset());

describe('Remove all edits confirm', () => {
	it('asks in plain words, removes everything on confirm, undo brings it back as one change', async () => {
		confirmDialog.mockResolvedValue(true);
		const next = await confirmRemoveAll(stackOfTwo());
		expect(confirmDialog).toHaveBeenCalledWith({
			title: 'Remove all edits?',
			message: '2 edits will be removed from this picture. Undo brings them back while this editor stays open.',
			variant: 'danger',
			confirmLabel: 'Remove all edits',
			cancelLabel: 'Keep them'
		});
		expect(appliedCount(next!)).toBe(0);
		expect(canUndo(next!)).toBe(true);
		const back = undo(next!);
		expect(appliedCount(back)).toBe(2);
		expect(back.steps.map((step) => step.label)).toEqual(['Crop', 'Light']);
	});

	it('leaves the edits untouched on Keep them', async () => {
		confirmDialog.mockResolvedValue(false);
		expect(await confirmRemoveAll(stackOfTwo())).toBeNull();
	});

	it('does not ask when there are no edits', async () => {
		expect(await confirmRemoveAll(createStack())).toBeNull();
		expect(confirmDialog).not.toHaveBeenCalled();
	});
});
