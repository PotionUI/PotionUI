// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { flushSync } from 'svelte';
import { GENERATIONS, LIBRARY, KIT_A, KIT_B, KIT_ITEMS_A, KIT_ITEMS_B } from './fixtures/mediaPickerFixtures';

const apiMocks = vi.hoisted(() => ({
	getGenerationHistory: vi.fn(),
	listLibraryItems: vi.fn(),
	listKits: vi.fn(),
	getKit: vi.fn(),
	getHistoryFacets: vi.fn(),
	listCollections: vi.fn(),
	getTags: vi.fn(),
	getGenerationThumbnailURL: (id: string, name: string) => `/thumb/${id}/${name}`,
	getClient: () => ({ get: vi.fn().mockResolvedValue({ data: { data: {} } }) })
}));

vi.mock('$lib/services/api/index', () => ({
	api: new Proxy(apiMocks as Record<string, any>, {
		get: (target, key: string) => (key in target ? target[key] : vi.fn().mockResolvedValue({ success: false }))
	})
}));

const { default: MediaPickerModal } = await import('$lib/components/modals/mediaPicker/MediaPickerModal.svelte');
const { mountComponent } = await import('./helpers/mount');
const { settle } = await import('./helpers/settle');
const { storage } = await import('$lib/utils/storage');
const { historyTileSize } = await import('$lib/stores/historyTileSize');
const { tagCatalog } = await import('$lib/stores/tagCatalog');

type Props = Record<string, unknown>;

function mountPicker(props: Props = {}) {
	const onClose = vi.fn();
	const view = mountComponent(MediaPickerModal as never, { isOpen: true, onClose, ...props });
	return { onClose, view };
}

async function ready(selector = '[data-pick-tile], [data-pick-kit]') {
	await vi.waitFor(() => expect(document.querySelector(selector)).not.toBeNull());
	await settle(2);
	flushSync();
}

function q<T extends HTMLElement = HTMLElement>(selector: string): T {
	const el = document.querySelector<T>(selector);
	if (!el) throw new Error(`missing ${selector}`);
	return el;
}

function all(selector: string): HTMLElement[] {
	return Array.from(document.querySelectorAll<HTMLElement>(selector));
}

async function click(el: HTMLElement) {
	el.click();
	await settle(2);
	flushSync();
}

async function change(el: HTMLSelectElement | HTMLInputElement, value: string) {
	el.value = value;
	el.dispatchEvent(new Event('change', { bubbles: true }));
	await settle(2);
	flushSync();
}

function buttonByText(text: string): HTMLButtonElement | undefined {
	return all('button').find((b) => b.textContent?.replace(/\s+/g, ' ').trim() === text) as HTMLButtonElement | undefined;
}

function tab(label: string): HTMLButtonElement {
	return all('[data-picker-tabs] button').find((b) => b.textContent?.includes(label)) as HTMLButtonElement;
}

function tileKeys(): string[] {
	return all('[data-pick-tile]').map((tile) => tile.getAttribute('data-pick-tile') as string);
}

function setWidth(width: number) {
	Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: width });
}

function lastHistoryRequest(): Record<string, unknown> {
	return apiMocks.getGenerationHistory.mock.calls.at(-1)![0];
}

function lastLibraryRequest(): Record<string, unknown> {
	return apiMocks.listLibraryItems.mock.calls.at(-1)![0];
}

