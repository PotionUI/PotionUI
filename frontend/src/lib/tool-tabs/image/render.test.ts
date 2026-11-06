import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const calls = vi.hoisted(() => ({
	uploads: [] as string[],
	decodes: [] as string[],
	frameSize: null as { width: number; height: number } | null,
	frames: [] as Array<{ steps: Array<{ tool: string; params: Record<string, unknown> }>; max_side?: number }>,
	failUpload: 0,
	blocked: null as string | null
}));

interface FakeContext {
	ops: string[];
	translate: (x: number, y: number) => void;
	scale: (x: number, y: number) => void;
	rotate: (angle: number) => void;
	drawImage: (...args: unknown[]) => void;
	getImageData: (x: number, y: number, w: number, h: number) => { width: number; height: number; data: Uint8ClampedArray };
	putImageData: () => void;
	imageSmoothingQuality: string;
}

interface FakeCanvas {
	width: number;
	height: number;
	context: FakeContext;
	getContext: () => FakeContext;
}

function fakeCanvas(width = 300, height = 150): FakeCanvas {
	const ops: string[] = [];
	const context: FakeContext = {
		ops,
		translate: (x, y) => ops.push(`translate ${x} ${y}`),
		scale: (x, y) => ops.push(`scale ${x} ${y}`),
		rotate: (angle) => ops.push(`rotate ${Math.round((angle * 180) / Math.PI)}`),
		drawImage: () => ops.push('draw'),
		getImageData: (_x, _y, w, h) => ({ width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }),
		putImageData: () => ops.push('put'),
		imageSmoothingQuality: 'low'
	};
	return { width, height, context, getContext: () => context };
}

vi.mock('$lib/components/imageEditor/loadPicked', () => ({
	canvasFromUrl: async (url: string) => {
		calls.decodes.push(url);
		return fakeCanvas(8, 6);
	},
	canvasFromBlob: async (blob: { size: number; width?: number }) => fakeCanvas(30, 20)
}));

vi.mock('$lib/components/imageEditor/flatten', () => ({
	canvasToPngFile: async (_canvas: unknown, name: string) => new File(['png'], name, { type: 'image/png' })
}));

vi.mock('$lib/plans/uploadLimit', () => ({
	blockedUploadMessage: () => calls.blocked
}));

vi.mock('$lib/services/api/mediaTools', () => ({
	uploadMediaToolArtifact: async () => {
		if (calls.failUpload > 0) {
			calls.failUpload -= 1;
			throw new Error('upload refused');
		}
		calls.uploads.push(`art-${calls.uploads.length + 1}`);
		return { id: calls.uploads[calls.uploads.length - 1], url: '/a.png' };
	},
	previewFrame: async (body: { steps: Array<{ tool: string; params: Record<string, unknown> }>; max_side?: number }) => {
		calls.frames.push(body);
		return { blob: new Blob(['x']), cached: false, ...(calls.frameSize ? { size: calls.frameSize } : {}) };
	}
}));

