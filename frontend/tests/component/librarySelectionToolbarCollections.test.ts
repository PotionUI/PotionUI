// @vitest-environment jsdom
//
// Library items and generations share one `collections` tree but sit on two
// different junctions, so a library selection must go to the /uploads endpoints
// - posting upload ids to the generation members route would succeed and add
// nothing. Nothing in the props carries the selection or the active folder
// (both live in libraryStore), so this mounts the real component against the
// real stores rather than testing an extracted handler.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		addUploadsToCollection: vi.fn(),
		removeUploadsFromCollection: vi.fn(),
		addToCollection: vi.fn(),
		removeFromCollection: vi.fn(),
		listCollections: vi.fn(),
		listLibraryItems: vi.fn()
	}
}));

const { api } = await import('$lib/services/api/index');
const { libraryStore } = await import('$lib/stores/library');
const { default: LibrarySelectionToolbar } = await import(
	'../../src/routes/library/components/LibrarySelectionToolbar.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function mountToolbar() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: LibrarySelectionToolbar as never,
		target,
		props: { onBulkDeleteClick: () => {} }
	});
	return {
		target,
		removeButton: () =>
			Array.from(target.querySelectorAll('button')).find((b) =>
				b.textContent?.includes('Remove from collection')
			),
		deleteButton: () =>
			Array.from(target.querySelectorAll('button')).find(
				(b) => b.textContent?.trim() === 'Delete'
			),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function currentLibraryState() {
	let state: any;
	libraryStore.subscribe((s) => (state = s))();
	return state;
}

let mounted: ReturnType<typeof mountToolbar> | undefined;

beforeEach(() => {
	libraryStore.reset();
	vi.mocked(api.listCollections).mockResolvedValue({
		success: true,
		data: { collections: [], total: 0 }
	} as never);
	vi.mocked(api.listLibraryItems).mockResolvedValue({
		success: true,
		data: { items: [], total: 0, limit: 24, offset: 0 }
	} as never);
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	libraryStore.reset();
	vi.clearAllMocks();
});

describe('library selection toolbar', () => {
	it('hides "remove from collection" while not browsing a folder', () => {
		libraryStore.toggleSelect('item-1');
		mounted = mountToolbar();

		expect(mounted.removeButton()).toBeUndefined();
	});

	it('shows "remove from collection" once a folder is the active filter', () => {
		libraryStore.setFilter('collectionId', 'col-1');
		libraryStore.toggleSelect('item-1');
		mounted = mountToolbar();

		expect(mounted.removeButton()).toBeDefined();
	});

	it('removes the selection through the uploads junction, not the generation one', async () => {
		vi.mocked(api.removeUploadsFromCollection).mockResolvedValue({
			success: true,
			data: { removed: 2 }
		} as never);

		libraryStore.setFilter('collectionId', 'col-1');
		libraryStore.toggleSelect('item-1');
		libraryStore.toggleSelect('item-2');

		mounted = mountToolbar();
		await settle();

		mounted.removeButton()!.click();
		await settle();

		expect(api.removeUploadsFromCollection).toHaveBeenCalledWith('col-1', ['item-1', 'item-2'], 'library');
		expect(api.removeFromCollection).not.toHaveBeenCalled();
		expect(api.listLibraryItems).toHaveBeenCalled();

		const state = currentLibraryState();
		expect(state.selectedIds).toEqual([]);
		expect(state.selectionMode).toBe(false);
	});

	it('leaves the selection intact when the removal fails', async () => {
		vi.mocked(api.removeUploadsFromCollection).mockResolvedValue({
			success: false,
			error: 'boom'
		} as never);

		libraryStore.setFilter('collectionId', 'col-1');
		libraryStore.toggleSelect('item-1');

		mounted = mountToolbar();
		await settle();

		mounted.removeButton()!.click();
		await settle();

		expect(currentLibraryState().selectedIds).toEqual(['item-1']);
	});

	it('offers a bulk delete once something is selected', () => {
		libraryStore.toggleSelect('item-1');
		mounted = mountToolbar();

		expect(mounted.deleteButton()).toBeDefined();
	});
});
