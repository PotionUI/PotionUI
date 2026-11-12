// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { flushSync } from 'svelte';
import { GENERATIONS, LIBRARY } from './fixtures/mediaPickerFixtures';

const apiMocks = vi.hoisted(() => ({
	getGenerationHistory: vi.fn(),
	listLibraryItems: vi.fn(),
	listCollections: vi.fn(),
	getTags: vi.fn(),
	getHistoryFacets: vi.fn(),
	getGenerationThumbnailURL: (id: string, name: string) => `/thumb/${id}/${name}`,
	getClient: () => ({ get: vi.fn().mockResolvedValue({ data: { data: {} } }) })
}));

vi.mock('$lib/services/api/index', () => ({
	api: new Proxy(apiMocks as Record<string, any>, {
		get: (target, key: string) => (key in target ? target[key] : vi.fn().mockResolvedValue({ success: false }))
	})
}));

const { default: GenerationHistoryModal } = await import('$lib/components/modals/GenerationHistoryModal.svelte');
const { default: UploadLibraryModal } = await import('$lib/components/modals/UploadLibraryModal.svelte');
const { default: MediaPickerModal } = await import('$lib/components/modals/mediaPicker/MediaPickerModal.svelte');
const { registerComponent, getRegistry } = await import('$lib/plugin-api/componentRegistry');
const { registerHostUiComponents } = await import('$lib/plugin-api/hostUi');
const { settle } = await import('./helpers/settle');

registerComponent('GenerationHistoryModal', GenerationHistoryModal);
registerComponent('UploadLibraryModal', UploadLibraryModal);

type Handle = unknown;
let mounted: Array<{ name: string; handle: Handle }> = [];

function mountAsPlugin(name: string, props: Record<string, unknown>) {
	const entry = getRegistry()[name];
	const target = document.body.appendChild(document.createElement('div'));
	const handle = entry.mount(target, { isOpen: true, onClose: vi.fn(), ...props });
	mounted.push({ name, handle });
	return { entry, handle, props };
}

async function ready() {
	await vi.waitFor(() => expect(document.querySelector('[data-pick-tile]')).not.toBeNull());
	await settle(2);
	flushSync();
}

function q<T extends HTMLElement = HTMLElement>(selector: string): T {
	const el = document.querySelector<T>(selector);
	if (!el) throw new Error(`missing ${selector}`);
	return el;
}

async function click(el: HTMLElement) {
	el.click();
	await settle(2);
	flushSync();
}

function buttonByText(text: string): HTMLButtonElement | undefined {
	return Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find((b) => b.textContent?.trim() === text);
}

beforeEach(() => {
	apiMocks.getGenerationHistory.mockResolvedValue({ success: true, data: { generations: GENERATIONS, total: 3 } });
	apiMocks.listLibraryItems.mockResolvedValue({ success: true, data: { items: LIBRARY, total: 3 } });
	apiMocks.getTags.mockResolvedValue({ success: true, data: { tags: [] } });
	apiMocks.listCollections.mockResolvedValue({ success: true, data: { collections: [] } });
	apiMocks.getHistoryFacets.mockResolvedValue({ success: true, data: { modes: [], presets: [], models: [] } });
});

afterEach(() => {
	for (const { name, handle } of mounted) getRegistry()[name].unmount(handle);
	mounted = [];
	document.body.innerHTML = '';
	document.body.style.overflow = '';
	vi.clearAllMocks();
});

