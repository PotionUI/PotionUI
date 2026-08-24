// @vitest-environment jsdom
//
// "Remove from collection" only reads from the real historyStore/collectionsStore
// (no props carry the active collection id or the selection), so a plain unit
// test around an extracted handler wouldn't exercise the actual wiring: whether
// the button is gated on filters.collectionId, whether it reaches
// api.removeFromCollection with the right args, and whether the grid actually
// reloads afterwards. This mounts the real component against the real stores.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		removeFromCollection: vi.fn(),
		listCollections: vi.fn(),
		getGenerationHistory: vi.fn()
	}
}));

const { api } = await import('$lib/services/api/index');
const { historyStore } = await import('$lib/stores/history');
const { default: HistorySelectionToolbar } = await import(
	'../../src/routes/history/components/HistorySelectionToolbar.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function mountToolbar() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: HistorySelectionToolbar as never,
		target,
		props: {
			onBulkDeleteClick: () => {},
			onCompareClick: () => {}
		}
	});
	return {
		target,
		component,
		removeButton: () =>
			Array.from(target.querySelectorAll('button')).find((b) =>
				b.textContent?.includes('Remove from collection')
			),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 6; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function currentHistoryState() {
	let state: any;
	historyStore.subscribe((s) => (state = s))();
	return state;
}

let mounted: ReturnType<typeof mountToolbar> | undefined;

beforeEach(() => {
	historyStore.setFilter('collectionId', undefined);
	historyStore.clearSelection();
	vi.mocked(api.listCollections).mockResolvedValue({
		success: true,
		data: { collections: [], total: 0 }
	} as never);
	vi.mocked(api.getGenerationHistory).mockResolvedValue({
		success: true,
		data: { generations: [], total: 0 }
	} as never);
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	historyStore.setFilter('collectionId', undefined);
	historyStore.clearSelection();
	vi.clearAllMocks();
});

describe('history selection toolbar - remove from collection', () => {
	it('hides the action while not browsing a collection', () => {
		historyStore.toggleSelect('gen-1');
		mounted = mountToolbar();

		expect(mounted.removeButton()).toBeUndefined();
	});

	it('shows the action once a collection is the active filter', () => {
		historyStore.setFilter('collectionId', 'col-1');
		historyStore.toggleSelect('gen-1');
		mounted = mountToolbar();

		expect(mounted.removeButton()).toBeDefined();
	});

	it('removes the selection from the active collection, clears selection, and reloads the grid', async () => {
		vi.mocked(api.removeFromCollection).mockResolvedValue({
			success: true,
			data: { removed: 2 }
		} as never);

		historyStore.setFilter('collectionId', 'col-1');
		historyStore.toggleSelect('gen-1');
		historyStore.toggleSelect('gen-2');

		mounted = mountToolbar();
		await settle();

		mounted.removeButton()!.click();
		await settle();

		expect(api.removeFromCollection).toHaveBeenCalledWith('col-1', ['gen-1', 'gen-2'], 'history');
		expect(api.getGenerationHistory).toHaveBeenCalled();

		const state = currentHistoryState();
		expect(state.selectedGenerationIds).toEqual([]);
		expect(state.selectionMode).toBe(false);
	});

	it('leaves the selection intact when the API call fails', async () => {
		vi.mocked(api.removeFromCollection).mockResolvedValue({
			success: false,
			error: 'boom'
		} as never);

		historyStore.setFilter('collectionId', 'col-1');
		historyStore.toggleSelect('gen-1');

		mounted = mountToolbar();
		await settle();

		mounted.removeButton()!.click();
		await settle();

		const state = currentHistoryState();
		expect(state.selectedGenerationIds).toEqual(['gen-1']);
	});
});
