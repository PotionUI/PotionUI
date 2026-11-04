import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import { filterCatalog } from '$lib/filters/catalog';
import type { FilterItem } from '$lib/filters/types';
import { emptyImageState } from '$lib/tool-tabs/image/state';
import type { ToolTabActions, ToolTabHost } from '$lib/tool-tabs/types';
import type { MediaRef } from '$lib/types/tabs';

const { default: ImageEditorTab } = await import('$lib/components/tool-tabs/image/ImageEditorTab.svelte');

const SOURCE: MediaRef = {
	path: 'outputs/2026-11-01/gen-1/observatory-dusk.png',
	relative_path: 'outputs/2026-11-01/gen-1/observatory-dusk.png',
	url: '/api/media/generations/gen-1/observatory-dusk.png',
	name: 'observatory-dusk.png',
	type: 'image',
	generation_file_id: '501'
};

function look(id: string, name: string, group: string, intensity = 100): FilterItem {
	return {
		id,
		name,
		group,
		order: 0,
		intensity,
		tags: [],
		source: 'builtin',
		owned: false,
		has_lut: false,
		steps: [{ op: 'tone', contrast: 10 }],
		unavailable_ops: [],
		needs_plugin: null,
		revision: 'r1'
	} as FilterItem;
}

function render(activeTool: string, extra: Partial<ToolTabHost> = {}) {
	const calls = { state: [] as Array<{ stack: { steps: Array<{ params: { values?: Record<string, number> } }>; cursor: number } }>, actions: [] as Array<ToolTabActions | null> };
	const host: ToolTabHost = {
		tabId: 'tab-1',
		tool: 'image',
		source: SOURCE,
		state: null,
		activeTool,
		run: null,
		hint: null,
		phone: false,
		setState: (next) => calls.state.push(next as never),
		setDirty: () => undefined,
		setTitle: () => undefined,
		setThumbnail: () => undefined,
		setSource: () => undefined,
		replaceSource: () => undefined,
		setActiveTool: () => undefined,
		setRun: () => undefined,
		clearHint: () => undefined,
		setActions: (actions) => calls.actions.push(actions),
		openToolTab: () => 'tab-2',
		...extra
	};
	const view = mountComponent(ImageEditorTab, { host });
	const q = <T extends Element = HTMLElement>(selector: string) => view.target.querySelector<T>(selector);
	const all = (selector: string) => Array.from(view.target.querySelectorAll<HTMLElement>(selector));
	const rows = () => all('[data-edit-stack] [data-stack-row]').map((row) => row.dataset.status);
	const written = () => calls.state[calls.state.length - 1]?.stack;
	return { view, calls, q, all, rows, written };
}

function drag(view: ReturnType<typeof render>, label: string, value: number) {
	const range = view.q<HTMLInputElement>(`[data-property-slider="${label}"] input[type="range"]`)!;
	range.value = String(value);
	range.dispatchEvent(new Event('input', { bubbles: true }));
	flushSync();
}

function release(view: ReturnType<typeof render>, label: string) {
	view.q<HTMLInputElement>(`[data-property-slider="${label}"] input[type="range"]`)!.dispatchEvent(new Event('change', { bubbles: true }));
	flushSync();
}

async function settle() {
	for (let i = 0; i < 6; i++) await Promise.resolve();
	flushSync();
}

beforeEach(() => {
	vi.useFakeTimers();
	filterCatalog.set({ status: 'ready', items: [look('reel', 'Reel', 'film', 60), look('matte', 'Matte', 'film'), look('noir', 'Noir', 'mono')], ops: [], groups: ['film', 'mono'], error: null });
});

afterEach(() => {
	vi.useRealTimers();
	document.body.innerHTML = '';
});

