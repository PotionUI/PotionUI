import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { Writable } from 'svelte/store';
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
vi.mock('$lib/stores/auth', async (importOriginal) => {
	const { writable } = await import('svelte/store');
	return { ...(await importOriginal<object>()), authStore: writable({ user: { account_type: 'USER' } }) };
});

class FakeResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}

const { default: Workbench } = await import('$lib/components/Workbench.svelte');
const { api } = await import('$lib/services/api/index');
const { authStore } = await import('$lib/stores/auth');
const { createClassComponent } = await import('svelte/legacy');

const IMAGE = '/api/media/generations/gen-1/1.png';
let originalRO: unknown;
let target: HTMLElement;
let component: { $destroy: () => void; $set: (props: object) => void } | undefined;

beforeEach(() => {
	originalRO = (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
	(globalThis as { ResizeObserver?: unknown }).ResizeObserver = FakeResizeObserver;
	target = document.createElement('div');
	document.body.appendChild(target);
});

afterEach(() => {
	component?.$destroy();
	component = undefined;
	target.remove();
	(globalThis as { ResizeObserver?: unknown }).ResizeObserver = originalRO;
	vi.clearAllMocks();
});

function mountWorkbench(props: Record<string, unknown> = {}) {
	component = createClassComponent({
		component: Workbench as never,
		target,
		props: { emptyLayout: 'pane', currentGeneration: null, isGenerating: false, workbenchIndex: 0, workbenchTotal: 0, ...props }
	}) as never;
}

const empty = () => target.querySelector<HTMLElement>('[data-testid="workbench-empty"]');
const state = () => empty()?.getAttribute('data-state');
const container = () => target.querySelector<HTMLElement>('#workbench-container')!;
const classes = (element: Element | null) => (element ? [...element.classList] : []);
const FRAME = ['rounded-lg', 'overflow-hidden', 'bg-canvas', 'border', 'shadow-floating'];
const FAILED = {
	currentGeneration: { id: 'gen-1', status: 'failed', message: 'Out of GPU memory', errorId: 'ERR-1', errorDetail: 'torch.OutOfMemoryError' }
};
const SUPPRESSED = { currentGeneration: { id: 'gen-1', status: 'running', preview_suppressed: true }, isGenerating: true };
const BLOCKED = { currentGeneration: { id: 'gen-1', status: 'completed', content_blocked: { blocked_count: 1, total: 2 } } };
const LAYOUTS = ['pane', 'fade', 'window'];
const STATES: [string, Record<string, unknown>][] = [['empty', {}], ['failed', FAILED], ['suppressed', SUPPRESSED], ['blocked', BLOCKED]];

async function eachLayout(props: Record<string, unknown>, run: (layout: string) => void | Promise<void>) {
	for (const layout of LAYOUTS) {
		mountWorkbench({ emptyLayout: layout, ...props });
		await settle(6);
		await run(layout);
		component!.$destroy();
	}
	component = undefined;
}

const expectFrameless = () => {
	for (const name of [...FRAME, 'overflow-visible']) expect(classes(container())).not.toContain(name);
};
const expectNoSmallText = () => expect(empty()!.querySelectorAll('[class*="text-xs"], [class*="text-2xs"]')).toHaveLength(0);

describe('Workbench empty state', () => {
	it.each(['pane', 'fade', 'window'])('shows the copy and no card frame in the %s layout', async (layout) => {
		mountWorkbench({ emptyLayout: layout });
		await settle(4);
		expect(empty()!.getAttribute('data-layout')).toBe(layout);
		expect(empty()!.textContent).toContain('Your creations appear here');
		expectFrameless();
	});

	it('covers the whole pane in the pane layout, centres the copy both ways and has no height slider', async () => {
		mountWorkbench({ emptyLayout: 'pane' });
		await settle(4);
		const stage = classes(empty());
		expect(stage).toEqual(expect.arrayContaining(['absolute', 'inset-x-0', 'top-[var(--workbench-stage-top,0px)]', 'bottom-[var(--dock-height)]', 'pointer-events-none']));
		expect(empty()!.getAttribute('style')).toBeNull();
		expect(classes(container())).not.toContain('relative');
		const copy = empty()!.querySelector('.select-none')!;
		expect(classes(copy)).toEqual(expect.arrayContaining(['h-full', 'flex', 'flex-col', 'items-center', '[justify-content:safe_center]', 'text-center']));
		expect(empty()!.querySelector('.dot-grid')!.classList.contains('inset-0')).toBe(true);
		expect(empty()!.querySelector('input[type="range"]')).toBeNull();
	});

	it('bleeds the dots to the column edges and fades them out at the bottom in the fade layout', async () => {
		mountWorkbench({ emptyLayout: 'fade', workbenchMaxHeight: '700' });
		await settle(4);
		const dots = empty()!.querySelector('.dot-grid')!;
		expect(classes(dots)).toEqual(expect.arrayContaining(['dot-grid-fade', '-inset-x-4', '-top-4', 'bottom-0']));
		expect(empty()!.style.height).toBe('700px');
		expect(classes(container())).toContain('relative');
		expect(empty()!.querySelector('input[type="range"]')).not.toBeNull();
		const copy = empty()!.querySelector('.select-none')!;
		expect(classes(copy)).toEqual(expect.arrayContaining(['absolute', 'inset-x-0', 'top-0', 'bottom-1/4', 'items-center', '[justify-content:safe_center]']));
		expect(dots.contains(copy)).toBe(false);
	});

	it('fills the window body without fading in the window layout', async () => {
		mountWorkbench({ emptyLayout: 'window' });
		await settle(4);
		const dots = empty()!.querySelector('.dot-grid')!;
		expect(classes(dots)).toEqual(expect.arrayContaining(['-inset-4', 'rounded-b-xl']));
		expect(classes(dots)).not.toContain('dot-grid-fade');
		expect(empty()!.style.height).toBe('600px');
	});

	it('uses no text below 12px in any state or layout', async () => {
		for (const [, props] of STATES) await eachLayout(props, expectNoSmallText);
	});

	it('scrolls its copy instead of cutting it off, and centres it only while it fits', async () => {
		await eachLayout(FAILED, () => {
			const copy = empty()!.querySelector('.select-none')!;
			expect(classes(copy)).toEqual(expect.arrayContaining(['overflow-y-auto', '[justify-content:safe_center]']));
			expect(classes(copy)).not.toContain('justify-center');
			const content = copy.firstElementChild!;
			expect(classes(content)).toEqual(['flex', 'flex-col', 'items-center']);
		});
	});

	it.each([
		[
			'a live preview',
			{ currentGeneration: { id: 'gen-1', status: 'running', current_image: '/api/media/previews/gen-1.png' }, isGenerating: true },
			'overflow-hidden'
		],
		[
			'a result',
			{
				currentGeneration: { id: 'gen-1', status: 'completed' },
				workbenchTotal: 1,
				batchImages: [{ url: IMAGE, originalUrl: IMAGE, file_type: 'image' }]
			},
			'overflow-visible'
		]
	])('is gone as soon as there is %s', async (_name, props, overflow) => {
		await eachLayout(props, () => {
			expect(empty()).toBeNull();
			expect(target.textContent).not.toContain('Your creations appear here');
			expect(classes(container())).toEqual(expect.arrayContaining(['relative', overflow]));
		});
	});

	it('keeps the slider for the empty state only', async () => {
		for (const [name, props] of STATES) {
			mountWorkbench({ emptyLayout: 'fade', ...props });
			await settle(6);
			expect(state()).toBe(name);
			expect(empty()!.querySelector('input[type="range"]') !== null).toBe(name === 'empty');
			component!.$destroy();
		}
		component = undefined;
	});

	it('swaps to the result and back without a leftover frame class', async () => {
		mountWorkbench({ emptyLayout: 'pane' });
		await settle(4);
		expect(empty()).not.toBeNull();
		component!.$set({
			currentGeneration: { id: 'gen-1', status: 'running', current_image: '/api/media/previews/gen-1.png' },
			isGenerating: true
		});
		await settle(6);
		expect(empty()).toBeNull();
		expect(classes(container())).toContain('relative');
		expect(classes(container())).toContain('overflow-hidden');
		component!.$set({ currentGeneration: null, isGenerating: false });
		await settle(6);
		expect(empty()).not.toBeNull();
		expect(classes(container())).not.toContain('relative');
		for (const name of FRAME) expect(classes(container())).not.toContain(name);
	});
});

describe('Workbench failed, suppressed and blocked states', () => {
	it.each(STATES.slice(1))('use the same full surface as the empty state when %s', async (name, props) => {
		await eachLayout(props, (layout) => {
			expect(state()).toBe(name);
			expect(empty()!.getAttribute('data-layout')).toBe(layout);
			expect(empty()!.querySelector('.dot-grid')).not.toBeNull();
			expectFrameless();
			expect(target.textContent).not.toContain('Your creations appear here');
		});
	});

	it('keeps the failure copy, the error id and its actions', async () => {
		mountWorkbench({ emptyLayout: 'pane', ...FAILED });
		await settle(6);
		const surface = empty()!;
		expect(surface.textContent).toContain('Generation failed');
		expect(surface.textContent).toContain('Out of GPU memory');
		expect(surface.textContent).toContain('ERR-1');
		expect(surface.querySelector('button[aria-label="Copy error ID"]')).not.toBeNull();
		expect(surface.querySelector('details[data-failure-technical-details] summary')!.textContent).toContain('Technical details');
	});

	it('keeps the failure actions clickable under the pointer-less pane stage', async () => {
		mountWorkbench({ emptyLayout: 'pane', ...FAILED });
		await settle(6);
		expect(classes(empty())).toContain('pointer-events-none');
		const copy = empty()!.querySelector('.select-none')!;
		expect(classes(copy)).toContain('pointer-events-auto');
		expect(copy.contains(empty()!.querySelector('button[aria-label="Copy error ID"]'))).toBe(true);
	});

	it('keeps the content policy tiles and their status role', async () => {
		mountWorkbench({ emptyLayout: 'window', ...SUPPRESSED });
		await settle(6);
		const preview = empty()!.querySelector('[data-content-policy-tile="preview"]')!;
		expect(preview.getAttribute('role')).toBe('status');
		expect(preview.textContent).toContain('Previews are hidden by the content policy');
		component!.$destroy();
		mountWorkbench({ emptyLayout: 'window', ...BLOCKED });
		await settle(6);
		const blocked = empty()!.querySelector('[data-content-policy-tile="blocked"]')!;
		expect(blocked.getAttribute('role')).toBe('status');
		expect(blocked.textContent).toContain('1 of 2 blocked');
	});

	it('puts a failure ahead of a suppressed preview and blocked content', async () => {
		mountWorkbench({
			emptyLayout: 'pane',
			currentGeneration: { ...FAILED.currentGeneration, preview_suppressed: true, content_blocked: { blocked_count: 1, total: 2 } }
		});
		await settle(6);
		expect(state()).toBe('failed');
		expect(empty()!.querySelector('[data-content-policy-tile]')).toBeNull();
	});

	it('swaps from the empty state to a failure and back without a leftover class', async () => {
		mountWorkbench({ emptyLayout: 'fade' });
		await settle(4);
		expect(state()).toBe('empty');
		component!.$set(FAILED);
		await settle(6);
		expect(state()).toBe('failed');
		expect(empty()!.getAttribute('data-layout')).toBe('fade');
		expectFrameless();
		component!.$set({ currentGeneration: null });
		await settle(6);
		expect(state()).toBe('empty');
	});
});

describe('Workbench failure admin gate', () => {
	const asAdmin = (admin: boolean) => (authStore as unknown as Writable<unknown>).set({ user: { account_type: admin ? 'ADMIN' : 'USER' } });
	const profileButton = () => [...target.querySelectorAll('button')].find((button) => button.textContent?.trim() === 'View resource profile');

	beforeEach(() => {
		vi.mocked(api.getGenerationById).mockResolvedValue({ success: true, data: { files: [], has_profile: true } } as never);
	});

	it('shows the resource profile to an admin and no hand-off line', async () => {
		asAdmin(true);
		mountWorkbench({ emptyLayout: 'pane', ...FAILED });
		await settle(10);
		expect(profileButton()).toBeDefined();
		expect(empty()!.textContent).not.toContain('Give this to your admin');
	});

	it('hides the resource profile from other users and tells them to hand the error id to an admin', async () => {
		asAdmin(false);
		mountWorkbench({ emptyLayout: 'pane', ...FAILED });
		await settle(10);
		expect(profileButton()).toBeUndefined();
		expect(empty()!.textContent).toContain('Give this to your admin');
	});

	it('hides the resource profile from an admin when no profile was captured', async () => {
		asAdmin(true);
		vi.mocked(api.getGenerationById).mockResolvedValue({ success: true, data: { files: [], has_profile: false } } as never);
		mountWorkbench({ emptyLayout: 'pane', ...FAILED });
		await settle(10);
		expect(profileButton()).toBeUndefined();
	});
});
