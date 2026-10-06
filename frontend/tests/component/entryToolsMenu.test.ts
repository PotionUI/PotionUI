// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeAll } from 'vitest';
import { flushSync } from 'svelte';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: vi.fn(() => ({
			get: vi.fn().mockRejectedValue(new Error('not mocked')),
			put: vi.fn().mockRejectedValue(new Error('not mocked'))
		})),
		getGenerationThumbnailURL: vi.fn(() => '/thumb.png')
	}
}));

const { default: GenerationCard } = await import('$lib/components/GenerationCard.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { registerEntryTools } = await import('$lib/tools/entryTools');
const { registerMediaTool, pluginMediaToolFromManifest } = await import('$lib/tools/tools');
const { createHistoryEntryMenu } = await import('$lib/tools/entryMenu');

import type { GenerationHistoryItem } from '$lib/types/history';
import type { ToolRunHost } from '$lib/tools/tools';

beforeAll(() => {
	vi.stubGlobal('ClipboardItem', class {});
	registerEntryTools();
	registerMediaTool({
		id: 'edit-image',
		label: 'Edit image',
		icon: 'paint-brush',
		shortcut: 'E',
		category: 'compose',
		source: 'core',
		scopes: ['history', 'library'],
		selection: { min: 1, max: 1 },
		kinds: ['image'],
		applies: (ctx) =>
			ctx.items.length === 1 ? { enabled: true } : { enabled: false, reason: 'Select exactly 1 image' },
		component: {} as never
	});
	registerMediaTool({
		id: 'compare',
		label: 'Compare',
		icon: 'layers',
		category: 'analyze',
		source: 'core',
		scopes: ['history', 'library'],
		selection: { min: 2, max: 2 },
		applies: (ctx) =>
			ctx.generations.length === 2 ? { enabled: true } : { enabled: false, reason: 'Select exactly 2 generations' },
		component: {} as never
	});
	registerMediaTool(
		pluginMediaToolFromManifest({
			id: 'upscale',
			plugin_id: 'upscaler',
			label: 'Upscale',
			category: 'compose',
			component: 'Upscale.js',
			applies_to: { max_selection: 1, media_kinds: ['image'] }
		})
	);
});

function file(type: string, index = 1) {
	return {
		id: index,
		file_type: type,
		is_final: true,
		file_path: `generations/2026-01-01/g/${index}.${type === 'image' ? 'png' : type === 'video' ? 'mp4' : type === 'audio' ? 'wav' : 'glb'}`,
		width: 1024,
		height: 768,
		duration_seconds: 4,
		created_at: '2026-01-01T00:00:00Z'
	};
}

function generation(id: string, overrides: Record<string, unknown> = {}): GenerationHistoryItem {
	return {
		id,
		preset_id: 'preset-a',
		form_data: { prompt: 'a castle' },
		status: 'completed',
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		files: [file('image')],
		rating: 2,
		is_favorite: false,
		...overrides
	} as unknown as GenerationHistoryItem;
}

interface Setup {
	generations?: GenerationHistoryItem[];
	selectedIds?: string[];
	host?: ToolRunHost;
	width?: number;
	selectable?: boolean;
}

function mountCard(gen: GenerationHistoryItem, setup: Setup = {}) {
	const host: ToolRunHost = setup.host ?? {
		open: vi.fn(),
		openGrid: vi.fn(),
		reuse: vi.fn(),
		remove: vi.fn(),
		cancel: vi.fn(),
		deselect: vi.fn(),
		rate: vi.fn(),
		favorite: vi.fn(),
		copyToLibrary: vi.fn(),
		addToCollection: vi.fn()
	};
	const all = setup.generations ?? [gen];
	const entryMenu = createHistoryEntryMenu({
		generations: () => all,
		selectedIds: () => setup.selectedIds ?? [],
		collections: () => [{ id: 'c1', name: 'Castles' }],
		host,
		onTool: vi.fn()
	});
	const target = document.createElement('div');
	document.body.appendChild(target);
	const deleted = vi.fn();
	const component = createClassComponent({
		component: GenerationCard as never,
		target,
		props: {
			generation: gen,
			tile: { width: setup.width ?? 320, height: 240 },
			entryMenu,
			showActions: !setup.selectable,
			selectable: setup.selectable ?? false,
			showCheckbox: true
		}
	});
	component.$on('deleteClick', (event: CustomEvent) => deleted(event.detail));
	return {
		host,
		deleted,
		target,
		card: () => target.querySelector<HTMLElement>('[data-generation-card]')!,
		media: () => target.querySelector<HTMLElement>('.media-zoom')!,
		rowLabels: () =>
			Array.from(target.querySelectorAll('[data-entry-actions] button')).map(
				(b) => b.getAttribute('aria-label') ?? ''
			),
		trigger: () => target.querySelector<HTMLButtonElement>('[data-entry-menu-trigger]')!,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

function menu(): HTMLElement | null {
	return document.body.querySelector('[data-tools-menu]');
}

function toolIds(): string[] {
	return Array.from(document.body.querySelectorAll('[data-tool]')).map((el) => el.getAttribute('data-tool')!);
}

function labelOf(id: string): string {
	return document.body.querySelector(`[data-tool="${id}"]`)?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
}

let mounted: ReturnType<typeof mountCard> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	document.body.innerHTML = '';
	vi.useRealTimers();
});

async function settle() {
	for (let i = 0; i < 4; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

describe('card hover row', () => {
	it('holds exactly Delete and the more-actions menu button', () => {
		mounted = mountCard(generation('g1'));
		expect(mounted.rowLabels()).toEqual(['Delete generation', 'More actions']);
	});

	it('no longer renders the favorite, details, download or reuse buttons', () => {
		mounted = mountCard(generation('g1'));
		const labels = Array.from(mounted.target.querySelectorAll('button')).map((b) => b.getAttribute('aria-label'));
		for (const retired of ['View generation details', 'Download', 'Reuse in this tab', 'Add to favorites']) {
			expect(labels).not.toContain(retired);
		}
	});

	it('Delete asks the page for the confirmed delete with the generation', () => {
		const gen = generation('g1');
		mounted = mountCard(gen);
		mounted.target.querySelector<HTMLButtonElement>('[aria-label="Delete generation"]')!.click();
		expect(mounted.deleted).toHaveBeenCalledWith(gen);
	});

	it('folds Delete into the menu on a narrow tile but keeps the menu button', () => {
		mounted = mountCard(generation('g1'), { width: 120 });
		expect(mounted.rowLabels()).toEqual(['More actions']);
		mounted.destroy();
		mounted = mountCard(generation('g1'), { width: 90 });
		expect(mounted.rowLabels()).toEqual(['More actions']);
		expect(mounted.trigger().style.width).toBe('20px');
	});

	it('shows only the menu button in selection mode', () => {
		mounted = mountCard(generation('g1'), { selectable: true });
		expect(mounted.rowLabels()).toEqual(['More actions']);
	});
});

describe('menu contents per variant', () => {
	async function open(gen: GenerationHistoryItem, setup: Setup = {}) {
		mounted = mountCard(gen, setup);
		mounted.trigger().click();
		await settle();
		return mounted;
	}

	it('image: grouped Open, Compose, Export, Organize and a red Delete last', async () => {
		await open(generation('g1'));
		expect(toolIds()).toEqual([
			'open-details',
			'reuse-settings',
			'edit-image',
			'upscale',
			'download',
			'copy-image',
			'copy-prompt',
			'rating',
			'favorite',
			'add-to-collection',
			'copy-to-library',
			'delete'
		]);
		const groups = Array.from(menu()!.querySelectorAll('[role="group"]')).map((el) => el.getAttribute('aria-label'));
		expect(groups).toEqual(['Open', 'Compose', 'Export', 'Organize', 'Danger']);
		expect(document.body.querySelector('[data-tool="delete"]')!.className).toContain('text-danger');
	});

	it('never lists a multi-item tool in the single-card menu', async () => {
		await open(generation('g1'));
		expect(toolIds()).not.toContain('compare');
		expect(toolIds()).not.toContain('stitch');
		expect(toolIds()).not.toContain('export-zip');
	});

	it('shows the shortcut hints', async () => {
		await open(generation('g1'));
		expect(labelOf('edit-image')).toContain('E');
		expect(labelOf('download')).toContain('D');
		expect(labelOf('favorite')).toContain('F');
		expect(labelOf('delete')).toContain('⌫');
		expect(labelOf('open-details')).toContain('↵');
	});

	it('plugin tools carry a PLUGIN tag, core tools do not', async () => {
		await open(generation('g1'));
		expect(labelOf('upscale').toLowerCase()).toContain('plugin');
		expect(labelOf('edit-image').toLowerCase()).not.toContain('plugin');
	});

	it('video: no image-only tools', async () => {
		await open(generation('g1', { files: [file('video')] }));
		expect(toolIds()).toContain('download');
		expect(toolIds()).not.toContain('edit-image');
		expect(toolIds()).not.toContain('copy-image');
		expect(toolIds()).not.toContain('upscale');
	});

	it('audio: details and export without image tools', async () => {
		await open(generation('g1', { files: [file('audio')] }));
		expect(toolIds()).not.toContain('edit-image');
		expect(toolIds()).not.toContain('copy-image');
		expect(toolIds()).toContain('open-details');
	});

	it('mesh: opens in the 3D viewer', async () => {
		await open(generation('g1', { files: [file('mesh')] }));
		expect(labelOf('open-details')).toContain('Open in 3D viewer');
		expect(toolIds()).not.toContain('edit-image');
	});

	it('X/Y stack: acts on the grid', async () => {
		await open(
			generation('g1', { grid: { id: 'grid-1', cols: 4, rows: 3, cell_count: 12 }, axis_values: { steps: '1' } })
		);
		expect(labelOf('open-details')).toContain('Open grid');
		expect(labelOf('delete')).toContain('Delete grid (12 cells)');
		expect(toolIds()).not.toContain('edit-image');
		expect(toolIds()).not.toContain('download');
		expect(toolIds()).not.toContain('copy-image');
	});

	it('loose cell: a normal image menu plus Show in grid', async () => {
		await open(generation('g1', { grid_id: 'grid-1', grid_x: 1, grid_y: 0 }));
		expect(toolIds()).toContain('show-in-grid');
		expect(toolIds()).toContain('edit-image');
	});

	it('failed: View error, Reuse, Copy error and Delete only', async () => {
		await open(generation('g1', { status: 'failed', files: [], error_message: 'boom' }));
		expect(toolIds()).toEqual(['open-details', 'reuse-settings', 'copy-error', 'delete']);
		expect(labelOf('open-details')).toContain('View error');
	});

	it('in progress: Tools waits for the finish and Delete becomes Cancel generation', async () => {
		await open(generation('g1', { status: 'running', files: [], progress: 0.4 }));
		expect(toolIds()).toEqual(['open-details', 'reuse-settings', 'pending-tools', 'delete']);
		const pending = document.body.querySelector('[data-tool="pending-tools"]')!;
		expect(pending.getAttribute('aria-disabled')).toBe('true');
		expect(pending.textContent).toContain('when finished');
		expect(labelOf('delete')).toContain('Cancel generation');
	});

	it('runs the picked tool against the entry context', async () => {
		await open(generation('g1'));
		document.body.querySelector<HTMLButtonElement>('[data-tool="open-details"]')!.click();
		expect(mounted!.host.open).toHaveBeenCalledTimes(1);
		const ctx = (mounted!.host.open as ReturnType<typeof vi.fn>).mock.calls[0][0];
		expect(ctx.generationIds).toEqual(['g1']);
	});

	it('Delete in the menu goes through the host remove path', async () => {
		await open(generation('g1'));
		document.body.querySelector<HTMLButtonElement>('[data-tool="delete"]')!.click();
		expect(mounted!.host.remove).toHaveBeenCalledTimes(1);
	});

	it('hides a tool the host cannot perform', async () => {
		await open(generation('g1'), { host: { open: vi.fn(), remove: vi.fn() } });
		expect(toolIds()).not.toContain('reuse-settings');
		expect(toolIds()).not.toContain('favorite');
		expect(toolIds()).toContain('open-details');
	});
});

describe('selection interplay', () => {
	const a = generation('a');
	const b = generation('b');
	const c = generation('c');

	it('acts on the selection from a selected card, under a signal header', async () => {
		mounted = mountCard(a, { generations: [a, b, c], selectedIds: ['a', 'b', 'c'] });
		mounted.trigger().click();
		await settle();
		const header = document.body.querySelector('[data-menu-header]')!;
		expect(header.textContent?.trim()).toBe('3 selected');
		expect(header.className).toContain('text-signal');
		expect(toolIds()).toContain('compare');
		expect(document.body.querySelector('[data-tool="compare"]')!.getAttribute('aria-disabled')).toBe('true');
		expect(toolIds()).toContain('deselect');
		expect(toolIds()).not.toContain('open-details');
		expect(labelOf('delete')).toContain('Delete 3');
		document.body.querySelector<HTMLButtonElement>('[data-tool="delete"]')!.click();
		const ctx = (mounted.host.remove as ReturnType<typeof vi.fn>).mock.calls[0][0];
		expect(ctx.generationIds).toEqual(['a', 'b', 'c']);
	});

	it('acts on that card alone when it is not part of the selection', async () => {
		mounted = mountCard(c, { generations: [a, b, c], selectedIds: ['a', 'b'] });
		mounted.trigger().click();
		await settle();
		expect(document.body.querySelector('[data-menu-header]')).toBeNull();
		expect(toolIds()).toContain('open-details');
		expect(toolIds()).not.toContain('deselect');
		expect(labelOf('delete')).toBe('Delete ⌫');
	});

	it('Deselect this card removes only that card from the selection', async () => {
		mounted = mountCard(b, { generations: [a, b, c], selectedIds: ['a', 'b', 'c'] });
		mounted.trigger().click();
		await settle();
		document.body.querySelector<HTMLButtonElement>('[data-tool="deselect"]')!.click();
		expect(mounted.host.deselect).toHaveBeenCalledWith(expect.anything(), 'b');
	});
});

describe('opening gestures', () => {
	it('right-click on the card opens the menu at the pointer', async () => {
		mounted = mountCard(generation('g1'));
		const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 120, clientY: 80 });
		mounted.media().dispatchEvent(event);
		await settle();
		expect(event.defaultPrevented).toBe(true);
		expect(menu()).not.toBeNull();
		expect(toolIds()).toContain('open-details');
	});

	it('shift+right-click leaves the browser menu alone', async () => {
		mounted = mountCard(generation('g1'));
		const event = new MouseEvent('contextmenu', {
			bubbles: true,
			cancelable: true,
			clientX: 120,
			clientY: 80,
			shiftKey: true
		});
		mounted.media().dispatchEvent(event);
		await settle();
		expect(event.defaultPrevented).toBe(false);
		expect(menu()).toBeNull();
	});

	it('a touch long-press opens the bottom sheet', async () => {
		vi.useFakeTimers();
		mounted = mountCard(generation('g1'));
		const down = new MouseEvent('pointerdown', { bubbles: true, clientX: 50, clientY: 50 });
		Object.defineProperty(down, 'pointerType', { value: 'touch' });
		mounted.media().dispatchEvent(down);
		vi.advanceTimersByTime(520);
		flushSync();
		vi.useRealTimers();
		await settle();
		expect(document.body.querySelector('[data-tools-sheet]')).not.toBeNull();
		expect(toolIds()).toContain('delete');
	});

	it('a touch that moves before the threshold does not open the sheet', async () => {
		vi.useFakeTimers();
		mounted = mountCard(generation('g1'));
		const down = new MouseEvent('pointerdown', { bubbles: true, clientX: 50, clientY: 50 });
		Object.defineProperty(down, 'pointerType', { value: 'touch' });
		mounted.media().dispatchEvent(down);
		const move = new MouseEvent('pointermove', { bubbles: true, clientX: 90, clientY: 90 });
		mounted.media().dispatchEvent(move);
		vi.advanceTimersByTime(600);
		flushSync();
		vi.useRealTimers();
		await settle();
		expect(document.body.querySelector('[data-tools-sheet]')).toBeNull();
	});
});
