// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';

const apiMocks = vi.hoisted(() => ({
	listLibraryItems: vi.fn(),
	deleteLibraryItem: vi.fn(),
	getTags: vi.fn(),
	getClient: () => ({ get: vi.fn().mockResolvedValue({ data: { data: {} } }) })
}));

vi.mock('$lib/services/api/index', () => ({
	api: new Proxy(apiMocks as Record<string, any>, {
		get: (target, key: string) =>
			key in target ? target[key] : vi.fn().mockResolvedValue({ success: false })
	})
}));

const { default: UploadLibraryModal } = await import('$lib/components/modals/UploadLibraryModal.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');
const { stubClientWidth } = await import('./helpers/stubClientWidth');

function libraryItem(id: string, mediaType: string, name: string, extra: Record<string, unknown> = {}) {
	return {
		id,
		filename: `stored-${name}`,
		original_filename: name,
		media_type: mediaType,
		url: `/media/${id}`,
		tags: [],
		...extra
	};
}

const ITEMS = [
	libraryItem('wide', 'image', 'wide.png', { width: 1600, height: 900, thumbnail_medium: '/thumb/wide.png' }),
	libraryItem('tall', 'image', 'tall.png', { width: 900, height: 1600, thumbnail_medium: '/thumb/tall.png' }),
	libraryItem('square', 'image', 'square.png', { width: 1000, height: 1000 }),
	libraryItem('clip', 'video', 'clip.mp4', {
		width: 1280,
		height: 720,
		duration_seconds: 8,
		thumbnail_medium: '/thumb/clip.png'
	}),
	libraryItem('bare', 'video', 'bare.mp4'),
	libraryItem('song', 'audio', 'song.wav', { duration_seconds: 12 })
];

let components: Array<{ $destroy: () => void }> = [];
let restoreWidth: () => void = () => {};

async function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	components.push(
		createClassComponent({
			component: UploadLibraryModal as never,
			target,
			props: { isOpen: true, onClose: () => {}, ...props }
		})
	);
	await vi.waitFor(() =>
		expect(document.querySelectorAll('[data-testid="library-file-tile"]').length).toBeGreaterThan(0)
	);
	await tick();
}

function tile(id: string): HTMLElement {
	const el = document.querySelector<HTMLElement>(`[data-item-id="${id}"]`);
	if (!el) throw new Error(`no tile for ${id}`);
	return el;
}

function frame(id: string): HTMLElement {
	return tile(id).querySelector<HTMLElement>('[style*="aspect-ratio"]')!;
}

function frameRatio(id: string): number {
	return parseFloat(/aspect-ratio:\s*([\d.]+)/.exec(frame(id).getAttribute('style') ?? '')?.[1] ?? 'NaN');
}

function checkbox(name: string): HTMLInputElement {
	const el = document.querySelector<HTMLInputElement>(`input[aria-label="Select ${name}"]`);
	if (!el) throw new Error(`no checkbox for ${name}`);
	return el;
}

function tileButton(id: string): HTMLButtonElement {
	return tile(id).querySelector<HTMLButtonElement>('button[aria-hidden="true"]')!;
}

function count(): string {
	return document.querySelector('[data-testid="picker-selected-count"]')?.textContent?.trim() ?? '';
}

function buttonByText(text: string): HTMLButtonElement | undefined {
	return [...document.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent?.trim() === text);
}

beforeEach(() => {
	restoreWidth = stubClientWidth(1000);
	apiMocks.listLibraryItems.mockResolvedValue({ success: true, data: { items: ITEMS, total: ITEMS.length } });
	apiMocks.deleteLibraryItem.mockResolvedValue({ success: true, data: { id: 'square', deleted: true } });
	apiMocks.getTags.mockResolvedValue({ success: true, data: { tags: [] } });
});

