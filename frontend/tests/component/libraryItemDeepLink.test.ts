// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
vi.mock('$lib/components/plugins/PluginSlot.svelte', async () => ({
	default: (await import('./fixtures/PluginSlotStub.svelte')).default
}));
import { settle } from './helpers/settle';

const goto = vi.fn().mockResolvedValue(undefined);
const toastError = vi.fn();

vi.mock('$app/navigation', () => ({ goto: (...args: unknown[]) => goto(...args) }));
vi.mock('$lib/stores/toast', () => ({
	toasts: { show: vi.fn(), success: vi.fn(), error: (...args: unknown[]) => toastError(...args), info: vi.fn(), warning: vi.fn() }
}));
vi.mock('$lib/services/api/index', () => ({
	api: {
		getOrganizeProvenance: vi.fn(() => Promise.resolve({ success: true, data: [] })),
		listLibraryItems: vi.fn(),
		getLibraryItem: vi.fn(),
		getLibraryFacets: vi.fn(),
		listCollections: vi.fn(),
		getTags: vi.fn()
	}
}));

class FakeResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}

const { api } = await import('$lib/services/api/index');
const { libraryStore } = await import('$lib/stores/library');
const { takePendingFresh, setPendingFresh } = await import('$lib/library/freshHandoff');
const { page } = await import('./stubs/appStores');
const { default: LibraryPage } = await import('../../src/routes/library/+page.svelte');
const { createClassComponent } = await import('svelte/legacy');

function row(id: string) {
	return {
		id,
		filename: `${id}.png`,
		original_filename: `${id}.png`,
		media_type: 'image',
		url: `/api/media/uploads/${id}.png`,
		width: 64,
		height: 64,
		tags: [],
		is_favorite: false
	};
}

let destroy: (() => void) | undefined;
let originalResizeObserver: unknown;

function mountPage() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: LibraryPage as never, target, props: {} });
	destroy = () => {
		component.$destroy();
		target.remove();
	};
}

const selectedId = () => {
	let value: string | null = null;
	libraryStore.subscribe((state) => (value = state.selectedItem?.id ?? null))();
	return value;
};

beforeEach(() => {
	originalResizeObserver = (globalThis as any).ResizeObserver;
	(globalThis as any).ResizeObserver = FakeResizeObserver;
	libraryStore.reset();
	goto.mockClear();
	toastError.mockClear();
	takePendingFresh();
	page.set({ url: new URL('http://localhost/library'), params: {}, route: { id: null }, status: 200, error: null, data: {}, form: undefined, state: {} });
	vi.mocked(api.listLibraryItems).mockResolvedValue({
		success: true,
		data: { items: [row('row-1'), row('row-2')], total: 2, limit: 24, offset: 0 }
	} as never);
	vi.mocked(api.getLibraryFacets).mockResolvedValue({ success: true, data: { media_types: { image: 2 } } } as never);
	vi.mocked(api.listCollections).mockResolvedValue({ success: true, data: { collections: [], total: 0 } } as never);
	vi.mocked(api.getTags).mockResolvedValue({ success: true, data: { tags: [] } } as never);
	vi.mocked(api.getLibraryItem).mockReset();
});

afterEach(() => {
	destroy?.();
	destroy = undefined;
	document.body.innerHTML = '';
	libraryStore.reset();
	(globalThis as any).ResizeObserver = originalResizeObserver;
});

function linkTo(id: string) {
	page.set({ url: new URL(`http://localhost/library?item=${id}`), params: {}, route: { id: null }, status: 200, error: null, data: {}, form: undefined, state: {} });
}

describe('/library?item=<id>', () => {
	it('opens an item that is already in the loaded page without asking the server', async () => {
		await libraryStore.load();
		linkTo('row-2');
		mountPage();
		await settle();

		expect(selectedId()).toBe('row-2');
		expect(api.getLibraryItem).not.toHaveBeenCalled();
	});

	it('fetches an item that is not in the current page or filters, and still opens it', async () => {
		vi.mocked(api.getLibraryItem).mockResolvedValue({ success: true, data: row('row-77') } as never);
		linkTo('row-77');
		mountPage();
		await settle();

		expect(api.getLibraryItem).toHaveBeenCalledWith('row-77');
		expect(selectedId()).toBe('row-77');
		expect(document.body.textContent).toContain('row-77.png');
	});

	it('toasts and clears the param when the item is not found or not owned', async () => {
		vi.mocked(api.getLibraryItem).mockResolvedValue({ success: false } as never);
		linkTo('ghost');
		mountPage();
		await settle();

		expect(selectedId()).toBeNull();
		expect(toastError).toHaveBeenCalledWith("That library item couldn't be found");
		expect(goto).toHaveBeenCalledWith('/library', expect.objectContaining({ replaceState: true }));
	});

	it('toasts and clears the param when the lookup fails', async () => {
		vi.mocked(api.getLibraryItem).mockRejectedValue(new Error('404'));
		linkTo('ghost');
		mountPage();
		await settle();

		expect(toastError).toHaveBeenCalledTimes(1);
		expect(goto).toHaveBeenCalledWith('/library', expect.objectContaining({ replaceState: true }));
	});

	it('ignores an unsafe id', async () => {
		linkTo('..%2Fetc');
		mountPage();
		await settle();

		expect(api.getLibraryItem).not.toHaveBeenCalled();
		expect(selectedId()).toBeNull();
	});

	it('removes the param with replaceState when the modal closes, so a reload does not reopen it', async () => {
		await libraryStore.load();
		linkTo('row-1');
		mountPage();
		await settle();
		goto.mockClear();

		libraryStore.setSelectedItem(null);
		await settle();

		expect(goto).toHaveBeenCalledWith('/library', expect.objectContaining({ replaceState: true }));
	});

	it('writes the param when an item is opened from the grid', async () => {
		await libraryStore.load();
		mountPage();
		await settle();
		goto.mockClear();

		libraryStore.setSelectedItem(row('row-1') as never);
		await settle();

		expect(goto).toHaveBeenCalledWith('/library?item=row-1', expect.objectContaining({ replaceState: true }));
	});
});

describe('items handed over from History', () => {
	it('are tagged new once the library has loaded, and only once', async () => {
		setPendingFresh(['row-1', 'row-2']);
		mountPage();
		await settle();

		let fresh: string[] = [];
		libraryStore.subscribe((state) => (fresh = state.freshIds))();
		expect(fresh).toEqual(['row-1', 'row-2']);
		expect(takePendingFresh()).toEqual([]);
	});
});

describe('walking the listed items from the open modal', () => {
	it('moves to the next item with the arrow key and keeps ?item= in step', async () => {
		await libraryStore.load();
		linkTo('row-1');
		mountPage();
		await settle();
		expect(selectedId()).toBe('row-1');
		expect(document.body.querySelector('[data-library-position]')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('1 / 2');

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
		await settle();

		expect(selectedId()).toBe('row-2');
		expect(goto).toHaveBeenCalledWith('/library?item=row-2', expect.objectContaining({ replaceState: true }));
		expect(document.body.querySelector('[data-library-position]')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('2 / 2');
	});

	it('stops at the last item', async () => {
		await libraryStore.load();
		linkTo('row-2');
		mountPage();
		await settle();
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
		await settle();
		expect(selectedId()).toBe('row-2');
	});
});
