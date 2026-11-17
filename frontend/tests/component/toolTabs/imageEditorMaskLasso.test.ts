import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from '../helpers/mount';
import { installPixelCanvas } from '../helpers/pixelCanvas';
import { emptyImageState, writeImageState } from '$lib/tool-tabs/image/state';
import type { ToolTabHost } from '$lib/tool-tabs/types';
import type { MediaRef } from '$lib/types/tabs';

const { default: ImageEditorTab } = await import('$lib/components/tool-tabs/image/ImageEditorTab.svelte');
const { resetMaskTool } = await import('$lib/tool-tabs/image/maskOverlays');

const SOURCE: MediaRef = {
	path: 'outputs/2026-11-01/gen-1/observatory-dusk.png',
	relative_path: 'outputs/2026-11-01/gen-1/observatory-dusk.png',
	url: '/api/media/generations/gen-1/observatory-dusk.png',
	name: 'observatory-dusk.png',
	type: 'image',
	generation_file_id: '501'
};

function render(phone = false) {
	const host = {
		tabId: 'tab-1',
		tool: 'image',
		source: SOURCE,
		state: writeImageState(emptyImageState('mask')),
		activeTool: 'mask',
		run: null,
		hint: null,
		phone,
		setState: () => undefined,
		setDirty: () => undefined,
		setTitle: () => undefined,
		setThumbnail: () => undefined,
		setSource: () => undefined,
		replaceSource: () => undefined,
		setActiveTool: () => undefined,
		setRun: () => undefined,
		clearHint: () => undefined,
		setActions: () => undefined,
		openToolTab: () => 'tab-2'
	} as unknown as ToolTabHost;
	const view = mountComponent(ImageEditorTab, { host });
	return { view, q: <T extends Element = HTMLElement>(selector: string) => view.target.querySelector<T>(selector) };
}

async function settle() {
	for (let i = 0; i < 20; i++) await Promise.resolve();
	flushSync();
}

describe('ImageEditorTab mask tool', () => {
	beforeEach(() => {
		resetMaskTool();
		installPixelCanvas();
	});

	afterEach(() => {
		vi.restoreAllMocks();
		vi.unstubAllGlobals();
	});

	it('offers Brush and Lasso and says where the marked area is used', async () => {
		const { q } = render();
		await settle();
		const options = q('[data-options-panel]')!;
		expect(options.querySelector('[aria-label="Marking tool"]')).not.toBeNull();
		expect(options.textContent).toContain('Brush');
		expect(options.textContent).toContain('Lasso');
		expect(options.textContent).toContain('Clear area');
		expect(options.textContent).toContain('Marked area');
		expect(options.textContent).not.toContain('The mask is not an edit step');
	});

	it('switches to the lasso options without leaving the tool', async () => {
		const { q } = render();
		await settle();
		const options = q('[data-options-panel]')!;
		Array.from(options.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Lasso')!.click();
		await settle();
		expect(options.querySelector('[aria-label="Lasso mode"]')).not.toBeNull();
		expect(options.textContent).toContain('Release to close the shape.');
	});
});
