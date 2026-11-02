import { generationTabs } from '$lib/types/tabs';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';
import { flushSync } from 'svelte';
import { settle } from './helpers/settle';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getGenerationById: vi.fn(),
		getGenerationParams: vi.fn(),
		updateGenerationTags: vi.fn(),
		getBaseURL: () => 'http://localhost',
		getToken: () => null,
		setOnAuthExpired: vi.fn(),
		setOnTokenRefreshed: vi.fn(),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } }),
		getClient: () => ({ get: vi.fn(), post: vi.fn() })
	}
}));
vi.mock('$app/stores', () => {
	const { writable } = require('svelte/store');
	return { page: writable({ url: new URL('http://localhost/generate') }) };
});
vi.mock('@google/model-viewer', () => ({}));
vi.mock('../../src/routes/generate/components/GenerationFormPane.svelte', async () => ({
	default: (await import('./stubs/BlankStub.svelte')).default
}));
vi.mock('../../src/routes/generate/components/PromptSection.svelte', async () => ({
	default: (await import('./stubs/BlankStub.svelte')).default
}));

class FakeResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}

const { tabsStore } = await import('$lib/stores/tabs');
const { default: GenerationPanels } = await import('../../src/routes/generate/components/GenerationPanels.svelte');
const { default: StoreFixture } = await import('./fixtures/GenerationPanelsStoreFixture.svelte');
const { createClassComponent } = await import('svelte/legacy');

let originalRO: unknown;
let host: HTMLElement;
let component: { $destroy: () => void } | undefined;

