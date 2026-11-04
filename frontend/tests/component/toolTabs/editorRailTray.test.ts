import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import { EMPTY_IMAGE_PLAN } from '$lib/media/editors/imageEditPlan';
import TrayHost from './TrayHost.svelte';

const { default: CropBar } = await import('$lib/components/tool-tabs/image/CropBar.svelte');
const { default: ToolRail } = await import('$lib/components/tool-tabs/frame/ToolRail.svelte');
const { default: PropertySlider } = await import('$lib/components/tool-tabs/frame/PropertySlider.svelte');

const groups = [
	{ id: 'shape', label: 'Shape', tools: [{ id: 'crop', label: 'Crop', icon: 'editor-crop' }, { id: 'resize', label: 'Resize', icon: 'editor-resize' }] },
	{ id: 'look', label: 'Look', tools: [{ id: 'effects', label: 'Looks', icon: 'editor-effects' }, { id: 'frame', label: 'Frame', icon: 'editor-frame', disabled: true, reason: 'Videos only' }] }
];

afterEach(() => {
	vi.useRealTimers();
	document.body.innerHTML = '';
});

describe('icon-only tool rail', () => {
	function rail(props: Record<string, unknown> = {}) {
		const picked: string[] = [];
		const view = mountComponent(ToolRail, { groups, active: 'crop', variant: 'icons', onselect: (id: string) => picked.push(id), ...props });
		return { picked, view, tool: (id: string) => view.target.querySelector<HTMLButtonElement>(`[data-tool="${id}"]`)! };
	}

	it('has no captions and no labels, only icons, dividers between groups', () => {
		const { view } = rail();
		const text = view.target.textContent!.replace(/\s+/g, '');
		expect(text).toBe('');
		expect(view.target.querySelector('[data-tool-rail="icons"]')!.className).toContain('w-[52px]');
		expect(view.target.querySelectorAll('[role="separator"]')).toHaveLength(1);
	});

	it('keeps the label as the accessible name and marks the active tool', () => {
		const { tool } = rail();
		expect(tool('resize').getAttribute('aria-label')).toBe('Resize');
		expect(tool('crop').getAttribute('aria-pressed')).toBe('true');
		expect(tool('effects').getAttribute('aria-label')).toBe('Looks');
	});

	it('selects a tool on click and ignores an unavailable one', () => {
		const { picked, tool } = rail();
		tool('resize').click();
		tool('frame').click();
		expect(picked).toEqual(['resize']);
		expect(tool('frame').getAttribute('aria-label')).toBe('Frame, Videos only');
	});

	it('moves focus with the arrow keys', () => {
		const { view, tool } = rail();
		tool('crop').focus();
		view.target.querySelector('[role="toolbar"]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
		expect(document.activeElement).toBe(tool('resize'));
	});
});

describe('PropertySlider', () => {
	function slider(props: Record<string, unknown> = {}) {
		const changes: number[] = [];
		let commits = 0;
		const view = mountComponent(PropertySlider, { label: 'Contrast', value: 0, min: -100, max: 100, defaultValue: 0, onchange: (value: number) => changes.push(value), oncommit: () => (commits += 1), ...props });
		const range = view.target.querySelector<HTMLInputElement>('input[type="range"]')!;
		return { view, range, changes, commits: () => commits };
	}

	it('reports each input as a change and the end of a drag as a commit', () => {
		const { range, changes, commits } = slider();
		range.value = '12';
		range.dispatchEvent(new Event('input', { bubbles: true }));
		range.value = '18';
		range.dispatchEvent(new Event('input', { bubbles: true }));
		expect(changes).toEqual([12, 18]);
		expect(commits()).toBe(0);
		range.dispatchEvent(new Event('change', { bubbles: true }));
		expect(commits()).toBe(1);
	});

	it('shows a signed mono readout and flags a moved value', () => {
		const { view } = slider({ value: 18 });
		expect(view.target.querySelector('[data-property-value]')!.textContent).toBe('+18');
		expect(view.target.querySelector('[data-property-slider]')!.hasAttribute('data-changed')).toBe(true);
		expect(view.target.querySelector('[data-property-value]')!.className).toContain('tabular-nums');
	});

	it('resets to the default on double click and on Backspace, then commits', () => {
		const { view, range, changes, commits } = slider({ value: 18 });
		view.target.querySelector('[data-property-slider]')!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
		range.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace', bubbles: true, cancelable: true }));
		expect(changes).toEqual([0, 0]);
		expect(commits()).toBe(2);
	});

	it('does not reset a value that is already at its default', () => {
		const { view, changes } = slider({ value: 0 });
		view.target.querySelector('[data-property-slider]')!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
		expect(changes).toEqual([]);
	});

	it('lays the label, track and value on one row when inline', () => {
		const { view } = slider({ inline: true, label: 'Intensity', value: 60, min: 0, max: 100, defaultValue: 100 });
		expect(view.target.querySelector('[data-property-slider]')!.className).toContain('items-center');
		expect(view.target.querySelector('[data-property-value]')!.textContent).toBe('60');
	});
});

describe('look tray', () => {
	const items = [
		{ id: 'reel', label: 'Reel', group: 'Film', hint: 'grain', selected: true },
		{ id: 'matte', label: 'Matte', group: 'Film' },
		{ id: 'noir', label: 'Noir', group: 'Mono' }
	];

	function tray(props: Record<string, unknown> = {}) {
		const log: string[] = [];
		const view = mountComponent(TrayHost, { items, original: { selected: false }, log: (entry: string) => log.push(entry), ...props });
		const tile = (id: string) => view.target.querySelector<HTMLButtonElement>(`[data-tray-item="${id}"]`)!;
		return { view, log, tile };
	}

	beforeEach(() => {
		vi.useFakeTimers();
	});

	it('groups tiles under captions with an Original tile first', () => {
		const { view } = tray();
		const groupsShown = Array.from(view.target.querySelectorAll('[data-tray-group]')).map((group) => group.getAttribute('data-tray-group'));
		expect(groupsShown).toEqual(['Original', 'Film', 'Mono']);
		expect(view.target.querySelectorAll('[data-tray-item]')).toHaveLength(4);
		expect(view.target.querySelector('[role="listbox"]')!.getAttribute('aria-label')).toBe('Looks');
	});

	it('marks the selected tile', () => {
		const { tile } = tray();
		expect(tile('reel').getAttribute('aria-selected')).toBe('true');
		expect(tile('matte').getAttribute('aria-selected')).toBe('false');
	});

	it('previews after a short hover intent and clears on leave, never picking', () => {
		const { view, log, tile } = tray();
		tile('matte').dispatchEvent(new PointerEvent('pointerenter', { pointerType: 'mouse' }));
		vi.advanceTimersByTime(100);
		expect(log).toEqual([]);
		vi.advanceTimersByTime(60);
		expect(log).toEqual(['preview:matte']);
		view.target.querySelector('[data-tray]')!.dispatchEvent(new PointerEvent('pointerleave', { pointerType: 'mouse' }));
		vi.advanceTimersByTime(100);
		expect(log).toEqual(['preview:matte', 'preview:null']);
	});

	it('moves between tiles without waiting once a preview is showing', () => {
		const { log, tile } = tray();
		tile('matte').dispatchEvent(new PointerEvent('pointerenter', { pointerType: 'mouse' }));
		vi.advanceTimersByTime(200);
		tile('noir').dispatchEvent(new PointerEvent('pointerenter', { pointerType: 'mouse' }));
		vi.advanceTimersByTime(0);
		expect(log).toEqual(['preview:matte', 'preview:noir']);
	});

	it('does not preview on touch and does not preview the Original tile', () => {
		const { log, tile } = tray();
		tile('matte').dispatchEvent(new PointerEvent('pointerenter', { pointerType: 'touch' }));
		tile('__original').dispatchEvent(new PointerEvent('pointerenter', { pointerType: 'mouse' }));
		vi.advanceTimersByTime(500);
		expect(log).toEqual([]);
	});

	it('a click picks the tile and drops any preview first', () => {
		const { log, tile } = tray();
		tile('matte').dispatchEvent(new PointerEvent('pointerenter', { pointerType: 'mouse' }));
		vi.advanceTimersByTime(200);
		tile('matte').click();
		flushSync();
		expect(log).toEqual(['preview:matte', 'preview:null', 'pick:matte']);
	});

	it('picks null for the Original tile', () => {
		const { log, tile } = tray();
		tile('__original').click();
		expect(log).toEqual(['pick:null']);
	});

	it('drops the captions on a phone', () => {
		const { view } = tray({ phone: true });
		expect(view.target.textContent).not.toContain('Film');
	});
});

describe('crop options bar', () => {
	function bar() {
		const changes: Array<{ aspect: string; plan: { rotation: number; flipHorizontal: boolean; rect: { width: number; height: number } } }> = [];
		const params = { aspect: 'free', plan: { ...EMPTY_IMAGE_PLAN, rect: { x: 0, y: 0, width: 1, height: 1 } } };
		const view = mountComponent(CropBar, { params, input: { width: 1000, height: 500 }, onchange: (next: never) => changes.push(next) });
		return { view, changes };
	}

	it('offers the aspect chips, orientation buttons and the live output size', () => {
		const { view } = bar();
		expect(Array.from(view.target.querySelectorAll('[data-crop-aspects] button')).map((button) => button.textContent)).toEqual(['Free', '1:1', '4:3', '3:2', '16:9', '9:16']);
		expect(view.target.querySelectorAll('[data-crop-orient]')).toHaveLength(4);
		expect(view.target.querySelector('[data-crop-output]')?.textContent).toBe('1000×500');
	});

	it('reshapes the crop from an aspect chip', () => {
		const { view, changes } = bar();
		view.target.querySelectorAll<HTMLButtonElement>('[data-crop-aspects] button')[1].click();
		expect(changes[0].aspect).toBe('1:1');
		expect(changes[0].plan.rect.width * 1000).toBeCloseTo(changes[0].plan.rect.height * 500 * 1);
	});

	it('rotates and flips from the bar', () => {
		const { view, changes } = bar();
		view.target.querySelector<HTMLButtonElement>('[data-crop-orient="rotate-right"]')!.click();
		view.target.querySelector<HTMLButtonElement>('[data-crop-orient="flip-h"]')!.click();
		expect(changes[0].plan.rotation).toBe(90);
		expect(changes[1].plan.flipHorizontal).toBe(true);
	});
});
