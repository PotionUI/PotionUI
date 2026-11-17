import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { installFakeCanvas } from '../helpers/fakeCanvas';
import { mountComponent } from '../helpers/mount';
import { resetRecentColours } from '$lib/paint/recentColours';

vi.mock('$lib/services/api/index', () => ({
	api: { listFilters: async () => ({ filters: [], ops: [], groups: [] }) }
}));

const { default: PaintPanel } = await import('$lib/components/paint/PaintPanel.svelte');
const { default: PaintOptions } = await import('$lib/components/tool-tabs/image/PaintOptions.svelte');
const { default: ColourControl } = await import('$lib/components/paint/ColourControl.svelte');
const { default: ToolForm } = await import('$lib/media-tools/ToolForm.svelte');
const { PaintSession } = await import('$lib/paint/session');

let restore: () => void;
let session: InstanceType<typeof PaintSession>;

function mountPanel(tool: string, component: unknown = PaintPanel, extra: Record<string, unknown> = {}) {
	session.setTool(tool);
	const view = mountComponent(component as never, { session, state: session.snapshot(), ...extra });
	session.subscribe(() => view.rerender({ state: session.snapshot() }));
	flushSync();
	return view;
}

const q = (selector: string) => document.body.querySelector<HTMLElement>(selector);
const sliders = () => Array.from(document.body.querySelectorAll<HTMLElement>('[data-paint-tool-panel] [data-property-slider]')).map((node) => node.dataset.propertySlider);
const rows = () => Array.from(document.body.querySelectorAll<HTMLElement>('[data-layer-row]'));

beforeEach(() => {
	restore = installFakeCanvas();
	resetRecentColours();
	session = new PaintSession();
	session.openBlank({ width: 32, height: 32, background: 'white', pen: null });
});

afterEach(() => {
	session.detach();
	document.body.innerHTML = '';
	restore();
});

describe('each paint entry panel shows only its own controls', () => {
	it('brush: size, opacity, colour', () => {
		mountPanel('brush');
		expect(sliders()).toEqual(['Size', 'Opacity']);
		expect(q('[data-paint-colour]')).not.toBeNull();
		expect(q('[data-paint-selection]')).toBeNull();
		expect(q('[data-paint-tools]')).toBeNull();
	});

	it('eraser: size only', () => {
		mountPanel('eraser');
		expect(sliders()).toEqual(['Size']);
		expect(q('[data-paint-colour]')).toBeNull();
	});

	it('fill: tolerance and colour', () => {
		mountPanel('fill');
		expect(sliders()).toEqual(['Tolerance']);
		expect(q('[data-paint-colour]')).not.toBeNull();
	});

	it('eyedropper: colour only', () => {
		mountPanel('pick');
		expect(sliders()).toEqual([]);
		expect(q('[data-paint-colour]')).not.toBeNull();
	});

	it.each(['rect', 'lasso', 'move'])('%s: the selection block and no sliders or colour', (tool) => {
		mountPanel(tool);
		expect(q('[data-paint-selection]')).not.toBeNull();
		expect(sliders()).toEqual([]);
		expect(q('[data-paint-colour]')).toBeNull();
		const labels = Array.from(document.body.querySelectorAll('[data-paint-selection] button')).map((button) => button.textContent?.trim());
		expect(labels).toEqual(['Cut', 'Copy', 'Paste', 'Delete', 'Select all', 'Deselect']);
	});

	it('shows the hint of the tool', () => {
		mountPanel('brush');
		expect(q('[data-paint-hint]')?.textContent).toContain('Pen pressure');
	});
});

