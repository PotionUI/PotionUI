import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getOrganizeProvenance: vi.fn(() => Promise.resolve({ success: true, data: [] })),
		getClient: vi.fn(() => ({
			get: vi.fn().mockRejectedValue(new Error('not mocked')),
			post: vi.fn().mockRejectedValue(new Error('not mocked')),
			put: vi.fn().mockRejectedValue(new Error('not mocked'))
		})),
		listPresets: vi.fn().mockResolvedValue({ success: true, data: [] }),
		getGenerationParams: vi.fn().mockResolvedValue({ success: true, data: { parameters: {}, models: [] } }),
		getGenerationById: vi.fn().mockResolvedValue({ success: false, error: 'not mocked' }),
		getGenerationImageURL: vi.fn(() => '/media.png'),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } }),
		getBaseURL: vi.fn(() => ''),
		getToken: vi.fn(() => null),
		setOnAuthExpired: vi.fn()
	}
}));

const { default: GridCellViewer } = await import('$lib/generation/compare/view/GridCellViewer.svelte');
const { default: GridDetailsCard } = await import('$lib/generation/compare/view/GridDetailsCard.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { emptyCell } = await import('$lib/generation/compare/serverGrid');

type Cell = ReturnType<typeof emptyCell>;

const sampler = {
	field: 'sampler',
	type: 'select',
	label: 'Sampler',
	values: ['euler', 'er_sde', 'dpmpp_2m', 'dpmpp_2m_sde'].map((v) => ({ value: v, label: v }))
};
const scheduler = {
	field: 'scheduler',
	type: 'select',
	label: 'Scheduler',
	values: ['simple', 'beta', 'karras'].map((v) => ({ value: v, label: v }))
};

function makeGrid(over: Record<number, Partial<Cell>> = {}) {
	const cells: Cell[] = [];
	for (let i = 0; i < 12; i += 1) {
		const x = i % 4;
		const y = Math.floor(i / 4);
		cells.push({
			...emptyCell(x, y),
			generationId: `gen-${i}`,
			status: 'completed',
			thumbnailUrl: `/t${i}.png`,
			mediaType: 'image',
			seed: 4211984,
			axisValues: { sampler: sampler.values[x].label, scheduler: scheduler.values[y].label },
			...over[i]
		});
	}
	return {
		id: 'g1',
		config: { armed: true, x: sampler, y: scheduler, lockSeed: true },
		cols: 4,
		rows: 3,
		cells
	};
}

function mountViewer(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: GridCellViewer as never,
		target,
		props: { grid: makeGrid(), index: 5, onIndex: vi.fn(), onClose: vi.fn(), ...props }
	});
	return {
		set: (next: Record<string, unknown>) => component.$set(next as never),
		press: (key: string, init: KeyboardEventInit = {}) =>
			window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, ...init })),
		button: (text: string) =>
			[...document.body.querySelectorAll('button')].find((b) => b.textContent?.includes(text)) as HTMLButtonElement | undefined,
		text: () => document.body.textContent?.replace(/\s+/g, ' ') ?? '',
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

