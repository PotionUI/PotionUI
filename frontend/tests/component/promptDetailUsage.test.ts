// @vitest-environment jsdom
//
// The "Used in generations" section regressed to 64px thumbnails with
// unreadable 8px labels, then (per a later maintainer redirect) needed the
// real justified generation-card mosaic instead of a tile grid. Covers that
// PromptDetailView renders `JustifiedGenerationGallery` (real GenerationCards,
// `.tile-frame` per card) and that no text-2xs/text-3xs survives anywhere in
// the rendered detail view.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getClient: vi.fn(() => ({ get: vi.fn().mockResolvedValue({ data: { data: {} } }) })),
		getGenerationImageURL: vi.fn(
			(generationId: string, filename: string) => `/api/media/generations/${generationId}/${filename}`
		),
		getGenerationThumbnailURL: vi.fn(
			(generationId: string, filename: string, size: string) =>
				`/api/media/generations/${generationId}/${filename}?size=${size}`
		),
		getModelById: vi.fn().mockResolvedValue({ data: { model: null } }),
		setOnAuthExpired: vi.fn()
	}
}));

vi.mock('../../src/lib/utils/chipParser', () => ({
	hydrateSegments: async (segments: unknown[]) => segments
}));

// Svelte 5's `bind:clientWidth` is implemented with a ResizeObserver - jsdom has
// none. Same fake as historyGridReflowIdentity.test.ts: stores observed
// elements/listener so a test can fire a resize on demand.
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

function stubClientWidth(el: HTMLElement, width: number) {
	Object.defineProperty(el, 'clientWidth', { configurable: true, get: () => width });
}

function resizeTo(el: HTMLElement, width: number) {
	stubClientWidth(el, width);
	const instance = FakeResizeObserver.instances.find((i) => i.targets.has(el));
	instance?.trigger(el);
}

const { default: PromptDetailView } = await import(
	'../../src/routes/prompts/components/PromptDetailView.svelte'
);
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');

function usageItem(overrides: Record<string, unknown> = {}) {
	return {
		id: 'gen-1',
		preset_id: 'minimax-h3',
		preset_name: 'MiniMax-H3',
		form_data: {},
		status: 'completed',
		progress: 1,
		created_at: '2026-09-20T00:00:00.000Z',
		updated_at: '2026-09-20T00:00:00.000Z',
		rating: 0,
		is_favorite: false,
		files: [
			{
				id: 'file-1',
				file_path: 'gen-1/image.png',
				file_type: 'image',
				is_final: true,
				created_at: '2026-09-20T00:00:00.000Z',
				width: 512,
				height: 512
			}
		],
		...overrides
	};
}

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | null = null;
let originalResizeObserver: unknown;

function mountDetail(usageItems: ReturnType<typeof usageItem>[], usageTotal = usageItems.length) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = createClassComponent({
		component: PromptDetailView as never,
		target,
		props: {
			mode: 'edit',
			prompt: { id: 'prompt-1', display_name: 'Dancing girl factory', usage_count: 3 },
			name: 'Dancing girl factory',
			usageHint: '',
			editModelId: null,
			editModelLabel: null,
			editorSegments: [{ id: 'a', content: 'a lighthouse keeper', type: 'content', chips: {}, enabled: true }],
			editorVariables: {},
			usageItems,
			usageTotal,
			usageLoading: false,
			saving: false,
			dirtyCount: 0,
			promptCollections: [],
			onBack: vi.fn(),
			onSave: vi.fn(),
			onDiscard: vi.fn(),
			onDelete: vi.fn(),
			onDuplicate: vi.fn(),
			onUse: vi.fn(),
			onAddToCollection: vi.fn(),
			onCreateAndAddToCollection: vi.fn(),
			onOpenVariableManager: vi.fn(),
			onVariableDefChange: vi.fn()
		}
	});
	return target;
}

function usageSectionBody(el: HTMLElement): HTMLElement {
	const heading = Array.from(el.querySelectorAll('h3')).find(
		(h) => h.textContent?.trim() === 'Used in generations'
	);
	if (!heading) throw new Error('Used in generations section not found');
	const section = heading.closest('section') as HTMLElement;
	return section.querySelector(':scope > div:last-of-type') as HTMLElement;
}

async function settle() {
	await tick();
	await new Promise((resolve) => setTimeout(resolve, 0));
	await tick();
}

beforeEach(() => {
	originalResizeObserver = (globalThis as any).ResizeObserver;
	(globalThis as any).ResizeObserver = FakeResizeObserver;
});

afterEach(() => {
	component?.$destroy();
	component = null;
	target?.remove();
	(globalThis as any).ResizeObserver = originalResizeObserver;
});

describe('PromptDetailView usage section', () => {
	it('renders the real generation-card mosaic, not a tile grid', async () => {
		const el = mountDetail([usageItem(), usageItem({ id: 'gen-2' })]);
		const body = usageSectionBody(el);
		const galleryRoot = body.querySelector(':scope > div') as HTMLElement;
		expect(galleryRoot).toBeTruthy();

		resizeTo(galleryRoot, 900);
		await settle();

		const cards = body.querySelectorAll('.tile-frame');
		expect(cards.length).toBe(2);
	});

	it('shows a "+N more" count in legible mono text when more generations exist than were loaded', async () => {
		const el = mountDetail([usageItem()], 5);
		const body = usageSectionBody(el);
		const galleryRoot = body.querySelector(':scope > div') as HTMLElement;
		resizeTo(galleryRoot, 900);
		await settle();

		const more = Array.from(body.querySelectorAll('p')).find((p) =>
			p.textContent?.includes('+4 more')
		);
		expect(more).toBeTruthy();
		expect(more!.className).toContain('text-xs');
		expect(more!.className).not.toContain('text-3xs');
		expect(more!.className).not.toContain('text-2xs');
	});
});
