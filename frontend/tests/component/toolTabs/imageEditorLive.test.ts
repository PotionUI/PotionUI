import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import { isRowDisabled, openEditorMenu, pickEditorMenuItem } from '../helpers/editorMenu';
import { filterCatalog } from '$lib/filters/catalog';
import type { FilterItem } from '$lib/filters/types';
import { emptyImageState } from '$lib/tool-tabs/image/state';
import { resetMediaToolCatalog } from '$lib/media-tools/catalog';
import { effectTools } from '$lib/effects/catalog';
import { EFFECT_CATALOG } from '../fixtures/effectCatalog';
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

async function settle(rounds = 6) {
	for (let i = 0; i < rounds; i++) await Promise.resolve();
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

describe('Light and Colour entries', () => {
	it('shows only the Light controls in the Light panel, with Reset and no Apply', () => {
		const view = render('light', { state: emptyImageState('light') as never });
		expect(view.all('[data-entry-options="light"] [data-property-slider]').map((slider) => slider.dataset.propertySlider)).toEqual(['Exposure', 'Brightness', 'Contrast', 'Black lift', 'White cap']);
		expect(view.q('[data-property-slider="Temperature"]')).toBeNull();
		expect(view.q('[data-property-slider="Saturation"]')).toBeNull();
		expect(view.all('[data-options-apply]')).toHaveLength(0);
		expect(view.q('[data-options-reset]')).not.toBeNull();
		expect(view.q('[data-title-name]')?.textContent).toBe('Light');
	});

	it('shows only the Colour controls in the Colour panel', () => {
		const view = render('colour');
		expect(view.all('[data-entry-options="colour"] [data-property-slider]').map((slider) => slider.dataset.propertySlider)).toEqual(['Temperature', 'Tint', 'Saturation', 'Hue', 'Vibrance']);
	});

	it('writes a slider move live into one step named Light, with no pending edit', () => {
		const view = render('light');
		expect(view.rows()).toEqual(['current']);
		drag(view, 'Contrast', 18);
		expect(view.rows()).toEqual(['original', 'current']);
		expect(view.all('[data-stack-row]').at(-1)?.textContent).toContain('Light');
		drag(view, 'Contrast', 30);
		drag(view, 'Brightness', -5);
		expect(view.rows()).toEqual(['original', 'current']);
		expect(view.written().steps).toHaveLength(1);
		expect(view.written().steps[0].params.values).toMatchObject({ 'tone.contrast': 30, 'tone.brightness': -5 });
	});

	it('keeps one step per entry across sliders and never mixes Light with Colour', async () => {
		const view = render('light');
		drag(view, 'Contrast', 18);
		drag(view, 'Exposure', 0.5);
		release(view, 'Exposure');
		view.q<HTMLButtonElement>('[data-tool-rail] button[data-tool="colour"]')!.click();
		await settle();
		drag(view, 'Saturation', -6);
		drag(view, 'Hue', 10);
		release(view, 'Hue');
		expect(view.rows()).toEqual(['original', 'applied', 'current']);
		const [light, colour] = view.written().steps as Array<{ label: string; params: { values: Record<string, number> } }>;
		expect(light.label).toBe('Light');
		expect(colour.label).toBe('Colour');
		expect(light.params.values['tone.saturation']).toBeUndefined();
		expect(colour.params.values['tone.contrast']).toBeUndefined();
		expect(colour.params.values).toMatchObject({ 'tone.saturation': -6, 'tone.hue': 10 });
	});

	it('undoes a whole drag in one step and a later drag separately', async () => {
		const view = render('light');
		drag(view, 'Contrast', 10);
		release(view, 'Contrast');
		drag(view, 'Contrast', 20);
		drag(view, 'Contrast', 25);
		drag(view, 'Contrast', 30);
		release(view, 'Contrast');
		view.q<HTMLButtonElement>('[data-frame-undo]')!.click();
		await settle();
		expect(view.written().steps[0].params.values?.['tone.contrast']).toBe(10);
		expect(view.rows()).toEqual(['original', 'current']);
		view.q<HTMLButtonElement>('[data-frame-undo]')!.click();
		await settle();
		expect(view.written().cursor).toBe(0);
		expect(view.rows()[0]).toBe('current');
	});

	it('commits a drag by itself once the sliders go quiet', () => {
		const view = render('light');
		drag(view, 'Contrast', 10);
		release(view, 'Contrast');
		drag(view, 'Contrast', 40);
		vi.advanceTimersByTime(700);
		flushSync();
		view.q<HTMLButtonElement>('[data-frame-undo]')!.click();
		flushSync();
		expect(view.written().steps[0].params.values?.['tone.contrast']).toBe(10);
	});

	it('drops the step again when a slider returns to its default in the same drag', () => {
		const view = render('light');
		drag(view, 'Contrast', 18);
		drag(view, 'Contrast', 0);
		expect(view.rows()).toEqual(['current']);
	});

	it('resets the entry from the panel footer and the Edit menu as an undoable edit, with a dot on the rail icon', async () => {
		const view = render('light');
		await openEditorMenu(view.view.target, 'edit');
		expect(isRowDisabled('edit.reset')).toBe(true);
		document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		await settle();
		drag(view, 'Contrast', 18);
		release(view, 'Contrast');
		expect(view.q('[data-tool-rail] button[data-tool="light"] [data-tool-applied]')).not.toBeNull();
		expect(view.q('[data-tool-rail] button[data-tool="colour"] [data-tool-applied]')).toBeNull();
		await pickEditorMenuItem(view.view.target, 'edit', 'edit.reset');
		expect(view.written().steps[0].params.values?.['tone.contrast']).toBe(0);
		expect(view.q('[data-tool-rail] button[data-tool="light"] [data-tool-applied]')).toBeNull();
	});

	it('enables Before / after in the View menu once something is adjusted', async () => {
		const view = render('light');
		await openEditorMenu(view.view.target, 'view');
		expect(isRowDisabled('view.compare')).toBe(true);
		document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		await settle();
		drag(view, 'Contrast', 18);
		await openEditorMenu(view.view.target, 'view');
		expect(isRowDisabled('view.compare')).toBe(false);
	});

	it('toggles Grayscale on and off with its one switch', async () => {
		const view = render('grayscale');
		await settle(30);
		const toggle = () => view.q<HTMLElement>('[data-entry-options="grayscale"] input[type="checkbox"]')!;
		expect(view.all('[data-entry-options="grayscale"] input[type="checkbox"]')).toHaveLength(1);
		toggle().click();
		await settle(30);
		expect(view.rows()).toEqual(['original', 'current']);
		toggle().click();
		await settle(30);
		expect(view.written().steps).toHaveLength(1);
	});

	it('opens the entry from its row in the edits instead of reopening it as pending', async () => {
		const view = render('crop');
		view.q<HTMLButtonElement>('[data-tool-rail] button[data-tool="light"]')!.click();
		await settle();
		drag(view, 'Contrast', 18);
		release(view, 'Contrast');
		view.q<HTMLButtonElement>('[data-tool-rail] button[data-tool="crop"]')!.click();
		await settle();
		const row = view.all('[data-stack-row]').at(-1)!;
		row.querySelector<HTMLButtonElement>('button')!.click();
		await settle();
		expect(view.q('[data-image-editor]')?.dataset.tool).toBe('light');
		expect(view.rows()).toEqual(['original', 'current']);
	});
});

describe('the other entries show only their own controls', () => {
	const labels = (view: ReturnType<typeof render>, entry: string) => view.all(`[data-entry-options="${entry}"] [data-property-slider]`).map((slider) => slider.dataset.propertySlider);

	it('Split tone has its five sliders and nothing from Colour', () => {
		const view = render('split_tone');
		expect(labels(view, 'split_tone')).toEqual(['Shadow hue', 'Shadow saturation', 'Highlight hue', 'Highlight saturation', 'Balance']);
	});

	it('Curves has the plot and its three sliders', () => {
		const view = render('curves');
		expect(labels(view, 'curves')).toEqual(['Shadows', 'Midtones', 'Highlights']);
		expect(view.q('[data-entry-options="curves"] svg, [data-entry-options="curves"] canvas')).not.toBeNull();
	});

	it('Invert is one switch', async () => {
		const view = render('invert');
		await settle(30);
		expect(view.all('[data-entry-options="invert"] input[type="checkbox"]')).toHaveLength(1);
		expect(view.all('[data-entry-options="invert"] [data-property-slider]')).toHaveLength(0);
	});

	it('Film grain has the controls of its effect and none of the Adjust sliders', async () => {
		resetMediaToolCatalog(EFFECT_CATALOG);
		try {
			const view = render('film_grain');
			await settle(30);
			const wanted = effectTools(EFFECT_CATALOG).find((entry) => entry.id === 'film_grain')!.params.map((param) => param.label ?? param.name);
			const text = view.q('[data-entry-options="film_grain"]')!.textContent ?? '';
			for (const label of wanted) expect(text).toContain(label);
			expect(view.q('[data-property-slider="Exposure"]')).toBeNull();
			expect(view.q('[data-options-apply]')).toBeNull();
		} finally {
			resetMediaToolCatalog([]);
		}
	});

	it('the Tools menu mirrors the groups as submenus with the entries, the current one marked', async () => {
		const view = render('vignette');
		await openEditorMenu(view.view.target, 'tools');
		const groupIds = Array.from(document.body.querySelectorAll<HTMLElement>('[data-editor-menu] [data-menu-row][data-menu-level="0"]')).map((row) => row.dataset.rowId);
		expect(groupIds).toEqual(['toolgroup:generate', 'toolgroup:shape', 'toolgroup:paint', 'toolgroup:select', 'toolgroup:look', 'toolgroup:fx', 'tool:effects', 'toolgroup:tools']);
		document.body.querySelector<HTMLButtonElement>('[data-row-id="toolgroup:fx"]')!.click();
		await settle();
		const sub = Array.from(document.body.querySelectorAll<HTMLElement>('[data-editor-submenu="toolgroup:fx"] [data-menu-row]'));
		expect(sub.map((row) => row.dataset.rowId)).toEqual(['tool:film_grain', 'tool:vignette', 'tool:grayscale', 'tool:invert']);
		expect(sub.map((row) => row.getAttribute('aria-checked'))).toEqual(['false', 'true', 'false', 'false']);
	});
});

describe('Looks panel', () => {
	function hover(view: ReturnType<typeof render>, id: string) {
		view.q(`[data-look-item="${id}"]`)!.dispatchEvent(new PointerEvent('pointerenter', { pointerType: 'mouse' }));
		vi.advanceTimersByTime(200);
		flushSync();
	}

	it('lists the looks in the panel, grouped, with Original first and no tray under the canvas', () => {
		const view = render('effects');
		expect(view.all('[data-look-item]').map((tile) => tile.dataset.lookItem)).toEqual(['__original', 'matte', 'reel', 'noir']);
		expect(view.all('[data-look-group]').map((group) => group.dataset.lookGroup)).toEqual(['film', 'mono']);
		expect(view.q('[data-options-panel] [data-look-list]')).not.toBeNull();
		expect(view.q('[data-tray]')).toBeNull();
	});

	it('previews a hovered look on the canvas without touching the stack, and says so', () => {
		const view = render('effects');
		hover(view, 'matte');
		const chip = view.q('[data-preview-chip]');
		expect(chip?.textContent).toContain('Previewing');
		expect(chip?.textContent).toContain('Matte');
		expect(chip?.textContent).toContain('click to apply');
		expect(view.rows()).toEqual(['current']);
		view.q('[data-look-list]')!.dispatchEvent(new PointerEvent('pointerleave', { pointerType: 'mouse' }));
		vi.advanceTimersByTime(100);
		flushSync();
		expect(view.q('[data-preview-chip]')).toBeNull();
		expect(view.rows()).toEqual(['current']);
	});

	it('a click selects the look as the pending step with its intensity in the bar, Apply commits it', async () => {
		const view = render('effects');
		hover(view, 'reel');
		view.q<HTMLButtonElement>('[data-look-item="reel"]')!.click();
		flushSync();
		expect(view.q('[data-preview-chip]')).toBeNull();
		expect(view.rows()).toEqual(['original', 'pending']);
		expect(view.q('[data-look-item="reel"]')!.getAttribute('aria-selected')).toBe('true');
		expect(view.q('[data-bar-look]')?.textContent).toBe('Reel');
		expect(view.q('[data-options-panel] [data-property-slider="Intensity"] [data-property-value]')?.textContent).toBe('60');
		expect(view.q('[data-options-apply]')?.textContent).toContain('Apply look');
		view.q<HTMLButtonElement>('[data-options-apply]')!.click();
		await settle();
		expect(view.rows()).toEqual(['original', 'current']);
	});

	it('lists what the chosen look contains, read only', () => {
		const view = render('effects');
		expect(view.q('[data-look-contains]')).toBeNull();
		view.q<HTMLButtonElement>('[data-look-item="reel"]')!.click();
		flushSync();
		expect(view.all('[data-look-contains] span').map((chip) => chip.textContent)).toEqual(['Tone']);
		expect(view.q('[data-look-contains-block]')?.textContent).toContain('Reel contains');
	});

	it('moves the in-bar intensity into the pending step', () => {
		const view = render('effects');
		view.q<HTMLButtonElement>('[data-look-item="reel"]')!.click();
		flushSync();
		const range = view.q<HTMLInputElement>('[data-options-panel] [data-property-slider="Intensity"] input')!;
		range.value = '35';
		range.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(view.written().steps).toHaveLength(0);
		expect(view.q('[data-edit-stack] [data-stack-row][data-status="pending"]')?.textContent).toContain('Intensity 35');
	});

	it('does not preview the look that is already selected', () => {
		const view = render('effects');
		view.q<HTMLButtonElement>('[data-look-item="reel"]')!.click();
		flushSync();
		hover(view, 'reel');
		expect(view.q('[data-preview-chip]')).toBeNull();
	});

	it('Original drops the pending look', () => {
		const view = render('effects');
		view.q<HTMLButtonElement>('[data-look-item="reel"]')!.click();
		flushSync();
		view.q<HTMLButtonElement>('[data-look-item="__original"]')!.click();
		flushSync();
		expect(view.rows()).toEqual(['current']);
	});

	it('filters the list with the search in the properties', () => {
		const view = render('effects');
		const search = view.q<HTMLInputElement>('input[type="search"]')!;
		search.value = 'noi';
		search.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(view.all('[data-look-item]').map((tile) => tile.dataset.lookItem)).toEqual(['__original', 'noir']);
	});

	it('has the list only for Looks', () => {
		const view = render('crop');
		expect(view.q('[data-look-list]')).toBeNull();
	});
});

describe('phone layout', () => {
	it('puts the tray above a one-line bar for Looks, with the tool strip at the bottom', () => {
		const view = render('effects', { phone: true });
		const tray = view.q('[data-look-list-phone]')!;
		const bar = view.q('[data-options-bar="phone"]')!;
		expect(view.q('[data-tool-rail="strip"]')).not.toBeNull();
		expect(tray.compareDocumentPosition(bar) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		expect(bar.querySelector('[data-options-apply]')?.textContent?.trim()).toBe('Apply');
		view.q<HTMLButtonElement>('[data-look-item="reel"]')!.click();
		flushSync();
		expect(view.q('[data-options-bar="phone"] [data-bar-look]')?.textContent).toBe('Reel');
		expect(view.q('[data-options-bar="phone"] [data-property-slider="Intensity"]')).not.toBeNull();
		expect(view.q<HTMLButtonElement>('[data-options-bar="phone"] [data-options-apply]')?.disabled).toBe(false);
	});

	it('has no bar and no Apply for Light, only its sliders', () => {
		const view = render('light', { phone: true });
		expect(view.q('[data-options-bar="phone"]')).toBeNull();
		expect(view.all('[data-options-apply]')).toHaveLength(0);
		expect(view.all('[data-entry-options="light"] [data-property-slider]')).toHaveLength(5);
	});

	it('keeps the other tools on the options panel with Apply', () => {
		const view = render('crop', { phone: true });
		expect(view.q('[data-options-bar="phone"]')).toBeNull();
		expect(view.q('[data-options-panel="phone"] [data-options-apply]')).not.toBeNull();
	});
});

describe('options bar', () => {
	it('names the tool in the title block, not in a panel row, and keeps the file name for readers only', () => {
		const view = render('crop');
		expect(view.q('[data-title-block] [data-title-name]')?.textContent).toBe('Crop');
		expect(view.q('[data-options-panel] h2')).toBeNull();
		expect(view.q('[data-frame-file]')?.className).toContain('sr-only');
	});

	it('keeps exactly one header bar above the canvas', () => {
		const view = render('crop');
		expect(view.all('[data-editor-toolbar]')).toHaveLength(1);
		expect(view.q('[data-tool-rail]')?.getAttribute('data-tool-rail')).toBe('icons');
	});
});

describe('Paint entries in the editor tab', () => {
	const press = (key: string, init: KeyboardEventInit = {}) => {
		window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init }));
		flushSync();
	};

	it('opens an old saved Paint tool on the Brush entry', async () => {
		const view = render('paint');
		await settle();
		expect(view.q('[data-image-editor]')?.getAttribute('data-tool')).toBe('paint.brush');
		expect(view.q('[data-tool-rail] button[data-tool="paint.brush"]')?.getAttribute('aria-pressed')).toBe('true');
	});

	it('selects the paint entries with the tool letters', async () => {
		const view = render('light');
		for (const [key, entry] of [['b', 'brush'], ['e', 'eraser'], ['g', 'fill'], ['i', 'pick'], ['m', 'rect'], ['l', 'lasso'], ['v', 'move']]) {
			press(key);
			await settle();
			expect(view.q('[data-image-editor]')?.getAttribute('data-tool')).toBe(`paint.${entry}`);
		}
	});

	it('ignores the letters with a modifier and while typing in a field', async () => {
		const view = render('light');
		press('e', { ctrlKey: true });
		await settle();
		expect(view.q('[data-image-editor]')?.getAttribute('data-tool')).toBe('light');
		const input = document.body.appendChild(document.createElement('input'));
		input.focus();
		input.dispatchEvent(new KeyboardEvent('keydown', { key: 'e', bubbles: true, composed: true }));
		await settle();
		expect(view.q('[data-image-editor]')?.getAttribute('data-tool')).toBe('light');
	});

	it('shows the paint entries as two rail groups', () => {
		const view = render('light');
		const labels = (id: string) => view.all(`[data-tool-rail] button[data-tool^="paint."]`).filter(() => id).map((button) => button.dataset.tool);
		expect(labels('x')).toEqual(['paint.brush', 'paint.eraser', 'paint.fill', 'paint.pick', 'paint.rect', 'paint.lasso', 'paint.move']);
	});
});

