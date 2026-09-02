// @vitest-environment jsdom
//
// Mounts the real /library page against the real stores: the toolbar, tags bar,
// folder sidebar, grid and its empty state are wired together only here, and
// the load sequence (items + facets + tags + collections) exists nowhere else
// to be tested.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		listLibraryItems: vi.fn(),
		getLibraryFacets: vi.fn(),
		listCollections: vi.fn(),
		getTags: vi.fn(),
		uploadLibraryMedia: vi.fn(),
		deleteLibraryItem: vi.fn()
	}
}));

// The grid measures itself with `bind:clientWidth`, which Svelte 5 implements
// with a ResizeObserver - jsdom has none.
class FakeResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}

const { api } = await import('$lib/services/api/index');
const { libraryStore } = await import('$lib/stores/library');
const { default: LibraryPage } = await import('../../src/routes/library/+page.svelte');
const { createClassComponent } = await import('svelte/legacy');

function mountPage() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: LibraryPage as never, target, props: {} });
	return {
		target,
		fileInput: () => target.querySelector('input[type="file"]') as HTMLInputElement,
		// LibraryItemModal (and the confirm-delete modal it opens) are
		// BaseModal-based, which portals its dialog onto document.body
		// (src/lib/actions/portal.ts) rather than staying inside `target` - so
		// text/button lookups search the whole body, which `target` is itself
		// a child of.
		text: () => document.body.textContent ?? '',
		buttonWithText: (text: string) =>
			Array.from(document.body.querySelectorAll('button')).find((b) => b.textContent?.includes(text)),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let mounted: ReturnType<typeof mountPage> | undefined;
let originalResizeObserver: unknown;

beforeEach(() => {
	originalResizeObserver = (globalThis as any).ResizeObserver;
	(globalThis as any).ResizeObserver = FakeResizeObserver;
	libraryStore.reset();
	vi.mocked(api.listLibraryItems).mockResolvedValue({
		success: true,
		data: { items: [], total: 0, limit: 24, offset: 0 }
	} as never);
	vi.mocked(api.getLibraryFacets).mockResolvedValue({
		success: true,
		data: { media_types: { image: 3, video: 1 } }
	} as never);
	vi.mocked(api.listCollections).mockResolvedValue({
		success: true,
		data: { collections: [], total: 0 }
	} as never);
	vi.mocked(api.getTags).mockResolvedValue({ success: true, data: { tags: [] } } as never);
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	libraryStore.reset();
	(globalThis as any).ResizeObserver = originalResizeObserver;
	vi.clearAllMocks();
});

describe('library page', () => {
	it('loads items, facets, tags and the folder tree on mount', async () => {
		mounted = mountPage();
		await settle();

		expect(api.listLibraryItems).toHaveBeenCalled();
		expect(api.getLibraryFacets).toHaveBeenCalled();
		expect(api.listCollections).toHaveBeenCalled();
		// The library owns its own tag vocabulary, never the generation one.
		expect(api.getTags).toHaveBeenCalledWith('UPLOAD');
	});

	it('shows the empty state with a way in when the library holds nothing', async () => {
		mounted = mountPage();
		await settle();

		expect(mounted.text()).toContain('Your library is empty');
		expect(mounted.buttonWithText('Upload media')).toBeDefined();
	});

	it('accepts image, video and audio uploads, several at a time', async () => {
		mounted = mountPage();
		await settle();

		const input = mounted.fileInput();
		expect(input.accept).toBe('image/*,video/*,audio/*');
		expect(input.multiple).toBe(true);
	});

	it('opens the file picker from the empty state', async () => {
		mounted = mountPage();
		await settle();

		const click = vi.spyOn(mounted.fileInput(), 'click');
		mounted.buttonWithText('Upload media')!.click();
		await settle();

		expect(click).toHaveBeenCalled();
	});

	it('previews the selected item and confirms before deleting it', async () => {
		mounted = mountPage();
		await settle();

		libraryStore.setSelectedItem({
			id: 'item-1',
			filename: '0d3f8e2a-1111-2222-3333-444455556666.png',
			original_filename: 'sunset.png',
			media_type: 'image',
			url: '/api/media/uploads/0d3f8e2a-1111-2222-3333-444455556666.png',
			width: 1024,
			height: 512,
			tags: []
		} as never);
		await settle();

		expect(mounted.text()).toContain('sunset.png');
		expect(mounted.text()).toContain('1024×512');

		mounted.buttonWithText('Delete')!.click();
		await settle();

		// Confirmation first - the file is removed from disk.
		expect(api.deleteLibraryItem).not.toHaveBeenCalled();
		expect(mounted.text()).toContain('Delete library item');
		expect(mounted.text()).toContain('cannot be undone');
	});

	it('reloads the page of items after an upload succeeds', async () => {
		vi.mocked(api.uploadLibraryMedia).mockResolvedValue({ success: true } as never);
		mounted = mountPage();
		await settle();

		const before = vi.mocked(api.listLibraryItems).mock.calls.length;
		await libraryStore.upload([new File(['x'], 'a.png', { type: 'image/png' })]);
		await settle();

		expect(api.uploadLibraryMedia).toHaveBeenCalled();
		expect(vi.mocked(api.listLibraryItems).mock.calls.length).toBeGreaterThan(before);
	});
});
