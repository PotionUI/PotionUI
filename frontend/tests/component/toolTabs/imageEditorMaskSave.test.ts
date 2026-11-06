import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { get } from 'svelte/store';
import { mountComponent } from '../helpers/mount';
import { EMPTY_IMAGE_PLAN } from '$lib/media/editors/imageEditPlan';
import { toasts } from '$lib/stores/toast';
import { apply, beginEdit, createStack } from '$lib/tool-tabs/frame/editStack';
import { emptyImageState, writeImageState } from '$lib/tool-tabs/image/state';
import { adjustDraft, cropDraft, resizeDraft } from '$lib/tool-tabs/image/steps';
import type { ToolTabHost } from '$lib/tool-tabs/types';
import type { MediaRef } from '$lib/types/tabs';

const log = vi.hoisted(() => ({ uploads: 0, starts: 0 }));

vi.mock('$lib/media/editors/loadImage', () => ({
	loadImage: async () => ({ naturalWidth: 8, naturalHeight: 6 })
}));

vi.mock('$lib/components/imageEditor/flatten', () => ({
	canvasToPngFile: async (_canvas: unknown, name: string) => new File(['png'], name, { type: 'image/png' }),
	flattenDocument: () => null
}));

vi.mock('$lib/services/api/mediaTools', () => ({
	listMediaTools: async () => [],
	startMediaToolRun: async () => {
		log.starts += 1;
		throw new Error('The edit could not be started');
	},
	getMediaToolRun: async () => {
		throw new Error('no run');
	},
	cancelMediaToolRun: async () => null,
	previewFrame: async () => {
		throw new Error('no preview');
	},
	uploadMediaToolArtifact: async () => {
		log.uploads += 1;
		throw new Error('The file is larger than your plan allows');
	}
}));

const { default: MaskLayer } = await import('$lib/components/tool-tabs/image/MaskLayer.svelte');
const { default: ImageEditorTab } = await import('$lib/components/tool-tabs/image/ImageEditorTab.svelte');

interface Recorder {
	puts: number;
	fills: number;
}

function stubCanvas(recorder: Recorder) {
	const context = new Proxy(
		{
			getImageData: (_x: number, _y: number, w: number, h: number) => {
				const data = new Uint8ClampedArray(w * h * 4);
				data[0] = 255;
				data[3] = 255;
				return { width: w, height: h, data };
			},
			putImageData: () => (recorder.puts += 1),
			fillRect: () => (recorder.fills += 1)
		} as Record<string | symbol, unknown>,
		{ get: (target, key) => (key in target ? target[key] : () => undefined), set: () => true }
	);
	vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => context as unknown as CanvasRenderingContext2D);
	vi.stubGlobal(
		'ImageData',
		class {
			constructor(
				readonly data: Uint8ClampedArray,
				readonly width: number,
				readonly height: number
			) {}
		}
	);
}

async function settle() {
	for (let i = 0; i < 20; i++) await Promise.resolve();
	flushSync();
}

describe('MaskLayer', () => {
	let recorder: Recorder;

	beforeEach(() => {
		recorder = { puts: 0, fills: 0 };
		stubCanvas(recorder);
	});

	afterEach(() => {
		vi.restoreAllMocks();
		vi.unstubAllGlobals();
	});

	it('restores a saved mask in one put and does not mark it dirty', async () => {
		const strokes: boolean[] = [];
		mountComponent(MaskLayer, { width: 400, height: 300, existing: '/m.png', onstroke: (painted: boolean) => strokes.push(painted) });
		await settle();
		expect(recorder.puts).toBe(1);
		expect(recorder.fills).toBe(0);
		expect(strokes).toEqual([]);
	});

	it('marks the mask dirty after a real stroke', async () => {
		const strokes: boolean[] = [];
		const view = mountComponent(MaskLayer, { width: 40, height: 30, onstroke: (painted: boolean) => strokes.push(painted) });
		await settle();
		const canvas = view.target.querySelector<HTMLCanvasElement>('[data-mask-layer]')!;
		canvas.getBoundingClientRect = () => ({ left: 0, top: 0, width: 40, height: 30, right: 40, bottom: 30, x: 0, y: 0, toJSON: () => ({}) });
		canvas.dispatchEvent(new PointerEvent('pointerdown', { button: 0, clientX: 5, clientY: 5, bubbles: true }));
		canvas.dispatchEvent(new PointerEvent('pointermove', { clientX: 15, clientY: 10, bubbles: true }));
		canvas.dispatchEvent(new PointerEvent('pointerup', { clientX: 15, clientY: 10, bubbles: true }));
		expect(strokes).toEqual([true]);
	});
});