describe('layers as one list', () => {
	it('lists rows top first with the note and no icon bar', () => {
		session.addLayer();
		mountPanel('brush');
		expect(rows().map((row) => row.dataset.layerRow)).toEqual(['1', '0']);
		expect(q('[data-layers-note]')?.textContent).toContain('one edit');
		expect(document.body.querySelector('[aria-label="Merge down"]')).toBeNull();
		expect(document.body.querySelectorAll('[data-layer-opacity]')).toHaveLength(1);
	});

	it('selects a row, toggles visibility and shows the opacity of every row', () => {
		session.addLayer();
		mountPanel('brush');
		q('[data-layer-row="0"] [data-layer-select]')!.click();
		expect(session.snapshot().activeIndex).toBe(0);
		q('[data-layer-row="0"] [data-layer-eye]')!.click();
		expect(session.snapshot().layers[0].visible).toBe(false);
		expect(Array.from(document.body.querySelectorAll('[data-layer-opacity-readout]')).map((node) => node.textContent)).toEqual(['100%', '100%']);
	});

	it('reorders rows by drag and drop', () => {
		session.addLayer();
		session.renameLayer(1, 'Top');
		mountPanel('brush');
		const handle = q('[data-layer-row="1"] [data-layer-handle]')!;
		const target = q('[data-layer-row="0"]')!;
		target.getBoundingClientRect = () => ({ top: 0, height: 40, left: 0, right: 100, bottom: 40, width: 100, x: 0, y: 0, toJSON() {} }) as DOMRect;
		handle.dispatchEvent(new Event('dragstart', { bubbles: true }));
		const over = new Event('dragover', { bubbles: true, cancelable: true }) as Event & { clientY: number };
		over.clientY = 35;
		target.dispatchEvent(over);
		target.dispatchEvent(new Event('drop', { bubbles: true, cancelable: true }));
		expect(session.snapshot().layers.map((layer) => layer.name)).toEqual(['Top', 'Background']);
	});

	it('runs rename, duplicate, merge down, move and delete from the row menu', async () => {
		session.addLayer();
		mountPanel('brush');
		const open = async (index: number) => {
			q(`[data-layer-row="${index}"] button[aria-label^="Actions for"]`)!.click();
			flushSync();
			await Promise.resolve();
		};
		const choose = async (label: string) => {
			const item = Array.from(document.body.querySelectorAll<HTMLButtonElement>('[data-layer-menu] [role="menuitem"]')).find((node) => node.textContent?.trim() === label)!;
			item.click();
			flushSync();
			await Promise.resolve();
		};
		await open(1);
		await choose('Duplicate');
		expect(session.snapshot().layers).toHaveLength(3);
		await open(2);
		await choose('Merge down');
		expect(session.snapshot().layers).toHaveLength(2);
		await open(1);
		await choose('Move down');
		expect(session.snapshot().activeIndex).toBe(0);
		await open(0);
		const labels = Array.from(document.body.querySelectorAll('[data-layer-menu] [role="menuitem"]')).map((node) => node.textContent?.trim());
		expect(labels).toEqual(['Rename', 'Duplicate', 'Merge down', 'Move up', 'Move down', 'Delete']);
		await choose('Delete');
		expect(session.snapshot().layers).toHaveLength(1);
	});

	it('renames from the menu', async () => {
		mountPanel('brush');
		q('[data-layer-row="0"] button[aria-label^="Actions for"]')!.click();
		flushSync();
		await Promise.resolve();
		Array.from(document.body.querySelectorAll<HTMLButtonElement>('[data-layer-menu] [role="menuitem"]')).find((node) => node.textContent?.trim() === 'Rename')!.click();
		flushSync();
		await Promise.resolve();
		const input = document.body.querySelector<HTMLInputElement>('input[aria-label="Layer name"]')!;
		input.value = 'Sky';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		flushSync();
		expect(session.snapshot().layers[0].name).toBe('Sky');
	});

	it('offers extra layer actions through the actions prop', async () => {
		const run = vi.fn();
		mountPanel('brush', PaintPanel, { layerActions: [{ id: 'adjust', label: 'Adjust colours…', icon: 'sliders', run }] });
		q('[data-layer-row="0"] button[aria-label^="Actions for"]')!.click();
		flushSync();
		await Promise.resolve();
		Array.from(document.body.querySelectorAll<HTMLButtonElement>('[data-layer-menu] [role="menuitem"]')).find((node) => node.textContent?.trim() === 'Adjust colours…')!.click();
		expect(run).toHaveBeenCalledWith(0);
	});
});

