import { generationTabs } from '$lib/types/tabs';
import { describe, it, expect, afterEach, beforeEach, vi, onTestFinished } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';
import { get } from 'svelte/store';
import { settle } from './helpers/settle';

const { authState, getPreset, declaration } = vi.hoisted(() => ({
	authState: { user: { account_type: 'USER' } as { account_type: string } | null },
	getPreset: vi.fn(),
	declaration: { current: { fields: ['seed'] } as unknown }
}));

vi.mock('$lib/stores/auth', () => ({
	authStore: { subscribe: (fn: (v: unknown) => void) => (fn(authState), () => {}) }
}));

vi.mock('$lib/services/api/index', async (orig) => {
	const real: any = await orig();
	return {
		...real,
		api: {
			...real.api,
			getPreset,
			getClient: () => ({}),
			getPresetPickerState: async () => ({ success: true, data: { pinned: [], recent: [] } })
		}
	};
});

vi.mock('$lib/stores/formulas', async (orig) => {
	const real: any = await orig();
	const { readable } = await import('svelte/store');
	return {
		...real,
		formulaLists: readable({ 'krea2 txt2img': { items: [{}, {}, {}], loading: false, loaded: true, error: null } }),
		getFormulaDeclaration: vi.fn(async () => declaration.current),
		loadFormulas: vi.fn(async () => {})
	};
});

const { default: ContextToolbar } = await import('../../src/routes/generate/components/ContextToolbar.svelte');
const { tabsStore } = await import('$lib/stores/tabs');
const { formAudienceStore } = await import('$lib/stores/formAudience');
const { reactiveProps } = await import('./stubs/reactiveProps.svelte');
const { keybindingsStore } = await import('$lib/stores/keybindings');
const { api } = await import('$lib/services/api/index');
const { default: Kbd } = await import('$lib/components/ui/Kbd.svelte');

const PRESETS = [{ id: 'krea2', name: 'Krea-2', version: '1.1.0', engine: 'native', category: 'image', tags: [], description: '' }];
const MODES = [
	{ id: 'txt2img', label: 'Text to Image', description: 'Generate an image from a text prompt.' },
	{ id: 'edit', label: 'Edit' }
];
const VARIANT_MODES = [
	{
		id: 'txt2img',
		label: 'Text to Image',
		variants: [
			{ name: 'image', label: 'Image', order: 0, default: true },
			{ name: 'video', label: 'Video', order: 1 }
		]
	},
	{ id: 'edit', label: 'Edit' }
];

function sessionState(overrides: Record<string, unknown> = {}) {
	const stamp = new Date(Date.now() - 120_000).toISOString();
	const current = { id: 'a', preset_id: 'krea2', name: 'Neon alley', data: {}, created_at: stamp, updated_at: stamp };
	return {
		sessions: [current],
		currentSession: current,
		selectedSessionId: 'a',
		isSessionLoading: false,
		isQuickSaving: false,
		hasUnsavedChanges: false,
		lastSavedTime: new Date(),
		sessionControlsEnabled: true,
		autoSaveEnabled: false,
		autoSaveInterval: 10000,
		historySessionId: null,
		historyVersions: [],
		isHistoryLoading: false,
		historyError: null,
		isRestoringVersion: false,
		...overrides
	};
}

function sessionActions() {
	return {
		select: vi.fn(),
		save: vi.fn(),
		saveAs: vi.fn(),
		startNew: vi.fn(),
		rename: vi.fn(),
		remove: vi.fn(),
		togglePin: vi.fn(),
		toggleAutosave: vi.fn(),
		setAutosaveInterval: vi.fn(),
		openHistory: vi.fn(),
		closeHistory: vi.fn(),
		restoreVersion: vi.fn()
	};
}

class FakeResizeObserver {
	static instances = new Set<FakeResizeObserver>();
	cb: () => void;
	observed = new Set<Element>();
	constructor(cb: () => void) {
		this.cb = cb;
		FakeResizeObserver.instances.add(this);
	}
	observe(element: Element) {
		this.observed.add(element);
		queueMicrotask(() => this.cb());
	}
	unobserve(element: Element) {
		this.observed.delete(element);
	}
	disconnect() {
		this.observed.clear();
		FakeResizeObserver.instances.delete(this);
	}
}

function resizeTargets(): Element[] {
	return Array.from(FakeResizeObserver.instances).flatMap((observer) => Array.from(observer.observed));
}

async function notifyResize(element: Element) {
	for (const observer of Array.from(FakeResizeObserver.instances)) if (observer.observed.has(element)) observer.cb();
	flushSync();
	await settle(8);
	flushSync();
}

const originalRO = (globalThis as any).ResizeObserver;
const clientDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth');
const scrollDescriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollWidth');

type Box = { client: number; scroll: (el: HTMLElement) => number };
let boxes: Record<string, Box> = {};

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;
let tabId = '';
let stopTabSync: (() => void) | null = null;
let liveProps: ReturnType<typeof reactiveProps> | null = null;

function tab() {
	return generationTabs(get(tabsStore).tabs).find((t) => t.id === tabId)!;
}

function measure(next: Record<string, Box>) {
	boxes = next;
}

function installMeasure() {
	Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
		configurable: true,
		get() {
			return boxes[this.dataset?.testid ?? '']?.client ?? 0;
		}
	});
	Object.defineProperty(Element.prototype, 'scrollWidth', {
		configurable: true,
		get() {
			const box = boxes[(this as HTMLElement).dataset?.testid ?? ''];
			return box ? box.scroll(this as HTMLElement) : 0;
		}
	});
}

function count(el: HTMLElement, selector: string): number {
	return el.querySelectorAll(selector).length;
}