beforeEach(() => {
	setWidth(1440);
	storage.remove('history-tile-size');
	historyTileSize.set('medium');
	tagCatalog.reset();
	apiMocks.getGenerationHistory.mockResolvedValue({ success: true, data: { generations: GENERATIONS, total: 3 } });
	apiMocks.listLibraryItems.mockResolvedValue({ success: true, data: { items: LIBRARY, total: 3, limit: 20, offset: 0 } });
	apiMocks.listKits.mockResolvedValue({
		success: true,
		data: {
			kits: [KIT_A, KIT_B],
			total: 2,
			page: 1,
			page_size: 20,
			labels: [
				{ label: 'Actor', count: 1 },
				{ label: 'Voice', count: 1 }
			]
		}
	});
	apiMocks.getKit.mockImplementation(async (id: string) => ({
		success: true,
		data: id === 'kt_a' ? { ...KIT_A, items: KIT_ITEMS_A } : { ...KIT_B, items: KIT_ITEMS_B }
	}));
	apiMocks.getHistoryFacets.mockResolvedValue({
		success: true,
		data: { modes: [], presets: [{ id: 'p1', name: 'Text to image', count: 2 }], models: [{ name: 'flux_fp8_scaled.safetensors', label: 'Flux 3', count: 2 }] }
	});
	apiMocks.listCollections.mockResolvedValue({ success: true, data: { collections: [{ id: 'c1', name: 'Cars', parent_id: null }] } });
	apiMocks.getTags.mockResolvedValue({ success: true, data: { tags: [{ id: 't1', name: 'night', color: '#336699', type: 'GENERATION' }] } });
});

afterEach(() => {
	document.body.innerHTML = '';
	document.body.style.overflow = '';
	vi.clearAllMocks();
	vi.useRealTimers();
});

describe('shell and tabs', () => {
	it('shows History, Library and Kits tabs on the title row and starts on the first source', async () => {
		mountPicker({ sources: ['history', 'library', 'kits'], mediaTypes: ['image'] });
		await ready();
		const labels = all('[data-picker-tabs] button').map((b) => b.textContent!.trim());
		expect(labels).toEqual(['History', 'Library', 'Kits']);
		expect(tab('History').getAttribute('aria-current')).toBe('page');
		expect(q('h2').textContent).toBe('Choose image');
	});

	it('hides the tab strip when the caller offers one source', async () => {
		mountPicker({ sources: ['history'], mediaTypes: ['image'] });
		await ready();
		expect(document.querySelector('[data-picker-tabs]')).toBeNull();
	});

	it('honours initialSource and a caller title', async () => {
		mountPicker({ sources: ['history', 'library'], initialSource: 'library', title: 'Replace image', mediaTypes: ['image'] });
		await ready();
		expect(q('h2').textContent).toBe('Replace image');
		expect(tab('Library').getAttribute('aria-current')).toBe('page');
		expect(apiMocks.listLibraryItems).toHaveBeenCalled();
		expect(apiMocks.getGenerationHistory).not.toHaveBeenCalled();
	});

	it('never narrows the tabs by kind: a video-only caller still sees all three', async () => {
		mountPicker({ sources: ['history', 'library', 'kits'], mediaTypes: ['video'] });
		await ready();
		expect(all('[data-picker-tabs] button')).toHaveLength(3);
	});
});