afterEach(() => {
	restoreWidth();
	components.forEach((c) => c.$destroy());
	components = [];
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

describe('library picker mosaic', () => {
	it.each([false, true])('renders Library tiles at native aspect ratios (multiple=%s)', async (multiple) => {
		await mount({ multiple });

		expect(document.querySelectorAll('[data-pick-tile]').length).toBe(ITEMS.length);
		expect(frameRatio('wide')).toBeCloseTo(16 / 9, 1);
		expect(frameRatio('tall')).toBeCloseTo(9 / 16, 1);
		expect(frameRatio('square')).toBeCloseTo(1, 1);
		expect(frameRatio('clip')).toBeCloseTo(16 / 9, 1);
		expect(frameRatio('tall')).toBeLessThan(frameRatio('wide'));
	});

	it('sizes each tile by its own ratio so a tall one is narrower than a wide one', async () => {
		await mount({ multiple: true });

		const basis = (id: string) => parseFloat(/--r:\s*([\d.]+)/.exec(tile(id).getAttribute('style') ?? '')?.[1] ?? 'NaN');
		expect(basis('wide')).toBeCloseTo(16 / 9, 1);
		expect(basis('tall')).toBeLessThan(basis('wide'));
		expect(tile('wide').getAttribute('style')).toContain('flex:');
	});

	it('shows the picture in a frame of the native aspect, from the thumbnail when there is one', async () => {
		await mount({ multiple: true });

		expect(frame('wide').querySelector('img')!.getAttribute('src')).toBe('/thumb/wide.png');
		expect(frame('square').querySelector('img')!.getAttribute('src')).toBe('/media/square');
		expect(frame('wide').querySelector('img')!.classList.contains('object-cover')).toBe(true);
	});

	it('shows a video through its thumbnail and audio as a waveform with its duration', async () => {
		await mount({ multiple: true });

		expect(frame('clip').querySelector('img')!.getAttribute('src')).toBe('/thumb/clip.png');
		expect(frame('clip').querySelector('video')).toBeNull();
		expect(frame('clip').textContent).toContain('0:08');

		expect(frame('song').querySelector('img, video')).toBeNull();
		expect(frame('song').querySelector('i')).not.toBeNull();
		expect(frame('song').textContent).toContain('0:12');
		expect(frameRatio('song')).toBeCloseTo(16 / 9, 1);
	});

	it('captions each tile with the file name', async () => {
		await mount({ multiple: true });
		expect(tile('wide').textContent).toContain('wide.png');
		expect(tile('song').textContent).toContain('song.wav');
	});

	it('shows the empty state instead of a mosaic when nothing matches', async () => {
		apiMocks.listLibraryItems.mockResolvedValue({ success: true, data: { items: [], total: 0 } });
		const target = document.createElement('div');
		document.body.appendChild(target);
		components.push(
			createClassComponent({
				component: UploadLibraryModal as never,
				target,
				props: { isOpen: true, onClose: () => {}, multiple: true }
			})
		);
		await vi.waitFor(() => expect(document.body.textContent).toContain('Nothing here yet'));
		expect(document.querySelectorAll('[data-pick-tile]').length).toBe(0);
	});
});

describe('library picker mosaic multi-select', () => {
	it('toggles selection from the labelled checkbox and counts it', async () => {
		const onSelectionChange = vi.fn();
		await mount({ multiple: true, onSelectionChange });

		const box = checkbox('tall.png');
		box.click();
		await tick();

		expect(onSelectionChange).toHaveBeenLastCalledWith(['tall'], expect.any(Array));
		expect(box.checked).toBe(true);
		expect(box.closest('label')).not.toBeNull();
		expect(tile('tall').getAttribute('data-selected')).toBe('true');
		expect(tile('wide').getAttribute('data-selected')).toBe('false');
		expect(count()).toBe('1 selected');
		expect(checkbox('wide.png').checked).toBe(false);
	});

	it('toggles selection when the tile is clicked and keeps the checkbox in sync', async () => {
		const onSelectionChange = vi.fn();
		await mount({ multiple: true, onSelectionChange });

		tileButton('clip').click();
		await tick();
		expect(onSelectionChange).toHaveBeenLastCalledWith(['clip'], expect.any(Array));
		expect(checkbox('clip.mp4').checked).toBe(true);
		expect(tile('clip').getAttribute('data-selected')).toBe('true');
		expect(document.querySelector('button[aria-label^="Preview"]')).toBeNull();

		tileButton('clip').click();
		await tick();
		expect(onSelectionChange).toHaveBeenLastCalledWith([], expect.any(Array));
		expect(checkbox('clip.mp4').checked).toBe(false);
	});

	it('marks a selected card so the mosaic shows which tiles are picked', async () => {
		await mount({ multiple: true, selectedKeys: ['wide'] });
		expect(tile('wide').querySelector('.bg-signal\\/15')).not.toBeNull();
		expect(tile('tall').querySelector('.bg-signal\\/15')).toBeNull();
	});

	it('disables media outside the type constraint and states the reason on that tile', async () => {
		const onSelectionChange = vi.fn();
		await mount({ multiple: true, onSelectionChange, selectableMediaTypes: ['image'] });

		const box = checkbox('song.wav');
		expect(box.disabled).toBe(true);
		expect(tile('song').textContent).toContain('Only image files can be selected here');
		expect(tile('song').querySelector('.opacity-40')).not.toBeNull();

		box.click();
		expect(tileButton('song').disabled).toBe(true);
		tileButton('song').click();
		expect(onSelectionChange).not.toHaveBeenCalled();

		expect(checkbox('wide.png').disabled).toBe(false);
		expect(tile('wide').textContent).not.toContain('Only image files can be selected here');
		expect(tile('wide').querySelector('.opacity-40')).toBeNull();
	});

	it('confirms the picked tiles', async () => {
		const onConfirm = vi.fn();
		const onClose = vi.fn();
		await mount({ multiple: true, onConfirm, onClose });

		expect(buttonByText('Use selected')!.disabled).toBe(true);
		checkbox('tall.png').click();
		checkbox('song.wav').click();
		await tick();
		buttonByText('Use selected')!.click();

		expect(onConfirm).toHaveBeenCalledWith(['tall', 'song'], expect.any(Array));
		expect(onClose).toHaveBeenCalled();
	});
});

describe('library picker mosaic single-pick', () => {
	it('offers button tiles without checkboxes, magnifiers or a selection bar', async () => {
		await mount({ multiple: false });

		expect(document.querySelector('input[type="checkbox"]')).toBeNull();
		expect(document.querySelector('[data-testid="picker-selected-count"]')).toBeNull();
		expect(document.querySelector('button[aria-label^="Preview "]')).toBeNull();
		expect(document.querySelectorAll('button[aria-label^="Select "]').length).toBe(ITEMS.length);
	});

	it('hands the clicked library item to onSelect', async () => {
		const onSelect = vi.fn();
		await mount({ multiple: false, onSelect });

		document.querySelector<HTMLButtonElement>('button[aria-label="Select tall.png"]')!.click();
		expect(onSelect).toHaveBeenCalledTimes(1);
		expect(onSelect.mock.calls[0][0]).toMatchObject({ id: 'tall', original_filename: 'tall.png' });

		document.querySelector<HTMLButtonElement>('button[aria-label="Select song.wav"]')!.click();
		expect(onSelect.mock.calls[1][0]).toMatchObject({ id: 'song', media_type: 'audio' });
	});

	it('offers no delete control on a tile: the Library page owns deletion', async () => {
		await mount({ multiple: false });

		expect(document.querySelector('button[aria-label^="Delete "]')).toBeNull();
		expect(apiMocks.deleteLibraryItem).not.toHaveBeenCalled();
	});
});