function barContent(bar: HTMLElement): number {
	return 400 + 100 * count(bar, '[data-testid="formulas-button"]') + 100 * count(bar, '[data-context-tool]') + 40 * count(bar, '[data-testid="context-tools-more"]');
}

function stagedContent(bar: HTMLElement): number {
	const atMinimum = count(bar, '[data-testid="preset-picker-trigger"].max-w-\\[96px\\]') > 0;
	const capped = count(bar, '[data-testid="preset-picker-trigger"].max-w-\\[160px\\]') > 0;
	const labelsHidden = count(bar, '[data-context-label].sr-only') > 0;
	return 300 + 100 * count(bar, '[data-testid="formulas-button"]') + 100 * count(bar, '[data-context-tool]') + 40 * count(bar, '[data-testid="context-tools-more"]') + (atMinimum ? 0 : capped ? 75 : 150) + (labelsHidden ? 0 : 120);
}

beforeEach(() => {
	authState.user = { account_type: 'USER' };
	declaration.current = { fields: ['seed'] };
	getPreset.mockResolvedValue({ success: true, data: { media: { gallery: [] } } });
	formAudienceStore.set('simple');
	(globalThis as any).ResizeObserver = FakeResizeObserver;
	FakeResizeObserver.instances.clear();
	boxes = {};
	installMeasure();
	tabId = tabsStore.addTabWithData('Toolbar tab', { selectedPreset: 'krea2', selectedMode: 'txt2img', layoutMode: 'two' });
});

afterEach(() => {
	stopTabSync?.();
	stopTabSync = null;
	if (component) unmount(component);
	component = null;
	target?.remove();
	tabsStore.removeTab(tabId);
	document.body.innerHTML = '';
	(globalThis as any).ResizeObserver = originalRO;
	if (clientDescriptor) Object.defineProperty(HTMLElement.prototype, 'clientWidth', clientDescriptor);
	if (scrollDescriptor) Object.defineProperty(Element.prototype, 'scrollWidth', scrollDescriptor);
	else delete (Element.prototype as any).scrollWidth;
});

async function render(overrides: Record<string, unknown> = {}) {
	const handlers = {
		onPresetChange: vi.fn(),
		onModeChange: vi.fn(),
		onVariantChange: vi.fn(),
		onReload: vi.fn()
	};
	const actions = sessionActions();
	target = document.createElement('div');
	document.body.appendChild(target);
	const live = reactiveProps({
		tab: tab(),
		presets: PRESETS,
		availableModes: MODES,
		session: sessionState(),
		actions,
		...handlers,
		...overrides
	} as Record<string, unknown>);
	liveProps = live;
	stopTabSync?.();
	stopTabSync = tabsStore.subscribe(() => {
		const current = tab();
		if (current) live.set({ tab: current });
	});
	component = mount(ContextToolbar as never, { target, props: live.props } as never);
	flushSync();
	await settle(8);
	flushSync();
	return { ...handlers, actions };
}

function bar() {
	return target.querySelector('[data-testid="context-bar"]') as HTMLElement;
}

function labels() {
	return Array.from(bar().querySelectorAll<HTMLElement>('[data-context-label]')).map((el) => el.textContent?.trim());
}

function toolOrder() {
	return Array.from(
		target.querySelectorAll<HTMLElement>('[data-testid="context-tools"] [data-testid="formulas-button"], [data-testid="context-tools"] [data-context-tool], [data-testid="context-tools-more"]')
	).map((el) => el.dataset.contextTool ?? (el.dataset.testid === 'formulas-button' ? 'formulas' : 'more'));
}

async function openMore() {
	(target.querySelector('[data-testid="context-tools-more"]') as HTMLElement).click();
	flushSync();
	await tick();
	return Array.from(document.body.querySelectorAll<HTMLElement>('[role="menu"][aria-label="More tools"] button'));
}

function nameOf(el: Element): string {
	const ids = el.getAttribute('aria-labelledby');
	if (!ids) return el.getAttribute('aria-label') ?? '';
	return ids
		.split(' ')
		.map((id) => document.getElementById(id)?.textContent?.trim() ?? '')
		.join(' ');
}

function saveButton() {
	return target.querySelector('[data-testid="session-save"]') as HTMLButtonElement;
}

describe('ContextToolbar background', () => {
	it('paints one canvas surface on the bar root and leaves every zone plain, with no divider between them', async () => {
		await render();
		const root = bar();
		expect(root.classList.contains('bg-canvas')).toBe(true);
		expect(root.classList.contains('bg-surface-1')).toBe(false);
		expect(root.classList.contains('border-b')).toBe(true);
		expect(root.classList.contains('border-line')).toBe(true);
		const zones = Array.from(root.children) as HTMLElement[];
		expect(zones.map((zone) => zone.dataset.testid)).toEqual(['context-zone', 'context-gap', 'context-tools']);
		for (const zone of zones) {
			expect(Array.from(zone.classList).filter((token) => token.startsWith('bg-')), `${zone.dataset.testid} paint`).toEqual([]);
			expect(Array.from(zone.classList).filter((token) => token.startsWith('border')), `${zone.dataset.testid} borders`).toEqual([]);
		}
		expect(Array.from(zones[0].classList).filter((token) => token.startsWith('gap-')), 'context zone gap').toEqual([]);
	});
});