describe('filters reach each tab\'s own list query', () => {
	it('History: search, favourites, date, model, preset, sort, collection and tags', async () => {
		mountPicker({ sources: ['history', 'library'], mediaTypes: ['image', 'video'] });
		await ready();
		expect(lastHistoryRequest()).toMatchObject({ limit: 20, offset: 0, status: 'completed' });
		expect(lastHistoryRequest().mediaType).toBeUndefined();

		const search = q<HTMLInputElement>('[data-picker-search]');
		search.value = 'harbour';
		search.dispatchEvent(new Event('input', { bubbles: true }));
		await vi.waitFor(() => expect(lastHistoryRequest().search).toBe('harbour'), { timeout: 1500 });

		await click(q('[data-favorites-filter]'));
		await vi.waitFor(() => expect(lastHistoryRequest().favoritesOnly).toBe(true));

		await click(q('[data-filters-trigger]'));
		await click(q('[data-date="last_week"]'));
		await vi.waitFor(() => expect(lastHistoryRequest().createdFrom).toBeTruthy());
		const modelOptions = Array.from(q<HTMLSelectElement>('#pk-filter-model').options).map((o) => o.textContent?.trim());
		expect(modelOptions).toEqual(['Any', 'Flux 3']);
		await change(q<HTMLSelectElement>('#pk-filter-model'), 'flux_fp8_scaled.safetensors');
		await vi.waitFor(() => expect(lastHistoryRequest().modelName).toBe('flux_fp8_scaled.safetensors'));
		await change(q<HTMLSelectElement>('#pk-filter-preset'), 'p1');
		await vi.waitFor(() => expect(lastHistoryRequest().presetId).toBe('p1'));

		await click(q('[data-clear-all]'));
		await vi.waitFor(() => expect(lastHistoryRequest().presetId).toBeUndefined());
		expect(lastHistoryRequest().favoritesOnly).toBeUndefined();
		expect(lastHistoryRequest().search).toBeUndefined();
	});

	it('History: sort and kind reach the request', async () => {
		mountPicker({ sources: ['history'], mediaTypes: ['image', 'video'] });
		await ready();
		await change(q<HTMLSelectElement>('select[aria-label="Sort"]'), 'largest');
		await vi.waitFor(() => expect(lastHistoryRequest()).toMatchObject({ sortBy: 'file_size', sortDir: 'desc' }));
		await click(buttonByText('Videos')!);
		await vi.waitFor(() => expect(lastHistoryRequest().mediaType).toBe('video'));
	});

	it('a single-kind caller sends its kind and shows no kind switch', async () => {
		mountPicker({ sources: ['history'], mediaTypes: ['image'] });
		await ready();
		expect(lastHistoryRequest().mediaType).toBe('image');
		expect(document.querySelector('[data-kind-filter]')).toBeNull();
	});

	it('Library: search, kind, favourites, collection with Unsorted, tags', async () => {
		setWidth(1440);
		mountPicker({ sources: ['library'], mediaTypes: ['image', 'video'] });
		await ready();
		expect(lastLibraryRequest()).toMatchObject({ limit: 20, offset: 0 });

		await click(buttonByText('Videos')!);
		await vi.waitFor(() => expect(lastLibraryRequest().media_type).toBe('video'));

		await click(q('[data-favorites-filter]'));
		await vi.waitFor(() => expect(lastLibraryRequest().favorites_only).toBe(true));

		await click(q('[data-collection-filter]'));
		await vi.waitFor(() => expect(document.querySelector('[role="option"]')).not.toBeNull());
		const names = all('[role="listbox"][aria-label="Collections"] [role="option"]').map((o) => o.textContent!.trim());
		expect(names.slice(0, 2)).toEqual(['All items', 'Unsorted']);
		await click(all('[role="option"]').find((o) => o.textContent!.includes('Unsorted'))!);
		await vi.waitFor(() => expect(lastLibraryRequest().unsorted).toBe(true));

		const search = q<HTMLInputElement>('[data-picker-search]');
		search.value = 'portrait';
		search.dispatchEvent(new Event('input', { bubbles: true }));
		await vi.waitFor(() => expect(lastLibraryRequest().search).toBe('portrait'), { timeout: 1500 });
	});

	it('Kits: search and label go to the kit list', async () => {
		mountPicker({ sources: ['kits'], mediaTypes: ['image'] });
		await ready();
		expect(apiMocks.listKits.mock.calls.at(-1)![0]).toMatchObject({ page: 1, page_size: 20, label: null });
		await click(buttonByText('Voice')!);
		await vi.waitFor(() => expect(apiMocks.listKits.mock.calls.at(-1)![0]).toMatchObject({ label: 'Voice' }));
		const search = q<HTMLInputElement>('[data-picker-search]');
		search.value = 'her';
		search.dispatchEvent(new Event('input', { bubbles: true }));
		await vi.waitFor(() => expect(apiMocks.listKits.mock.calls.at(-1)![0]).toMatchObject({ search: 'her' }), { timeout: 1500 });
	});

	it('paging and page size go to the request and reset to page 1', async () => {
		apiMocks.getGenerationHistory.mockResolvedValue({ success: true, data: { generations: GENERATIONS, total: 95 } });
		mountPicker({ sources: ['history'], mediaTypes: ['image'] });
		await ready();
		await click(q('[aria-label="Page 3"]'));
		await vi.waitFor(() => expect(lastHistoryRequest()).toMatchObject({ offset: 40, limit: 20 }));
		await change(q<HTMLSelectElement>('select[aria-label="Items per page"]'), '50');
		await vi.waitFor(() => expect(lastHistoryRequest()).toMatchObject({ offset: 0, limit: 50 }));
	});
});

