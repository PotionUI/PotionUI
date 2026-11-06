// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
vi.mock('$lib/components/plugins/PluginSlot.svelte', async () => ({
	default: (await import('./fixtures/PluginSlotStub.svelte')).default
}));
import { CORE_TOOLS } from './fixtures/mediaToolCatalog';
import { settle } from './helpers/settle';

const openMediaTool = vi.fn();
const openToolShortcut = vi.fn();
const listMediaTools = vi.fn();

vi.mock('$lib/media-tools/open', () => ({ openMediaTool: (...args: unknown[]) => openMediaTool(...args) }));
vi.mock('$lib/tool-tabs/openShortcut', () => ({
	openToolShortcut: (...args: unknown[]) => openToolShortcut(...args)
}));
vi.mock('$lib/services/api/mediaTools', () => ({
	listMediaTools: (...args: unknown[]) => listMediaTools(...args),
	startMediaToolRun: vi.fn(),
	getMediaToolRun: vi.fn(),
	cancelMediaToolRun: vi.fn(),
	previewMediaTool: vi.fn()
}));
vi.mock('$lib/stores/toast', () => ({
	toasts: { show: vi.fn(), success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }
}));
vi.mock('$lib/services/api/index', () => ({
	api: {
		getOrganizeProvenance: vi.fn(() => Promise.resolve({ success: true, data: [] })),
		listLibraryItems: vi.fn(),
		getLibraryFacets: vi.fn(),
		listCollections: vi.fn(),
		getTags: vi.fn(),
		addUploadsToCollection: vi.fn(),
		removeUploadsFromCollection: vi.fn(),
		setLibraryFavorite: vi.fn()
	}
}));

class FakeResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}

const { api } = await import('$lib/services/api/index');
const { libraryStore } = await import('$lib/stores/library');
const { recentLooks } = await import('$lib/filters/recentLooks');
const { resetMediaToolCatalog } = await import('$lib/media-tools/catalog');
const { unregisterMediaTools } = await import('$lib/tools/tools');
const { default: LibraryPage } = await import('../../src/routes/library/+page.svelte');
const { createClassComponent } = await import('svelte/legacy');

function row(id: string, kind: string, name: string) {
	return {
		id,
		filename: `${id}-file`,
		original_filename: name,
		media_type: kind,
		url: `/u/${id}`,
		thumbnail_medium: `/t/${id}.jpg`,
		width: 640,
		height: 480,
		created_at: new Date().toISOString(),
		tags: []
	};
}

let destroy: (() => void) | undefined;
let widthSpy: ReturnType<typeof vi.spyOn> | undefined;
let originalResizeObserver: unknown;

async function mountPage() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: LibraryPage as never, target, props: {} });
	destroy = () => {
		component.$destroy();
		target.remove();
	};
	await settle(12);
	return target;
}

const cardIds = () =>
	Array.from(document.body.querySelectorAll('[data-library-card]')).map((card) => card.getAttribute('data-library-card'));

const toolRow = (id: string) => document.body.querySelector<HTMLButtonElement>(`[data-tools-menu] [data-tool="${id}"]`);

async function openBarMenu(submenu?: 'edit' | 'effects') {
	Array.from(document.body.querySelectorAll('button'))
		.find((button) => (button.textContent ?? '').trim().startsWith('Tools'))!
		.click();
	await settle(4);
	if (submenu) await openSubmenu(submenu);
}

async function openSubmenu(submenu: 'edit' | 'effects') {
	document.body.querySelector<HTMLButtonElement>(`[data-tools-menu] [data-submenu-row="${submenu}"]`)!.click();
	await settle(4);
}

async function openCardMenu(id: string, submenu?: 'edit' | 'effects') {
	document.body
		.querySelector(`[data-library-card="${id}"]`)!
		.querySelector<HTMLButtonElement>('[data-entry-menu-trigger]')!
		.click();
	await settle(4);
	if (submenu) await openSubmenu(submenu);
}