describe('ContextToolbar ribbon', () => {
	const CUT = '[data-testid="context-separator"]';
	const CUT_WIDTH = 20;
	const ZONE_PARTS: Record<string, string> = {
		'context-separator': 'cut',
		'preset-header-picker': 'preset',
		'preset-header-mode': 'mode',
		'preset-variant-switch': 'variant',
		'context-session': 'session'
	};

	function zone(): HTMLElement {
		return target.querySelector('[data-testid="context-zone"]') as HTMLElement;
	}

	function zoneSequence(): string[] {
		return Array.from(zone().children).map((child) => {
			const id = Object.keys(ZONE_PARTS).find((testid) => (child as HTMLElement).dataset.testid === testid || child.querySelector(`[data-testid="${testid}"]`));
			return id ? ZONE_PARTS[id] : 'unknown';
		});
	}

	function steps(): HTMLElement[] {
		return Array.from(zone().children).filter((child) => (child as HTMLElement).dataset.testid !== 'context-separator') as HTMLElement[];
	}

	it('cuts the ribbon once between Preset, Mode and Session, with none before the first step or after Save', async () => {
		await render();
		expect(zoneSequence()).toEqual(['preset', 'cut', 'mode', 'cut', 'session']);
		expect(zone().lastElementChild!.contains(saveButton())).toBe(true);
	});

	it('adds a cut before the Variant step when the mode has variants', async () => {
		await render({ availableModes: VARIANT_MODES });
		expect(zoneSequence()).toEqual(['preset', 'cut', 'mode', 'cut', 'variant', 'cut', 'session']);
	});

	it('cuts once between Preset and Session when there is only one mode', async () => {
		await render({ availableModes: [MODES[0]] });
		expect(zoneSequence()).toEqual(['preset', 'cut', 'session']);
	});

	it('keeps every cut decorative and out of the accessibility tree', async () => {
		await render();
		const cuts = Array.from(target.querySelectorAll<HTMLElement>(CUT));
		expect(cuts).toHaveLength(2);
		for (const cut of cuts) {
			expect(cut.getAttribute('aria-hidden')).toBe('true');
			expect(cut.querySelector('button, a, [tabindex]')).toBeNull();
			expect(cut.textContent!.trim()).toBe('');
		}
	});

	it('gives every step the same inline padding and narrows it once the labels are hidden', async () => {
		await render({ availableModes: VARIANT_MODES });
		expect(steps()).toHaveLength(4);
		for (const step of steps()) expect(step.classList.contains('px-4'), step.dataset.testid ?? 'preset').toBe(true);
		unmount(component!);
		component = null;
		target.remove();
		measure({ 'context-bar': { client: 500, scroll: stagedContent } });
		await render({ availableModes: VARIANT_MODES });
		for (const step of steps()) {
			expect(step.classList.contains('px-2.5'), step.dataset.testid ?? 'preset').toBe(true);
			expect(step.classList.contains('px-4'), step.dataset.testid ?? 'preset').toBe(false);
		}
	});

	async function updateTabAndSettle(patch: Record<string, unknown>) {
		tabsStore.updateTab(tabId, patch);
		flushSync();
		await settle(8);
		flushSync();
	}

	it('shows only Preset, with no cut and no Save, before a preset is chosen', async () => {
		tabsStore.updateTab(tabId, { selectedPreset: null, selectedMode: null });
		await render({ availableModes: MODES });
		expect(zoneSequence()).toEqual(['preset']);
		expect(target.querySelectorAll(CUT)).toHaveLength(0);
		expect(saveButton()).toBeNull();
		expect(labels()).toEqual(['Preset']);
	});

	it('shows Preset and Mode, with no Session, while a preset with modes has no mode resolved yet', async () => {
		tabsStore.updateTab(tabId, { selectedMode: null });
		await render({ availableModes: MODES });
		expect(zoneSequence()).toEqual(['preset', 'cut', 'mode']);
		expect(saveButton()).toBeNull();
		expect(labels()).toEqual(['Preset', 'Mode']);
	});

	it('cuts between Preset, Variant and Session for a single-mode preset whose mode has variants', async () => {
		await render({ availableModes: [VARIANT_MODES[0]] });
		expect(zoneSequence()).toEqual(['preset', 'cut', 'variant', 'cut', 'session']);
	});

	it('keeps Session hidden for a single-mode preset until its mode is resolved', async () => {
		tabsStore.updateTab(tabId, { selectedMode: null });
		await render({ availableModes: [MODES[0]] });
		expect(zoneSequence()).toEqual(['preset']);
		await updateTabAndSettle({ selectedMode: 'txt2img' });
		expect(zoneSequence()).toEqual(['preset', 'cut', 'session']);
	});

	it('shows nothing past Preset when modes linger from another preset and no preset is chosen', async () => {
		tabsStore.updateTab(tabId, { selectedPreset: null, selectedMode: 'txt2img' });
		await render({ availableModes: VARIANT_MODES });
		expect(zoneSequence()).toEqual(['preset']);
	});

	it('adds Mode and then Session as the tab gains a preset and a mode, and takes them away again', async () => {
		tabsStore.updateTab(tabId, { selectedPreset: null, selectedMode: null });
		await render({ availableModes: MODES });
		expect(zoneSequence()).toEqual(['preset']);
		await updateTabAndSettle({ selectedPreset: 'krea2' });
		expect(zoneSequence()).toEqual(['preset', 'cut', 'mode']);
		await updateTabAndSettle({ selectedMode: 'txt2img' });
		expect(zoneSequence()).toEqual(['preset', 'cut', 'mode', 'cut', 'session']);
		await updateTabAndSettle({ selectedPreset: null, selectedMode: null });
		expect(zoneSequence()).toEqual(['preset']);
		expect(target.querySelectorAll(CUT)).toHaveLength(0);
	});

	it('re-fits when a step appears without the bar or the tab changing', async () => {
		measure({ 'context-bar': { client: 1000, scroll: (el) => barContent(el) + 230 + CUT_WIDTH * count(el, CUT) } });
		await render({ availableModes: MODES });
		const withModes = toolOrder();
		unmount(component!);
		component = null;
		target.remove();

		await render({ availableModes: [] });
		const withoutModes = toolOrder();
		expect(withoutModes).not.toEqual(withModes);
		liveProps!.set({ availableModes: MODES });
		flushSync();
		await settle(8);
		flushSync();
		expect(zoneSequence()).toEqual(['preset', 'cut', 'mode', 'cut', 'session']);
		expect(toolOrder()).toEqual(withModes);
	});

	it('re-fits when a step appears even where ResizeObserver is unavailable', async () => {
		measure({ 'context-bar': { client: 1000, scroll: (el) => barContent(el) + 230 + CUT_WIDTH * count(el, CUT) } });
		(globalThis as any).ResizeObserver = undefined;
		await render({ availableModes: [] });
		const withoutModes = toolOrder();
		liveProps!.set({ availableModes: MODES });
		flushSync();
		await settle(8);
		flushSync();
		expect(zoneSequence()).toEqual(['preset', 'cut', 'mode', 'cut', 'session']);
		expect(toolOrder()).not.toEqual(withoutModes);
	});

	it('has no tools zone until a preset is chosen, and keeps the bar one row tall', async () => {
		tabsStore.updateTab(tabId, { selectedPreset: null, selectedMode: null });
		await render({ availableModes: MODES });
		expect(target.querySelector('[data-testid="context-tools"]')).toBeNull();
		expect(target.querySelector('[data-testid="formulas-button"]')).toBeNull();
		expect(target.querySelector('[data-context-tool]')).toBeNull();
		expect(target.querySelector('[data-testid="context-tools-more"]')).toBeNull();
		expect(Array.from(bar().children).map((child) => (child as HTMLElement).dataset.testid)).toEqual(['context-zone', 'context-gap']);
		expect(bar().classList.contains('h-12')).toBe(true);
	});

	it('brings every tool in once a preset is chosen', async () => {
		tabsStore.updateTab(tabId, { selectedPreset: null, selectedMode: null });
		await render({ availableModes: MODES });
		await updateTabAndSettle({ selectedPreset: 'krea2', selectedMode: 'txt2img' });
		expect(target.querySelector('[data-testid="context-tools"]')).not.toBeNull();
		expect(toolOrder()).toEqual(['formulas', 'fields', 'layout', 'more']);
		expect(Array.from(bar().children).map((child) => (child as HTMLElement).dataset.testid)).toEqual(['context-zone', 'context-gap', 'context-tools']);
	});

	it('shows the tools as soon as a preset is chosen, before its mode is resolved', async () => {
		tabsStore.updateTab(tabId, { selectedMode: null });
		await render({ availableModes: MODES });
		expect(zoneSequence()).toEqual(['preset', 'cut', 'mode']);
		expect(target.querySelector('[data-testid="context-tools"]')).not.toBeNull();
	});

	it('takes the tools away again when the preset is cleared', async () => {
		await render({ availableModes: MODES });
		expect(target.querySelector('[data-testid="context-tools"]')).not.toBeNull();
		await updateTabAndSettle({ selectedPreset: null, selectedMode: null });
		expect(target.querySelector('[data-testid="context-tools"]')).toBeNull();
	});

	it('re-fits the tools when they appear instead of keeping a fold from before', async () => {
		measure({ 'context-bar': { client: 1000, scroll: (el) => barContent(el) + 300 } });
		tabsStore.updateTab(tabId, { selectedPreset: null, selectedMode: null });
		await render({ availableModes: MODES });
		expect(toolOrder()).toEqual([]);
		await updateTabAndSettle({ selectedPreset: 'krea2', selectedMode: 'txt2img' });
		expect(toolOrder()).toEqual(['formulas', 'fields', 'more']);
	});

	it('measures a bar without tools without touching missing elements', async () => {
		measure({ 'context-bar': { client: 600, scroll: stagedContent } });
		tabsStore.updateTab(tabId, { selectedPreset: null, selectedMode: null });
		await render({ availableModes: MODES });
		expect(target.querySelector('[data-testid="context-tools"]')).toBeNull();
		expect(labels()).toEqual(['Preset']);
	});

	it('observes every visible step, including one that appears later', async () => {
		await render({ availableModes: [] });
		const stepEls = Array.from(zone().querySelectorAll('[data-context-step]'));
		expect(stepEls).toHaveLength(2);
		for (const step of stepEls) expect(resizeTargets(), step.className).toContain(step);
		for (const cut of Array.from(target.querySelectorAll(CUT))) expect(resizeTargets()).not.toContain(cut);
		expect(target.querySelector('[data-testid="preset-header-mode"]')).toBeNull();

		liveProps!.set({ availableModes: MODES });
		flushSync();
		await settle(8);
		flushSync();
		const mode = target.querySelector('[data-testid="preset-header-mode"]')!;
		expect(mode.hasAttribute('data-context-step')).toBe(true);
		expect(resizeTargets()).toContain(mode);
		for (const cut of Array.from(target.querySelectorAll(CUT))) expect(resizeTargets()).not.toContain(cut);
	});

	it('observes a Variant step and a Session step that appear later', async () => {
		tabsStore.updateTab(tabId, { selectedMode: null });
		await render({ availableModes: [MODES[0]] });
		expect(Array.from(zone().querySelectorAll('[data-context-step]'))).toHaveLength(1);

		await updateTabAndSettle({ selectedMode: 'txt2img' });
		const session = target.querySelector('[data-testid="context-session"]')!.closest('[data-context-step]')!;
		expect(resizeTargets()).toContain(session);

		liveProps!.set({ availableModes: [VARIANT_MODES[0]] });
		flushSync();
		await settle(8);
		flushSync();
		const variant = target.querySelector('[data-testid="preset-variant-switch"]')!;
		expect(variant.hasAttribute('data-context-step')).toBe(true);
		expect(resizeTargets()).toContain(variant);
	});

	it('stops observing the bar and its steps when the toolbar goes away', async () => {
		await render();
		expect(resizeTargets().length).toBeGreaterThan(2);
		unmount(component!);
		component = null;
		expect(resizeTargets()).toEqual([]);
	});

	it('re-fits when a step grows after the first fit while the bar keeps its size', async () => {
		let grown = false;
		measure({ 'context-bar': { client: 1000, scroll: (el) => barContent(el) + 200 + (grown ? 100 : 0) } });
		await render();
		expect(toolOrder()).toEqual(['formulas', 'fields', 'layout', 'more']);

		grown = true;
		await notifyResize(zone().children[0]);

		expect(toolOrder()).toEqual(['formulas', 'fields', 'more']);
	});

	it('leaves the fit alone when a step resizes and nothing overflows', async () => {
		let reads = 0;
		measure({
			'context-bar': {
				client: 1000,
				scroll: (el) => {
					reads += 1;
					return barContent(el) + 200;
				}
			}
		});
		await render();
		const before = reads;
		await notifyResize(zone().children[0]);
		expect(reads - before).toBe(1);
		expect(toolOrder()).toEqual(['formulas', 'fields', 'layout', 'more']);
	});

	it('counts the cuts in the width the fit logic measures, folding a tool they push out', async () => {
		measure({ 'context-bar': { client: 1000, scroll: (el) => barContent(el) + 260 + CUT_WIDTH * count(el, CUT) } });
		await render();
		expect(toolOrder()).toEqual(['formulas', 'fields', 'more']);
	});

	it('keeps the same tools when the cuts are not counted against the bar', async () => {
		measure({ 'context-bar': { client: 1000, scroll: (el) => barContent(el) + 260 } });
		await render();
		expect(toolOrder()).toEqual(['formulas', 'fields', 'layout', 'more']);
	});
});