describe('the Filters popover', () => {
	it('holds date, model and preset at 1440 and shows the active count', async () => {
		mountPicker({ sources: ['history'], mediaTypes: ['image'] });
		await ready();
		expect(document.querySelector('[data-filters-count]')).toBeNull();
		await click(q('[data-filters-trigger]'));
		expect(q('[data-picker-popover]').getAttribute('aria-label')).toBe('Filters');
		expect(document.querySelector('#pk-filter-collection')).toBeNull();
		await click(q('[data-date="today"]'));
		expect(q('[data-filters-count]').textContent).toBe('1');
	});

	it('moves Collection and Tags inside below 1280', async () => {
		setWidth(1024);
		mountPicker({ sources: ['history'], mediaTypes: ['image'] });
		await ready();
		expect(document.querySelector('[data-collection-filter]')).toBeNull();
		expect(document.querySelector('[data-filter-tags]')).toBeNull();
		await click(q('[data-filters-trigger]'));
		expect(document.querySelector('#pk-filter-collection')).not.toBeNull();
		await vi.waitFor(() => expect(document.querySelector('[data-filter-tag="t1"]')).not.toBeNull());
		await click(q('[data-filter-tag="t1"]'));
		await vi.waitFor(() => expect(lastHistoryRequest().tagIds).toEqual(['t1']));
		expect(q('[data-filters-count]').textContent).toBe('1');
	});

	it('moves kind, favourites and sort inside on a phone and keeps the tile size control out', async () => {
		setWidth(390);
		mountPicker({ sources: ['history'], mediaTypes: ['image', 'video'] });
		await ready();
		expect(document.querySelector('[data-kind-filter]')).toBeNull();
		expect(document.querySelector('[data-favorites-filter]')).toBeNull();
		expect(document.querySelector('select[aria-label="Sort"]')).toBeNull();
		expect(document.querySelector('[data-tile-size]')).not.toBeNull();
		await click(q('[data-filters-trigger]'));
		expect(buttonByText('Videos')).toBeTruthy();
		expect(document.querySelector('#pk-filter-sort')).not.toBeNull();
	});
});

describe('thumbnail size', () => {
	it('writes the pages\' shared setting and resizes the grid', async () => {
		mountPicker({ sources: ['history'], mediaTypes: ['image'] });
		await ready();
		expect(q('[data-picker-body]').getAttribute('style')).toContain('--pk-m: 1');
		await click(q('[data-size="large"]'));
		expect(storage.get('history-tile-size')).toBe('large');
		expect(q('[data-picker-body]').getAttribute('style')).toContain('--pk-m: 2.1');
		await click(q('[data-size="small"]'));
		expect(storage.get('history-tile-size')).toBe('small');
		expect(q('[data-size="small"]').getAttribute('aria-checked')).toBe('true');
	});

	it('reads the stored size on open', async () => {
		historyTileSize.set('small');
		mountPicker({ sources: ['history'], mediaTypes: ['image'] });
		await ready();
		expect(q('[data-picker-body]').getAttribute('style')).toContain('--pk-m: 0.72');
	});
});

describe('single select', () => {
	it('has no checkbox and no confirm; a click picks the file and closes', async () => {
		const onPick = vi.fn();
		const { onClose } = mountPicker({ sources: ['history'], mediaTypes: ['image'], onPick });
		await ready();
		expect(document.querySelector('input[type="checkbox"]')).toBeNull();
		expect(document.querySelector('[data-picker-confirm]')).toBeNull();
		expect(buttonByText('Cancel')).toBeTruthy();
		await click(q('[aria-label^="Select a.png"]'));
		expect(onPick).toHaveBeenCalledTimes(1);
		const picked = onPick.mock.calls[0][0];
		expect(picked.origin).toEqual({ kind: 'history', generationId: 'g1', fileId: 1 });
		expect(picked.generation.id).toBe('g1');
		expect(onClose).toHaveBeenCalled();
	});

	it('has no magnifier or preview button on any tile', async () => {
		mountPicker({ sources: ['history'], mediaTypes: ['image'], multiple: true });
		await ready();
		expect(all('button').some((b) => /preview/i.test(b.getAttribute('aria-label') ?? ''))).toBe(false);
	});

	it('shows the range text instead of a selection count', async () => {
		mountPicker({ sources: ['history'], mediaTypes: ['image'] });
		await ready();
		expect(q('[data-picker-range]').textContent).toBe('Showing 1–3 of 3');
	});
});

