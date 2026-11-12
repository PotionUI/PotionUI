// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';

const apiMocks = vi.hoisted(() => ({
	getGenerationHistory: vi.fn(),
	getTags: vi.fn()
}));

vi.mock('$lib/services/api/index', () => ({
	api: new Proxy(apiMocks as Record<string, any>, {
		get: (target, key: string) =>
			key in target ? target[key] : vi.fn().mockResolvedValue({ success: false })
	})
}));

const { default: GenerationHistoryModal } = await import('$lib/components/modals/GenerationHistoryModal.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');
const { stubClientWidth } = await import('./helpers/stubClientWidth');

function file(id: number, type: string, name: string, width?: number, height?: number) {
	return {
		id,
		file_path: `out/${name}`,
		file_type: type,
		is_final: true,
		width,
		height,
		created_at: '2026-01-01T00:00:00Z'
	};
}

function generation(id: string, prompt: string, files: ReturnType<typeof file>[]) {
	return {
		id,
		preset_name: 'Preset',
		status: 'completed',
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		form_data: { prompt },
		files,
		rating: 0,
		is_favorite: false
	};
}

const GENERATIONS = [
	generation('wide', 'harbour at dusk', [file(1, 'image', 'wide.png', 1600, 900)]),
	generation('tall', 'standing portrait', [file(2, 'image', 'tall.png', 900, 1600)]),
	generation('pair', 'two crops', [file(3, 'image', 'pair-a.png', 1000, 1000), file(4, 'image', 'pair-b.png', 1000, 1000)]),
	generation('song', 'lullaby', [file(5, 'audio', 'song.wav')])
];

let components: Array<{ $destroy: () => void }> = [];
let restoreWidth: () => void = () => {};

async function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	components.push(
		createClassComponent({
			component: GenerationHistoryModal as never,
			target,
			props: { isOpen: true, onClose: () => {}, ...props }
		})
	);
	await vi.waitFor(() => expect(document.querySelectorAll('[data-testid="history-file-tile"]').length).toBeGreaterThan(0));
	await tick();
}

function tile(fileId: number): HTMLElement {
	const el = document.querySelector<HTMLElement>(`[data-file-id="${fileId}"]`);
	if (!el) throw new Error(`no tile for file ${fileId}`);
	return el;
}

function frame(fileId: number): HTMLElement {
	return tile(fileId).querySelector<HTMLElement>('[style*="aspect-ratio"]')!;
}

function frameRatio(fileId: number): number {
	return parseFloat(/aspect-ratio:\s*([\d.]+)/.exec(frame(fileId).getAttribute('style') ?? '')?.[1] ?? 'NaN');
}

beforeEach(() => {
	restoreWidth = stubClientWidth(1000);
	apiMocks.getGenerationHistory.mockResolvedValue({ success: true, data: { generations: GENERATIONS, total: GENERATIONS.length } });
	apiMocks.getTags.mockResolvedValue({ success: true, data: { tags: [] } });
});

afterEach(() => {
	restoreWidth();
	components.forEach((c) => c.$destroy());
	components = [];
	document.body.innerHTML = '';
	vi.clearAllMocks();
});

describe('history picker mosaic', () => {
	it.each([false, true])('renders History tiles with native aspect ratios (multiple=%s)', async (multiple) => {
		await mount({ multiple });

		expect(document.querySelectorAll('[data-pick-tile]').length).toBe(5);
		expect(frameRatio(1)).toBeCloseTo(16 / 9, 1);
		expect(frameRatio(2)).toBeCloseTo(9 / 16, 1);
		expect(frameRatio(3)).toBeCloseTo(1, 1);
		expect(frameRatio(2)).toBeLessThan(frameRatio(1));
	});

	it('offers one tile per file of a multi-file generation', async () => {
		await mount({ multiple: true });
		const ids = [...document.querySelectorAll('[data-testid="history-file-tile"]')].map((el) => el.getAttribute('data-file-id'));
		expect(ids).toEqual(['1', '2', '3', '4', '5']);
	});

	it('captions each tile with the prompt and keeps the file name in the accessible name', async () => {
		await mount({ multiple: true });
		expect(tile(1).textContent).toContain('harbour at dusk');
		expect(document.querySelector('input[aria-label="Select wide.png (harbour at dusk)"]')).not.toBeNull();
		expect(document.querySelector('button[aria-label^="Preview"]')).toBeNull();
	});

	it('keeps audio pickable in a waveform tile', async () => {
		const onSelect = vi.fn();
		await mount({ onSelect });
		expect(frame(5).querySelector('i')).not.toBeNull();
		tile(5).querySelector<HTMLElement>('button')!.click();
		expect(onSelect).toHaveBeenCalledTimes(1);
		expect(onSelect.mock.calls[0][0]).toMatchObject({ id: 'song' });
		expect(onSelect.mock.calls[0][1]).toMatchObject({ id: 5 });
	});

	it('hands the original generation, not the per-file view, to onSelect', async () => {
		const onSelect = vi.fn();
		await mount({ onSelect });
		tile(4).querySelector<HTMLElement>('button')!.click();
		expect(onSelect.mock.calls[0][0].files.map((f: { id: number }) => f.id)).toEqual([3, 4]);
		expect(onSelect.mock.calls[0][1]).toMatchObject({ id: 4 });
	});

	it('toggles selection from the card checkbox and counts it', async () => {
		const onSelectionChange = vi.fn();
		await mount({ multiple: true, onSelectionChange });
		const box = document.querySelector<HTMLInputElement>('input[aria-label^="Select pair-b.png"]')!;
		box.click();
		await tick();
		expect(onSelectionChange).toHaveBeenLastCalledWith(['pair:4'], expect.any(Array));
		expect(box.checked).toBe(true);
		expect(document.querySelector('[data-testid="picker-selected-count"]')?.textContent?.trim()).toBe('1 selected');
		expect(document.querySelector<HTMLInputElement>('input[aria-label^="Select pair-a.png"]')!.checked).toBe(false);
	});

	it('disables files outside the type constraint and states the reason', async () => {
		const onSelectionChange = vi.fn();
		await mount({ multiple: true, onSelectionChange, selectableMediaTypes: ['image'] });
		const box = document.querySelector<HTMLInputElement>('input[aria-label^="Select song.wav"]')!;
		expect(box.disabled).toBe(true);
		expect(tile(5).textContent).toContain('Only image files can be selected here');
		box.click();
		tile(5).querySelector<HTMLButtonElement>('button[aria-hidden="true"]')!.click();
		expect(onSelectionChange).not.toHaveBeenCalled();
		expect(document.querySelector<HTMLInputElement>('input[aria-label^="Select wide.png"]')!.disabled).toBe(false);
	});

	it('toggles selection when the tile image is clicked and keeps the checkbox in sync', async () => {
		const onSelectionChange = vi.fn();
		await mount({ multiple: true, onSelectionChange });
		const image = tile(4).querySelector<HTMLButtonElement>('button[aria-hidden="true"]')!;
		image.click();
		await tick();
		expect(onSelectionChange).toHaveBeenLastCalledWith(['pair:4'], expect.any(Array));
		expect(document.querySelector<HTMLInputElement>('input[aria-label^="Select pair-b.png"]')!.checked).toBe(true);
		image.click();
		await tick();
		expect(onSelectionChange).toHaveBeenLastCalledWith([], expect.any(Array));
	});
});