describe('ContextToolbar control styles', () => {
	it('draws the context selects without a fill, Save as a filled button and tools as bordered buttons', async () => {
		await render({ session: sessionState({ hasUnsavedChanges: true }) });
		const preset = target.querySelector('[data-testid="preset-picker-trigger"]') as HTMLElement;
		const mode = target.querySelector('[data-testid="preset-header-mode"] button') as HTMLElement;
		const session = target.querySelector('[data-testid="session-trigger"]') as HTMLElement;
		for (const control of [preset, mode, session]) {
			expect(control.classList.contains('ctx-select')).toBe(true);
			expect(Array.from(control.classList).filter((token) => /^(hover:)?bg-|^rounded|^px-|^h-8$|^border/.test(token))).toEqual([]);
			expect(control.classList.contains('is-open')).toBe(false);
		}
		expect(session.classList.contains('ctx-select-session')).toBe(true);
		expect(preset.classList.contains('text-md')).toBe(true);
		expect(preset.classList.contains('font-semibold')).toBe(true);
		expect(mode.classList.contains('font-medium')).toBe(true);
		mode.click();
		await tick();
		flushSync();
		expect(mode.getAttribute('aria-expanded')).toBe('true');
		expect(mode.classList.contains('is-open')).toBe(true);
		expect(Array.from(mode.classList).filter((token) => token.startsWith('bg-'))).toEqual([]);
		expect(mode.className).not.toMatch(/segment-open|shadow-|border-b|border-signal/);
		expect(mode.querySelector('svg:last-child')!.getAttribute('class')).toContain('rotate-180');
		session.click();
		flushSync();
		expect(session.getAttribute('aria-expanded')).toBe('true');
		expect(session.classList.contains('is-open')).toBe(true);
		expect(Array.from(session.classList).filter((token) => token.startsWith('bg-'))).toEqual([]);
		expect(session.className).not.toMatch(/segment-open|shadow-|border-signal/);
		const save = saveButton();
		expect(['h-8', 'rounded', 'bg-surface-3', 'text-accent', 'font-semibold'].every((token) => save.classList.contains(token))).toBe(true);
		for (const tool of Array.from(target.querySelectorAll<HTMLElement>('[data-context-tool], [data-testid="formulas-button"], [data-testid="context-tools-more"]'))) {
			expect(['h-8', 'border', 'border-line', 'bg-control-fill', 'hover:bg-control-fill-hover'].every((token) => tool.classList.contains(token)), tool.textContent ?? '').toBe(true);
		}
	});

	it('marks the Preset select open only while its dropdown is', async () => {
		await render({ session: sessionState() });
		const preset = target.querySelector('[data-testid="preset-picker-trigger"]') as HTMLElement;
		expect(preset.classList.contains('is-open')).toBe(false);
		preset.click();
		await tick();
		flushSync();
		expect(preset.getAttribute('aria-expanded')).toBe('true');
		expect(preset.classList.contains('is-open')).toBe(true);
	});

	it('turns the Preset chevron with the same transition as Mode and Session while its dropdown is open', async () => {
		await render({ session: sessionState() });
		const preset = target.querySelector('[data-testid="preset-picker-trigger"]') as HTMLElement;
		const mode = target.querySelector('[data-testid="preset-header-mode"] button') as HTMLElement;
		const chevron = () => preset.lastElementChild!.getAttribute('class')!.split(' ');
		const modeChevron = mode.querySelector('svg:last-child')!.getAttribute('class')!.split(' ');
		expect(chevron()).toEqual(modeChevron);
		expect(chevron()).toContain('transition-transform');
		expect(chevron()).not.toContain('rotate-180');
		preset.click();
		flushSync();
		await settle(4);
		expect(preset.getAttribute('aria-expanded')).toBe('true');
		expect(document.body.querySelector('[role="dialog"][aria-label="Choose preset"]')).not.toBeNull();
		expect(chevron()).toContain('rotate-180');
		preset.click();
		flushSync();
		await settle(2);
		expect(preset.getAttribute('aria-expanded')).toBe('false');
		expect(chevron()).not.toContain('rotate-180');
	});

	it('greys a clean Save on a half-filled pill', async () => {
		await render();
		const save = saveButton();
		expect(['bg-surface-3/50', 'text-fg-disabled', 'cursor-not-allowed'].every((token) => save.classList.contains(token))).toBe(true);
	});
});