describe('multi select', () => {
	it('toggles with the checkbox, counts, confirms the keys and closes', async () => {
		const onConfirm = vi.fn();
		const onSelectionChange = vi.fn();
		const { onClose } = mountPicker({ sources: ['history'], mediaTypes: ['image'], multiple: true, onConfirm, onSelectionChange });
		await ready();
		const confirm = q<HTMLButtonElement>('[data-picker-confirm]');
		expect(confirm.disabled).toBe(true);
		await click(q('input[aria-label^="Select a.png"]'));
		await click(q('input[aria-label^="Select b.png"]'));
		expect(q('[data-testid="picker-selected-count"]').textContent!.trim()).toBe('2 selected');
		expect(onSelectionChange).toHaveBeenLastCalledWith(['g1:1', 'g1:2'], expect.any(Array));
		await click(confirm);
		await vi.waitFor(() => expect(onConfirm).toHaveBeenCalled());
		expect(onConfirm.mock.calls[0][0]).toEqual(['g1:1', 'g1:2']);
		expect(onConfirm.mock.calls[0][1].map((i: { file: { id: number } }) => i.file.id)).toEqual([1, 2]);
		expect(onClose).toHaveBeenCalled();
	});

	it('restores the opening selection on Cancel', async () => {
		const onSelectionChange = vi.fn();
		const { onClose } = mountPicker({ sources: ['history'], mediaTypes: ['image'], multiple: true, selectedKeys: ['g1:1'], onSelectionChange });
		await ready();
		await click(q('input[aria-label^="Select b.png"]'));
		expect(q('[data-testid="picker-selected-count"]').textContent!.trim()).toBe('2 selected');
		await click(buttonByText('Cancel')!);
		expect(onSelectionChange).toHaveBeenLastCalledWith(['g1:1'], expect.any(Array));
		expect(onClose).toHaveBeenCalled();
	});

	it('dims the rest with the limit reason once the limit is reached', async () => {
		mountPicker({ sources: ['history'], mediaTypes: ['image'], multiple: true, maxCount: 1 });
		await ready();
		expect(q('[data-testid="picker-selected-count"]').textContent!.trim()).toBe('0 of 1 selected');
		await click(q('input[aria-label^="Select a.png"]'));
		const other = q<HTMLInputElement>('input[aria-label^="Select b.png"]');
		expect(other.disabled).toBe(true);
		expect(q('[data-pick-tile="g1:2"] [data-pick-reason]').textContent).toContain('Limit of 1 reached');
		expect(q('[data-testid="picker-selected-count"]').className).toContain('text-warning');
	});

	it('keeps the selection across tabs and shows the per-tab count', async () => {
		mountPicker({ sources: ['history', 'library'], mediaTypes: ['image'], multiple: true });
		await ready();
		await click(q('input[aria-label^="Select a.png"]'));
		expect(tab('History').textContent).toContain('1');
		await click(tab('Library'));
		await ready();
		await click(q('input[aria-label="Select one.png"]'));
		expect(q('[data-testid="picker-selected-count"]').textContent!.trim()).toBe('2 selected');
		expect(tab('Library').textContent).toContain('1');
		await click(tab('History'));
		await ready();
		expect(q<HTMLInputElement>('input[aria-label^="Select a.png"]').checked).toBe(true);
	});

	it('dims kinds the selectableMediaTypes list does not allow, with the reason', async () => {
		mountPicker({ sources: ['history'], multiple: true, selectableMediaTypes: ['image'] });
		await ready();
		const video = q('[data-pick-tile="g2:3"]');
		expect(video.querySelector('input')!.hasAttribute('disabled')).toBe(true);
		expect(video.querySelector('[data-pick-reason]')!.textContent).toContain('Only image files can be selected here');
	});
});