describe('GenerationHistoryModal, mounted the way plugins mount it', () => {
	it('is available under its documented name and locks the source: no tab strip', async () => {
		mountAsPlugin('GenerationHistoryModal', { title: 'Import from History' });
		await ready();
		expect(getRegistry().GenerationHistoryModal).toBeDefined();
		expect(document.querySelector('[data-picker-tabs]')).toBeNull();
		expect(apiMocks.listLibraryItems).not.toHaveBeenCalled();
		expect(q('h2').textContent).toBe('Import from History');
	});

	it('keeps its default title', async () => {
		mountAsPlugin('GenerationHistoryModal', {});
		await ready();
		expect(q('h2').textContent).toBe('Select Media from Generation History');
	});

	it('mediaType filters what is listed', async () => {
		mountAsPlugin('GenerationHistoryModal', { mediaType: 'video' });
		await vi.waitFor(() => expect(document.querySelector('[data-pick-tile]')).not.toBeNull());
		expect(apiMocks.getGenerationHistory.mock.calls.at(-1)![0]).toMatchObject({ mediaType: 'video' });
		expect(Array.from(document.querySelectorAll('[data-pick-tile]')).map((el) => el.getAttribute('data-pick-tile'))).toEqual(['g2:3']);
	});

	it('single select calls onSelect(generation, file) with the original generation, and leaves closing to the host', async () => {
		const onSelect = vi.fn();
		const onClose = vi.fn();
		mountAsPlugin('GenerationHistoryModal', { onSelect, onClose, mediaType: 'image' });
		await ready();
		await click(q('[aria-label^="Select b.png"]'));
		expect(onSelect).toHaveBeenCalledTimes(1);
		expect(onSelect.mock.calls[0][0].id).toBe('g1');
		expect(onSelect.mock.calls[0][0].files).toHaveLength(2);
		expect(onSelect.mock.calls[0][1]).toMatchObject({ id: 2, file_path: 'out/b.png' });
		expect(onClose).not.toHaveBeenCalled();
	});

	it('multiple: typed keys and items, Use selected calls onConfirm then onClose, onSelect is not called', async () => {
		const onSelect = vi.fn();
		const onSelectionChange = vi.fn();
		const onConfirm = vi.fn();
		const onClose = vi.fn();
		mountAsPlugin('GenerationHistoryModal', { multiple: true, mediaType: 'image', onSelect, onSelectionChange, onConfirm, onClose });
		await ready();
		await click(q('input[aria-label^="Select a.png"]'));
		await click(q('input[aria-label^="Select b.png"]'));
		expect(onSelectionChange).toHaveBeenLastCalledWith(['g1:1', 'g1:2'], expect.any(Array));
		const items = onSelectionChange.mock.calls.at(-1)![1];
		expect(items[0]).toMatchObject({ key: 'g1:1', mediaType: 'image', filename: 'a.png', origin: { kind: 'history', generationId: 'g1', fileId: 1 } });
		expect(items[0].generation.id).toBe('g1');
		expect(items[0].file.id).toBe(1);
		await click(q('[data-picker-confirm]'));
		expect(onConfirm).toHaveBeenCalledWith(['g1:1', 'g1:2'], expect.any(Array));
		expect(onClose).toHaveBeenCalled();
		expect(onSelect).not.toHaveBeenCalled();
	});

	it('Cancel, Escape and the close button restore the selection the picker opened with', async () => {
		const onSelectionChange = vi.fn();
		const onClose = vi.fn();
		mountAsPlugin('GenerationHistoryModal', { multiple: true, mediaType: 'image', selectedKeys: ['g1:1'], onSelectionChange, onClose });
		await ready();
		await click(q('input[aria-label^="Select b.png"]'));
		await click(buttonByText('Cancel')!);
		expect(onSelectionChange).toHaveBeenLastCalledWith(['g1:1'], expect.any(Array));
		expect(onClose).toHaveBeenCalledTimes(1);
		await click(q('input[aria-label^="Select b.png"]'));
		await click(q('button[aria-label="Close"]'));
		expect(onSelectionChange).toHaveBeenLastCalledWith(['g1:1'], expect.any(Array));
		expect(onClose).toHaveBeenCalledTimes(2);
	});

	it('follows selectedKeys, including pushes from the host after mount', async () => {
		const { entry, handle } = mountAsPlugin('GenerationHistoryModal', { multiple: true, mediaType: 'image', selectedKeys: ['g1:2'] });
		await ready();
		expect(q<HTMLInputElement>('input[aria-label^="Select b.png"]').checked).toBe(true);
		entry.update(handle, { selectedKeys: ['g1:1'] });
		await settle(2);
		flushSync();
		expect(q<HTMLInputElement>('input[aria-label^="Select a.png"]').checked).toBe(true);
		expect(q<HTMLInputElement>('input[aria-label^="Select b.png"]').checked).toBe(false);
	});

	it('selectableMediaTypes keeps other files listed but dimmed, with the documented reason', async () => {
		mountAsPlugin('GenerationHistoryModal', { multiple: true, selectableMediaTypes: ['image'] });
		await ready();
		const video = q('[data-pick-tile="g2:3"]');
		expect(video.querySelector<HTMLInputElement>('input')!.disabled).toBe(true);
		expect(video.textContent).toContain('Only image files can be selected here');
	});

	it('reports an item for every key, including ones it never loaded', async () => {
		const onConfirm = vi.fn();
		mountAsPlugin('GenerationHistoryModal', { multiple: true, mediaType: 'image', selectedKeys: ['g1:1', 'old:9'], onConfirm });
		await ready();
		await click(q('[data-picker-confirm]'));
		const [keys, items] = onConfirm.mock.calls[0];
		expect(items).toHaveLength(keys.length);
		expect(items[1]).toEqual({ key: 'old:9', mediaType: null, filename: null, origin: { kind: 'history', generationId: 'old', fileId: 9 } });
	});
});