describe('ContextToolbar context group', () => {
	it('puts each label above its control and names each control by its label and value', async () => {
		await render();
		expect(labels()).toEqual(['Preset', 'Mode', 'Session']);
		for (const label of Array.from(bar().querySelectorAll<HTMLElement>('[data-context-label]'))) {
			const step = label.parentElement!;
			expect(step.classList.contains('ctx-step')).toBe(true);
			expect(step.firstElementChild).toBe(label);
			expect(label.nextElementSibling!.contains(label)).toBe(false);
			expect(label.classList.contains('ctx-label')).toBe(true);
			expect(label.classList.contains('text-fg-subtle')).toBe(true);
			expect(step.classList.contains('items-center')).toBe(false);
		}
		const preset = target.querySelector('[data-testid="preset-picker-trigger"]') as HTMLElement;
		expect(nameOf(preset)).toBe('Preset Krea-2');
		expect(preset.classList.contains('ctx-select')).toBe(true);
		const mode = target.querySelector('[data-testid="preset-header-mode"] button') as HTMLElement;
		expect(nameOf(mode)).toBe('Mode Text to Image');
		expect(mode.classList.contains('ctx-select')).toBe(true);
		const session = target.querySelector('[data-testid="session-trigger"]') as HTMLElement;
		expect(session.textContent).toContain('Neon alley');
		expect(nameOf(session)).toBe('Session Neon alley');
		expect(session.hasAttribute('aria-label')).toBe(false);
		const segment = target.querySelector('[data-testid="context-session"]')!;
		expect(segment.classList.contains('ctx-seg')).toBe(true);
		expect(segment.classList.contains('h-8')).toBe(false);
		expect(segment.classList.contains('items-center')).toBe(false);
		expect(target.querySelector('[data-testid="preset-variant-switch"]')).toBeNull();
	});

	it('gives the selects a 132px minimum only while they are not squeezed, so the fit can still cap them', async () => {
		await render({ availableModes: VARIANT_MODES });
		const selects = () => [
			target.querySelector('[data-testid="preset-picker-trigger"]'),
			target.querySelector('[data-testid="preset-header-mode"] button'),
			target.querySelector('[data-testid="preset-variant-switch"] button')
		] as HTMLElement[];
		for (const select of selects()) expect(select.classList.contains('min-w-[132px]'), select.dataset.testid ?? 'select').toBe(true);
		unmount(component!);
		component = null;
		target.remove();
		measure({ 'context-bar': { client: 650, scroll: stagedContent } });
		await render({ availableModes: VARIANT_MODES });
		for (const select of selects()) {
			expect(select.classList.contains('min-w-[132px]')).toBe(false);
			expect(select.classList.contains('max-w-[160px]') || select.classList.contains('max-w-[96px]')).toBe(true);
		}
	});

	it('gives the Variant select the same label above and rule as the others', async () => {
		await render({ availableModes: VARIANT_MODES });
		const variant = target.querySelector('[data-testid="preset-variant-switch"]') as HTMLElement;
		const control = variant.querySelector('button') as HTMLElement;
		expect(variant.classList.contains('ctx-step')).toBe(true);
		expect(variant.firstElementChild!.classList.contains('ctx-label')).toBe(true);
		expect(control.classList.contains('ctx-select')).toBe(true);
		expect(Array.from(control.classList).filter((token) => token.startsWith('bg-'))).toEqual([]);
	});

	it('keeps the labels in the document but out of sight once the fit hides them', async () => {
		measure({ 'context-bar': { client: 500, scroll: stagedContent } });
		await render();
		for (const label of Array.from(bar().querySelectorAll<HTMLElement>('[data-context-label]'))) {
			expect(label.classList.contains('sr-only')).toBe(true);
			expect(label.classList.contains('ctx-label')).toBe(false);
		}
	});

	it('changes mode from the Mode control list', async () => {
		const { onModeChange } = await render();
		(target.querySelector('[data-testid="preset-header-mode"] button') as HTMLElement).click();
		await tick();
		flushSync();
		const options = Array.from(document.body.querySelectorAll<HTMLElement>('[role="option"]'));
		options.find((o) => o.textContent?.includes('Edit'))!.click();
		expect(onModeChange).toHaveBeenCalledWith('edit');
	});

	it('shows Variant only when the mode has variants and picks one', async () => {
		const { onVariantChange } = await render({ availableModes: VARIANT_MODES });
		expect(labels()).toEqual(['Preset', 'Mode', 'Variant', 'Session']);
		const variant = target.querySelector('[data-testid="preset-variant-switch"] button') as HTMLElement;
		expect(nameOf(variant)).toBe('Variant Image');
		variant.click();
		await tick();
		flushSync();
		Array.from(document.body.querySelectorAll<HTMLElement>('[role="option"]'))
			.find((o) => o.textContent?.includes('Video'))!
			.click();
		expect(onVariantChange).toHaveBeenCalledWith('video');
	});

	it('shows only a preset call to action before a preset is chosen', async () => {
		tabsStore.updateTab(tabId, { selectedPreset: null, selectedMode: null });
		await render({
			availableModes: [],
			session: sessionState({ sessionControlsEnabled: false, currentSession: null, selectedSessionId: '' })
		});
		const preset = target.querySelector('[data-testid="preset-picker-trigger"]') as HTMLElement;
		expect(nameOf(preset)).toBe('Preset Choose a preset');
		expect(preset.getAttribute('aria-haspopup')).toBe('dialog');
		expect(preset.classList.contains('preset-picker-cta')).toBe(true);
		expect(target.querySelector('[data-testid="session-trigger"]')).toBeNull();
	});
});

