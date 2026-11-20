import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { flushSync } from 'svelte';
import { get } from 'svelte/store';
import { mountComponent } from './helpers/mount';
import MaskBindFixture from './stubs/MaskBindFixture.svelte';
import { maskToolState, resetMaskTool, BRUSH_OVERLAY } from '$lib/tool-tabs/image/maskOverlays';

async function settle() {
	for (let i = 0; i < 40; i++) await Promise.resolve();
	flushSync();
}

afterEach(() => {
	document.body.innerHTML = '';
});

beforeEach(() => resetMaskTool());

const bound = (view: { target: HTMLElement }) => view.target.querySelector('[data-bound]')!.textContent;

describe('Mask panel', () => {
	it('offers the brush size as a field beside the tool and mode toggles', async () => {
		const view = mountComponent<Record<string, unknown>>(MaskBindFixture, {});
		await settle();
		const names = Array.from(view.target.querySelectorAll('[data-field-name]')).map((node) => (node as HTMLElement).dataset.fieldName);
		expect(names).toContain('brush');
		expect(names).not.toContain('tool');
	});

	it('writes the brush size and the erase mode into the bound values', async () => {
		const view = mountComponent<Record<string, unknown>>(MaskBindFixture, {});
		await settle();
		const range = view.target.querySelector<HTMLInputElement>('[data-field-name="brush"] input[type="range"]')!;
		range.value = '55';
		range.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(bound(view)).toBe('55|false');
		const modes = Array.from(view.target.querySelectorAll<HTMLButtonElement>('[aria-label="Mask brush"] button'));
		modes[1].click();
		await settle();
		expect(bound(view)).toBe('55|true');
		modes[0].click();
		await settle();
		expect(bound(view)).toBe('55|false');
	});

	it('sets the marking tool in the shared store and follows a tool chosen elsewhere', async () => {
		const view = mountComponent<Record<string, unknown>>(MaskBindFixture, {});
		await settle();
		const tools = Array.from(view.target.querySelectorAll<HTMLButtonElement>('[aria-label="Marking tool"] button'));
		expect(tools.length).toBeGreaterThan(1);
		tools[1].click();
		await settle();
		expect(get(maskToolState).overlay).not.toBe(BRUSH_OVERLAY);
		expect(view.target.querySelector('[aria-label="Lasso mode"]')).not.toBeNull();
		expect(view.target.querySelector('[aria-label="Mask brush"]')).toBeNull();
		view.target.querySelectorAll<HTMLButtonElement>('[aria-label="Marking tool"] button')[0].click();
		await settle();
		expect(get(maskToolState).overlay).toBe(BRUSH_OVERLAY);
		expect(view.target.querySelector('[aria-label="Mask brush"]')).not.toBeNull();
		expect(view.target.querySelector('[aria-label="Lasso mode"]')).toBeNull();
		maskToolState.set({ overlay: BRUSH_OVERLAY });
		await settle();
		expect(get(maskToolState).overlay).toBe(BRUSH_OVERLAY);
		expect(view.target.querySelector('[data-field-name="brush"]')).not.toBeNull();
	});

	it('takes a click on the other tool after the tool was switched by the editor on entry', async () => {
		const view = mountComponent<Record<string, unknown>>(MaskBindFixture, {});
		maskToolState.set({ overlay: 'mask.lasso' });
		await settle();
		expect(view.target.querySelector('[aria-label="Lasso mode"]')).not.toBeNull();
		view.target.querySelectorAll<HTMLButtonElement>('[aria-label="Marking tool"] button')[0].click();
		await settle();
		expect(get(maskToolState).overlay).toBe(BRUSH_OVERLAY);
		expect(view.target.querySelector('[aria-label="Mask brush"]')).not.toBeNull();
	});
});
