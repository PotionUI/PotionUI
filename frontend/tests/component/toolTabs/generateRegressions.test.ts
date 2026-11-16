import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { get } from 'svelte/store';
import { mountComponent } from '../helpers/mount';
import { mediaToolCatalogRevision } from '$lib/media-tools/catalog';
import { markEditorExtensionsReady } from '$lib/editors';
import { api } from '$lib/services/api/index';
import { apply, beginEdit, createStack } from '$lib/tool-tabs/frame/editStack';
import { emptyImageState, writeImageState } from '$lib/tool-tabs/image/state';
import { generateDraft, type GenerateParams } from '$lib/tool-tabs/image/generate/generateStep';
import { runViews } from '$lib/tool-tabs/image/generate/runEngine';
import { maskArea } from '$lib/tool-tabs/image/maskOverlays';
import type { ToolTabHost } from '$lib/tool-tabs/types';

vi.mock('$lib/media/loadImage', () => ({
	loadImage: async () => ({ naturalWidth: 8, naturalHeight: 6 })
}));

const { default: ImageEditorTab } = await import('$lib/components/tool-tabs/image/ImageEditorTab.svelte');
const { default: MaskLayer } = await import('$lib/components/tool-tabs/image/MaskLayer.svelte');

async function settle() {
	for (let i = 0; i < 30; i++) await Promise.resolve();
	flushSync();
}

function params(status: 'queued' | 'running'): GenerateParams {
	return {
		v: 1, task: 'edit_region', prompt: 'a lantern', candidates: 1, results: [], chosen: 0, seed: null, rect: null, soft: false,
		setup: { preset_id: 'qwen', mode: 'edit', variant: null, form_data: {}, source: { kind: 'defaults', id: null, name: null }, label: '' },
		area: { region: null, size: null }, run: { id: 'er_1', status, error: null }
	};
}

function mountTab(status: 'queued' | 'running') {
	const stack = apply(beginEdit(createStack(), generateDraft(params(status), 'Edit a region', 'sparkles')));
	const host = {
		tabId: 'tab-reg', tool: 'image',
		source: { path: 'outputs/a.png', relative_path: 'outputs/a.png', url: '/api/media/generations/gen-1/a.png', name: 'a.png', type: 'image', generation_file_id: '501' },
		state: writeImageState({ ...emptyImageState('crop'), stack }),
		activeTool: null, run: null, hint: null, phone: false,
		setState: () => undefined, setDirty: () => undefined, setTitle: () => undefined, setThumbnail: () => undefined, setSource: () => undefined, replaceSource: () => undefined,
		setActiveTool: () => undefined, setRun: () => undefined, clearHint: () => undefined, setActions: () => undefined, openToolTab: () => 'tab-2'
	} as unknown as ToolTabHost;
	const view = mountComponent(ImageEditorTab, { host });
	return { view, row: () => (view.target.querySelector('[data-stack-row="s1"]')?.textContent ?? '').replace(/\s+/g, ' ').trim() };
}

describe('the stack row follows the run', () => {
	beforeEach(() => {
		vi.useFakeTimers();
		markEditorExtensionsReady();
		mediaToolCatalogRevision.set(1);
		vi.spyOn(api, 'getEditorRun').mockRejectedValue({ message: 'Network Error' });
		vi.spyOn(api, 'listEditorTasks').mockResolvedValue([]);
	});

	afterEach(() => {
		runViews.set({});
		vi.restoreAllMocks();
		vi.useRealTimers();
	});

	it('goes from Waiting to Generating with its progress to Did not finish, and drops the state text when done', async () => {
		const { row, view } = mountTab('queued');
		await settle();
		expect(row()).toContain('Waiting');
		runViews.update((all) => ({ ...all, er_1: { ...all.er_1, status: 'running', progress: 0.4 } }));
		await settle();
		expect(row()).toContain('Generating 40%');
		runViews.update((all) => ({ ...all, er_1: { ...all.er_1, status: 'running', progress: 0.8 } }));
		await settle();
		expect(row()).toContain('Generating 80%');
		runViews.update((all) => ({ ...all, er_1: { ...all.er_1, status: 'failed', error: 'timed out' } }));
		await settle();
		expect(row()).toContain('Did not finish: timed out');
		runViews.update((all) => ({ ...all, er_1: { ...all.er_1, status: 'done' } }));
		await settle();
		expect(row()).not.toContain('Generating');
		expect(row()).not.toContain('Did not finish');
		view.destroy();
	});
});

describe('the marked area when the mask layer is created again after landing', () => {
	function stubCanvas() {
		let painted = false;
		const context = new Proxy(
			{
				getImageData: (_x: number, _y: number, w: number, h: number) => {
					const data = new Uint8ClampedArray(w * h * 4);
					if (painted) for (let y = 2; y < 8; y++) for (let x = 3; x < 9; x++) data[(y * w + x) * 4 + 3] = 255;
					return { width: w, height: h, data };
				},
				putImageData: () => {
					painted = true;
				}
			} as Record<string | symbol, unknown>,
			{ get: (target, key) => (key in target ? target[key] : () => undefined), set: () => true }
		);
		vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => context as unknown as CanvasRenderingContext2D);
		vi.stubGlobal('ImageData', class { constructor(readonly data: Uint8ClampedArray, readonly width: number, readonly height: number) {} });
	}

	beforeEach(() => {
		maskArea.set(null);
		stubCanvas();
	});

	afterEach(() => {
		vi.restoreAllMocks();
		vi.unstubAllGlobals();
		maskArea.set(null);
	});

	it('keeps the readout instead of dropping to None', async () => {
		const first = mountComponent(MaskLayer, { width: 20, height: 12, existing: '/m.png' });
		await settle();
		expect(get(maskArea)).toEqual({ width: 6, height: 6 });
		first.destroy();
		expect(get(maskArea)).toBeNull();
		mountComponent(MaskLayer, { width: 20, height: 12, existing: '/m.png' });
		await settle();
		expect(get(maskArea)).toEqual({ width: 6, height: 6 });
	});
});
