import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getGenerationById: vi.fn().mockResolvedValue({ success: false }),
		getGenerationImageURL: vi.fn(() => '/media.mp4'),
		getClient: vi.fn(() => ({ get: vi.fn(), post: vi.fn() }))
	}
}));

const { default: CompareGrid } = await import('$lib/generation/compare/view/CompareGrid.svelte');
const { default: CompareCell } = await import('$lib/generation/compare/view/CompareCell.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { emptyCell } = await import('$lib/generation/compare/serverGrid');

type Cell = ReturnType<typeof emptyCell>;
type Status = Cell['status'];

const sampler = {
	field: 'sampler',
	type: 'select',
	label: 'Sampler',
	values: ['euler', 'er_sde', 'dpmpp_2m'].map((v) => ({ value: v, label: v }))
};
const scheduler = {
	field: 'scheduler',
	type: 'select',
	label: 'Scheduler',
	values: ['simple', 'beta'].map((v) => ({ value: v, label: v }))
};

function cell(i: number, status: Status, over: Partial<Cell> = {}): Cell {
	return {
		...emptyCell(i % 3, Math.floor(i / 3)),
		generationId: `gen-${i}`,
		status,
		axisValues: { sampler: sampler.values[i % 3].label, scheduler: scheduler.values[Math.floor(i / 3)].label },
		...over
	};
}

function makeGrid(statuses: Status[], over: Record<number, Partial<Cell>> = {}) {
	return {
		id: 'g1',
		config: { armed: true, x: sampler, y: scheduler, lockSeed: true },
		cols: 3,
		rows: 2,
		cells: statuses.map((s, i) => cell(i, s, over[i]))
	};
}

const mounted: Array<() => void> = [];

function mount(component: unknown, props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = createClassComponent({ component: component as never, target, props });
	mounted.push(() => {
		instance.$destroy();
		target.remove();
	});
	return { target, instance };
}

afterEach(() => {
	while (mounted.length) mounted.pop()?.();
});

const q = (root: ParentNode, selector: string) => root.querySelector(selector);
const qa = (root: ParentNode, selector: string) => [...root.querySelectorAll(selector)];

describe('compare cell states', () => {
	it('draws an empty cell as a dotted placeholder that cannot be opened', () => {
		const onOpen = vi.fn();
		const { target } = mount(CompareCell, { cell: emptyCell(0, 0), index: 0, onOpen });
		expect(q(target, '[data-cell-state="empty"]')).not.toBeNull();
		expect(q(target, '[data-testid="compare-cell-empty"]')).not.toBeNull();
		(q(target, '[data-testid="compare-cell"]') as HTMLElement).click();
		expect(onOpen).not.toHaveBeenCalled();
	});

	it('shows a queued cell with its queue number', () => {
		const { target } = mount(CompareCell, { cell: cell(2, 'queued'), index: 2, ordinal: 3 });
		expect(target.textContent).toContain('Queued');
		expect(q(target, '[data-testid="compare-cell-ordinal"]')?.textContent).toBe('#3');
	});

	it('shows a running cell with the blurred live preview, the step count and the tick bar', () => {
		const running = cell(1, 'running', { progress: { step: 14, total: 28 }, previewUrl: '/live.png' });
		const { target } = mount(CompareCell, { cell: running, index: 1 });
		expect(q(target, '[data-testid="compare-cell-steps"]')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('14 / 28');
		const preview = q(target, 'img') as HTMLImageElement;
		expect(preview.getAttribute('src')).toBe('/live.png');
		expect(preview.className).toContain('blur');
		const tick = q(target, '[data-testid="compare-cell-tick"] > div') as HTMLElement;
		expect(tick.style.width).toBe('50%');
	});

	it('shows a running cell without progress as plain Running', () => {
		const { target } = mount(CompareCell, { cell: cell(1, 'running'), index: 1 });
		expect(target.textContent).toContain('Running');
		expect(q(target, 'img')).toBeNull();
	});

	it('shows a completed cell with its thumbnail and time', () => {
		const done = cell(0, 'completed', { thumbnailUrl: '/t0.png', mediaType: 'image' });
		const { target } = mount(CompareCell, { cell: done, index: 0, seconds: 18.2 });
		expect((q(target, 'img') as HTMLImageElement).getAttribute('src')).toBe('/t0.png');
		expect(q(target, '[data-testid="compare-cell-time"]')?.textContent?.trim()).toBe('18 s');
	});

	it('omits the time when none was measured', () => {
		const done = cell(0, 'completed', { thumbnailUrl: '/t0.png' });
		const { target } = mount(CompareCell, { cell: done, index: 0 });
		expect(q(target, '[data-testid="compare-cell-time"]')).toBeNull();
	});

	it('opens a completed cell on click and on Enter', () => {
		const onOpen = vi.fn();
		const done = cell(4, 'completed', { thumbnailUrl: '/t.png' });
		const { target } = mount(CompareCell, { cell: done, index: 4, onOpen });
		const el = q(target, '[data-testid="compare-cell"]') as HTMLElement;
		el.click();
		el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		expect(onOpen).toHaveBeenCalledTimes(2);
		expect(onOpen).toHaveBeenCalledWith(4);
	});

	it('tints a failed cell red, gives the plain reason and a Retry that does not open it', () => {
		const onOpen = vi.fn();
		const onRetry = vi.fn();
		const failed = cell(3, 'failed', { error: 'This was too much for the GPU right now.' });
		const { target } = mount(CompareCell, { cell: failed, index: 3, onOpen, onRetry });
		const el = q(target, '[data-testid="compare-cell"]') as HTMLElement;
		expect(el.className).toContain('border-danger');
		expect(el.className).toContain('bg-danger/10');
		expect(q(target, '[data-testid="compare-cell-error"]')?.textContent).toBe('This was too much for the GPU right now.');
		const retry = qa(target, 'button').find((b) => b.textContent?.includes('Retry')) as HTMLButtonElement;
		retry.click();
		expect(onRetry).toHaveBeenCalledWith(3);
		expect(onOpen).not.toHaveBeenCalled();
	});

	it('draws a deleted cell as a hole that offers Retry', () => {
		const onRetry = vi.fn();
		const { target } = mount(CompareCell, { cell: cell(5, 'deleted'), index: 5, onRetry });
		expect(target.textContent).toContain('Deleted');
		const retry = qa(target, 'button').find((b) => b.textContent?.includes('Retry')) as HTMLButtonElement;
		retry.click();
		expect(onRetry).toHaveBeenCalledWith(5);
	});

	it('marks the selected cell', () => {
		const { target } = mount(CompareCell, { cell: cell(0, 'completed'), index: 0, selected: true });
		expect((q(target, '[data-testid="compare-cell"]') as HTMLElement).className).toContain('ring-signal');
	});

	it('describes itself to assistive tech', () => {
		const { target } = mount(CompareCell, { cell: cell(0, 'queued'), index: 0 });
		expect(q(target, '[data-testid="compare-cell"]')?.getAttribute('aria-label')).toBe(
			'Cell 1, sampler = euler, scheduler = simple, queued'
		);
	});
});

describe('compare grid view', () => {
	const mid = ['completed', 'completed', 'failed', 'running', 'queued', 'queued'] as Status[];

	it('labels the columns and rows from the axes', () => {
		const { target } = mount(CompareGrid, { grid: makeGrid(mid) });
		expect(qa(target, '[data-testid="compare-x-label"]').map((e) => e.textContent?.trim())).toEqual([
			'euler',
			'er_sde',
			'dpmpp_2m'
		]);
		expect(qa(target, '[data-testid="compare-y-label"]').map((e) => e.textContent?.trim())).toEqual(['simple', 'beta']);
		expect(qa(target, '[data-testid="compare-cell"]')).toHaveLength(6);
	});

	it('shows the axis legend in the corner', () => {
		const { target } = mount(CompareGrid, { grid: makeGrid(mid) });
		const text = target.textContent?.replace(/\s+/g, ' ') ?? '';
		expect(text).toContain('X Sampler');
		expect(text).toContain('Y Scheduler');
	});

	it('shows one progress segment per cell, the count, the failed badge and Cancel all while active', () => {
		const onCancelAll = vi.fn();
		const { target } = mount(CompareGrid, { grid: makeGrid(mid), onCancelAll, onRetryFailed: vi.fn() });
		expect(qa(target, '[data-segment]').map((s) => s.getAttribute('data-segment'))).toEqual([
			'done',
			'done',
			'failed',
			'running',
			'queued',
			'queued'
		]);
		expect(q(target, '[data-testid="compare-count"]')?.textContent?.trim()).toBe('2/6');
		expect(q(target, '[data-testid="compare-failed"]')?.textContent).toContain('1 failed');
		const cancel = qa(target, 'button').find((b) => b.textContent?.includes('Cancel all')) as HTMLButtonElement;
		cancel.click();
		expect(onCancelAll).toHaveBeenCalled();
		expect(qa(target, 'button').some((b) => b.textContent?.includes('Retry failed'))).toBe(false);
	});

	it('swaps Cancel all for Retry failed once the run settles with failures', () => {
		const onRetryFailed = vi.fn();
		const settled = makeGrid(['completed', 'failed', 'completed', 'completed', 'completed', 'completed']);
		const { target } = mount(CompareGrid, { grid: settled, onCancelAll: vi.fn(), onRetryFailed });
		expect(qa(target, 'button').some((b) => b.textContent?.includes('Cancel all'))).toBe(false);
		const retry = qa(target, 'button').find((b) => b.textContent?.includes('Retry failed')) as HTMLButtonElement;
		retry.click();
		expect(onRetryFailed).toHaveBeenCalled();
	});

	it('numbers the queued cells in the grid', () => {
		const { target } = mount(CompareGrid, { grid: makeGrid(mid) });
		expect(qa(target, '[data-testid="compare-cell-ordinal"]').map((e) => e.textContent)).toEqual(['#1', '#2']);
	});

	it('shows the server elapsed time and only falls back to the measured one when it is null', () => {
		const grid = makeGrid(new Array(6).fill('completed'), {
			0: { thumbnailUrl: '/a.png', elapsedSeconds: 21.4 },
			1: { thumbnailUrl: '/b.png', elapsedSeconds: null }
		});
		const { target } = mount(CompareGrid, { grid });
		const times = qa(target, '[data-testid="compare-cell-time"]').map((e) => e.textContent?.trim());
		expect(times).toEqual(['21 s']);
	});

	it('says All done when every cell finished', () => {
		const all = makeGrid(new Array(6).fill('completed'));
		const { target } = mount(CompareGrid, { grid: all });
		expect(target.textContent).toContain('All done');
		expect(q(target, '[data-testid="compare-count"]')?.textContent?.trim()).toBe('6/6');
	});

	it('shows the dimensions in the overview header', () => {
		const { target } = mount(CompareGrid, { grid: makeGrid(mid), overview: true });
		expect(target.textContent).toContain('3 × 2');
	});

	it('draws a single-axis grid as one unlabelled row', () => {
		const single = { ...makeGrid(['completed', 'completed', 'completed']), rows: 1 } as Record<string, unknown>;
		single.config = { armed: true, x: sampler, y: null, lockSeed: true };
		const { target } = mount(CompareGrid, { grid: single });
		expect(qa(target, '[data-testid="compare-y-label"]')).toHaveLength(1);
		expect(qa(target, '[data-testid="compare-y-label"]')[0].textContent?.trim()).toBe('');
	});

	it('reports each cell click with its index', () => {
		const onOpenCell = vi.fn();
		const { target } = mount(CompareGrid, {
			grid: makeGrid(['completed', 'completed', 'completed', 'completed', 'completed', 'completed']),
			onOpenCell
		});
		(qa(target, '[data-testid="compare-cell"]')[4] as HTMLElement).click();
		expect(onOpenCell).toHaveBeenCalledWith(4);
	});

	it('pins the labels with sticky positioning inside one scroller', () => {
		const { target } = mount(CompareGrid, { grid: makeGrid(mid) });
		expect(qa(target, '[data-testid="compare-x-label"]').every((e) => e.className.includes('sticky'))).toBe(true);
		expect(qa(target, '[data-testid="compare-y-label"]').every((e) => e.className.includes('sticky'))).toBe(true);
		expect(qa(target, '[data-testid="compare-scroller"]')).toHaveLength(1);
	});

	it('does not offer a video transport for an image grid', () => {
		const { target } = mount(CompareGrid, { grid: makeGrid(new Array(6).fill('completed')) });
		expect(q(target, '[data-testid="video-transport"]')).toBeNull();
	});
});

describe('compare grid left edge', () => {
	function wide() {
		const values = Array.from({ length: 12 }, (_, i) => ({ value: `s${i}`, label: `s${i}` }));
		return {
			id: 'wide',
			config: { armed: true, x: { ...sampler, values }, lockSeed: true },
			cols: 12,
			rows: 1,
			cells: values.map((_, i) => ({ ...emptyCell(i, 0), axisValues: { sampler: `s${i}` } }))
		};
	}

	it('keeps the header outside the horizontal scroller', () => {
		const { target } = mount(CompareGrid, { grid: wide() });
		const scroller = q(target, '[data-testid="compare-scroller"]') as HTMLElement;
		const header = q(target, '[data-testid="compare-header"]') as HTMLElement;
		expect(scroller.contains(header)).toBe(false);
		expect(header.parentElement).toBe(scroller.parentElement);
	});

	it('never centres the scrolled content with justify-center or items-center', () => {
		const { target } = mount(CompareGrid, { grid: wide() });
		const scroller = q(target, '[data-testid="compare-scroller"]') as HTMLElement;
		const content = scroller.firstElementChild as HTMLElement;
		for (const el of [scroller, content, scroller.parentElement as HTMLElement]) {
			expect(el.className).not.toMatch(/justify-center|items-center/);
		}
		expect(scroller.className).toContain('overflow-auto');
		expect(content.className).toContain('mx-auto');
		expect(content.className).toContain('w-max');
	});

	it('lets the section and scroller shrink to their container', () => {
		const { target } = mount(CompareGrid, { grid: wide() });
		const section = q(target, '[data-testid="compare-grid"]') as HTMLElement;
		const scroller = q(target, '[data-testid="compare-scroller"]') as HTMLElement;
		for (const el of [section, scroller]) {
			expect(el.className).toContain('min-w-0');
			expect(el.className).toContain('max-w-full');
		}
	});

	it('pins the axis corner and the Y labels to the left of the scroller', () => {
		const { target } = mount(CompareGrid, { grid: wide() });
		const scroller = q(target, '[data-testid="compare-scroller"]') as HTMLElement;
		const corner = (scroller.firstElementChild as HTMLElement).firstElementChild as HTMLElement;
		expect(corner.className).toContain('sticky');
		expect(corner.className).toContain('left-0');
		expect(qa(target, '[data-testid="compare-y-label"]').every((el) => el.className.includes('left-0'))).toBe(true);
	});
});