describe('ContextToolbar session Save', () => {
	it('is always present at the same width and only enabled when there is something to save', async () => {
		const widths: string[] = [];
		const states = [
			{ overrides: {}, disabled: 'true', label: 'Session saved' },
			{ overrides: { hasUnsavedChanges: true }, disabled: null, label: 'Save session' },
			{ overrides: { currentSession: null, selectedSessionId: '' }, disabled: null, label: 'Save as a new session' },
			{ overrides: { isQuickSaving: true, hasUnsavedChanges: true }, disabled: null, label: 'Saving session' },
			{ overrides: { sessionControlsEnabled: false, currentSession: null }, disabled: 'true', label: 'Session save unavailable' }
		];
		for (const state of states) {
			const { actions } = await render({ session: sessionState(state.overrides) });
			const save = saveButton();
			expect(save, state.label).not.toBeNull();
			expect(save.getAttribute('aria-label')).toBe(state.label);
			expect(save.getAttribute('aria-disabled')).toBe(state.disabled);
			expect(target.querySelector('[data-testid="context-session"]')!.contains(save)).toBe(true);
			widths.push(save.className.split(/\s+/).filter((token) => token.startsWith('w-')).join(' '));
			save.click();
			const called = actions.save.mock.calls.length + actions.saveAs.mock.calls.length;
			expect(called, state.label).toBe(state.disabled ? 0 : 1);
			stopTabSync?.();
			stopTabSync = null;
			unmount(component!);
			component = null;
			target.remove();
		}
		expect(new Set(widths)).toEqual(new Set(['w-[78px]']));
	});
});

