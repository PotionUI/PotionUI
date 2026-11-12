// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';

const apiMocks = vi.hoisted(() => ({
	getGenerationHistory: vi.fn(),
	listLibraryItems: vi.fn(),
	listCollections: vi.fn(),
	getTags: vi.fn(),
	getClient: () => ({ get: vi.fn().mockResolvedValue({ data: { data: {} } }) })
}));

vi.mock('$lib/services/api/index', () => ({
	api: new Proxy(apiMocks as Record<string, any>, {
		get: (target, key: string) =>
			key in target ? target[key] : vi.fn().mockResolvedValue({ success: false })
	})
}));

const { default: GenerationHistoryModal } = await import('$lib/components/modals/GenerationHistoryModal.svelte');
const { default: UploadLibraryModal } = await import('$lib/components/modals/UploadLibraryModal.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');
const { stubClientWidth } = await import('./helpers/stubClientWidth');

function file(id: number, type: string, name: string, width?: number, height?: number) {
	return {
		id,
		width,
		height,
		file_path: `out/${name}`,
		file_type: type,
		is_final: true,
		created_at: '2026-01-01T00:00:00Z'
	};
}

function generation(id: string, files: ReturnType<typeof file>[]) {
	return {
		id,
		preset_name: 'Preset',
		status: 'completed',
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		form_data: {},
		files,
		rating: 0,
		is_favorite: false
	};
}

const GENERATIONS = [
	generation('g1', [file(1, 'image', 'a.png', 1024, 576), file(2, 'image', 'b.png', 576, 1024)]),
	generation('g2', [file(3, 'video', 'c.mp4', 1024, 576)])
];

function libraryItem(id: string, mediaType: string, name: string) {
	return {
		id,
		filename: `stored-${name}`,
		original_filename: name,
		media_type: mediaType,
		url: `/media/${id}`,
		tags: []
	};
}

const LIBRARY = [libraryItem('l1', 'image', 'one.png'), libraryItem('l2', 'image', 'two.png'), libraryItem('l3', 'video', 'three.mp4')];

let components: Array<{ $destroy: () => void }> = [];
let restoreWidth: () => void = () => {};

function mountPicker(Component: unknown, props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: Component as never,
		target,
		props: { isOpen: true, ...props }
	});
	components.push(component);
	return component;
}

async function settle() {
	await vi.waitFor(() => expect(document.querySelector('[role="dialog"]')).not.toBeNull());
	await vi.waitFor(() =>
		expect(document.querySelector('input[type="checkbox"], [aria-label^="Use "], [aria-label^="Select"], .media-zoom')).not.toBeNull()
	);
	await tick();
}

function checkbox(label: string): HTMLInputElement {
	const el = document.querySelector<HTMLInputElement>(`input[data-file="${label}"]`);
	if (!el) throw new Error(`no checkbox for ${label}`);
	return el;
}

function buttonByText(text: string): HTMLButtonElement | undefined {
	return [...document.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent?.trim() === text);
}

function count(): string {
	return document.querySelector('[data-testid="picker-selected-count"]')?.textContent?.trim() ?? '';
}

function historyCheckbox(name: string): HTMLInputElement {
	const el = document.querySelector<HTMLInputElement>(`input[aria-label^="Select ${name}"]`);
	if (!el) throw new Error(`no history checkbox for ${name}`);
	return el;
}

function isChecked(el: HTMLInputElement): boolean {
	return el.checked;
}

beforeEach(() => {
	Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: 1440 });
	restoreWidth = stubClientWidth(1000);
	apiMocks.getGenerationHistory.mockResolvedValue({ success: true, data: { generations: GENERATIONS, total: 2 } });
	apiMocks.listLibraryItems.mockResolvedValue({ success: true, data: { items: LIBRARY, total: 3 } });
	apiMocks.getTags.mockResolvedValue({ success: true, data: { tags: [] } });
});

