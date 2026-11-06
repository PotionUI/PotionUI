// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { settle } from './helpers/settle';

vi.mock('$lib/components/plugins/PluginSlot.svelte', async () => ({
	default: (await import('./fixtures/PluginSlotStub.svelte')).default
}));
const openToolShortcut = vi.fn();
vi.mock('$lib/tool-tabs/openShortcut', () => ({
	openToolShortcut: (...args: unknown[]) => openToolShortcut(...args)
}));
vi.mock('$app/navigation', () => ({ goto: vi.fn().mockResolvedValue(undefined) }));
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
const { registerDismissLayer, unregisterDismissLayer } = await import('$lib/actions/layerStack');
const { default: LibraryPage } = await import('../../src/routes/library/+page.svelte');
const { createClassComponent } = await import('svelte/legacy');

function row(id: string, kind = 'image') {
	return {
		id,
		filename: `${id}.${kind === 'image' ? 'png' : 'mp4'}`,
		original_filename: `${id}.${kind === 'image' ? 'png' : 'mp4'}`,
		media_type: kind,
		url: `/api/media/uploads/${id}`,
		width: 64,
		height: 64,
		tags: [],
		is_favorite: false
	};
}

let destroy: (() => void) | undefined;
let originalResizeObserver: unknown;

async function mountPage() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: LibraryPage as never, target, props: {} });
	destroy = () => {
		component.$destroy();
		target.remove();
	};
	await settle();
}

const press = (key: string, target: EventTarget = window, init: KeyboardEventInit = {}) => {
	target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init }));
};
const opened = () => openToolShortcut.mock.calls.map((call) => call[1][0].url as string);

beforeEach(() => {
	openToolShortcut.mockReset();
	openToolShortcut.mockReturnValue(true);
	originalResizeObserver = (globalThis as any).ResizeObserver;
	(globalThis as any).ResizeObserver = FakeResizeObserver;
	Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 1000 });
	libraryStore.reset();
	vi.mocked(api.listLibraryItems).mockResolvedValue({
		success: true,
		data: { items: [row('row-1'), row('row-2'), row('row-3', 'video')], total: 3, limit: 24, offset: 0 }
	} as never);
	vi.mocked(api.getLibraryFacets).mockResolvedValue({ success: true, data: { media_types: { image: 2, video: 1 } } } as never);
	vi.mocked(api.listCollections).mockResolvedValue({ success: true, data: { collections: [], total: 0 } } as never);
	vi.mocked(api.getTags).mockResolvedValue({ success: true, data: { tags: [] } } as never);
});

afterEach(() => {
	destroy?.();
	destroy = undefined;
	document.body.innerHTML = '';
	libraryStore.reset();
	delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth;
	(globalThis as any).ResizeObserver = originalResizeObserver;
});

describe('E on the Library grid', () => {
	it('opens the Image editor for the one selected image', async () => {
		await mountPage();
		libraryStore.toggleSelect('row-2');
		await settle(2);

		press('e');
		await settle(3);

		expect(openToolShortcut.mock.calls[0][0]).toBe('edit-image');
		expect(opened()).toEqual(['/api/media/uploads/row-2']);
	});

	it('opens the editor for the focused card when nothing is selected', async () => {
		await mountPage();
		const card = document.body.querySelector<HTMLElement>('[data-library-card="row-1"] .media-zoom');
		expect(card).not.toBeNull();
		card!.focus();

		press('e', card!);
		await settle(3);

		expect(opened()).toEqual(['/api/media/uploads/row-1']);
	});

	it('does nothing with no selection and no focused card', async () => {
		await mountPage();

		press('e');
		await settle(3);

		expect(openToolShortcut).not.toHaveBeenCalled();
	});

	it('does nothing with several items selected', async () => {
		await mountPage();
		libraryStore.toggleSelect('row-1');
		libraryStore.toggleSelect('row-2');
		await settle(2);

		press('e');
		await settle(3);

		expect(openToolShortcut).not.toHaveBeenCalled();
	});

	it('opens the Video editor for a video', async () => {
		await mountPage();
		libraryStore.toggleSelect('row-3');
		await settle(2);

		press('e');
		await settle(3);

		expect(openToolShortcut.mock.calls[0][1][0]).toMatchObject({ kind: 'video' });
	});

	it('does nothing while typing in a field', async () => {
		await mountPage();
		libraryStore.toggleSelect('row-1');
		await settle(2);
		const input = document.createElement('input');
		document.body.appendChild(input);
		input.focus();

		press('e', input);
		await settle(3);

		expect(openToolShortcut).not.toHaveBeenCalled();
	});

	it('does nothing while a menu or dialog is open above the page', async () => {
		await mountPage();
		libraryStore.toggleSelect('row-1');
		await settle(2);
		const layer = registerDismissLayer({ node: document.createElement('div') });

		press('e');
		await settle(3);
		unregisterDismissLayer(layer);

		expect(openToolShortcut).not.toHaveBeenCalled();
	});
});