describe('Kits tab', () => {
	it('lists kits as cards in the grid and opens one on click', async () => {
		mountPicker({ sources: ['kits'], mediaTypes: ['image'] });
		await ready();
		expect(all('[data-pick-kit]')).toHaveLength(2);
		await click(q('[aria-label="Open Hero"]'));
		await ready();
		expect(tileKeys()).toEqual(['kit:ki1', 'kit:ki2', 'kit:ki3', 'kit:ki4']);
		expect(document.querySelector('[data-use-whole-kit]')).toBeNull();
		await click(buttonByText('All kits')!);
		await ready('[data-pick-kit]');
	});

	it('dims items the caller cannot take with their reason and picks one image in a single caller', async () => {
		const onPick = vi.fn();
		mountPicker({ sources: ['kits'], mediaTypes: ['image'], onPick });
		await ready();
		await click(q('[aria-label="Open Hero"]'));
		await ready();
		expect(q('[data-pick-tile="kit:ki3"] [data-pick-reason]').textContent!.trim()).toBe('Images only');
		expect(q<HTMLButtonElement>('[data-pick-tile="kit:ki3"] [data-pick-main]').disabled).toBe(true);
		await click(q('[aria-label="Select face-b.png"]'));
		expect(onPick).toHaveBeenCalledTimes(1);
		expect(onPick.mock.calls[0][0].origin).toEqual({ kind: 'kit', kitId: 'kt_a', itemId: 'ki2' });
	});

	it('dims a kit with nothing the caller takes: "No videos in this kit"', async () => {
		mountPicker({ sources: ['kits'], mediaTypes: ['video'] });
		await ready();
		expect(q('[data-pick-kit="kt_b"] [data-pick-reason]').textContent!.trim()).toBe('No videos in this kit');
		expect(q<HTMLButtonElement>('[data-pick-kit="kt_b"] [data-pick-main]').disabled).toBe(true);
		expect(document.querySelector('[data-pick-kit="kt_a"] [data-pick-reason]')).toBeNull();
	});

	it('whole-kit checkbox counts the kit\'s acceptable items against the limit and loads whole kits on confirm', async () => {
		const onLoad = vi.fn();
		const onConfirm = vi.fn();
		const { onClose } = mountPicker({
			sources: ['history', 'kits'],
			initialSource: 'kits',
			mediaTypes: ['image'],
			multiple: true,
			maxCount: 3,
			kits: { onLoad },
			onConfirm
		});
		await ready();
		const heroBox = q<HTMLInputElement>('input[aria-label="Use whole kit Hero"]');
		await click(heroBox);
		expect(q('[data-testid="picker-selected-count"]').textContent!.trim()).toBe('2 of 3 selected');
		await click(tab('History'));
		await ready();
		await click(q('input[aria-label^="Select a.png"]'));
		expect(q('[data-testid="picker-selected-count"]').textContent!.trim()).toBe('3 of 3 selected');
		expect(q<HTMLInputElement>('input[aria-label^="Select b.png"]').disabled).toBe(true);
		await click(q('[data-picker-confirm]'));
		await vi.waitFor(() => expect(onLoad).toHaveBeenCalled());
		expect(onLoad.mock.calls[0][0].map((k: { id: string }) => k.id)).toEqual(['kt_a']);
		expect(onConfirm.mock.calls[0][0]).toEqual(['g1:1']);
		expect(onClose).toHaveBeenCalled();
	});

	it('refuses a whole kit that does not fit in the room left', async () => {
		mountPicker({ sources: ['kits'], mediaTypes: ['image'], multiple: true, maxCount: 1, kits: { onLoad: vi.fn() } });
		await ready();
		const box = q<HTMLInputElement>('input[aria-label="Use whole kit Hero"]');
		expect(box.disabled).toBe(true);
		expect(q('[data-pick-kit="kt_a"] [data-pick-reason]').textContent!.trim()).toBe('Limit of 1 reached');
	});

	it('Use whole kit inside an open kit loads it', async () => {
		const onLoad = vi.fn();
		const { onClose } = mountPicker({ sources: ['kits'], mediaTypes: ['image', 'video'], multiple: true, kits: { onLoad } });
		await ready();
		await click(q('[aria-label="Open Hero"]'));
		await ready();
		const whole = q<HTMLButtonElement>('[data-use-whole-kit]');
		expect(whole.textContent).toContain('3 items');
		await click(whole);
		await vi.waitFor(() => expect(onLoad).toHaveBeenCalled());
		expect(onLoad.mock.calls[0][0][0].id).toBe('kt_a');
		expect(onClose).toHaveBeenCalled();
	});

	it('has no whole-kit checkbox in a single caller', async () => {
		mountPicker({ sources: ['kits'], mediaTypes: ['image'], kits: { onLoad: vi.fn() } });
		await ready();
		expect(document.querySelector('input[type="checkbox"]')).toBeNull();
	});

	it('marks kits that are already loaded', async () => {
		mountPicker({ sources: ['kits'], mediaTypes: ['image'], multiple: true, kits: { onLoad: vi.fn(), loadedIds: ['kt_a'] } });
		await ready();
		expect(q('[data-pick-kit="kt_a"] [data-pick-reason]').textContent!.trim()).toBe('Already loaded');
	});
});