const SOURCE: MediaRef = {
	path: 'outputs/2026-11-01/gen-1/dusk.png',
	url: '/api/media/generations/gen-1/dusk.png',
	name: 'dusk.png',
	type: 'image',
	generation_file_id: '501'
};

function render(state: unknown) {
	const dirty: number[] = [];
	const host = {
		tabId: 'tab-1',
		tool: 'image',
		source: SOURCE,
		state,
		activeTool: 'resize',
		run: null,
		phone: false,
		setState: () => undefined,
		setDirty: (count: number) => dirty.push(count),
		setTitle: () => undefined,
		setThumbnail: () => undefined,
		setSource: () => undefined,
		replaceSource: () => undefined,
		setActiveTool: () => undefined,
		setRun: () => undefined,
		setActions: () => undefined,
		openToolTab: () => 'tab-2'
	} as unknown as ToolTabHost;
	const view = mountComponent(ImageEditorTab, { host });
	return { view, dirty, q: <T extends HTMLElement = HTMLElement>(selector: string) => view.target.querySelector<T>(selector) };
}

function shapeSteps() {
	let stack = apply(beginEdit(createStack(), cropDraft({ plan: { ...EMPTY_IMAGE_PLAN, rect: { x: 0, y: 0, width: 0.5, height: 1 } }, aspect: 'free' }, 'Free', { width: 4, height: 6 })));
	stack = apply(beginEdit(stack, resizeDraft({ width: 2, height: 3, lock: true })));
	return writeImageState({ ...emptyImageState('resize'), stack });
}

function lookSteps() {
	const stack = apply(beginEdit(createStack(), adjustDraft({ kind: 'adjust', op: 'invert', name: 'Invert', values: {} })));
	return writeImageState({ ...emptyImageState('resize'), stack });
}

describe('ImageEditorTab save failures', () => {
	beforeEach(() => {
		log.uploads = 0;
		log.starts = 0;
		stubCanvas({ puts: 0, fills: 0 });
	});

	afterEach(() => {
		vi.restoreAllMocks();
		vi.unstubAllGlobals();
	});

	it('shows a refused run and keeps the stack unsaved', async () => {
		const { q, dirty } = render(shapeSteps());
		await settle();
		q('[data-frame-save]')!.click();
		await settle();
		expect(log.starts).toBe(1);
		const failure = get(toasts).find((toast) => toast.title === 'Save failed');
		expect(failure?.message).toContain('could not be started');
		expect(dirty[dirty.length - 1]).toBe(2);
		expect(q('[data-frame-save]')?.textContent?.trim()).toBe('Save as new');
		expect(q<HTMLButtonElement>('[data-frame-save]')?.disabled).toBe(false);
	});

	it('shows a refused look upload and never starts the run', async () => {
		const { q, dirty } = render(lookSteps());
		await settle();
		q('[data-frame-save]')!.click();
		await settle();
		await settle();
		expect(log.uploads).toBe(1);
		expect(log.starts).toBe(0);
		const failure = get(toasts).find((toast) => toast.title === 'Save failed');
		expect(failure?.message).toContain('larger than your plan');
		expect(dirty[dirty.length - 1]).toBe(1);
		expect(q('[data-frame-save]')?.textContent?.trim()).toBe('Save as new');
	});
});
