import { generationTabs } from '$lib/types/tabs';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';
import { flushSync } from 'svelte';
import { settle } from './helpers/settle';
import { stubClientWidth } from './helpers/stubClientWidth';

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
const { PANE_HANDLE_STRIP, PANE_HANDLE_GRIP } = await import('$lib/generation/paneHandle');
const { PANE_HANDLE_WIDTH, WORKBENCH_FLOOR_WIDTH } = await import('$lib/stores/generationLayout');
const { default: Fixture } = await import('./fixtures/GenerationPanelsStoreFixture.svelte');
const { createClassComponent } = await import('svelte/legacy');

const PANELS = 1384;
const PROMPT_LEFT = 12;
const GRAB_AT = 600;
const GRAB_OFFSET = 6;
let originalRO: unknown;
let restoreWidth: (() => void) | undefined;
let rectSpy: ReturnType<typeof vi.spyOn> | undefined;
let host: HTMLElement;
let component: { $destroy: () => void } | undefined;
let tabId = '';

beforeEach(() => {
	originalRO = (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
	(globalThis as { ResizeObserver?: unknown }).ResizeObserver = FakeResizeObserver;
	host = document.body.appendChild(document.createElement('div'));
});

afterEach(() => {
	component?.$destroy();
	component = undefined;
	rectSpy?.mockRestore();
	restoreWidth?.();
	host.remove();
	(globalThis as { ResizeObserver?: unknown }).ResizeObserver = originalRO;
});

function mountPanels(patch: Record<string, unknown> = {}, width = PANELS) {
	restoreWidth = stubClientWidth(width);
	rectSpy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
		const id = this.getAttribute('data-testid');
		const [left, right] =
			id === 'generation-panels-root'
				? [0, width]
				: id === 'prompts-pane'
					? [PROMPT_LEFT, PROMPT_LEFT + 640]
					: id === 'prompts-workbench-handle'
						? [GRAB_AT - GRAB_OFFSET, GRAB_AT - GRAB_OFFSET + 12]
						: [0, 0];
		return { left, right, top: 0, bottom: 0, width: right - left, height: 0, x: left, y: 0, toJSON: () => ({}) } as DOMRect;
	});
	tabId = tabsStore.addTabWithData('Handle', { selectedPreset: 'preset', selectedMode: 'image', layoutMode: 'three', ...patch });
	component = createClassComponent({ component: Fixture as never, target: host, props: { tabId } }) as never;
	flushSync();
}

const tab = () => generationTabs(get(tabsStore).tabs).find((candidate) => candidate.id === tabId)!;
const handle = () => host.querySelector<HTMLButtonElement>('[data-testid="prompts-workbench-handle"]')!;
const formHandle = () => host.querySelector<HTMLButtonElement>('button[aria-label="Collapse generation settings"]')!;
const tokens = (classes: string) => classes.split(/\s+/).filter(Boolean);
const pointer = (type: string, target: EventTarget, clientX: number) => target.dispatchEvent(new MouseEvent(type, { clientX, bubbles: true, cancelable: true }));
const key = (name: string) => handle().dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true, cancelable: true }));
const folded = () => tab().promptPanelWidthFolded;

describe('prompts and workbench resize handle', () => {
	it('is the same strip as the form handle, with a grip pill and a column-resize cursor', async () => {
		mountPanels({}, 1864);
		await settle(6);
		expect(formHandle()).not.toBeNull();
		for (const name of tokens(PANE_HANDLE_STRIP)) {
			expect(handle().classList.contains(name)).toBe(true);
			expect(formHandle().classList.contains(name)).toBe(true);
		}
		expect(handle().classList.contains('cursor-col-resize')).toBe(true);
		for (const name of ['w-1', 'bg-line']) expect(handle().classList.contains(name)).toBe(false);
		const grip = handle().querySelector('span[aria-hidden="true"]')!;
		for (const name of tokens(PANE_HANDLE_GRIP)) expect(grip.classList.contains(name)).toBe(true);
		expect(handle().querySelectorAll('div')).toHaveLength(0);
		expect(handle().getAttribute('aria-label')).toBe('Resize prompt panel');
	});

	it('reserves its 12px in the drag ceiling, so the workbench keeps its floor', async () => {
		mountPanels({ leftPanelCollapsed: true });
		await settle(6);
		const down = handle();
		pointer('pointerdown', down, 600);
		pointer('pointermove', document, 5000);
		flushSync();
		expect(folded()).toBe(PANELS - PROMPT_LEFT - WORKBENCH_FLOOR_WIDTH - PANE_HANDLE_WIDTH);
		pointer('pointerup', document, 5000);
	});

	it('resizes with a drag and clamps at the readable minimum', async () => {
		mountPanels({ leftPanelCollapsed: true });
		await settle(6);
		pointer('pointerdown', handle(), GRAB_AT);
		pointer('pointermove', document, 900);
		flushSync();
		expect(folded()).toBe(900 - GRAB_OFFSET - PROMPT_LEFT);
		pointer('pointermove', document, 100);
		flushSync();
		expect(folded()).toBe(640);
		pointer('pointerup', document, 100);
		expect(tab().workbenchCollapsed).toBe(false);
	});

	it('shows the dragging state only while a drag is in progress', async () => {
		mountPanels({ leftPanelCollapsed: true });
		await settle(6);
		expect(handle().getAttribute('data-dragging')).toBe('false');
		pointer('pointerdown', handle(), 600);
		pointer('pointermove', document, 700);
		flushSync();
		expect(handle().getAttribute('data-dragging')).toBe('true');
		expect(document.body.style.cursor).toBe('col-resize');
		pointer('pointerup', document, 700);
		flushSync();
		expect(handle().getAttribute('data-dragging')).toBe('false');
		expect(document.body.style.cursor).toBe('');
	});

	it('resizes 24px per arrow key and ignores other keys', async () => {
		mountPanels({ leftPanelCollapsed: true, promptPanelWidthFolded: 800 });
		await settle(6);
		key('ArrowRight');
		flushSync();
		expect(folded()).toBe(824);
		key('ArrowLeft');
		flushSync();
		expect(folded()).toBe(800);
		key('ArrowLeft');
		flushSync();
		expect(folded()).toBe(776);
		key('Enter');
		flushSync();
		expect(folded()).toBe(776);
	});

	it('bounds the prompts pane so the workbench column keeps its floor and the handle', async () => {
		mountPanels({});
		await settle(6);
		const pane = host.querySelector<HTMLElement>('[data-testid="prompts-pane"]')!;
		expect(pane.getAttribute('style')).toContain(`${WORKBENCH_FLOOR_WIDTH + PANE_HANDLE_WIDTH}px`);
		expect(pane.getAttribute('style')).toContain('332px');
	});

	it('still folds the workbench on a click that never travels', async () => {
		mountPanels({ leftPanelCollapsed: true });
		await settle(6);
		pointer('pointerdown', handle(), 600);
		pointer('pointerup', document, 602);
		flushSync();
		expect(tab().workbenchCollapsed).toBe(true);
		expect(handle()).toBeNull();
	});
});