describe('the floating piece row', () => {
	function lift() {
		session.setTool('rect');
		session.selectAll();
		session.copySelection();
		session.pasteClipboard();
	}

	it('is one marked row with Place and Enter, and Place merges it', () => {
		lift();
		mountPanel('move');
		expect(document.body.querySelectorAll('[data-floating-row]')).toHaveLength(1);
		expect(q('[data-floating-row]')!.textContent).toContain('Press');
		expect(rows()).toHaveLength(1);
		q('[data-floating-place]')!.click();
		flushSync();
		expect(q('[data-floating-row]')).toBeNull();
		expect(session.snapshot().layers).toHaveLength(1);
	});

	it('keeps the piece as a layer from its menu', async () => {
		lift();
		mountPanel('move');
		q('[data-floating-row] button[aria-label="Floating piece actions"]')!.click();
		flushSync();
		await Promise.resolve();
		Array.from(document.body.querySelectorAll<HTMLButtonElement>('[data-floating-menu] [role="menuitem"]')).find((node) => node.textContent?.trim() === 'Keep as a new layer')!.click();
		flushSync();
		expect(q('[data-floating-row]')).toBeNull();
		expect(session.snapshot().layers).toHaveLength(2);
		expect(rows()).toHaveLength(2);
	});
});

describe('the colour control', () => {
	it('opens a portaled picker with swatches and recent colours and sets the colour', () => {
		const picked: string[] = [];
		mountComponent(ColourControl as never, { color: '#f5a524', onchange: (hex: string) => picked.push(hex) });
		expect(document.body.querySelectorAll('[data-colour-swatches]')).toHaveLength(0);
		q('[data-colour-swatch]')!.click();
		flushSync();
		const picker = q('[data-colour-picker]')!;
		expect(picker.parentElement).toBe(document.body);
		expect(picker.querySelectorAll('[data-colour-swatches] button')).toHaveLength(8);
		picker.querySelector<HTMLButtonElement>('[aria-label="Use colour #e5484d"]')!.click();
		expect(picked).toEqual(['#e5484d']);
		const hex = picker.querySelector<HTMLInputElement>('[data-colour-hex-input]')!;
		hex.value = '#00ff00';
		hex.dispatchEvent(new Event('input', { bubbles: true }));
		expect(picked.at(-1)).toBe('#00ff00');
	});

	it('closes on Escape before anything else', () => {
		mountComponent(ColourControl as never, { color: '#111111', onchange: () => {} });
		q('[data-colour-swatch]')!.click();
		flushSync();
		q('[data-colour-picker]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		flushSync();
		expect(q('[data-colour-picker]')).toBeNull();
	});

	it('has no permanent swatch strip in the panel', () => {
		mountPanel('brush');
		expect(document.body.querySelectorAll('[data-paint-panel] button[aria-label^="Use colour"]')).toHaveLength(0);
	});
});

describe('phone options', () => {
	it('shows the tool settings and a Layers button with the count', () => {
		session.addLayer();
		mountPanel('brush', PaintOptions, { phone: true });
		expect(q('[data-paint-layers-button]')!.textContent).toContain('2');
		expect(rows()).toHaveLength(0);
		q('[data-paint-layers-button]')!.click();
		flushSync();
		expect(rows()).toHaveLength(2);
	});
});

describe('seed parameters', () => {
	const params = [
		{ name: 'amount', type: 'integer', min: 0, max: 100, default: 20 },
		{ name: 'seed', type: 'integer', min: 0, max: 2147483647, default: 0 },
		{ name: 'points', type: 'integer', min: 0, max: 50000, default: 10 }
	];

	it('renders a seed as a number field with a roll button, and wide integer ranges as number boxes', () => {
		const changes: Array<[string, unknown]> = [];
		mountComponent(ToolForm as never, { params, values: { amount: 20, seed: 5, points: 10 }, onChange: (name: string, value: unknown) => changes.push([name, value]) });
		expect(q('[data-param="amount"]')!.dataset.control).toBe('slider');
		expect(q('[data-param="seed"]')!.dataset.control).toBe('seed');
		expect(q('[data-param="points"]')!.dataset.control).toBe('number');
		const input = q('[data-param="seed"] input[type="number"]') as HTMLInputElement;
		expect(input.value).toBe('5');
		expect(input.className).toContain('tabular-nums');
		(q('[data-param="seed"] button[aria-label="Roll a random seed"]') as HTMLButtonElement).click();
		expect(changes[0][0]).toBe('seed');
		expect(Number.isInteger(changes[0][1])).toBe(true);
		expect(q('[data-param="seed"] input[type="range"]')).toBeNull();
	});
});