describe('ContextToolbar tools', () => {
	it('carries the six real tools: three on the bar, the window toggles in More', async () => {
		await render();
		expect(toolOrder()).toEqual(['formulas', 'fields', 'layout', 'more']);
		expect(target.querySelector('[data-testid="formulas-count"]')?.textContent).toBe('3');
		const items = await openMore();
		expect(items.map((item) => item.getAttribute('aria-label'))).toEqual(['Float form', 'Float workbench', 'Fold form']);
		expect(items.map((item) => item.getAttribute('role'))).toEqual(['menuitemcheckbox', 'menuitemcheckbox', 'menuitemcheckbox']);
		expect(items.map((item) => item.getAttribute('aria-checked'))).toEqual(['false', 'false', 'false']);
	});

	it('shows each tool shortcut in the More menu as a keyboard key, styled like the tooltips', async () => {
		const keybindings = [
			['toggle_floating_form', 'q'],
			['toggle_floating_workbench', 'w'],
			['toggle_left_panel', 'f']
		].map(([action_id, key]) => ({ action_id, key, modifiers: '', label: action_id, category: 'general', context: 'generate', enabled: true, is_custom: false }));
		vi.spyOn(api, 'getKeybindings').mockResolvedValue({ success: true, data: { keybindings } } as never);
		await keybindingsStore.loadBindings();
		onTestFinished(() => keybindingsStore.reset());
		const host = document.body.appendChild(document.createElement('div'));
		const reference = mount(Kbd, { target: host, props: { keys: 'Q' } });
		const tooltipKey = host.querySelector('kbd')!.className;
		unmount(reference);
		host.remove();
		await render();
		const items = await openMore();
		for (const [name, key] of [['Float form', 'Q'], ['Float workbench', 'W'], ['Fold form', 'F']]) {
			const item = items.find((entry) => entry.getAttribute('aria-label') === name)!;
			const keys = item.querySelectorAll('kbd');
			expect(keys, name).toHaveLength(1);
			expect(keys[0].textContent!.trim(), name).toBe(key);
			expect(keys[0].className, name).toBe(tooltipKey);
			expect(keys[0].className, name).toContain('font-mono');
			expect(keys[0].className, name).toContain('tabular-nums');
			const withoutKey = item.cloneNode(true) as HTMLElement;
			withoutKey.querySelector('kbd')!.parentElement!.remove();
			expect(withoutKey.textContent!.trim(), name).toBe(item.querySelector('.truncate')!.textContent!.trim());
		}
	});

	it('toggles field visibility and the pane layout', async () => {
		await render();
		const fields = target.querySelector('[data-context-tool="fields"]') as HTMLElement;
		expect(fields.getAttribute('aria-label')).toBe('Field visibility: Simple');
		expect(fields.getAttribute('aria-pressed')).toBe('false');
		fields.click();
		flushSync();
		expect(get(formAudienceStore)).toBe('advanced');
		expect(fields.getAttribute('aria-label')).toBe('Field visibility: Advanced');
		expect(fields.getAttribute('aria-pressed')).toBe('true');
		(target.querySelector('[data-context-tool="layout"]') as HTMLElement).click();
		expect(tab().layoutMode).toBe('three');
	});

	it('floats the form, floats the workbench and folds the form from More', async () => {
		await render();
		(await openMore()).find((item) => item.getAttribute('aria-label') === 'Float form')!.click();
		expect(tab().formFloating).toBe(true);
		(await openMore()).find((item) => item.getAttribute('aria-label') === 'Float workbench')!.click();
		expect(tab().workbenchFloating).toBe(true);
		expect(tab().formFloating).toBe(false);
		(await openMore()).find((item) => item.getAttribute('aria-label') === 'Fold form')!.click();
		expect(tab().leftPanelCollapsed).toBe(true);
	});

	it('folds the lowest priority tools into More tools and keeps Formulas', async () => {
		measure({ 'context-bar': { client: 1000, scroll: (el) => barContent(el) + 300 } });
		await render();
		expect(toolOrder()).toEqual(['formulas', 'fields', 'more']);
		const items = await openMore();
		expect(items.map((item) => item.getAttribute('aria-label'))).toEqual(['Layout: Two panes', 'Float form', 'Float workbench', 'Fold form']);
		items[0].click();
		expect(tab().layoutMode).toBe('three');
	});

	it('folds tools when the context zone spills over even though the bar itself fits', async () => {
		measure({
			'context-bar': { client: 1200, scroll: () => 1200 },
			'context-zone': { client: 500, scroll: (zone) => 440 + 40 * count(zone.parentElement!, '[data-context-tool]') }
		});
		await render();
		expect(toolOrder()).toEqual(['formulas', 'fields', 'more']);
	});

	it('folds every tool but Formulas when there is no room and marks More while a folded tool is active', async () => {
		measure({ 'context-bar': { client: 1000, scroll: (el) => barContent(el) + 460 } });
		await render();
		expect(toolOrder()).toEqual(['formulas', 'more']);
		expect(target.querySelector('[data-testid="context-tools-more-active"]')).toBeNull();
		formAudienceStore.set('advanced');
		flushSync();
		await settle(8);
		flushSync();
		expect(toolOrder()).toEqual(['formulas', 'more']);
		expect(target.querySelector('[data-testid="context-tools-more-active"]')).not.toBeNull();
	});

	it('gives the tool slots to the other tools when the preset declares no formulas', async () => {
		declaration.current = null;
		await render();
		expect(toolOrder()).toEqual(['fields', 'layout', 'float-form', 'more']);
		expect(target.querySelector('[data-testid="formulas-button"]')).toBeNull();
	});
});