describe('states', () => {
	it('empty with no filters says Nothing here yet', async () => {
		apiMocks.getGenerationHistory.mockResolvedValue({ success: true, data: { generations: [], total: 0 } });
		mountPicker({ sources: ['history'], mediaTypes: ['image'] });
		await vi.waitFor(() => expect(document.body.textContent).toContain('Nothing here yet'));
		expect(document.body.textContent).toContain('Generations you make show up here.');
	});

	it('empty with filters says No matches and Clear filters brings the list back', async () => {
		apiMocks.getGenerationHistory.mockResolvedValue({ success: true, data: { generations: [], total: 0 } });
		mountPicker({ sources: ['history'], mediaTypes: ['image'] });
		await vi.waitFor(() => expect(document.body.textContent).toContain('Nothing here yet'));
		await click(q('[data-favorites-filter]'));
		await vi.waitFor(() => expect(document.body.textContent).toContain('No matches'));
		apiMocks.getGenerationHistory.mockResolvedValue({ success: true, data: { generations: GENERATIONS, total: 3 } });
		await click(buttonByText('Clear filters')!);
		await ready();
		expect(tileKeys().length).toBeGreaterThan(0);
	});

	it('shows a loading state, then an error with Retry that keeps the selection', async () => {
		apiMocks.getGenerationHistory.mockRejectedValue(new Error('down'));
		mountPicker({ sources: ['history'], mediaTypes: ['image'], multiple: true, selectedKeys: ['g1:1'] });
		await vi.waitFor(() => expect(document.body.textContent).toContain('Could not load History'));
		expect(q('[data-testid="picker-selected-count"]').textContent!.trim()).toBe('1 selected');
		apiMocks.getGenerationHistory.mockResolvedValue({ success: true, data: { generations: GENERATIONS, total: 3 } });
		await click(buttonByText('Retry')!);
		await ready();
		expect(tileKeys()).toContain('g1:1');
	});

	it('ignores a late response from an older request', async () => {
		let release: (value: unknown) => void = () => {};
		apiMocks.getGenerationHistory.mockImplementationOnce(
			() => new Promise((resolve) => (release = resolve))
		);
		mountPicker({ sources: ['history'], mediaTypes: ['image'] });
		await settle(2);
		apiMocks.getGenerationHistory.mockResolvedValue({
			success: true,
			data: { generations: [{ ...GENERATIONS[0], files: [GENERATIONS[0].files[0]] }], total: 1 }
		});
		await click(q('[data-favorites-filter]'));
		await ready();
		release({ success: true, data: { generations: GENERATIONS, total: 3 } });
		await settle(3);
		expect(tileKeys()).toEqual(['g1:1']);
	});
});