let mounted: ReturnType<typeof mountViewer> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('grid cell viewer', () => {
	it('reuses the details modal with a Compare grid title, the position and the status', async () => {
		mounted = mountViewer({});
		await flush();
		expect(mounted.text()).toContain('Compare grid');
		expect(document.body.querySelector('[data-generation-position]')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('6 / 12');
		expect(mounted.text().toLowerCase()).toContain('completed');
	});

	it('tags the axis fields in Parameters even when the run did not record them as parameters', async () => {
		mounted = mountViewer({});
		for (let i = 0; i < 5; i += 1) await flush();
		const tags = [...document.body.querySelectorAll('[data-testid="axis-tag"]')];
		expect(tags.map((tag) => tag.textContent?.trim())).toEqual(['X', 'Y']);
		const rows = tags.map((tag) => tag.parentElement?.parentElement?.textContent?.replace(/\s+/g, ' ').trim());
		expect(rows[0]).toContain('er_sde');
		expect(rows[1]).toContain('beta');
	});

	it('lists the seed once, labelled by the axis, when the seed is an axis', async () => {
		const seed = { field: 'seed', type: 'seed', label: 'Seed', values: [4211984, 7].map((v) => ({ value: v, label: String(v) })) };
		const base = makeGrid();
		const grid = {
			...base,
			config: { ...base.config, y: seed },
			cells: base.cells.map((cell, i) => ({ ...cell, axisValues: { sampler: sampler.values[i % 4].label, seed: '7' } }))
		};
		mounted = mountViewer({ grid });
		await flush();
		const card = document.body.querySelector('[data-testid="cell-viewer-card"]') as HTMLElement;
		const labels = [...card.querySelectorAll('span')].map((span) => span.textContent?.trim().toLowerCase());
		expect(labels.filter((label) => label === 'seed')).toHaveLength(1);
		expect(card.textContent).not.toMatch(/locked|random/);
	});

	it('keeps the locked seed row when the seed is not an axis', async () => {
		mounted = mountViewer({});
		await flush();
		const card = document.body.querySelector('[data-testid="cell-viewer-card"]') as HTMLElement;
		expect(card.textContent?.replace(/\s+/g, ' ')).toContain('4211984 locked');
	});

	it('says what Use these settings puts in, prompt replacement included', async () => {
		const prompt = {
			field: '__prompt__',
			type: 'prompt',
			label: 'Prompt: find and replace',
			values: ['dusk', 'dawn'].map((v) => ({ value: { find: 'dusk', replace: v }, label: v }))
		};
		const seed = { field: 'seed', type: 'seed', label: 'Seed', values: [4211984, 7].map((v) => ({ value: v, label: String(v) })) };
		const base = makeGrid();
		const grid = {
			...base,
			config: { ...base.config, x: prompt, y: seed },
			cols: 2,
			rows: 2,
			cells: base.cells.slice(0, 4).map((cell, i) => ({
				...cell,
				x: i % 2,
				y: Math.floor(i / 2),
				axisValues: { __prompt__: 'dawn', seed: '7' }
			}))
		};
		mounted = mountViewer({ grid, index: 3 });
		await flush();
		const note = document.body.querySelector('[data-testid="use-settings-note"]');
		expect(note?.textContent?.replace(/\s+/g, ' ')).toContain('seed = 7');
		expect(note?.textContent).toContain('prompt "dusk" replaced with "dawn"');
	});

	it('walks the grid in 2D with the arrow keys', async () => {
		const onIndex = vi.fn();
		mounted = mountViewer({ onIndex });
		await flush();
		mounted.press('ArrowRight');
		mounted.press('ArrowLeft');
		mounted.press('ArrowDown');
		mounted.press('ArrowUp');
		expect(onIndex.mock.calls.map((c) => c[0])).toEqual([6, 4, 9, 1]);
	});

	it('stays on the cell at an edge and does not fall through to history navigation', async () => {
		const onIndex = vi.fn();
		mounted = mountViewer({ onIndex, index: 0 });
		await flush();
		mounted.press('ArrowLeft');
		mounted.press('ArrowUp');
		expect(onIndex).not.toHaveBeenCalled();
	});

	it('steps with the header previous and next buttons', async () => {
		const onIndex = vi.fn();
		mounted = mountViewer({ onIndex });
		await flush();
		(document.body.querySelector('button[aria-label="Next generation"]') as HTMLButtonElement).click();
		await flush();
		(document.body.querySelector('button[aria-label="Previous generation"]') as HTMLButtonElement).click();
		await flush();
		expect(onIndex.mock.calls.map((c) => c[0])).toEqual([6, 4]);
	});

	it('shows the cell axis values and a mini map of every cell', async () => {
		mounted = mountViewer({});
		await flush();
		expect(mounted.text()).toContain('This cell');
		expect(mounted.text()).toContain('er_sde');
		expect(mounted.text()).toContain('beta');
		const cells = document.body.querySelectorAll('[data-testid="grid-minimap-cell"]');
		expect(cells).toHaveLength(12);
		expect(cells[5].getAttribute('aria-current')).toBe('true');
	});

	it('shows the server elapsed time in the cell card', async () => {
		mounted = mountViewer({ grid: makeGrid({ 5: { elapsedSeconds: 22.2 } }), seconds: 99 });
		await flush();
		expect(mounted.text()).toContain('Time 22 s');
		mounted.set({ grid: makeGrid({ 5: { elapsedSeconds: null } }) });
		await flush();
		expect(mounted.text()).toContain('Time 99 s');
	});

	it('shows failed cells in the mini map and counts them on Retry failed', async () => {
		const grid = makeGrid({ 6: { status: 'failed', error: 'GPU ran out of memory.' } });
		const onRetryFailed = vi.fn();
		mounted = mountViewer({ grid, onRetryFailed });
		await flush();
		const map = [...document.body.querySelectorAll('[data-testid="grid-minimap-cell"]')];
		expect(map[6].getAttribute('data-cell-state')).toBe('failed');
		const retry = mounted.button('Retry failed 1') as HTMLButtonElement;
		expect(retry.disabled).toBe(false);
		retry.click();
		expect(onRetryFailed).toHaveBeenCalled();
	});

	it('disables Retry failed when nothing failed', async () => {
		mounted = mountViewer({});
		await flush();
		expect((mounted.button('Retry failed') as HTMLButtonElement).disabled).toBe(true);
	});

	it('jumps to a cell from the mini map', async () => {
		const onIndex = vi.fn();
		mounted = mountViewer({ onIndex });
		await flush();
		(document.body.querySelectorAll('[data-testid="grid-minimap-cell"]')[9] as HTMLButtonElement).click();
		expect(onIndex).toHaveBeenCalledWith(9);
	});

	it('writes the cell settings through Use these settings and says what it will write', async () => {
		const onUse = vi.fn();
		mounted = mountViewer({ onUse });
		await flush();
		expect(document.body.querySelector('[data-testid="use-settings-note"]')?.textContent?.replace(/\s+/g, ' ').trim()).toBe(
			'Puts sampler = er_sde and scheduler = beta into the form.'
		);
		(mounted.button('Use these settings') as HTMLButtonElement).click();
		expect(onUse).toHaveBeenCalledWith(5);
	});

	it('cannot use a cell whose axes the form cannot take', async () => {
		const grid = makeGrid() as Record<string, any>;
		grid.config = {
			...grid.config,
			x: { ...sampler, field: '__prompt__', type: 'prompt' },
			y: null
		};
		mounted = mountViewer({ grid });
		await flush();
		expect((mounted.button('Use these settings') as HTMLButtonElement).disabled).toBe(true);
	});

	it('exports the stitched grid', async () => {
		const onExport = vi.fn();
		mounted = mountViewer({ onExport });
		await flush();
		(mounted.button('Export stitched') as HTMLButtonElement).click();
		expect(onExport).toHaveBeenCalled();
	});

	it('does not offer a stitched export for a video grid', async () => {
		const grid = makeGrid();
		grid.cells = grid.cells.map((c) => ({ ...c, mediaType: 'video' }));
		mounted = mountViewer({ grid });
		await flush();
		expect((mounted.button('Export stitched') as HTMLButtonElement).disabled).toBe(true);
	});

	it('shows the plain reason and a Retry for a failed cell', async () => {
		const grid = makeGrid({ 5: { status: 'failed', error: 'This was too much for the GPU right now.' } });
		const onRetryFailed = vi.fn();
		mounted = mountViewer({ grid, onRetryFailed });
		await flush();
		const empty = document.body.querySelector('[data-testid="cell-viewer-empty"]') as HTMLElement;
		expect(empty.textContent).toContain('This was too much for the GPU right now.');
		(empty.querySelector('button') as HTMLButtonElement).click();
		expect(onRetryFailed).toHaveBeenCalled();
	});

	it('shows progress for a running cell and a hole for a deleted one', async () => {
		const grid = makeGrid({
			5: { status: 'running', progress: { step: 14, total: 28 } },
			6: { status: 'deleted' }
		});
		mounted = mountViewer({ grid });
		await flush();
		expect((document.body.querySelector('[data-testid="cell-viewer-empty"]') as HTMLElement).textContent).toContain('14 / 28');
		mounted.set({ index: 6 });
		await flush();
		expect((document.body.querySelector('[data-testid="cell-viewer-empty"]') as HTMLElement).textContent).toContain('Deleted');
	});

	it('switches to the plain details view and back', async () => {
		mounted = mountViewer({});
		await flush();
		(mounted.button('Open generation details') as HTMLButtonElement).click();
		await flush();
		expect(mounted.text()).toContain('Generation Details');
		expect(document.body.querySelector('[data-testid="grid-details-card"]')).not.toBeNull();
		(mounted.button('Back to compare view') as HTMLButtonElement).click();
		await flush();
		expect(mounted.text()).toContain('Compare grid');
	});
});

describe('plain details mode', () => {
	it('stops claiming the vertical arrows so the details view is not a grid', async () => {
		const onIndex = vi.fn();
		mounted = mountViewer({ onIndex });
		await flush();
		(mounted.button('Open generation details') as HTMLButtonElement).click();
		await flush();
		mounted.press('ArrowDown');
		mounted.press('ArrowUp');
		expect(onIndex).not.toHaveBeenCalled();
	});
});

describe('grid details card', () => {
	function mountCard(props: Record<string, unknown>) {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = createClassComponent({ component: GridDetailsCard as never, target, props });
		return {
			target,
			destroy: () => {
				component.$destroy();
				target.remove();
			}
		};
	}

	it('says which grid and which axis values the generation belongs to, with the map and Open grid', () => {
		const onOpenGrid = vi.fn();
		const card = mountCard({ grid: makeGrid(), index: 4, onOpenGrid });
		expect(card.target.querySelector('[data-testid="grid-details-summary"]')?.textContent?.replace(/\s+/g, ' ').trim()).toBe(
			'Part of X/Y grid · sampler = euler, scheduler = beta'
		);
		expect(card.target.textContent).toContain('cell 5 of 12');
		expect(card.target.querySelectorAll('[data-testid="grid-minimap-cell"]')).toHaveLength(12);
		(card.target.querySelector('button:not([data-testid])') as HTMLButtonElement).click();
		expect(onOpenGrid).toHaveBeenCalled();
		card.destroy();
	});
});
