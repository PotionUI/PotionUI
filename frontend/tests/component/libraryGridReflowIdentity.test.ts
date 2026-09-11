// @vitest-environment jsdom
//
// LibraryGrid's justified rows used to render as a nested {#each rows as row}
// {#each row as box (box.item.id)} - the OUTER each was unkeyed, so when a
// `gridWidth` reflow (sidebar toggle, window resize) reshuffled which row an
// item belongs to, Svelte destroyed that item's card in its old row block and
// created a fresh one in its new row block: the <img> remounted (thumbnail
// flash), losing any hover/video state. This mounts the real component, seeds
// it with real items via the real store, and drives an actual `clientWidth`
// reflow through a fake ResizeObserver to prove a card's own DOM node survives
// it.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { layoutJustifiedRows, flattenJustifiedRows, clampAspect } from '$lib/utils/justifiedLayout';

vi.mock('$lib/services/api/index', () => ({
	api: {
		listLibraryItems: vi.fn(),
		getTags: vi.fn()
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
const { libraryStore } = await import('$lib/stores/library');
const { default: LibraryGrid } = await import('../../src/routes/library/components/LibraryGrid.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');

function libraryItem(id: string, aspect: number) {
	// height fixed, width derived, so `aspect` round-trips through
	// libraryItemAspect()/clampAspect() exactly.
	const height = 1000;
	return {
		id,
		filename: `${id}.png`,
		original_filename: `${id}.png`,
		media_type: 'image',
		url: `/media/${id}.png`,
		width: Math.round(height * clampAspect(aspect)),
		height,
		created_at: '2026-01-01T00:00:00Z',
		tags: []
	};
}

function mountGrid() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: LibraryGrid as never,
		target,
		props: { onDeleteRequest: vi.fn(), onUploadRequest: vi.fn() }
	});
	return {
		target,
		gridRoot: () => target.querySelector<HTMLElement>('[role="listbox"]')!,
		imgFor: (id: string) => target.querySelector<HTMLImageElement>(`[data-library-card="${id}"] img`),
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
	// Svelte's ResizeObserver wrapper is a module-level singleton that
	// constructs the real (here: fake) observer lazily, once, the first time
	// anything calls `.observe()` in this file's whole run - so `instances`
	// must NOT be reset per test, or `resizeTo` below can no longer find the
	// one live instance every subsequent mount's `bind:clientWidth` actually
	// registers against.
	originalResizeObserver = (globalThis as any).ResizeObserver;
	(globalThis as any).ResizeObserver = FakeResizeObserver;
	libraryStore.reset();
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	libraryStore.reset();
	(globalThis as any).ResizeObserver = originalResizeObserver;
	vi.clearAllMocks();
});

describe('LibraryGrid reflow identity', () => {
	it('keeps a card\'s own DOM node across a gridWidth reflow that moves it to a different row', async () => {
		const aspects = [1.78, 1, 0.75, 1.33, 1, 1.5, 0.9, 1.6, 1.2, 0.8];
		const items = aspects.map((a, i) => libraryItem(`item-${i}`, a));

		// Same formula LibraryGrid itself uses (historyTileSize defaults to
		// 'medium', multiplier 1) - pick two widths where the real packer
		// actually reassigns at least one item to a different row, so the
		// assertion below exercises the reflow the bug depended on rather than
		// happening to land on a width where nothing moved.
		const GAP = 12;
		const targetRowHeight = (w: number) => Math.round(Math.max(180, Math.min(320, w / 4.6)));
		const rowIndexById = (w: number) => {
			const rows = layoutJustifiedRows(
				items.map((item) => ({ item, aspect: clampAspect(item.width / item.height) })),
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
		const movedId = items.map((i) => i.id).find((id) => rowsAtA.get(id) !== rowsAtB.get(id));
		expect(movedId, 'fixture must actually cross a row boundary between the two widths').toBeDefined();

		vi.mocked(api.listLibraryItems).mockResolvedValue({
			success: true,
			data: { items, total: items.length, limit: 24, offset: 0 }
		} as never);

		mounted = mountGrid();
		// The component's own mount-time effect reads the real (jsdom) 0 before
		// this stub is in place, so establish the starting width through the
		// same resize-observer path a later reflow uses, rather than relying on
		// that first untracked read.
		const widthRef = { width: 0 };
		stubClientWidth(mounted.gridRoot(), widthRef);
		resizeTo(mounted.gridRoot(), widthRef, WIDTH_A);
		await settle();

		await libraryStore.load();
		await settle();

		const before = mounted.imgFor(movedId!);
		expect(before, 'the moved item must actually be rendered before the reflow').not.toBeNull();

		resizeTo(mounted.gridRoot(), widthRef, WIDTH_B);
		await settle();

		const after = mounted.imgFor(movedId!);
		expect(after, 'the moved item must still be rendered after the reflow').not.toBeNull();
		expect(after, 'the reflow must not destroy and remount the card').toBe(before);
	});

	it('flags each row\'s last box with data-row-end, matching the packer output', async () => {
		const aspects = [1.78, 1, 0.75, 1.33, 1, 1.5, 0.9, 1.6, 1.2, 0.8];
		const items = aspects.map((a, i) => libraryItem(`item-${i}`, a));

		vi.mocked(api.listLibraryItems).mockResolvedValue({
			success: true,
			data: { items, total: items.length, limit: 24, offset: 0 }
		} as never);

		mounted = mountGrid();
		const widthRef = { width: 0 };
		stubClientWidth(mounted.gridRoot(), widthRef);
		const WIDTH = 900;
		resizeTo(mounted.gridRoot(), widthRef, WIDTH);
		await settle();

		await libraryStore.load();
		await settle();

		const GAP = 12;
		const targetRowHeight = Math.round(Math.max(180, Math.min(320, WIDTH / 4.6)));
		const rows = layoutJustifiedRows(
			items.map((item) => ({ item, aspect: clampAspect(item.width / item.height) })),
			WIDTH,
			targetRowHeight,
			GAP
		);
		expect(rows.length, 'fixture must actually wrap onto more than one row').toBeGreaterThan(1);
		const expected = flattenJustifiedRows(rows).map((box) => ({ id: box.item.id, rowEnd: box.rowEnd }));

		const rendered = Array.from(
			mounted.target.querySelectorAll<HTMLElement>('[data-library-card]')
		).map((el) => ({ id: el.dataset.libraryCard, rowEnd: el.dataset.rowEnd === 'true' }));

		// Rendered DOM order (one flat keyed {#each}) and row-end flags must
		// match the packer's own row-major output exactly - this is the DOM's
		// only observable trace of "where a row breaks" now that there is no
		// per-row wrapper element to query in jsdom (no real layout engine).
		expect(rendered).toEqual(expected);
	});
});