describe('ContextToolbar below 1000px', () => {
	it('folds every tool but Formulas when the bar is narrower than 944px, even with room to spare', async () => {
		measure({ 'context-bar': { client: 900, scroll: () => 600 } });
		await render();
		expect(toolOrder()).toEqual(['formulas', 'more']);
		expect(bar().querySelectorAll('[data-context-label].sr-only').length).toBe(0);
	});

	it('then caps preset and mode at 160px first, giving up only what the bar needs', async () => {
		measure({ 'context-bar': { client: 650, scroll: stagedContent } });
		await render();
		expect(toolOrder()).toEqual(['formulas', 'more']);
		expect(target.querySelector('[data-testid="preset-picker-trigger"]')!.classList.contains('max-w-[160px]')).toBe(true);
		expect(target.querySelector('[data-testid="preset-header-mode"] button')!.classList.contains('max-w-[160px]')).toBe(true);
		expect(bar().querySelectorAll('[data-context-label].sr-only').length).toBe(0);
	});

	it('then truncates preset and mode to their minimum before hiding the labels', async () => {
		measure({ 'context-bar': { client: 600, scroll: stagedContent } });
		await render();
		expect(toolOrder()).toEqual(['formulas', 'more']);
		expect(target.querySelector('[data-testid="preset-picker-trigger"]')!.classList.contains('max-w-[96px]')).toBe(true);
		expect(target.querySelector('[data-testid="preset-header-mode"] button')!.classList.contains('max-w-[96px]')).toBe(true);
		expect(target.querySelector('[data-testid="preset-header-mode"]')!.classList.contains('flex-shrink-0')).toBe(true);
		expect(bar().querySelectorAll('[data-context-label].sr-only').length).toBe(0);
	});

	it('hides the inline labels last and keeps every control named', async () => {
		measure({ 'context-bar': { client: 500, scroll: stagedContent } });
		await render();
		expect(Array.from(bar().querySelectorAll('[data-context-label]')).every((label) => label.classList.contains('sr-only'))).toBe(true);
		expect(nameOf(target.querySelector('[data-testid="preset-picker-trigger"]')!)).toBe('Preset Krea-2');
		expect(nameOf(target.querySelector('[data-testid="preset-header-mode"] button')!)).toBe('Mode Text to Image');
		expect(nameOf(target.querySelector('[data-testid="session-trigger"]')!)).toBe('Session Neon alley');
	});

	it('opens the Mode menu as the preset-style panel with descriptions and a checked selected row', async () => {
		await render();
		(target.querySelector('[data-testid="preset-header-mode"] button[aria-haspopup="listbox"]') as HTMLElement).click();
		await tick();
		flushSync();
		expect(document.body.querySelector('[role="dialog"][aria-label="Choose mode"]')).not.toBeNull();
		const [current, other] = Array.from(document.body.querySelectorAll<HTMLElement>('[role="option"]'));
		expect(current.getAttribute('aria-selected')).toBe('true');
		expect(current.className).toContain('is-current');
		expect(current.textContent).toContain('Generate an image from a text prompt.');
		expect(current.querySelector('svg.text-signal')).not.toBeNull();
		expect(other.className).not.toContain('is-current');
	});
});