afterEach(() => {
	restoreWidth();
	components.forEach((c) => c.$destroy());
	components = [];
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

describe('GenerationHistoryModal multi-select', () => {
	it('selects two files of one generation independently and reports typed keys and items', async () => {
		const onSelectionChange = vi.fn();
		mountPicker(GenerationHistoryModal, { multiple: true, onClose: vi.fn(), onSelectionChange });
		await settle();

		historyCheckbox('a.png').click();
		historyCheckbox('b.png').click();
		await tick();

		expect(onSelectionChange).toHaveBeenLastCalledWith(['g1:1', 'g1:2'], expect.any(Array));
		const items = onSelectionChange.mock.calls.at(-1)![1];
		expect(items[0]).toMatchObject({
			key: 'g1:1',
			mediaType: 'image',
			filename: 'a.png',
			origin: { kind: 'history', generationId: 'g1', fileId: 1 }
		});
		expect(count()).toBe('2 selected');
		expect(isChecked(historyCheckbox('c.mp4'))).toBe(false);
	});

	it('keeps the selection when filters reload the list', async () => {
		const component = mountPicker(GenerationHistoryModal, { multiple: true, onClose: vi.fn() });
		await settle();
		historyCheckbox('a.png').click();
		await tick();
		const calls = apiMocks.getGenerationHistory.mock.calls.length;

		document.querySelector<HTMLButtonElement>('[data-filters-trigger]')!.click();
		await vi.waitFor(() => expect(document.querySelector('[data-date="last_week"]')).not.toBeNull());
		document.querySelector<HTMLButtonElement>('[data-date="last_week"]')!.click();
		await vi.waitFor(() => expect(apiMocks.getGenerationHistory.mock.calls.length).toBeGreaterThan(calls));
		await vi.waitFor(() => expect(document.querySelector('input[aria-label^="Select a.png"]')).not.toBeNull());

		expect(isChecked(historyCheckbox('a.png'))).toBe(true);
		expect(count()).toBe('1 selected');
		component.$destroy();
	});

	it('shows a controlled selection after the picker is reopened', async () => {
		const first = mountPicker(GenerationHistoryModal, { multiple: true, onClose: vi.fn(), selectedKeys: ['g1:2'] });
		await settle();
		expect(isChecked(historyCheckbox('b.png'))).toBe(true);
		first.$destroy();
		document.body.innerHTML = '';

		mountPicker(GenerationHistoryModal, { multiple: true, onClose: vi.fn(), selectedKeys: ['g1:2', 'g2:3'] });
		await settle();
		expect(isChecked(historyCheckbox('b.png'))).toBe(true);
		expect(isChecked(historyCheckbox('c.mp4'))).toBe(true);
		expect(count()).toBe('2 selected');
	});

	describe('Library filters', () => {
		const lastQuery = () => apiMocks.listLibraryItems.mock.calls.at(-1)![0] as Record<string, unknown>;
		const loaded = () => {
			const calls = apiMocks.listLibraryItems.mock.calls.length;
			return vi.waitFor(() => expect(apiMocks.listLibraryItems.mock.calls.length).toBeGreaterThan(calls), { timeout: 3000 });
		};

		beforeEach(() => {
			apiMocks.getTags.mockResolvedValue({ success: true, data: { tags: [{ id: 't1', name: 'Mara', color: '#88aaff' }] } });
			apiMocks.listCollections.mockResolvedValue({
				success: true,
				data: { collections: [{ id: 'c1', name: 'Faces', parent_id: null }] }
			});
		});

		it('passes the chosen kind, collection and tag to the list query', async () => {
			mountPicker(UploadLibraryModal, { multiple: true, onClose: vi.fn() });
			await settle();

			let pending = loaded();
			buttonByText('Videos')!.click();
			await pending;
			expect(lastQuery()).toMatchObject({ media_type: 'video' });

			pending = loaded();
			document.querySelector<HTMLButtonElement>('[data-collection-filter]')!.click();
			await vi.waitFor(() => expect(document.querySelector('[role="option"]')).not.toBeNull());
			await vi.waitFor(() => expect(document.body.textContent).toContain('Faces'));
			[...document.querySelectorAll<HTMLElement>('[role="option"]')].find((el) => el.textContent?.includes('Faces'))!.click();
			await pending;
			expect(lastQuery()).toMatchObject({ media_type: 'video', collection_id: 'c1' });

			pending = loaded();
			document.querySelector<HTMLButtonElement>('[data-filter-tags]')!.click();
			await vi.waitFor(() => expect([...document.querySelectorAll('[role="option"]')].some((el) => el.textContent?.includes('Mara'))).toBe(true));
			[...document.querySelectorAll<HTMLElement>('[role="option"]')].find((el) => el.textContent?.includes('Mara'))!.click();
			await pending;
			expect(lastQuery()).toMatchObject({ media_type: 'video', collection_id: 'c1', tag_ids: 't1' });
		});

		it('offers Unsorted and Favorites and sends them', async () => {
			mountPicker(UploadLibraryModal, { multiple: true, onClose: vi.fn() });
			await settle();

			let pending = loaded();
			document.querySelector<HTMLButtonElement>('[data-favorites-filter]')!.click();
			await pending;
			expect(lastQuery()).toMatchObject({ favorites_only: true });

			pending = loaded();
			document.querySelector<HTMLButtonElement>('[data-collection-filter]')!.click();
			await vi.waitFor(() => expect([...document.querySelectorAll('[role="option"]')].some((el) => el.textContent?.trim() === 'Unsorted')).toBe(true));
			[...document.querySelectorAll<HTMLElement>('[role="option"]')].find((el) => el.textContent?.trim() === 'Unsorted')!.click();
			await pending;
			expect(lastQuery()).toMatchObject({ unsorted: true });
		});

		it('keeps the selection when a filter changes', async () => {
			mountPicker(UploadLibraryModal, { multiple: true, onClose: vi.fn() });
			await settle();
			checkbox('one.png').click();
			await tick();

			apiMocks.listLibraryItems.mockResolvedValue({ success: true, data: { items: [LIBRARY[2]], total: 1 } });
			const pending = loaded();
			buttonByText('Videos')!.click();
			await pending;
			await vi.waitFor(() => expect(document.querySelector('input[data-file="one.png"]')).toBeNull());
			expect(count()).toBe('1 selected');

			apiMocks.listLibraryItems.mockResolvedValue({ success: true, data: { items: LIBRARY, total: 3 } });
			buttonByText('All')!.click();
			await vi.waitFor(() => expect(document.querySelector('input[data-file="one.png"]')).not.toBeNull());
			expect(checkbox('one.png').checked).toBe(true);
		});

		it('hides the kind filter when the caller already fixed the kind', async () => {
			mountPicker(UploadLibraryModal, { multiple: true, onClose: vi.fn(), mediaType: 'image' });
			await settle();
			expect(buttonByText('Videos')).toBeUndefined();
			expect(lastQuery()).toMatchObject({ media_type: 'image' });
		});

		it('pages with numbered pagination and a bounded page size', async () => {
			apiMocks.listLibraryItems.mockResolvedValue({ success: true, data: { items: LIBRARY, total: 55 } });
			mountPicker(UploadLibraryModal, { multiple: true, onClose: vi.fn() });
			await settle();
			expect(lastQuery()).toMatchObject({ limit: 20, offset: 0 });
			expect(document.querySelector('nav[aria-label="Pagination"]')).not.toBeNull();
			const pending = loaded();
			buttonByText('2')!.click();
			await pending;
			expect(lastQuery()).toMatchObject({ limit: 20, offset: 20 });
		});
	});

	it('dims files outside the media type constraint and explains why', async () => {
		const onSelectionChange = vi.fn();
		mountPicker(GenerationHistoryModal, {
			multiple: true,
			onClose: vi.fn(),
			onSelectionChange,
			selectableMediaTypes: ['image']
		});
		await settle();

		expect(historyCheckbox('c.mp4').disabled).toBe(true);
		expect(historyCheckbox('a.png').disabled).toBe(false);
		expect(document.body.textContent).toContain('Only image files can be selected here');
		historyCheckbox('c.mp4').click();
		expect(onSelectionChange).not.toHaveBeenCalled();
	});

	it('blocks Use selected while empty and confirms a selection', async () => {
		const onConfirm = vi.fn();
		const onClose = vi.fn();
		mountPicker(GenerationHistoryModal, { multiple: true, onClose, onConfirm });
		await settle();

		expect(buttonByText('Use selected')!.disabled).toBe(true);
		buttonByText('Use selected')!.click();
		expect(onConfirm).not.toHaveBeenCalled();

		historyCheckbox('a.png').click();
		await tick();
		expect(buttonByText('Use selected')!.disabled).toBe(false);
		buttonByText('Use selected')!.click();
		expect(onConfirm).toHaveBeenCalledWith(['g1:1'], expect.any(Array));
		expect(onClose).toHaveBeenCalled();
	});

	it('cancel restores the selection it was opened with', async () => {
		const onSelectionChange = vi.fn();
		const onConfirm = vi.fn();
		const onClose = vi.fn();
		mountPicker(GenerationHistoryModal, {
			multiple: true,
			onClose,
			onConfirm,
			onSelectionChange,
			selectedKeys: ['g1:1']
		});
		await settle();

		historyCheckbox('b.png').click();
		await tick();
		buttonByText('Cancel')!.click();

		expect(onSelectionChange.mock.calls.at(-1)![0]).toEqual(['g1:1']);
		expect(onClose).toHaveBeenCalled();
		expect(onConfirm).not.toHaveBeenCalled();
	});

	it('clicking the tile image toggles the selection', async () => {
		const onSelectionChange = vi.fn();
		mountPicker(GenerationHistoryModal, { multiple: true, onClose: vi.fn(), onSelectionChange });
		await settle();

		const tileButton = historyCheckbox('a.png').closest('[data-testid="history-file-tile"]')!.querySelector<HTMLElement>('button[aria-hidden="true"]')!;
		tileButton.click();
		await tick();
		expect(historyCheckbox('a.png').checked).toBe(true);
		expect(document.querySelector('button[aria-label^="Preview"]')).toBeNull();
		tileButton.click();
		await tick();
		expect(historyCheckbox('a.png').checked).toBe(false);
	});

	it('single-select keeps the card flow without checkboxes or selection bar', async () => {
		const onSelect = vi.fn();
		mountPicker(GenerationHistoryModal, { onClose: vi.fn(), onSelect });
		await settle();

		expect(document.querySelector('[data-testid="picker-selected-count"]')).toBeNull();
		expect(document.querySelector('input[type="checkbox"]')).toBeNull();
		expect(buttonByText('Use selected')).toBeUndefined();
		expect(buttonByText('Cancel')).toBeDefined();
	});

	it('single-select calls onSelect with the generation and the clicked file', async () => {
		const onSelect = vi.fn();
		mountPicker(GenerationHistoryModal, { onClose: vi.fn(), onSelect });
		await settle();

		const tiles = [...document.querySelectorAll<HTMLElement>('[data-testid="history-file-tile"] button')];
		expect(tiles.length).toBe(3);
		tiles[1].click();
		expect(onSelect).toHaveBeenCalledTimes(1);
		expect(onSelect.mock.calls[0][0]).toMatchObject({ id: 'g1' });
		expect(onSelect.mock.calls[0][1]).toMatchObject({ id: 2 });

		tiles[2].click();
		expect(onSelect.mock.calls[1][0]).toMatchObject({ id: 'g2' });
		expect(onSelect.mock.calls[1][1]).toMatchObject({ id: 3 });
	});

	it('confirm reports an item for every key, including ones never loaded', async () => {
		const onConfirm = vi.fn();
		mountPicker(GenerationHistoryModal, { multiple: true, onClose: vi.fn(), onConfirm, selectedKeys: ['g1:1', 'old:9'] });
		await settle();

		buttonByText('Use selected')!.click();
		const [keys, items] = onConfirm.mock.calls[0];
		expect(items).toHaveLength(keys.length);
		expect(items[0]).toMatchObject({ key: 'g1:1', filename: 'a.png', mediaType: 'image' });
		expect(items[1]).toEqual({
			key: 'old:9',
			mediaType: null,
			filename: null,
			origin: { kind: 'history', generationId: 'old', fileId: 9 }
		});
	});
});

describe('UploadLibraryModal multi-select', () => {
	it('clicking the tile image toggles the selection and no tile offers a preview button', async () => {
		const onSelectionChange = vi.fn();
		mountPicker(UploadLibraryModal, { multiple: true, onClose: vi.fn(), onSelectionChange });
		await settle();

		document.querySelector<HTMLElement>('button[aria-hidden="true"][data-file="one.png"]')!.click();
		await tick();
		expect(checkbox('one.png').checked).toBe(true);
		expect(document.querySelector('button[aria-label^="Preview"]')).toBeNull();
	});

	it('reports typed keys and items for library selections', async () => {
		const onSelectionChange = vi.fn();
		mountPicker(UploadLibraryModal, { multiple: true, onClose: vi.fn(), onSelectionChange });
		await settle();

		checkbox('one.png').click();
		checkbox('three.mp4').click();
		await tick();

		expect(onSelectionChange).toHaveBeenLastCalledWith(['l1', 'l3'], expect.any(Array));
		const items = onSelectionChange.mock.calls.at(-1)![1];
		expect(items[1]).toMatchObject({
			key: 'l3',
			mediaType: 'video',
			filename: 'three.mp4',
			origin: { kind: 'library', itemId: 'l3' }
		});
	});

	it('keeps the selection through a search that replaces the list', async () => {
		mountPicker(UploadLibraryModal, { multiple: true, onClose: vi.fn() });
		await settle();
		checkbox('one.png').click();
		await tick();

		apiMocks.listLibraryItems.mockResolvedValue({ success: true, data: { items: [LIBRARY[1]], total: 1 } });
		const input = document.querySelector<HTMLInputElement>('input[placeholder^="Search"]')!;
		input.value = 'two';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await vi.waitFor(() => expect(document.querySelector('input[data-file="one.png"]')).toBeNull(), {
			timeout: 3000
		});

		expect(count()).toBe('1 selected');
		apiMocks.listLibraryItems.mockResolvedValue({ success: true, data: { items: LIBRARY, total: 3 } });
		input.value = '';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await vi.waitFor(() => expect(document.querySelector('input[data-file="one.png"]')).not.toBeNull(), {
			timeout: 3000
		});
		expect(checkbox('one.png').checked).toBe(true);
	});

	it('dims files outside the media type constraint and explains why', async () => {
		mountPicker(UploadLibraryModal, { multiple: true, onClose: vi.fn(), selectableMediaTypes: ['video'] });
		await settle();

		expect(checkbox('one.png').disabled).toBe(true);
		expect(checkbox('three.mp4').disabled).toBe(false);
		expect(document.body.textContent).toContain('Only video files can be selected here');
	});

	it('blocks Use selected while empty and cancel restores the opening selection', async () => {
		const onSelectionChange = vi.fn();
		const onConfirm = vi.fn();
		const onClose = vi.fn();
		mountPicker(UploadLibraryModal, { multiple: true, onClose, onConfirm, onSelectionChange, selectedKeys: ['l2'] });
		await settle();

		checkbox('two.png').click();
		await tick();
		expect(buttonByText('Use selected')!.disabled).toBe(true);
		buttonByText('Use selected')!.click();
		expect(onConfirm).not.toHaveBeenCalled();

		checkbox('one.png').click();
		await tick();
		buttonByText('Cancel')!.click();

		expect(onSelectionChange.mock.calls.at(-1)![0]).toEqual(['l2']);
		expect(onClose).toHaveBeenCalled();
		expect(onConfirm).not.toHaveBeenCalled();
	});

	it('confirm reports an item for every key, including ones never loaded', async () => {
		const onConfirm = vi.fn();
		mountPicker(UploadLibraryModal, { multiple: true, onClose: vi.fn(), onConfirm, selectedKeys: ['l1', 'gone'] });
		await settle();

		buttonByText('Use selected')!.click();
		const [keys, items] = onConfirm.mock.calls[0];
		expect(items).toHaveLength(keys.length);
		expect(items[1]).toEqual({
			key: 'gone',
			mediaType: null,
			filename: null,
			origin: { kind: 'library', itemId: 'gone' }
		});
	});

	it('single-select still picks an item from its card', async () => {
		const onSelect = vi.fn();
		mountPicker(UploadLibraryModal, { onClose: vi.fn(), onSelect });
		await settle();

		expect(document.querySelector('input[type="checkbox"]')).toBeNull();
		document.querySelector<HTMLButtonElement>('[aria-label="Select one.png"]')!.click();
		expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 'l1' }));
	});
});
