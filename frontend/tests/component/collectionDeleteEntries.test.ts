import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';

vi.mock('$lib/stores/confirm', () => ({ confirmDialog: vi.fn() }));

const { default: CollectionActionsMenu } = await import(
	'../../src/lib/components/collections/CollectionActionsMenu.svelte'
);
const { confirmDialog } = await import('../../src/lib/stores/confirm');
const { toasts } = await import('../../src/lib/stores/toast');
const { resetLayerStackForTests } = await import('../../src/lib/actions/layerStack');
const { mountComponent } = await import('./helpers/mount');
const { settle } = await import('./helpers/settle');

const collections = [
	{ id: 'potions', parent_id: null, name: 'Potion studies', item_count: 42 },
	{ id: 'glass', parent_id: 'potions', name: 'Blue glass', item_count: 18 }
];

function treeActions(entries?: unknown) {
	return {
		onSelect: vi.fn(),
		onRename: vi.fn().mockResolvedValue({ success: true }),
		onCreate: vi.fn().mockResolvedValue({ success: true }),
		onDelete: vi.fn().mockResolvedValue({ success: true }),
		onMove: vi.fn().mockResolvedValue({ success: true }),
		...(entries ? { entries } : {})
	};
}

function entries(count = 3) {
	return {
		scope: 'history' as const,
		count: vi.fn().mockResolvedValue(count),
		deleteAll: vi.fn().mockResolvedValue({ deleted: count, blocked: [], total: count })
	};
}

async function openMenu(actions: ReturnType<typeof treeActions>, item = collections[1]) {
	const { target } = mountComponent(CollectionActionsMenu as never, {
		variant: 'toolbar',
		item,
		collections,
		treeActions: actions
	});
	target.querySelector<HTMLButtonElement>('button[aria-label="Collection actions"]')!.click();
	await settle();
	return Array.from(document.querySelectorAll<HTMLElement>('[role="menu"] [role="menuitem"]'));
}

beforeEach(() => {
	resetLayerStackForTests();
	vi.mocked(confirmDialog).mockReset();
});

afterEach(() => {
	document.body.innerHTML = '';
	vi.restoreAllMocks();
});

describe('Delete entries in the collection menu', () => {
	it('sits just above Delete collection, in the danger colour', async () => {
		const items = await openMenu(treeActions(entries()));
		const labels = items.map((item) => item.textContent?.trim());
		expect(labels).toEqual(['New subcollection', 'Rename…', 'Move to…', 'Delete entries…', 'Delete collection…']);
		const entry = items[3];
		expect(entry.className).toBe(items[4].className);
		expect(entry.classList.contains('text-danger')).toBe(true);
	});

	it('is not offered where the scope has no entry delete, as for prompts', async () => {
		const labels = (await openMenu(treeActions())).map((item) => item.textContent?.trim());
		expect(labels).not.toContain('Delete entries…');
	});

	it('confirms with the count and the collection name, then deletes and reports', async () => {
		const scope = entries(3);
		const success = vi.spyOn(toasts, 'success');
		vi.mocked(confirmDialog).mockResolvedValue(true);
		const items = await openMenu(treeActions(scope));
		items.find((item) => item.textContent?.trim() === 'Delete entries…')!.click();
		await settle();
		expect(scope.count).toHaveBeenCalledWith('glass');
		const options = vi.mocked(confirmDialog).mock.calls[0][0];
		expect(options.variant).toBe('danger');
		expect(options.message).toContain('3 generations');
		expect(options.message).toContain('“Blue glass”');
		expect(scope.deleteAll).toHaveBeenCalledWith('glass');
		expect(success).toHaveBeenCalledWith('Deleted 3 generations.');
	});

	it('mentions that subcollection items stay for a collection with subcollections', async () => {
		vi.mocked(confirmDialog).mockResolvedValue(false);
		const sentence = 'Items in its subcollections are not deleted.';
		const parent = await openMenu(treeActions(entries(3)), collections[0]);
		parent.find((item) => item.textContent?.trim() === 'Delete entries…')!.click();
		await settle();
		expect(vi.mocked(confirmDialog).mock.calls[0][0].message).toContain(sentence);
		document.body.innerHTML = '';
		const leaf = await openMenu(treeActions(entries(3)), collections[1]);
		leaf.find((item) => item.textContent?.trim() === 'Delete entries…')!.click();
		await settle();
		expect(vi.mocked(confirmDialog).mock.calls[1][0].message).not.toContain(sentence);
	});

	it('deletes nothing when the dialog is cancelled', async () => {
		const scope = entries(3);
		vi.mocked(confirmDialog).mockResolvedValue(false);
		const items = await openMenu(treeActions(scope));
		items.find((item) => item.textContent?.trim() === 'Delete entries…')!.click();
		await settle();
		expect(confirmDialog).toHaveBeenCalledTimes(1);
		expect(scope.deleteAll).not.toHaveBeenCalled();
	});
});