beforeEach(() => {
	recentLooks.set([]);
	originalResizeObserver = (globalThis as any).ResizeObserver;
	(globalThis as any).ResizeObserver = FakeResizeObserver;
	widthSpy = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1200);
	libraryStore.reset();
	openMediaTool.mockReset();
	openToolShortcut.mockReset();
	openToolShortcut.mockReturnValue(false);
	listMediaTools.mockReset();
	listMediaTools.mockResolvedValue(CORE_TOOLS);
	resetMediaToolCatalog();
	unregisterMediaTools((tool) => !!tool.catalogToolId);
	vi.mocked(api.listLibraryItems).mockResolvedValue({
		success: true,
		data: {
			items: [row('a', 'image', 'a.png'), row('b', 'video', 'b.mp4'), row('c', 'image', 'c.png')],
			total: 3,
			limit: 24,
			offset: 0
		}
	} as never);
	vi.mocked(api.getLibraryFacets).mockResolvedValue({ success: true, data: { media_types: { image: 2, video: 1 } } } as never);
	vi.mocked(api.listCollections).mockResolvedValue({ success: true, data: { collections: [], total: 0 } } as never);
	vi.mocked(api.getTags).mockResolvedValue({ success: true, data: { tags: [] } } as never);
});

afterEach(() => {
	destroy?.();
	destroy = undefined;
	widthSpy?.mockRestore();
	libraryStore.reset();
	(globalThis as any).ResizeObserver = originalResizeObserver;
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

describe('Library page media tools', () => {
	it('draws the cards the wiring tests below act on', async () => {
		await mountPage();

		expect(cardIds()).toEqual(['a', 'b', 'c']);
	});

	it('opens a catalog tool picked from the selection bar on the selected items', async () => {
		await mountPage();
		libraryStore.toggleSelect('b');
		await settle(4);
		await openBarMenu('edit');

		toolRow('trim')!.click();

		expect(openMediaTool).toHaveBeenCalledTimes(1);
		const call = openMediaTool.mock.calls[0][0];
		expect(call.toolId).toBe('trim');
		expect(call.scope).toBe('library');
		expect(call.items.map((entry: { id: string }) => entry.id)).toEqual(['b']);
	});

	it('puts a result next to its source tagged New and clears the selection', async () => {
		await mountPage();
		libraryStore.toggleSelect('b');
		await settle(4);
		await openBarMenu('edit');
		toolRow('trim')!.click();

		await openMediaTool.mock.calls[0][0].onResult(
			[
				{
					id: 'b2',
					filename: 'b2-file.mp4',
					original_filename: 'b-trimmed.mp4',
					media_type: 'video',
					url: '/u/b2-file.mp4'
				}
			],
			{ toolId: 'trim', replaced: false }
		);
		await settle(6);

		expect(cardIds()).toEqual(['a', 'b2', 'b', 'c']);
		const fresh = document.body.querySelector('[data-library-card="b2"] [data-new-tag]');
		expect(fresh?.textContent).toBe('New');
		expect(document.body.querySelector('[data-library-card="b"] [data-new-tag]')).toBeNull();
		expect(document.body.textContent).not.toContain('1 selected');
	});

	it('keeps a result newer than its source in the source day, without breaking the grid', async () => {
		const old = new Date(Date.now() - 3 * 86_400_000).toISOString();
		vi.mocked(api.listLibraryItems).mockResolvedValue({
			success: true,
			data: {
				items: [
					row('a', 'image', 'a.png'),
					{ ...row('b', 'video', 'b.mp4'), created_at: old },
					{ ...row('c', 'image', 'c.png'), created_at: old }
				],
				total: 3,
				limit: 24,
				offset: 0
			}
		} as never);
		await mountPage();
		libraryStore.toggleSelect('c');
		await settle(4);
		await openBarMenu('edit');
		toolRow('crop')!.click();

		await openMediaTool.mock.calls[0][0].onResult(
			[
				{
					id: 'c2',
					filename: 'c2-file.png',
					original_filename: 'c-crop.png',
					media_type: 'image',
					url: '/u/c2-file.png',
					created_at: new Date().toISOString()
				}
			],
			{ toolId: 'crop', replaced: false }
		);
		await settle(6);

		expect(cardIds()).toEqual(['a', 'b', 'c2', 'c']);
		const headers = Array.from(document.body.querySelectorAll('.font-mono.uppercase')).map((node) => node.textContent?.trim());
		expect(headers.filter((text) => text === 'Today')).toHaveLength(1);
	});

	it('refreshes the counts after a result', async () => {
		await mountPage();
		libraryStore.toggleSelect('a');
		await settle(4);
		await openBarMenu('edit');
		toolRow('crop')!.click();
		vi.mocked(api.getLibraryFacets).mockClear();
		vi.mocked(api.listCollections).mockClear();

		await openMediaTool.mock.calls[0][0].onResult(
			[{ id: 'a2', filename: 'a2-file.png', media_type: 'image', url: '/u/a2' }],
			{ toolId: 'crop', replaced: false }
		);

		expect(api.getLibraryFacets).toHaveBeenCalled();
		expect(api.listCollections).toHaveBeenCalled();
	});

	it('opens a catalog tool picked from a card menu and leaves the selection alone', async () => {
		await mountPage();
		libraryStore.toggleSelect('c');
		libraryStore.toggleSelect('a');
		await settle(4);

		await openCardMenu('b', 'edit');
		expect(toolRow('trim')).toBeTruthy();
		expect(toolRow('concat')).toBeNull();
		toolRow('trim')!.click();

		const call = openMediaTool.mock.calls[0][0];
		expect(call.items.map((entry: { id: string }) => entry.id)).toEqual(['b']);

		await call.onResult([], { toolId: 'trim', replaced: false });
		await settle(2);
		let state: any;
		libraryStore.subscribe((value) => (state = value))();
		expect(state.selectedIds).toEqual(['c', 'a']);
	});

	it('favorites the card from its menu through the library store', async () => {
		vi.mocked(api.setLibraryFavorite).mockResolvedValue({ success: true, data: { id: 'a', is_favorite: true } } as never);
		await mountPage();

		const card = document.body.querySelector('[data-library-card="a"]')!;
		card.querySelector<HTMLButtonElement>('[data-entry-menu-trigger]')!.click();
		await settle(4);
		toolRow('favorite')!.click();
		await settle(4);

		expect(api.setLibraryFavorite).toHaveBeenCalledWith('a', true);
		let state: any;
		libraryStore.subscribe((value) => (state = value))();
		expect(state.items.find((entry: { id: string }) => entry.id === 'a').is_favorite).toBe(true);
	});

	async function pickFromBar(id: string, toolId: string) {
		libraryStore.toggleSelect(id);
		await settle(4);
		await openBarMenu('edit');
		toolRow(toolId)!.click();
	}

	const cardImage = (id: string) =>
		document.body.querySelector<HTMLImageElement>(`[data-library-card="${id}"] img`)?.getAttribute('src');

	it('points the card of a replaced item at the new file, not at the thumbnail of the deleted one', async () => {
		await mountPage();
		expect(cardImage('a')).toBe('/t/a.jpg');
		await pickFromBar('a', 'crop');

		await openMediaTool.mock.calls[0][0].onResult(
			[{ id: 'a', filename: 'a-new-file.png', media_type: 'image', url: '/u/a-new-file.png' }],
			{ toolId: 'crop', replaced: true }
		);
		await settle(4);

		expect(cardImage('a')).toBe('/u/a-new-file.png');
	});

	it('uses the thumbnail the backend provides for the replaced file', async () => {
		await mountPage();
		await pickFromBar('a', 'crop');

		await openMediaTool.mock.calls[0][0].onResult(
			[
				{
					id: 'a',
					filename: 'a-new-file.png',
					media_type: 'image',
					url: '/u/a-new-file.png',
					thumbnail_medium: '/t/a-new-file.jpg'
				}
			],
			{ toolId: 'crop', replaced: true }
		);
		await settle(4);

		expect(cardImage('a')).toBe('/t/a-new-file.jpg');
	});

	it('does not move to page 1 or reload when a replace finishes after the user paged away', async () => {
		await mountPage();
		await pickFromBar('a', 'crop');
		libraryStore.setPage(2);
		vi.mocked(api.listLibraryItems).mockResolvedValue({
			success: true,
			data: { items: [row('x', 'image', 'x.png'), row('y', 'image', 'y.png')], total: 30, limit: 24, offset: 24 }
		} as never);
		await libraryStore.load();
		vi.mocked(api.listLibraryItems).mockClear();

		await openMediaTool.mock.calls[0][0].onResult(
			[{ id: 'a', filename: 'a-new.png', media_type: 'image', url: '/u/a-new.png' }],
			{ toolId: 'crop', replaced: true }
		);
		await settle(4);

		let state: any;
		libraryStore.subscribe((value) => (state = value))();
		expect(state.currentPage).toBe(2);
		expect(vi.mocked(api.listLibraryItems)).not.toHaveBeenCalled();
	});

	it('forgets the New mark and the placement when the page is left and opened again', async () => {
		const old = new Date(Date.now() - 3 * 86_400_000).toISOString();
		const server = [
			row('a', 'image', 'a.png'),
			{ ...row('b', 'video', 'b.mp4'), created_at: old },
			{ ...row('c', 'image', 'c.png'), created_at: old }
		];
		vi.mocked(api.listLibraryItems).mockResolvedValue({
			success: true,
			data: { items: server, total: 3, limit: 24, offset: 0 }
		} as never);
		await mountPage();
		await pickFromBar('c', 'crop');
		const created = new Date().toISOString();
		await openMediaTool.mock.calls[0][0].onResult(
			[{ id: 'c2', filename: 'c2.png', media_type: 'image', url: '/u/c2.png', created_at: created }],
			{ toolId: 'crop', replaced: false }
		);
		await settle(6);
		const headers = () =>
			Array.from(document.body.querySelectorAll('.font-mono.uppercase')).map((node) => node.textContent?.trim());
		expect(document.body.querySelector('[data-library-card="c2"] [data-new-tag]')).toBeTruthy();
		expect(headers().filter((text) => text === 'Today')).toHaveLength(1);
		expect(cardIds()).toEqual(['a', 'b', 'c2', 'c']);
		const group = (id: string) => document.body.querySelector(`[data-library-card="${id}"]`)!.parentElement;
		expect(group('c2')).toBe(group('c'));
		expect(group('c2')).not.toBe(group('a'));

		destroy?.();
		destroy = undefined;
		vi.mocked(api.listLibraryItems).mockResolvedValue({
			success: true,
			data: {
				items: [{ ...row('c2', 'image', 'c-crop.png'), created_at: created }, ...server],
				total: 4,
				limit: 24,
				offset: 0
			}
		} as never);
		await mountPage();

		expect(cardIds()).toEqual(['c2', 'a', 'b', 'c']);
		expect(document.body.querySelector('[data-new-tag]')).toBeNull();
		expect(headers().filter((text) => text === 'Today')).toHaveLength(1);
		const placed = (id: string) => document.body.querySelector(`[data-library-card="${id}"]`)!.parentElement;
		expect(placed('c2')).toBe(placed('a'));
		expect(placed('c2')).not.toBe(placed('c'));
	});

	it('clears the marks when the page is left', async () => {
		await mountPage();
		await pickFromBar('a', 'crop');
		await openMediaTool.mock.calls[0][0].onResult(
			[{ id: 'a2', filename: 'a2.png', media_type: 'image', url: '/u/a2.png' }],
			{ toolId: 'crop', replaced: false }
		);
		await settle(4);
		let state: any;
		const stop = libraryStore.subscribe((value) => (state = value));
		expect(state.freshIds).toEqual(['a2']);

		destroy?.();
		destroy = undefined;

		expect(state.freshIds).toEqual([]);
		expect(state.placedIds).toEqual([]);
		stop();
	});

	it('opens an Image editor tab for a tool picked on an image and keeps the selection', async () => {
		openToolShortcut.mockReturnValue(true);
		await mountPage();
		libraryStore.toggleSelect('a');
		libraryStore.toggleSelect('c');
		await settle(4);
		await openBarMenu('edit');

		toolRow('crop')!.click();
		await settle(4);

		expect(openMediaTool).not.toHaveBeenCalled();
		expect(openToolShortcut).toHaveBeenCalledTimes(1);
		expect(openToolShortcut.mock.calls[0][0]).toBe('crop');
		expect(openToolShortcut.mock.calls[0][1].map((entry: { id: string }) => entry.id)).toEqual(['a', 'c']);
		let state: any;
		libraryStore.subscribe((value) => (state = value))();
		expect([...state.selectedIds]).toEqual(['a', 'c']);
	});

	it('opens a recent look from the bar on that look', async () => {
		openToolShortcut.mockReturnValue(true);
		recentLooks.set([{ id: 'noir', name: 'Noir', group: 'Black and white' }]);
		await mountPage();
		libraryStore.toggleSelect('a');
		await settle(4);
		await openBarMenu('effects');

		expect(toolRow('look:noir')!.textContent).toContain('Noir');
		toolRow('look:noir')!.click();
		await settle(4);

		expect(openToolShortcut.mock.calls[0][0]).toBe('look:noir');
	});

	it('opens a recent look from a card menu on that look', async () => {
		openToolShortcut.mockReturnValue(true);
		recentLooks.set([{ id: 'ember', name: 'Ember', group: 'Colour' }]);
		await mountPage();

		await openCardMenu('c', 'effects');
		toolRow('look:ember')!.click();
		await settle(4);

		expect(openToolShortcut.mock.calls[0][0]).toBe('look:ember');
		expect(openToolShortcut.mock.calls[0][1][0].id).toBe('c');
	});

	it('shows no recent look on a card menu for a video', async () => {
		recentLooks.set([{ id: 'ember', name: 'Ember', group: 'Colour' }]);
		await mountPage();

		await openCardMenu('b');

		expect(document.body.querySelector('[data-submenu-row="effects"]')).toBeNull();
		expect(toolRow('look:ember')).toBeNull();
		expect(toolRow('apply-look')).toBeNull();
	});

	it('clears marks that arrived while the page was closed, as soon as it opens', async () => {
		await mountPage();
		destroy?.();
		destroy = undefined;
		await libraryStore.applyToolResult(
			[{ id: 'late', filename: 'late.png', media_type: 'image', url: '/u/late.png' }],
			['gone']
		);
		vi.mocked(api.listLibraryItems).mockRejectedValue(new Error('offline'));
		let state: any;
		const stop = libraryStore.subscribe((value) => (state = value));
		expect(state.freshIds).toEqual(['late']);

		await mountPage();

		expect(state.freshIds).toEqual([]);
		stop();
	});

	it('files a row that arrived through a reload under its real day, not under its neighbour', async () => {
		const old = new Date(Date.now() - 3 * 86_400_000).toISOString();
		const created = new Date().toISOString();
		await mountPage();
		await pickFromBar('a', 'crop');
		vi.mocked(api.listLibraryItems).mockResolvedValue({
			success: true,
			data: {
				items: [
					{ ...row('x', 'image', 'x.png'), created_at: old },
					{ ...row('y', 'image', 'y.png'), created_at: old }
				],
				total: 2,
				limit: 24,
				offset: 0
			}
		} as never);
		await libraryStore.load();
		vi.mocked(api.listLibraryItems).mockResolvedValue({
			success: true,
			data: {
				items: [
					{ ...row('late', 'image', 'late.png'), created_at: created },
					{ ...row('x', 'image', 'x.png'), created_at: old },
					{ ...row('y', 'image', 'y.png'), created_at: old }
				],
				total: 3,
				limit: 24,
				offset: 0
			}
		} as never);

		await openMediaTool.mock.calls[0][0].onResult(
			[{ id: 'late', filename: 'late.png', media_type: 'image', url: '/u/late.png', created_at: created }],
			{ toolId: 'crop', replaced: false }
		);
		await settle(6);

		const group = (id: string) => document.body.querySelector(`[data-library-card="${id}"]`)!.parentElement;
		expect(cardIds()).toEqual(['late', 'x', 'y']);
		expect(group('late')).not.toBe(group('x'));
		expect(group('x')).toBe(group('y'));
		expect(document.body.querySelector('[data-library-card="late"] [data-new-tag]')).toBeTruthy();
	});
});