beforeEach(() => {
	originalRO = (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
	(globalThis as { ResizeObserver?: unknown }).ResizeObserver = FakeResizeObserver;
	host = document.body.appendChild(document.createElement('div'));
});

afterEach(() => {
	component?.$destroy();
	component = undefined;
	host.remove();
	(globalThis as { ResizeObserver?: unknown }).ResizeObserver = originalRO;
});

function mountPanels(patch: Record<string, unknown>) {
	const noop = () => {};
	const id = tabsStore.addTabWithData('Empty layouts', { selectedPreset: 'preset', selectedMode: 'image', ...patch });
	const tab = generationTabs(get(tabsStore).tabs).find((candidate) => candidate.id === id)!;
	component = createClassComponent({
		component: GenerationPanels as never,
		target: host,
		props: {
			tab,
			tabHandlers: {},
			promptRelayActive: false,
			canBatch: false,
			isActive: true,
			leftPanelWidth: 420,
			dynamicFormRefs: {},
			presets: [],
			onFormDataChange: noop,
			onWorkbenchPrevious: noop,
			onWorkbenchNext: noop,
			onWorkbenchHeightChange: noop,
			onMoveToWorkbench: noop
		}
	}) as never;
	flushSync();
}

const empties = () => [...document.querySelectorAll<HTMLElement>('[data-testid="workbench-empty"]')];
const layouts = () => empties().map((element) => element.getAttribute('data-layout'));
const POSITIONED = ['relative', 'absolute', 'fixed', 'sticky'];

describe('workbench empty layout per pane arrangement', () => {
	it('fills the docked pane in three-pane mode, with no positioned element between the pane and the empty state', async () => {
		mountPanels({ layoutMode: 'three' });
		await settle(6);
		expect(layouts()).toEqual(['pane']);
		const pane = host.querySelector<HTMLElement>('[data-testid="workbench-pane"]')!;
		expect(pane.classList.contains('relative')).toBe(true);
		let node = empties()[0].parentElement;
		while (node && node !== pane) {
			expect(POSITIONED.some((name) => node!.classList.contains(name))).toBe(false);
			node = node.parentElement;
		}
		expect(node).toBe(pane);
	});

	it('fades the dots above the prompt segments in two-pane mode', async () => {
		mountPanels({ layoutMode: 'two' });
		await settle(6);
		expect(layouts()).toEqual(['fade']);
		expect(empties()[0].querySelector('.dot-grid-fade')).not.toBeNull();
	});

	it.each(['three', 'two'])('shows the empty state only in the floating window while the workbench floats in %s-pane mode', async (mode) => {
		mountPanels({ layoutMode: mode, workbenchFloating: true, workbenchFloatingWidth: '640' });
		await settle(8);
		expect(layouts()).toEqual(['window']);
		expect(host.textContent).toContain('Workbench is floating');
		expect(host.querySelector('[data-testid="workbench-empty"]')).toBeNull();
		expect(document.querySelector('[role="dialog"][aria-label="Workbench"] [data-testid="workbench-empty"]')).not.toBeNull();
	});
});

const NOTICE = 'Generation cancelled';

function generation(overrides: Record<string, unknown> = {}) {
	return {
		isGenerating: false,
		currentGeneration: null,
		currentProgress: null,
		pipeTimers: {},
		startedAt: null,
		totalTime: null,
		lastDurationMs: null,
		batchImages: [],
		batchVideos: [],
		batchAudios: [],
		artifacts: [{ artifact_type: 'text', artifact_data: { title: 'Caption', text: 'A glass flask of blue liquid.' } }],
		workbenchIndex: 0,
		workbenchTotal: 0,
		queue: [],
		submittedPromptTemplate: null,
		cancelNotice: NOTICE,
		...overrides
	};
}

function mountFromStore(patch: Record<string, unknown>) {
	const id = tabsStore.addTabWithData('Siblings', {
		selectedPreset: 'preset',
		selectedMode: 'image',
		generation: generation() as never,
		...patch
	});
	component = createClassComponent({ component: StoreFixture as never, target: host, props: { tabId: id } }) as never;
	flushSync();
	return id;
}

const root = () => host.querySelector<HTMLElement>('#workbench-container')!;
const notice = () => host.querySelector<HTMLElement>('[data-testid="cancel-notice"]')!;
const artifacts = () => host.querySelector<HTMLElement>('[data-live-text-artifacts]')!;
const follows = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
const stageHeight = () => root().querySelector<HTMLElement>('div.relative.isolate')?.style.height;

describe('workbench siblings next to the empty state', () => {
	it('leaves the cancel notice and the text artifacts in flow, outside the dots layer', async () => {
		mountFromStore({ layoutMode: 'three' });
		await settle(8);
		const surface = empties()[0];
		expect(follows(notice(), root())).toBe(true);
		expect(follows(root(), artifacts())).toBe(true);
		expect(surface.classList.contains('pointer-events-none')).toBe(true);
		for (const sibling of [notice(), artifacts()]) {
			expect(surface.contains(sibling)).toBe(false);
			expect(sibling.contains(surface)).toBe(false);
		}
	});

	it('reserves the stage height ahead of a following sibling and gives it back unchanged when the first preview arrives', async () => {
		const id = mountFromStore({ layoutMode: 'three', workbenchMaxHeight: '700' });
		await settle(8);
		expect(root().getAttribute('style')).toContain('--workbench-stage: 700px');
		for (const name of ['group/workbench', '[&:has(~*)]:min-h-[var(--workbench-stage)]']) expect(root().classList.contains(name)).toBe(true);
		expect(empties()[0].querySelector('.select-none')!.classList.contains('group-[:has(~*)]/workbench:h-[min(100%,var(--workbench-region))]')).toBe(true);
		expect(stageHeight()).toBeUndefined();
		const before = generationTabs(get(tabsStore).tabs).find((candidate) => candidate.id === id)!.generation;
		tabsStore.updateTab(id, {
			generation: { ...before, isGenerating: true, currentGeneration: { id: 'run-1', status: 'running', current_image: '/api/media/previews/run-1.png' } } as never
		});
		flushSync();
		await settle(8);
		expect(empties()).toHaveLength(0);
		expect(stageHeight()).toBe('700px');
		expect(root().getAttribute('style')).toBeNull();
		expect(follows(notice(), root())).toBe(true);
		expect(follows(root(), artifacts())).toBe(true);
	});

	it('keeps the in-flow stage height in two-pane mode, equal to the stage the preview takes', async () => {
		const id = mountFromStore({ layoutMode: 'two' });
		await settle(8);
		expect(empties()[0].style.height).toBe('600px');
		expect(root().getAttribute('style')).toBeNull();
		expect(follows(root(), artifacts())).toBe(true);
		const before = generationTabs(get(tabsStore).tabs).find((candidate) => candidate.id === id)!.generation;
		tabsStore.updateTab(id, {
			generation: { ...before, isGenerating: true, currentGeneration: { id: 'run-1', status: 'running', current_image: '/api/media/previews/run-1.png' } } as never
		});
		flushSync();
		await settle(8);
		expect(stageHeight()).toBe('600px');
	});
});

describe('workbench stage below a cancel notice', () => {
	let restore: (() => void) | undefined;

	function stubNoticeSlot(height: number) {
		const original = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientHeight');
		Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
			configurable: true,
			get(this: HTMLElement) {
				return this.getAttribute('data-testid') === 'cancel-notice-slot' ? height : 0;
			}
		});
		restore = () => {
			if (original) Object.defineProperty(HTMLElement.prototype, 'clientHeight', original);
			else delete (HTMLElement.prototype as { clientHeight?: number }).clientHeight;
		};
	}

	afterEach(() => restore?.());

	it('starts the pane stage below the notice and its pane padding', async () => {
		stubNoticeSlot(54);
		mountFromStore({ layoutMode: 'three' });
		await settle(8);
		expect(root().getAttribute('style')).toContain('--workbench-stage-top: 70px');
		expect(root().getAttribute('style')).toContain('--workbench-region: 600px');
	});

	it('starts the pane stage at the pane top when no notice is showing', async () => {
		stubNoticeSlot(0);
		mountFromStore({ layoutMode: 'three', generation: generation({ cancelNotice: null }) as never });
		await settle(8);
		expect(root().getAttribute('style')).toContain('--workbench-stage-top: 0px');
		expect(root().getAttribute('style')).toContain('--workbench-region: 616px');
		expect(notice()).toBeNull();
	});

	it('keeps the notice inside its own slot ahead of the workbench', async () => {
		mountFromStore({ layoutMode: 'three' });
		await settle(8);
		const slot = host.querySelector<HTMLElement>('[data-testid="cancel-notice-slot"]')!;
		expect(slot.classList.contains('flow-root')).toBe(true);
		expect(slot.contains(notice())).toBe(true);
		expect(follows(slot, root())).toBe(true);
	});
});
