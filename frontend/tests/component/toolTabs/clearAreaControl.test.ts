import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import { installPixelCanvas } from '../helpers/pixelCanvas';
import { emptyImageState, writeImageState } from '$lib/tool-tabs/image/state';
import type { ToolTabHost } from '$lib/tool-tabs/types';
import type { MediaRef } from '$lib/types/tabs';

const { default: MaskOptions } = await import('$lib/components/tool-tabs/image/MaskOptions.svelte');
const { default: ImageEditorTab } = await import('$lib/components/tool-tabs/image/ImageEditorTab.svelte');
const { maskArea, resetMaskTool } = await import('$lib/tool-tabs/image/maskOverlays');

const SOURCE: MediaRef = {
	path: 'outputs/2026-11-01/gen-1/observatory-dusk.png',
	relative_path: 'outputs/2026-11-01/gen-1/observatory-dusk.png',
	url: '/api/media/generations/gen-1/observatory-dusk.png',
	name: 'observatory-dusk.png',
	type: 'image',
	generation_file_id: '501'
};

async function settle() {
	for (let i = 0; i < 20; i++) await Promise.resolve();
	flushSync();
}

const clearButton = (root: ParentNode) =>
	Array.from(root.querySelectorAll<HTMLButtonElement>('button')).find((button) => button.textContent?.trim() === 'Clear area');

beforeEach(() => {
	resetMaskTool();
	installPixelCanvas();
});

afterEach(() => {
	vi.restoreAllMocks();
	vi.unstubAllGlobals();
});

describe.each([
	['Edit a region', null],
	['Remove an object', 'object you want removed']
])('%s options', (_name, what) => {
	it('shows Clear area beside the readout, disabled with nothing marked', async () => {
		const view = mountComponent(MaskOptions, { what, onclear: () => undefined });
		await settle();
		const readout = view.target.querySelector('[data-mask-area-readout]')!;
		expect(clearButton(readout)?.disabled).toBe(true);
		expect(readout.textContent).toContain('None');
	});

	it('enables Clear area when an area is marked and calls the clear action', async () => {
		const onclear = vi.fn(() => maskArea.set(null));
		const view = mountComponent(MaskOptions, { what, onclear });
		maskArea.set({ width: 412, height: 380 });
		await settle();
		const readout = view.target.querySelector('[data-mask-area-readout]')!;
		expect(readout.textContent).toContain('412×380');
		expect(clearButton(readout)?.disabled).toBe(false);
		clearButton(readout)!.click();
		await settle();
		expect(onclear).toHaveBeenCalledTimes(1);
		expect(readout.textContent).toContain('None');
		expect(clearButton(readout)?.disabled).toBe(true);
	});
});

describe('Mask tool in the editor', () => {
	it('offers Clear area in the panel, following the shared area', async () => {
		const host = {
			tabId: 'tab-1', tool: 'image', source: SOURCE, state: writeImageState(emptyImageState('mask')), activeTool: 'mask', run: null, hint: null, phone: false,
			setState: () => undefined, setDirty: () => undefined, setTitle: () => undefined, setThumbnail: () => undefined, setSource: () => undefined,
			replaceSource: () => undefined, setActiveTool: () => undefined, setRun: () => undefined, clearHint: () => undefined, setActions: () => undefined,
			openToolTab: () => 'tab-2'
		} as unknown as ToolTabHost;
		const view = mountComponent(ImageEditorTab, { host });
		await settle();
		const options = view.target.querySelector('[data-options-panel]')!;
		expect(clearButton(options)?.disabled).toBe(true);
		maskArea.set({ width: 50, height: 40 });
		await settle();
		expect(clearButton(options)?.disabled).toBe(false);
		maskArea.set(null);
		await settle();
		expect(clearButton(options)?.disabled).toBe(true);
	});
});