describe('keyboard and focus', () => {
	it('focuses the search field first and restores focus to the opener on close', async () => {
		const opener = document.createElement('button');
		document.body.appendChild(opener);
		opener.focus();
		const { view } = mountPicker({ sources: ['history'], mediaTypes: ['image'] });
		await ready();
		await vi.waitFor(() => expect(document.activeElement).toBe(q('[data-picker-search]')));
		view.rerender({ isOpen: false });
		await vi.waitFor(() => expect(document.activeElement).toBe(opener));
	});

	it('Escape closes, restoring the opening selection in a multi caller', async () => {
		const onSelectionChange = vi.fn();
		const { onClose } = mountPicker({ sources: ['history'], mediaTypes: ['image'], multiple: true, onSelectionChange });
		await ready();
		await click(q('input[aria-label^="Select a.png"]'));
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
		await settle(2);
		expect(onClose).toHaveBeenCalled();
		expect(onSelectionChange).toHaveBeenLastCalledWith([], expect.any(Array));
	});

	it('arrow keys move focus between tiles and Home and End jump', async () => {
		mountPicker({ sources: ['history'], mediaTypes: ['image'] });
		await ready();
		const stops = all('[data-pick-focus]');
		stops[0].focus();
		stops[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true, cancelable: true }));
		expect(document.activeElement).toBe(stops[1]);
		stops[1].dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true, cancelable: true }));
		expect(document.activeElement).toBe(stops[0]);
		stops[0].dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true, cancelable: true }));
		expect(document.activeElement).toBe(stops[stops.length - 1]);
		stops[1].dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true, cancelable: true }));
		expect(document.activeElement).toBe(stops[0]);
	});

	it('Space toggles and Enter toggles the focused checkbox in a multi caller', async () => {
		mountPicker({ sources: ['history'], mediaTypes: ['image'], multiple: true });
		await ready();
		const box = q<HTMLInputElement>('input[aria-label^="Select a.png"]');
		box.focus();
		box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
		await settle(2);
		flushSync();
		expect(q<HTMLInputElement>('input[aria-label^="Select a.png"]').checked).toBe(true);
		await click(q('input[aria-label^="Select a.png"]'));
		expect(q<HTMLInputElement>('input[aria-label^="Select a.png"]').checked).toBe(false);
	});

	it('the tile button is the focus stop in single mode and the checkbox in multi mode', async () => {
		const single = mountPicker({ sources: ['history'], mediaTypes: ['image'] });
		await ready();
		expect(q('[data-pick-main]').getAttribute('tabindex')).toBe('0');
		single.view.destroy();
		document.body.innerHTML = '';
		mountPicker({ sources: ['history'], mediaTypes: ['image'], multiple: true });
		await ready();
		expect(q('[data-pick-main]').getAttribute('tabindex')).toBe('-1');
		expect(q('[data-pick-tile] input').hasAttribute('data-pick-focus')).toBe(true);
	});
});

describe('short dates and phone focus', () => {
	it('shows the date in the short app format in its own non-shrinking span, and the model part may truncate', async () => {
		mountPicker({ sources: ['history', 'library'], mediaTypes: ['image'] });
		await ready();
		const sub = q('[data-pick-tile="g1:1"] [data-pick-sublabel]');
		expect(sub.textContent).toContain('Jan 1');
		expect(sub.textContent).not.toContain('2026-01-01');
		expect(sub.lastElementChild!.className).toContain('shrink-0');
		expect(sub.firstElementChild!.className).toContain('truncate');
		await click(tab('Library'));
		await ready();
		expect(q('[data-pick-tile="l1"] [data-pick-sublabel]').textContent).toContain('Feb 3');
	});

	it('focuses the search field on desktop but not on a phone', async () => {
		const desktop = mountPicker({ sources: ['history'], mediaTypes: ['image'] });
		await ready();
		expect(q('[data-picker-search]').hasAttribute('data-autofocus')).toBe(true);
		desktop.view.destroy();
		document.body.innerHTML = '';
		setWidth(390);
		mountPicker({ sources: ['history'], mediaTypes: ['image'] });
		await ready();
		expect(q('[data-picker-search]').hasAttribute('data-autofocus')).toBe(false);
	});
});
