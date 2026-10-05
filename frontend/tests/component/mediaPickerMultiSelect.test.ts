// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';

const apiMocks = vi.hoisted(() => ({
	getGenerationHistory: vi.fn(),
	listLibraryItems: vi.fn(),
	getTags: vi.fn()
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

function file(id: number, type: string, name: string) {
	return {
		id,
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
	generation('g1', [file(1, 'image', 'a.png'), file(2, 'image', 'b.png')]),
	generation('g2', [file(3, 'video', 'c.mp4')])
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
	const el = document.querySelector<HTMLInputElement>(`input[aria-label="Select ${label}"]`);
	if (!el) throw new Error(`no checkbox for ${label}`);
	return el;
}

function buttonByText(text: string): HTMLButtonElement | undefined {
	return [...document.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent?.trim() === text);
}

function count(): string {
	return document.querySelector('[data-testid="picker-selected-count"]')?.textContent?.trim() ?? '';
}

beforeEach(() => {
	apiMocks.getGenerationHistory.mockResolvedValue({ success: true, data: { generations: GENERATIONS, total: 2 } });
	apiMocks.listLibraryItems.mockResolvedValue({ success: true, data: { items: LIBRARY, total: 3 } });
	apiMocks.getTags.mockResolvedValue({ success: true, data: { tags: [] } });
});

afterEach(() => {
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

		checkbox('a.png').click();
		checkbox('b.png').click();
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
		expect(checkbox('c.mp4').checked).toBe(false);
	});

	it('keeps the selection when filters reload the list', async () => {
		const component = mountPicker(GenerationHistoryModal, { multiple: true, onClose: vi.fn() });
		await settle();
		checkbox('a.png').click();
		await tick();
		const calls = apiMocks.getGenerationHistory.mock.calls.length;

		buttonByText('7 Days')!.click();
		await vi.waitFor(() => expect(apiMocks.getGenerationHistory.mock.calls.length).toBeGreaterThan(calls));
		await vi.waitFor(() => expect(document.querySelector('input[aria-label="Select a.png"]')).not.toBeNull());

		expect(checkbox('a.png').checked).toBe(true);
		expect(count()).toBe('1 selected');
		component.$destroy();
	});

	it('shows a controlled selection after the picker is reopened', async () => {
		const first = mountPicker(GenerationHistoryModal, { multiple: true, onClose: vi.fn(), selectedKeys: ['g1:2'] });
		await settle();
		expect(checkbox('b.png').checked).toBe(true);
		first.$destroy();
		document.body.innerHTML = '';

		mountPicker(GenerationHistoryModal, { multiple: true, onClose: vi.fn(), selectedKeys: ['g1:2', 'g2:3'] });
		await settle();
		expect(checkbox('b.png').checked).toBe(true);
		expect(checkbox('c.mp4').checked).toBe(true);
		expect(count()).toBe('2 selected');
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

		expect(checkbox('c.mp4').disabled).toBe(true);
		expect(checkbox('a.png').disabled).toBe(false);
		expect(document.body.textContent).toContain('Only image files can be selected here');
		checkbox('c.mp4').click();
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

		checkbox('a.png').click();
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

		checkbox('b.png').click();
		await tick();
		buttonByText('Cancel')!.click();

		expect(onSelectionChange.mock.calls.at(-1)![0]).toEqual(['g1:1']);
		expect(onClose).toHaveBeenCalled();
		expect(onConfirm).not.toHaveBeenCalled();
	});

	it('previewing a file does not select it', async () => {
		const onSelectionChange = vi.fn();
		mountPicker(GenerationHistoryModal, { multiple: true, onClose: vi.fn(), onSelectionChange });
		await settle();

		document.querySelector<HTMLButtonElement>('button[aria-label="Preview a.png"]')!.click();
		await tick();

		expect(document.querySelector('[data-testid="picker-preview"]')).not.toBeNull();
		expect(onSelectionChange).not.toHaveBeenCalled();
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

		const tiles = [...document.querySelectorAll<HTMLButtonElement>('[data-testid="history-file-tile"] button')];
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
		await vi.waitFor(() => expect(document.querySelector('input[aria-label="Select one.png"]')).toBeNull(), {
			timeout: 3000
		});

		expect(count()).toBe('1 selected');
		apiMocks.listLibraryItems.mockResolvedValue({ success: true, data: { items: LIBRARY, total: 3 } });
		input.value = '';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await vi.waitFor(() => expect(document.querySelector('input[aria-label="Select one.png"]')).not.toBeNull(), {
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
		document.querySelector<HTMLButtonElement>('[aria-label="Use one.png"]')!.click();
		expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 'l1' }));
	});
});