describe('the tool letter handler stays out of the way', () => {
	const toolOf = (view: ReturnType<typeof render>) => view.q('[data-image-editor]')?.getAttribute('data-tool');
	const fire = (target: Element | Document | Window, key = 'e') => {
		const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, composed: true });
		target.dispatchEvent(event);
		flushSync();
		return event;
	};

	it.each([
		['an input', () => document.createElement('input')],
		['a textarea', () => document.createElement('textarea')],
		['a select', () => document.createElement('select')],
		['a contenteditable', () => {
			const node = document.createElement('div');
			node.setAttribute('contenteditable', 'true');
			return node;
		}],
		['a textbox role (the floating prompt)', () => {
			const node = document.createElement('div');
			node.setAttribute('role', 'textbox');
			return node;
		}]
	])('does nothing while focus is in %s', async (_name, make) => {
		const view = render('light');
		const node = document.body.appendChild(make());
		const event = fire(node);
		await settle();
		expect(toolOf(view)).toBe('light');
		expect(event.defaultPrevented).toBe(false);
	});

	it('does nothing while a menu, popover or modal holds a dismiss layer', async () => {
		const { registerDismissLayer, unregisterDismissLayer } = await import('$lib/actions/layerStack');
		const view = render('light');
		const layer = { node: document.body.appendChild(document.createElement('div')) } as never;
		registerDismissLayer(layer);
		try {
			const event = fire(document.body);
			await settle();
			expect(toolOf(view)).toBe('light');
			expect(event.defaultPrevented).toBe(false);
		} finally {
			unregisterDismissLayer(layer);
		}
	});

	it('takes E before the global handler, and leaves E to the global handler once the editor is gone', async () => {
		const global = vi.fn((event: KeyboardEvent) => event.preventDefault());
		document.addEventListener('keydown', global);
		try {
			const view = render('light');
			fire(document.body);
			await settle();
			expect(toolOf(view)).toBe('paint.eraser');
			expect(global).not.toHaveBeenCalled();
			view.view.destroy();
			fire(document.body);
			expect(global).toHaveBeenCalledTimes(1);
		} finally {
			document.removeEventListener('keydown', global);
		}
	});
});