describe('UploadLibraryModal, mounted the way plugins mount it', () => {
	it('is available under its documented name and locks the source: no tab strip', async () => {
		mountAsPlugin('UploadLibraryModal', {});
		await ready();
		expect(document.querySelector('[data-picker-tabs]')).toBeNull();
		expect(apiMocks.getGenerationHistory).not.toHaveBeenCalled();
		expect(q('h2').textContent).toBe('Select from Your Library');
	});

	it('single select calls onSelect(item) with the library item and leaves closing to the host', async () => {
		const onSelect = vi.fn();
		const onClose = vi.fn();
		mountAsPlugin('UploadLibraryModal', { onSelect, onClose, title: 'Pick a file', mediaType: 'image' });
		await ready();
		expect(q('h2').textContent).toBe('Pick a file');
		await click(q('[aria-label="Select two.png"]'));
		expect(onSelect).toHaveBeenCalledTimes(1);
		expect(onSelect.mock.calls[0][0]).toMatchObject({ id: 'l2', original_filename: 'two.png', media_type: 'image' });
		expect(onClose).not.toHaveBeenCalled();
	});

	it('multiple: keys are the item ids, items carry origin and item, Use selected confirms and closes', async () => {
		const onSelectionChange = vi.fn();
		const onConfirm = vi.fn();
		const onClose = vi.fn();
		mountAsPlugin('UploadLibraryModal', { multiple: true, onSelectionChange, onConfirm, onClose, selectableMediaTypes: ['image', 'video'] });
		await ready();
		await click(q('input[aria-label="Select one.png"]'));
		await click(q('input[aria-label="Select three.mp4"]'));
		expect(onSelectionChange).toHaveBeenLastCalledWith(['l1', 'l3'], expect.any(Array));
		const items = onSelectionChange.mock.calls.at(-1)![1];
		expect(items[1]).toMatchObject({ key: 'l3', mediaType: 'video', filename: 'three.mp4', origin: { kind: 'library', itemId: 'l3' } });
		expect(items[1].item.id).toBe('l3');
		await click(q('[data-picker-confirm]'));
		expect(onConfirm).toHaveBeenCalledWith(['l1', 'l3'], expect.any(Array));
		expect(onClose).toHaveBeenCalled();
	});

	it('dims what selectableMediaTypes does not allow', async () => {
		mountAsPlugin('UploadLibraryModal', { multiple: true, selectableMediaTypes: ['video'] });
		await ready();
		expect(q<HTMLInputElement>('input[aria-label="Select one.png"]').disabled).toBe(true);
		expect(q<HTMLInputElement>('input[aria-label="Select three.mp4"]').disabled).toBe(false);
		expect(q('[data-pick-tile="l1"]').textContent).toContain('Only video files can be selected here');
	});

	it('follows selectedKeys', async () => {
		mountAsPlugin('UploadLibraryModal', { multiple: true, selectedKeys: ['l2'] });
		await ready();
		expect(q<HTMLInputElement>('input[aria-label="Select two.png"]').checked).toBe(true);
	});
});

describe('the unified picker as a plugin component', () => {
	it('is offered to plugins next to the two documented names', () => {
		registerHostUiComponents();
		expect(getRegistry().MediaPickerModal).toBeDefined();
		expect(getRegistry().UploadLibraryModal).toBeDefined();
	});

	it('shows tabs for several sources and reports a pick through onPick', async () => {
		const onPick = vi.fn();
		const target = document.body.appendChild(document.createElement('div'));
		const { createClassComponent } = await import('svelte/legacy');
		const view = createClassComponent({
			component: MediaPickerModal as never,
			target,
			props: { isOpen: true, onClose: vi.fn(), sources: ['history', 'library'], mediaTypes: ['image'], onPick }
		});
		await ready();
		expect(document.querySelectorAll('[data-picker-tabs] button')).toHaveLength(2);
		await click(q('[aria-label^="Select a.png"]'));
		expect(onPick).toHaveBeenCalledTimes(1);
		view.$destroy();
	});
});

describe('the way the video-producer plugin mounts the pickers', () => {
	it('mounts with multiple, empty selectedKeys, selectableMediaTypes and closes through update({ isOpen: false })', async () => {
		const onConfirm = vi.fn();
		const { entry, handle } = mountAsPlugin('UploadLibraryModal', {
			title: 'Import',
			mediaType: 'video',
			multiple: true,
			selectedKeys: [],
			selectableMediaTypes: ['video'],
			onConfirm
		});
		await ready();
		expect(document.querySelectorAll('[data-pick-tile]')).toHaveLength(1);
		await click(q('input[aria-label="Select three.mp4"]'));
		await click(q('[data-picker-confirm]'));
		const items = onConfirm.mock.calls[0][1];
		expect(items[0].origin).toEqual({ kind: 'library', itemId: 'l3' });
		expect(items[0].filename).toBe('three.mp4');
		entry.update(handle, { isOpen: false });
		await settle(4);
		flushSync();
		expect(document.querySelector('[data-picker-body]')).toBeNull();
	});
});