describe('Adjust tool', () => {
	it('shows every Light, Colour and Effects slider at once with no Apply', () => {
		const view = render('adjust', { state: emptyImageState('adjust') as never });
		expect(view.all('[data-adjust-section]').map((section) => section.dataset.adjustSection)).toEqual(['light', 'colour', 'effects']);
		expect(view.all('[data-adjust-options] input[type="range"]').length).toBeGreaterThanOrEqual(20);
		expect(view.q('[data-property-slider="Exposure"]')).not.toBeNull();
		expect(view.q('[data-property-slider="Temperature"]')).not.toBeNull();
		expect(view.q('[data-property-slider="Grain"]')).toBeNull();
		expect(view.q('[data-adjust-caption="grain"]')).not.toBeNull();
		expect(view.all('[data-options-apply]')).toHaveLength(0);
		expect(view.q('[data-options-panel] h2')?.firstChild?.textContent).toBe('Adjust');
	});

	it('writes a slider move live into one step, with no pending edit', () => {
		const view = render('adjust');
		expect(view.rows()).toEqual(['current']);
		drag(view, 'Contrast', 18);
		expect(view.rows()).toEqual(['original', 'current']);
		expect(view.all('[data-stack-row]').at(-1)?.textContent).toContain('Tone');
		drag(view, 'Contrast', 30);
		drag(view, 'Brightness', -5);
		expect(view.rows()).toEqual(['original', 'current']);
		expect(view.written().steps).toHaveLength(1);
		expect(view.written().steps[0].params.values).toMatchObject({ contrast: 30, brightness: -5 });
	});

	it('keeps one step per op across sliders', () => {
		const view = render('adjust');
		drag(view, 'Contrast', 18);
		drag(view, 'Exposure', 0.5);
		drag(view, 'Saturation', -6);
		drag(view, 'Exposure', 0.9);
		expect(view.rows()).toEqual(['original', 'applied', 'current']);
		expect(view.written().steps).toHaveLength(2);
	});

	it('undoes a whole drag in one step and a later drag separately', async () => {
		const view = render('adjust');
		drag(view, 'Contrast', 10);
		release(view, 'Contrast');
		drag(view, 'Contrast', 20);
		drag(view, 'Contrast', 25);
		drag(view, 'Contrast', 30);
		release(view, 'Contrast');
		view.q<HTMLButtonElement>('[data-frame-undo]')!.click();
		await settle();
		expect(view.written().steps[0].params.values?.contrast).toBe(10);
		expect(view.rows()).toEqual(['original', 'current']);
		view.q<HTMLButtonElement>('[data-frame-undo]')!.click();
		await settle();
		expect(view.written().cursor).toBe(0);
		expect(view.rows()[0]).toBe('current');
	});

	it('commits a drag by itself once the sliders go quiet', () => {
		const view = render('adjust');
		drag(view, 'Contrast', 10);
		release(view, 'Contrast');
		drag(view, 'Contrast', 40);
		vi.advanceTimersByTime(700);
		flushSync();
		view.q<HTMLButtonElement>('[data-frame-undo]')!.click();
		flushSync();
		expect(view.written().steps[0].params.values?.contrast).toBe(10);
	});

	it('drops the step again when a slider returns to its default in the same drag', () => {
		const view = render('adjust');
		drag(view, 'Contrast', 18);
		drag(view, 'Contrast', 0);
		expect(view.rows()).toEqual(['current']);
	});

	it('resets everything from the bar, as an undoable edit, and shows a dot for edited sections', () => {
		const view = render('adjust');
		expect(view.q<HTMLButtonElement>('[data-options-reset]')!.disabled).toBe(true);
		drag(view, 'Contrast', 18);
		release(view, 'Contrast');
		expect(view.q('[data-adjust-section="light"] [data-adjust-dot]')).not.toBeNull();
		expect(view.q('[data-adjust-section="effects"] [data-adjust-dot]')).toBeNull();
		view.q<HTMLButtonElement>('[data-options-reset]')!.click();
		flushSync();
		expect(view.written().steps[0].params.values?.contrast).toBe(0);
		expect(view.q('[data-adjust-section="light"] [data-adjust-dot]')).toBeNull();
	});

	it('enables Before / after once something is adjusted', () => {
		const view = render('adjust');
		expect(view.q<HTMLButtonElement>('[data-frame-compare]')!.disabled).toBe(true);
		drag(view, 'Contrast', 18);
		expect(view.q<HTMLButtonElement>('[data-frame-compare]')!.disabled).toBe(false);
	});

	it('toggles a no-parameter adjustment on and off', () => {
		const view = render('adjust');
		const toggle = () => view.q<HTMLElement>('[data-adjust-section="effects"] [role="switch"][aria-label="Grayscale"]')!;
		toggle().click();
		flushSync();
		expect(view.rows()).toEqual(['original', 'current']);
		toggle().click();
		flushSync();
		expect(view.written().steps).toHaveLength(1);
	});

	it('opens Adjust from an adjustment row in the edits instead of reopening it as pending', async () => {
		const view = render('crop');
		view.q<HTMLButtonElement>('[data-tool-rail] button[data-tool="adjust"]')!.click();
		await settle();
		drag(view, 'Contrast', 18);
		release(view, 'Contrast');
		view.q<HTMLButtonElement>('[data-tool-rail] button[data-tool="crop"]')!.click();
		await settle();
		const row = view.all('[data-stack-row]').at(-1)!;
		row.querySelector<HTMLButtonElement>('button')!.click();
		await settle();
		expect(view.q('[data-image-editor]')?.dataset.tool).toBe('adjust');
		expect(view.rows()).toEqual(['original', 'current']);
	});
});

