// @vitest-environment jsdom
//
// Same structural fix as LibraryGrid (see libraryGridReflowIdentity.test.ts's
// header) applied byte-for-byte to HistoryGrid - this mounts the real
// component at least once to prove it, rather than trusting the diff shape
// alone. Kept intentionally lighter than the library test (order + row-end
// flags only): HistoryGrid pulls in historyStore, nsfwFilterStore and
// GenerationCard, all of which the library grid doesn't need.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { layoutJustifiedRows, flattenJustifiedRows, clampAspect } from '$lib/utils/justifiedLayout';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getGenerationHistory: vi.fn(),
		getClient: vi.fn(() => ({
			get: vi.fn().mockRejectedValue(new Error('not mocked')),
			put: vi.fn().mockRejectedValue(new Error('not mocked'))
		})),
		getGenerationThumbnailURL: vi.fn(() => '/thumb.png')
	}
}));

// Svelte 5's `bind:clientWidth` is implemented with a ResizeObserver - jsdom has
// none. This fake actually stores the observed elements/listener (unlike a
// pure no-op stub) so a test can fire a resize on demand.
class FakeResizeObserver {
	static instances: FakeResizeObserver[] = [];
	targets = new Set<Element>();
	constructor(private callback: (entries: { target: Element }[]) => void) {
		FakeResizeObserver.instances.push(this);
	}
	observe(el: Element) {
		this.targets.add(el);
	}
	unobserve(el: Element) {
		this.targets.delete(el);
	}
	disconnect() {
		this.targets.clear();
	}
	trigger(el: Element) {
		this.callback([{ target: el } as ResizeObserverEntry]);
	}
}

function stubClientWidth(el: HTMLElement, ref: { width: number }) {
	Object.defineProperty(el, 'clientWidth', {
		configurable: true,
		get: () => ref.width
	});
}

function resizeTo(el: HTMLElement, ref: { width: number }, width: number) {
	ref.width = width;
	const instance = FakeResizeObserver.instances.find((i) => i.targets.has(el));
	instance?.trigger(el);
}

const { api } = await import('$lib/services/api/index');
const { historyStore } = await import('$lib/stores/history');
const { default: HistoryGrid } = await import('../../src/routes/history/components/HistoryGrid.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');

function generation(id: string, aspect: number) {
	const height = 1000;
	const width = Math.round(height * clampAspect(aspect));
	return {
		id,
		form_data: {},
		status: 'completed' as const,
		progress: 1,
		created_at: '2026-01-01T00:00:00Z',
		updated_at: '2026-01-01T00:00:00Z',
		rating: 0,
		is_favorite: false,
		files: [
			{
				id: 1,
				file_path: `generations/2026-01-01/${id}/0.png`,
				file_type: 'image',
				is_final: true,
				created_at: '2026-01-01T00:00:00Z',
				width,
				height
			}
		]
	};
}

function mountGrid() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: HistoryGrid as never,
		target,
		props: { onDeleteRequest: vi.fn() }
	});
	return {
		target,
		gridRoot: () => target.querySelector<HTMLElement>('[role="listbox"]')!,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	await tick();
	await new Promise((resolve) => setTimeout(resolve, 0));
	await tick();
}

let mounted: ReturnType<typeof mountGrid> | undefined;
let originalResizeObserver: unknown;

beforeEach(() => {
	// See libraryGridReflowIdentity.test.ts: the singleton observer wrapper is
	// constructed once for this file's whole run, so `instances` must persist
	// across tests for `resizeTo` to keep finding it.
	originalResizeObserver = (globalThis as any).ResizeObserver;
	(globalThis as any).ResizeObserver = FakeResizeObserver;
	historyStore.reset();
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	historyStore.reset();
	(globalThis as any).ResizeObserver = originalResizeObserver;
	vi.clearAllMocks();
});