import { EMPTY_IMAGE_PLAN } from '$lib/media/editors/imageEditPlan';
import { apply, appliedSteps, beginEdit, createStack, toggleHidden, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { FULL_SIDE, ImageRenderer, PREVIEW_SIDE, bakeIsCapped, fitSide, orient, sourceKey } from './render';
import { adjustDraft, catalogDraft, cropDraft, resizeDraft, type CropParams } from './steps';

const SOURCE = { url: '/src.png', size: { width: 8, height: 6 }, ref: { generation_file_id: '1' } };

function push(state: EditStackState<unknown>, draft: Parameters<typeof beginEdit>[1]): EditStackState<unknown> {
	return apply(beginEdit(state, draft));
}

const grain = () => adjustDraft({ kind: 'adjust', op: 'grain', name: 'Grain', values: { amount: 20, size: 1, seed: 3 } });

beforeEach(() => {
	calls.uploads = [];
	calls.decodes = [];
	calls.frameSize = null;
	calls.frames = [];
	calls.failUpload = 0;
	calls.blocked = null;
	vi.stubGlobal('document', { createElement: () => fakeCanvas() });
	vi.stubGlobal('ImageData', class {
		constructor(
			readonly data: Uint8ClampedArray,
			readonly width: number,
			readonly height: number
		) {}
	});
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('fitSide', () => {
	it('keeps sizes at or under the cap and scales larger ones by the long side', () => {
		expect(fitSide({ width: 640, height: 480 }, 1600)).toEqual({ width: 640, height: 480 });
		expect(fitSide({ width: 1600, height: 900 }, 1600)).toEqual({ width: 1600, height: 900 });
		expect(fitSide({ width: 3200, height: 1801 }, 1600)).toEqual({ width: 1600, height: 901 });
		expect(fitSide({ width: 10, height: 20000 }, 1000)).toEqual({ width: 1, height: 1000 });
		expect(bakeIsCapped({ width: FULL_SIDE + 1, height: 10 })).toBe(true);
		expect(bakeIsCapped({ width: FULL_SIDE, height: 10 })).toBe(false);
	});
});

describe('orient', () => {
	const params = (rotation: 0 | 90 | 180 | 270, flipHorizontal = false): CropParams => ({ plan: { ...EMPTY_IMAGE_PLAN, rotation, flipHorizontal }, aspect: 'free' });

	it('swaps width and height for a quarter turn and rotates about the centre', () => {
		const source = fakeCanvas(40, 20) as unknown as HTMLCanvasElement;
		const out = orient(source, params(90)) as unknown as FakeCanvas;
		expect([out.width, out.height]).toEqual([20, 40]);
		expect(out.context.ops).toEqual(['translate 10 20', 'scale 1 1', 'rotate 90', 'draw']);
		const half = orient(source, params(270)) as unknown as FakeCanvas;
		expect([half.width, half.height]).toEqual([20, 40]);
	});

	it('mirrors without changing the size, and returns the same canvas when nothing changes', () => {
		const source = fakeCanvas(40, 20) as unknown as HTMLCanvasElement;
		const flipped = orient(source, params(0, true)) as unknown as FakeCanvas;
		expect([flipped.width, flipped.height]).toEqual([40, 20]);
		expect(flipped.context.ops).toContain('scale -1 1');
		expect(orient(source, params(0))).toBe(source);
	});
});

describe('ImageRenderer after a lost drawing surface', () => {
	it('decodes the source once, then decodes it again and replays the whole stack after a reset', async () => {
		const renderer = new ImageRenderer(SOURCE);
		let stack = push(createStack(), cropDraft({ plan: { ...EMPTY_IMAGE_PLAN, rect: { x: 0, y: 0, width: 0.5, height: 1 } }, aspect: 'free' }, 'Free', { width: 4, height: 6 }));
		stack = push(stack, resizeDraft({ width: 2, height: 3, lock: true }));
		const steps = appliedSteps(stack);

		const first = await renderer.render(steps);
		expect(first.size).toEqual({ width: 2, height: 3 });
		await renderer.render(steps);
		expect(calls.decodes).toEqual(['/src.png']);

		renderer.reset();
		const again = await renderer.render(steps);
		expect(calls.decodes).toEqual(['/src.png', '/src.png']);
		expect(again.size).toEqual(first.size);
		expect([again.canvas.width, again.canvas.height]).toEqual([2, 3]);
	});
});

describe('ImageRenderer bakes', () => {
	it('uploads one look layer per source and step chain, and a hidden step does not change it', async () => {
		const renderer = new ImageRenderer(SOURCE);
		let stack = push(createStack(), resizeDraft({ width: 8, height: 6, lock: true }));
		stack = push(stack, grain());
		const steps = appliedSteps(stack);
		expect(await renderer.bake(steps)).toBe('art-1');
		expect(await renderer.bake(steps)).toBe('art-1');
		const withHidden = appliedSteps(toggleHidden(push(stack, resizeDraft({ width: 4, height: 3, lock: true })), stack.steps[0].id)).slice(0, 2);
		expect(await renderer.bake(withHidden)).toBe('art-2');
		expect(await renderer.bake([steps[0], ...withHidden.slice(1)])).toBe('art-1');
		expect(calls.uploads).toEqual(['art-1', 'art-2']);
	});

	it('bakes again after the source changes or the renderer is reset', async () => {
		const renderer = new ImageRenderer(SOURCE);
		const steps = appliedSteps(push(createStack(), grain()));
		expect(await renderer.bake(steps)).toBe('art-1');
		renderer.setSource({ ...SOURCE });
		expect(await renderer.bake(steps)).toBe('art-1');
		renderer.setSource({ ...SOURCE, url: '/src.png?v=1' });
		expect(await renderer.bake(steps)).toBe('art-2');
		renderer.setSource({ ...SOURCE, url: '/src.png?v=1', ref: { generation_file_id: '2' } });
		expect(await renderer.bake(steps)).toBe('art-3');
		renderer.reset();
		expect(await renderer.bake(steps)).toBe('art-4');
		expect(sourceKey(SOURCE)).not.toBe(sourceKey({ ...SOURCE, ref: { generation_file_id: '2' } }));
	});

	it('drops every bake when the source changes, so going back renders the pixels again', async () => {
		const renderer = new ImageRenderer(SOURCE);
		const steps = appliedSteps(push(createStack(), grain()));
		expect(await renderer.bake(steps)).toBe('art-1');
		renderer.setSource({ ...SOURCE, url: '/other.png' });
		expect(await renderer.bake(steps)).toBe('art-2');
		renderer.setSource(SOURCE);
		expect(await renderer.bake(steps)).toBe('art-3');
	});

	it('forgets a failed bake so the next save retries it', async () => {
		const renderer = new ImageRenderer(SOURCE);
		const steps = appliedSteps(push(createStack(), grain()));
		calls.failUpload = 1;
		await expect(renderer.bake(steps)).rejects.toThrow('upload refused');
		expect(await renderer.bake(steps)).toBe('art-1');
	});

	it('refuses a bake over the upload limit without uploading', async () => {
		const renderer = new ImageRenderer(SOURCE);
		calls.blocked = 'look.png is larger than your plan allows';
		await expect(renderer.bake(appliedSteps(push(createStack(), grain())))).rejects.toThrow('larger than your plan');
		expect(calls.uploads).toEqual([]);
	});
});

describe('ImageRenderer payload', () => {
	it('resolves a crop after a server step through a full size render, then bakes the look after it', async () => {
		const renderer = new ImageRenderer(SOURCE);
		let stack = push(createStack(), catalogDraft('subject', { margin: 0 }));
		stack = push(stack, cropDraft({ plan: { ...EMPTY_IMAGE_PLAN, rect: { x: 0, y: 0, width: 0.5, height: 1 } }, aspect: 'free' }, 'Free', { width: 15, height: 20 }));
		stack = push(stack, grain());
		const payload = await renderer.payload(appliedSteps(stack));
		expect(payload).toEqual([
			{ tool: 'crop_subject', params: { margin: 0 } },
			{ tool: 'crop', params: { x: 0, y: 0, width: 15, height: 20 } },
			{ tool: 'paint_layer', params: { layer: { item_id: 'art-1' } } }
		]);
		expect(calls.frames.map((frame) => [frame.steps.map((step) => step.tool), frame.max_side])).toEqual([[['crop_subject'], FULL_SIDE]]);
	});

	it('asks the server for a preview at the preview size and keeps the full output size it reports', async () => {
		const renderer = new ImageRenderer(SOURCE);
		calls.frameSize = { width: 6000, height: 4000 };
		const subject = appliedSteps(push(createStack(), catalogDraft('subject', { margin: 0 })));
		const rendered = await renderer.render(subject);
		expect(calls.frames.map((frame) => frame.max_side)).toEqual([PREVIEW_SIDE]);
		expect(rendered.size).toEqual({ width: 6000, height: 4000 });
		expect([rendered.canvas.width, rendered.canvas.height]).toEqual([1600, 1067]);
	});

	it('shares one server prefix between renders and aborts it only when every waiter is gone', async () => {
		const renderer = new ImageRenderer(SOURCE);
		const subject = appliedSteps(push(createStack(), catalogDraft('colorKey', { tolerance: 20 })));
		const withLook = appliedSteps(push(push(createStack(), catalogDraft('colorKey', { tolerance: 20 })), grain()));
		await renderer.render(subject);
		await renderer.render(withLook);
		expect(calls.frames).toHaveLength(1);
		const controller = new AbortController();
		renderer.reset();
		const pending = renderer.render(subject, { signal: controller.signal });
		controller.abort();
		await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
		await renderer.render(subject);
		expect(calls.frames).toHaveLength(3);
	});
});
