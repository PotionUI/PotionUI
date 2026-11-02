import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';

const { default: ToolRail } = await import('$lib/components/tool-tabs/frame/ToolRail.svelte');
const { default: OptionsPanel } = await import('$lib/components/tool-tabs/frame/OptionsPanel.svelte');

const groups = [
	{
		id: 'shape',
		label: 'Shape',
		tools: [
			{ id: 'crop', label: 'Crop', icon: 'editor-crop' },
			{ id: 'resize', label: 'Resize', icon: 'editor-resize' }
		]
	},
	{
		id: 'look',
		label: 'Look',
		tools: [
			{ id: 'effects', label: 'Effects', icon: 'editor-effects' },
			{ id: 'looks', label: 'Looks', icon: 'editor-effects', later: true },
			{ id: 'frame', label: 'Frame', icon: 'editor-frame', disabled: true, reason: 'Videos only' }
		]
	}
];

function rail(props: Record<string, unknown> = {}) {
	const picked: string[] = [];
	const view = mountComponent(ToolRail, { groups, active: 'crop', onselect: (id: string) => picked.push(id), ...props });
	const tool = (id: string) => view.target.querySelector<HTMLButtonElement>(`[data-tool="${id}"]`)!;
	return { picked, view, tool };
}

afterEach(() => {
	vi.useRealTimers();
	vi.restoreAllMocks();
	document.body.innerHTML = '';
});

describe('ToolRail', () => {
	it('groups tools under mono labels with a 12px label per tool', () => {
		const { view, tool } = rail();
		const text = view.target.textContent!.replace(/\s+/g, ' ');
		expect(text).toContain('Shape');
		expect(text).toContain('Look');
		expect(tool('crop').className).toContain('text-sm');
		expect(view.target.querySelector('[data-tool-rail="rail"]')!.className).toContain('w-[76px]');
	});

	it('marks the active tool pressed in signal and selects another', () => {
		const { tool, picked } = rail();
		expect(tool('crop').getAttribute('aria-pressed')).toBe('true');
		expect(tool('crop').className).toContain('text-signal');
		expect(tool('resize').getAttribute('aria-pressed')).toBe('false');
		tool('resize').click();
		expect(picked).toEqual(['resize']);
	});

	it('shows a dot on pending tools and says so to screen readers', () => {
		const { tool } = rail({ pending: ['crop'] });
		expect(tool('crop').querySelector('[data-tool-pending]')).not.toBeNull();
		expect(tool('crop').getAttribute('aria-label')).toBe('Crop, pending');
		expect(tool('resize').querySelector('[data-tool-pending]')).toBeNull();
	});

	it('keeps disabled and Later tools focusable but inert, with their reason', async () => {
		vi.useFakeTimers();
		const { tool, picked } = rail();
		tool('looks').click();
		tool('frame').click();
		expect(picked).toEqual([]);
		expect(tool('looks').getAttribute('aria-disabled')).toBe('true');
		expect(tool('looks').getAttribute('aria-label')).toBe('Looks, Later');
		expect(tool('frame').getAttribute('aria-label')).toBe('Frame, Videos only');
		expect(tool('frame').className).toContain('text-fg-disabled');
		tool('frame').dispatchEvent(new MouseEvent('mouseenter'));
		await vi.advanceTimersByTimeAsync(400);
		flushSync();
		expect(document.body.textContent).toContain('Videos only');
		expect(document.querySelectorAll('[title]')).toHaveLength(0);
	});

	it('moves focus with the arrow keys along its orientation', () => {
		const { tool } = rail();
		tool('crop').focus();
		tool('crop').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
		expect(document.activeElement).toBe(tool('resize'));
		tool('resize').dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }));
		expect(document.activeElement).toBe(tool('frame'));
		tool('frame').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
		expect(document.activeElement).toBe(tool('frame'));
	});

	it('scrolls the active tool into view in the phone strip', () => {
		const spy = vi.spyOn(Element.prototype, 'scrollIntoView');
		const { view, tool } = rail({ variant: 'strip' });
		expect(view.target.querySelector('[data-tool-rail="strip"]')!.getAttribute('aria-orientation')).toBe('horizontal');
		expect(view.target.textContent).not.toContain('Shape');
		spy.mockClear();
		view.rerender({ active: 'effects' });
		expect(spy).toHaveBeenCalledTimes(1);
		expect(spy.mock.contexts[0]).toBe(tool('effects'));
	});

	it('does not scroll the desktop rail', () => {
		const spy = vi.spyOn(Element.prototype, 'scrollIntoView');
		const { view } = rail();
		view.rerender({ active: 'effects' });
		expect(spy).not.toHaveBeenCalled();
	});
});

function panel(props: Record<string, unknown> = {}) {
	const calls: string[] = [];
	const view = mountComponent(OptionsPanel, {
		title: 'Crop',
		icon: 'editor-crop',
		applyLabel: 'Apply crop',
		onapply: () => calls.push('apply'),
		onreset: () => calls.push('reset'),
		...props
	});
	const q = <T extends Element = HTMLElement>(sel: string) => view.target.querySelector<T>(sel);
	return { calls, view, q };
}

describe('OptionsPanel', () => {
	it('shows the tool name, Reset and Apply with the Enter key', () => {
		const { q, calls } = panel();
		expect(q('h2')!.textContent).toBe('Crop');
		const apply = q<HTMLButtonElement>('[data-options-apply]')!;
		expect(apply.textContent).toContain('Apply crop');
		expect(apply.textContent).toContain('↵');
		apply.click();
		q<HTMLButtonElement>('[data-options-reset]')!.click();
		expect(calls).toEqual(['apply', 'reset']);
	});

	it('disables Apply and Reset on request', () => {
		const { q } = panel({ canApply: false, canReset: false });
		expect(q<HTMLButtonElement>('[data-options-apply]')!.disabled).toBe(true);
		expect(q<HTMLButtonElement>('[data-options-reset]')!.disabled).toBe(true);
	});

	it('collapses to its header and expands again', () => {
		const { q } = panel();
		const toggle = q<HTMLButtonElement>('[data-options-collapse]')!;
		expect(toggle.getAttribute('aria-expanded')).toBe('true');
		toggle.click();
		flushSync();
		expect(q('[data-options-panel]')!.hasAttribute('data-collapsed')).toBe(true);
		expect(q('[data-options-apply]')).toBeNull();
		expect(toggle.getAttribute('aria-label')).toBe('Expand tool options');
		toggle.click();
		flushSync();
		expect(q('[data-options-apply]')).not.toBeNull();
	});

	it('puts Reset and Apply in the header on a phone, with no collapse', () => {
		const { q } = panel({ phone: true });
		const header = q('[data-options-panel="phone"] > div')!;
		expect(header.querySelector('[data-options-apply]')!.textContent!.trim()).toBe('Apply');
		expect(q('[data-options-collapse]')).toBeNull();
	});
});