describe('Looks tray', () => {
	function hover(view: ReturnType<typeof render>, id: string) {
		view.q(`[data-tray-item="${id}"]`)!.dispatchEvent(new PointerEvent('pointerenter', { pointerType: 'mouse' }));
		vi.advanceTimersByTime(200);
		flushSync();
	}

	it('lists the looks as a tray under the canvas, grouped, with Original first', () => {
		const view = render('effects');
		expect(view.all('[data-tray-item]').map((tile) => tile.dataset.trayItem)).toEqual(['__original', 'matte', 'reel', 'noir']);
		expect(view.all('[data-tray-group]').map((group) => group.dataset.trayGroup)).toEqual(['Original', 'film', 'mono']);
	});

	it('previews a hovered look on the canvas without touching the stack, and says so', () => {
		const view = render('effects');
		hover(view, 'matte');
		const chip = view.q('[data-preview-chip]');
		expect(chip?.textContent).toContain('Previewing');
		expect(chip?.textContent).toContain('Matte');
		expect(chip?.textContent).toContain('click to apply');
		expect(view.rows()).toEqual(['current']);
		view.q('[data-tray]')!.dispatchEvent(new PointerEvent('pointerleave', { pointerType: 'mouse' }));
		vi.advanceTimersByTime(100);
		flushSync();
		expect(view.q('[data-preview-chip]')).toBeNull();
		expect(view.rows()).toEqual(['current']);
	});

	it('a click selects the look as the pending step with its intensity in the bar, Apply commits it', async () => {
		const view = render('effects');
		hover(view, 'reel');
		view.q<HTMLButtonElement>('[data-tray-item="reel"]')!.click();
		flushSync();
		expect(view.q('[data-preview-chip]')).toBeNull();
		expect(view.rows()).toEqual(['original', 'pending']);
		expect(view.q('[data-tray-item="reel"]')!.getAttribute('aria-selected')).toBe('true');
		expect(view.q('[data-bar-look]')?.textContent).toBe('Reel');
		expect(view.q('[data-options-bar] [data-property-slider="Intensity"] [data-property-value]')?.textContent).toBe('60');
		expect(view.q('[data-options-apply]')?.textContent).toContain('Apply look');
		view.q<HTMLButtonElement>('[data-options-apply]')!.click();
		await settle();
		expect(view.rows()).toEqual(['original', 'current']);
	});

	it('moves the in-bar intensity into the pending step', () => {
		const view = render('effects');
		view.q<HTMLButtonElement>('[data-tray-item="reel"]')!.click();
		flushSync();
		const range = view.q<HTMLInputElement>('[data-options-bar] [data-property-slider="Intensity"] input')!;
		range.value = '35';
		range.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(view.written().steps).toHaveLength(0);
		expect(view.q('[data-edit-stack] [data-stack-row][data-status="pending"]')?.textContent).toContain('Intensity 35');
	});

	it('does not preview the look that is already selected', () => {
		const view = render('effects');
		view.q<HTMLButtonElement>('[data-tray-item="reel"]')!.click();
		flushSync();
		hover(view, 'reel');
		expect(view.q('[data-preview-chip]')).toBeNull();
	});

	it('Original drops the pending look', () => {
		const view = render('effects');
		view.q<HTMLButtonElement>('[data-tray-item="reel"]')!.click();
		flushSync();
		view.q<HTMLButtonElement>('[data-tray-item="__original"]')!.click();
		flushSync();
		expect(view.rows()).toEqual(['current']);
	});

	it('filters the tray with the search in the properties', () => {
		const view = render('effects');
		const search = view.q<HTMLInputElement>('input[type="search"]')!;
		search.value = 'noi';
		search.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(view.all('[data-tray-item]').map((tile) => tile.dataset.trayItem)).toEqual(['__original', 'noir']);
	});

	it('has the tray only for Looks', () => {
		const view = render('crop');
		expect(view.q('[data-tray]')).toBeNull();
	});
});

