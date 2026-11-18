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
	it('offers the tool, the mode and the brush size as fields', async () => {
		const view = mountComponent<Record<string, unknown>>(MaskBindFixture, {});
		await settle();
		const names = Array.from(view.target.querySelectorAll('[data-field-name]')).map((node) => (node as HTMLElement).dataset.fieldName);
		expect(names).toEqual(expect.arrayContaining(['tool', 'mode', 'brush']));
	});

	it('writes the brush size and the erase mode into the bound values', async () => {
		const view = mountComponent<Record<string, unknown>>(MaskBindFixture, {});
		await settle();
		const range = view.target.querySelector<HTMLInputElement>('[data-field-name="brush"] input[type="range"]')!;
		range.value = '55';
		range.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();
		expect(bound(view)).toBe('55|false');
		const modes = Array.from(view.target.querySelectorAll<HTMLButtonElement>('[data-field-name="mode"] button'));
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
		const tools = Array.from(view.target.querySelectorAll<HTMLButtonElement>('[data-field-name="tool"] button'));
		expect(tools.length).toBeGreaterThan(1);
		tools[1].click();
		await settle();
		expect(get(maskToolState).overlay).not.toBe(BRUSH_OVERLAY);
		maskToolState.set({ overlay: BRUSH_OVERLAY });
		await settle();
		expect(get(maskToolState).overlay).toBe(BRUSH_OVERLAY);
		expect(view.target.querySelector('[data-field-name="brush"]')).not.toBeNull();
	});
});