describe('HistoryGrid reflow identity', () => {
	it('renders one card per generation, in row-major order, flagged with data-row-end matching the packer output', async () => {
		const aspects = [1.78, 1, 0.75, 1.33, 1, 1.5, 0.9, 1.6, 1.2, 0.8];
		const generations = aspects.map((a, i) => generation(`gen-${i}`, a));

		vi.mocked(api.getGenerationHistory).mockResolvedValue({
			success: true,
			data: { generations, total: generations.length }
		} as never);

		mounted = mountGrid();
		const widthRef = { width: 0 };
		stubClientWidth(mounted.gridRoot(), widthRef);
		const WIDTH = 900;
		resizeTo(mounted.gridRoot(), widthRef, WIDTH);
		await settle();

		await historyStore.loadGenerations();
		await settle();

		const GAP = 12;
		const targetRowHeight = Math.round(Math.max(180, Math.min(320, WIDTH / 4.6)));
		const rows = layoutJustifiedRows(
			generations.map((g) => ({ item: g, aspect: clampAspect(g.files[0].width! / g.files[0].height!) })),
			WIDTH,
			targetRowHeight,
			GAP
		);
		expect(rows.length, 'fixture must actually wrap onto more than one row').toBeGreaterThan(1);
		const expected = flattenJustifiedRows(rows).map((box) => ({ id: box.item.id, rowEnd: box.rowEnd }));

		const rendered = Array.from(mounted.target.querySelectorAll<HTMLElement>('[data-history-card]')).map(
			(el) => ({ id: el.dataset.historyCard, rowEnd: el.dataset.rowEnd === 'true' })
		);

		expect(rendered).toEqual(expected);
	});

	it("keeps a card's own DOM node across a gridWidth reflow that moves it to a different row", async () => {
		const aspects = [1.78, 1, 0.75, 1.33, 1, 1.5, 0.9, 1.6, 1.2, 0.8];
		const generations = aspects.map((a, i) => generation(`gen-${i}`, a));

		const GAP = 12;
		const targetRowHeight = (w: number) => Math.round(Math.max(180, Math.min(320, w / 4.6)));
		const rowIndexById = (w: number) => {
			const rows = layoutJustifiedRows(
				generations.map((g) => ({
					item: g,
					aspect: clampAspect(g.files[0].width! / g.files[0].height!)
				})),
				w,
				targetRowHeight(w),
				GAP
			);
			const map = new Map<string, number>();
			rows.forEach((row, rowIndex) => row.forEach((box) => map.set(box.item.id, rowIndex)));
			return map;
		};
		const WIDTH_A = 420;
		const WIDTH_B = 1400;
		const rowsAtA = rowIndexById(WIDTH_A);
		const rowsAtB = rowIndexById(WIDTH_B);
		const movedId = generations.map((g) => g.id).find((id) => rowsAtA.get(id) !== rowsAtB.get(id));
		expect(movedId, 'fixture must actually cross a row boundary between the two widths').toBeDefined();

		vi.mocked(api.getGenerationHistory).mockResolvedValue({
			success: true,
			data: { generations, total: generations.length }
		} as never);

		mounted = mountGrid();
		const widthRef = { width: 0 };
		stubClientWidth(mounted.gridRoot(), widthRef);
		resizeTo(mounted.gridRoot(), widthRef, WIDTH_A);
		await settle();

		await historyStore.loadGenerations();
		await settle();

		const imgFor = (id: string) =>
			mounted!.target.querySelector<HTMLImageElement>(`[data-history-card="${id}"] img`);

		const before = imgFor(movedId!);
		expect(before, 'the moved item must actually be rendered before the reflow').not.toBeNull();

		resizeTo(mounted.gridRoot(), widthRef, WIDTH_B);
		await settle();

		const after = imgFor(movedId!);
		expect(after, 'the moved item must still be rendered after the reflow').not.toBeNull();
		expect(after, 'the reflow must not destroy and remount the card').toBe(before);
	});
});