describe('phone layout', () => {
	it('puts the tray above a one-line bar for Looks, with the tool strip at the bottom', () => {
		const view = render('effects', { phone: true });
		const tray = view.q('[data-tray]')!;
		const bar = view.q('[data-options-bar="phone"]')!;
		expect(view.q('[data-tool-rail="strip"]')).not.toBeNull();
		expect(tray.compareDocumentPosition(bar) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		expect(bar.querySelector('[data-options-apply]')?.textContent?.trim()).toBe('Apply');
		view.q<HTMLButtonElement>('[data-tray-item="reel"]')!.click();
		flushSync();
		expect(view.q('[data-options-bar="phone"] [data-bar-look]')?.textContent).toBe('Reel');
		expect(view.q('[data-options-bar="phone"] [data-property-slider="Intensity"]')).not.toBeNull();
		expect(view.q<HTMLButtonElement>('[data-options-bar="phone"] [data-options-apply]')?.disabled).toBe(false);
	});

	it('has no bar and no Apply for Adjust, only section tabs over the sliders', () => {
		const view = render('adjust', { phone: true });
		expect(view.q('[data-options-bar="phone"]')).toBeNull();
		expect(view.all('[data-options-apply]')).toHaveLength(0);
		expect(view.all('[data-adjust-section]').map((section) => section.dataset.adjustSection)).toEqual(['light']);
		view.q<HTMLButtonElement>('[aria-label="Adjustment section"] button:nth-child(2)')!.click();
		flushSync();
		expect(view.all('[data-adjust-section]').map((section) => section.dataset.adjustSection)).toEqual(['colour']);
	});

	it('keeps the other tools on the options panel with Apply', () => {
		const view = render('crop', { phone: true });
		expect(view.q('[data-options-bar="phone"]')).toBeNull();
		expect(view.q('[data-options-panel="phone"] [data-options-apply]')).not.toBeNull();
	});
});

describe('options bar', () => {
	it('replaces the file name with the tool name and the live output size for Crop', () => {
		const view = render('crop');
		expect(view.q('[data-options-bar] [data-bar-title]')?.textContent).toContain('Crop');
		expect(view.q('[data-options-panel] h2')?.firstChild?.textContent).toBe('Crop');
		expect(view.q('[data-frame-file]')?.className).toContain('sr-only');
	});

	it('keeps exactly one header bar above the canvas', () => {
		const view = render('crop');
		expect(view.all('[data-editor-toolbar]')).toHaveLength(1);
		expect(view.q('[data-tool-rail]')?.getAttribute('data-tool-rail')).toBe('icons');
	});
});
