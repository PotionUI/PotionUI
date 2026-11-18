import { get, writable } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const calls = vi.hoisted(() => ({ decodes: [] as string[] }));

function fakeCanvas(width = 8, height = 6) {
	const context = {
		translate: () => undefined,
		scale: () => undefined,
		rotate: () => undefined,
		drawImage: () => undefined,
		getImageData: (_x: number, _y: number, w: number, h: number) => ({ width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }),
		putImageData: () => undefined,
		imageSmoothingQuality: 'low'
	};
	return { width, height, getContext: () => context };
}

vi.mock('$lib/paint/loadPicked', () => ({
	canvasFromUrl: async (url: string) => {
		calls.decodes.push(url);
		return fakeCanvas();
	},
	canvasFromBlob: async () => fakeCanvas(30, 20)
}));

vi.mock('$lib/paint/flatten', () => ({ canvasToPngFile: async (_canvas: unknown, name: string) => new File(['png'], name, { type: 'image/png' }) }));
vi.mock('$lib/plans/uploadLimit', () => ({ blockedUploadMessage: () => null }));
vi.mock('$lib/services/api/mediaTools', () => ({
	uploadMediaToolArtifact: async () => ({ id: 'art', url: '/a.png' }),
	previewFrame: async () => ({ blob: new Blob(['x']), cached: false })
}));

import { EMPTY_IMAGE_PLAN } from '$lib/media/imageEditPlan';
import type { EditorRun, EditorTaskInfo, TaskOffer } from '$lib/services/api/editorTasks';
import {
	appliedSteps,
	apply,
	beginEdit,
	canMove,
	canUndo,
	commitLive,
	createStack,
	deserialize,
	move,
	remove,
	reopen,
	serialize,
	toggleHidden,
	undo,
	updatePending,
	type EditStackState,
	type EditStep
} from '$lib/tool-tabs/frame/editStack';
import { stepKinds } from '$lib/editors/stepKinds';
import { ensureCoreImageEditor } from '../coreEditor';
import { geometryKey } from '../mask';
import { outputSize, runSteps } from '../payload';
import { ImageRenderer } from '../render';
import { IMAGE_PINNED_LANES, adjustDraft, catalogDraft, cropDraft, lookDraft, paintDraft, resizeDraft } from '../steps';
import { imageRowInfo, outOfDateCount, outOfDateNotice, stepOutOfDate, stepsBeneath } from '../stepKinds';
import { createGenerateController, type ControllerDeps } from './controller';
import {
	GENERATE_TOOL,
	basisFor,
	generateDraft,
	generateStepKind,
	readGenerateParams,
	remapArea,
	shapeChange,
	summaryOf,
	type GenerateParams
} from './generateStep';
import { createRunEngine, runViews, type RunPorts, type RunViews } from './runEngine';

const SOURCE = { width: 1000, height: 800 };

function registered() {
	ensureCoreImageEditor();
	return stepKinds.get('image', GENERATE_TOOL)!;
}

const cropOf = (x: number, width: number) => cropDraft({ plan: { ...EMPTY_IMAGE_PLAN, rect: { x, y: 0, width, height: 1 } }, aspect: 'free' }, 'Free', { width: 500, height: 800 });
const push = (state: EditStackState<unknown>, draft: Parameters<typeof beginEdit>[1]) => apply(beginEdit(state, draft));
const resize = (width: number) => resizeDraft({ width, height: Math.round(width * 0.8), lock: true });

function genParams(beneath: readonly EditStep[], over: Partial<GenerateParams> = {}): GenerateParams {
	return {
		v: 1,
		task: 'edit_region',
		prompt: 'a red sailboat',
		setup: { preset_id: 'qwen', mode: 'editor:edit', form_data: { steps: 28, seed: 5 }, source: { kind: 'defaults', id: null, name: null }, label: 'Qwen' },
		area: { region: { item_id: 'mask1' }, size: { width: 400, height: 300 } },
		candidates: 2,
		results: [
			{ patch: { item_id: 'p1' }, url: '/p1.png', seed: 11 },
			{ patch: { item_id: 'p2' }, url: '/p2.png', seed: 22 }
		],
		chosen: 1,
		seed: 22,
		rect: { x: 0.1, y: 0.1, width: 0.3, height: 0.3 },
		soft: false,
		run: { id: 'er_1', status: 'done', error: null },
		basis: basisFor(beneath, outputSize(beneath, SOURCE)),
		...over
	};
}

function withGenerate(stack: EditStackState<unknown>, over: Partial<GenerateParams> = {}): EditStackState<unknown> {
	const params = genParams(appliedSteps(stack).filter((step) => !step.hidden), over);
	return push(stack, generateDraft(params, 'Edit a region', 'sparkles') as never);
}

const generateOf = (stack: EditStackState<unknown>) => stack.steps.find((step) => step.tool === GENERATE_TOOL)!;
const outOfDate = (stack: EditStackState<unknown>, id?: string) => {
	const steps = stack.steps;
	const at = id ? steps.findIndex((step) => step.id === id) : steps.findIndex((step) => step.tool === GENERATE_TOOL);
	return stepOutOfDate(steps[at], stepsBeneath(steps, at));
};

afterEach(() => runViews.set({}));

describe('the shape beneath a generated step', () => {
	const base = () => push(createStack(), cropOf(0, 0.5));

	it('is the geometry key of the visible steps beneath, so looks, adjustments, paint and hidden steps do not change it', () => {
		let stack = base();
		const steps = () => appliedSteps(stack);
		const key = geometryKey(steps());
		stack = push(stack, lookDraft({ kind: 'look', filter: 'reel', name: 'Reel', intensity: 50 }));
		stack = push(stack, adjustDraft({ kind: 'adjust', op: 'grain', name: 'Grain', values: { amount: 20 } }));
		stack = push(stack, paintDraft({ layer: 'art', url: '/a.png', layers: 1 }, SOURCE));
		expect(geometryKey(steps())).toBe(key);
		stack = push(stack, resize(250));
		const withResize = geometryKey(steps());
		expect(withResize).not.toBe(key);
		stack = toggleHidden(stack, stack.steps[stack.steps.length - 1].id);
		expect(geometryKey(steps())).toBe(key);
	});

	it('changes with a crop, a resize, a canvas change and a subject crop', () => {
		const empty = geometryKey([]);
		const cases = [
			push(createStack(), cropOf(0, 0.5)),
			push(createStack(), resize(500)),
			push(createStack(), catalogDraft('canvas', { width: 1024, height: 1024 })),
			push(createStack(), catalogDraft('subject', { padding: 10 }))
		];
		for (const stack of cases) expect(geometryKey(appliedSteps(stack))).not.toBe(empty);
		expect(geometryKey(appliedSteps(push(createStack(), cropOf(0, 0.5))))).not.toBe(geometryKey(appliedSteps(push(createStack(), cropOf(0.25, 0.5)))));
		const turned = cropDraft({ plan: { ...EMPTY_IMAGE_PLAN, rotation: 90 }, aspect: 'free' }, 'Free', { width: 800, height: 1000 });
		expect(geometryKey(appliedSteps(push(createStack(), turned)))).not.toBe(empty);
	});

	it('is recorded as a basis with the picture size, and a step saved without one reads back without it', () => {
		const stack = withGenerate(base());
		const params = readGenerateParams(generateOf(stack).params)!;
		expect(params.basis).toEqual({ geometry: geometryKey(appliedSteps(base())), size: { width: 500, height: 800 } });
		const old = { ...(generateOf(stack).params as object) } as Record<string, unknown>;
		delete old.basis;
		const loaded = readGenerateParams(old)!;
		expect('basis' in loaded).toBe(false);
	});
});

describe('out of date, derived from the stack', () => {
	it('is not flagged while nothing beneath changed shape', () => {
		let stack = withGenerate(base());
		expect(outOfDate(stack)).toBe(false);
		stack = push(stack, lookDraft({ kind: 'look', filter: 'reel', name: 'Reel', intensity: 50 }));
		expect(outOfDate(stack)).toBe(false);
	});

	function base() {
		return push(createStack(), cropOf(0, 0.5));
	}

	function reshape(stack: EditStackState<unknown>, draft: Parameters<typeof beginEdit>[1]) {
		return apply(updatePending(reopen(stack, 's1'), draft as never));
	}

	it('is flagged after a crop beneath changes, and is up to date again when that change is undone', () => {
		const stack = reshape(withGenerate(base()), cropOf(0.25, 0.5));
		expect(outOfDate(stack)).toBe(true);
		expect(canUndo(stack)).toBe(true);
		const back = undo(stack);
		expect(outOfDate(back)).toBe(false);
	});

	it('is flagged after a resize, a canvas or a subject change beneath', () => {
		const pairs = [
			[resize(500), resize(250)],
			[catalogDraft('canvas', { width: 900, height: 900 }), catalogDraft('canvas', { width: 800, height: 800 })],
			[catalogDraft('subject', { padding: 5 }), catalogDraft('subject', { padding: 10 })]
		];
		for (const [first, second] of pairs) {
			const stack = withGenerate(push(createStack(), first));
			expect(outOfDate(stack)).toBe(false);
			expect(outOfDate(reshape(stack, second))).toBe(true);
		}
	});

	it('is flagged when the step that shaped the picture is hidden or removed, and up to date again when it is shown', () => {
		let stack = withGenerate(base());
		stack = toggleHidden(stack, 's1');
		expect(outOfDate(stack)).toBe(true);
		stack = toggleHidden(stack, 's1');
		expect(outOfDate(stack)).toBe(false);
		stack = remove(stack, 's1');
		expect(outOfDate(stack)).toBe(true);
	});

	it('never flags a step saved before there was a basis, a hidden step, or a run that did not finish', () => {
		const stack = base();
		const plain = push(stack, generateDraft({ ...genParams([]), basis: undefined } as GenerateParams, 'Edit a region', 'sparkles') as never);
		expect(outOfDate(plain)).toBe(false);
		const hidden = toggleHidden(reshape(withGenerate(base()), cropOf(0.25, 0.5)), 's2');
		expect(outOfDate(reshape(withGenerate(base()), cropOf(0.25, 0.5)))).toBe(true);
		expect(outOfDate(hidden)).toBe(false);
		const failed = withGenerate(base(), { run: { id: 'er_1', status: 'failed', error: 'x' }, results: [] });
		const moved = reshape(failed, cropOf(0.25, 0.5));
		expect(outOfDate(moved)).toBe(false);
	});

	it('counts what is left out and words it for one or several', () => {
		const stack = reshape(withGenerate(base()), cropOf(0.25, 0.5));
		expect(outOfDateCount(appliedSteps(stack))).toBe(1);
		expect(outOfDateNotice(0)).toBeNull();
		expect(outOfDateNotice(1)).toBe('1 edit is not included: the picture beneath it changed.');
		expect(outOfDateNotice(2)).toBe('2 edits are not included: the picture beneath them changed.');
	});

	it('shows the warning row with Redo and no hide control, and a normal row while it is up to date', () => {
		const stack = reshape(withGenerate(base()), cropOf(0.25, 0.5));
		const steps = appliedSteps(stack);
		const row = imageRowInfo(steps[1], stepsBeneath(steps, 1))!;
		expect(row.state).toEqual({ kind: 'skipped', tone: 'warning', label: 'The picture changed', detail: 'The picture beneath changed' });
		expect(row.actions).toEqual([{ id: 'redo', label: 'Redo on the new picture', icon: 'refresh' }]);
		expect(row.hideToggle).toBe(false);
		expect(row.thumb).toBeUndefined();
		const fresh = withGenerate(base());
		const live = appliedSteps(fresh);
		expect(imageRowInfo(live[1], stepsBeneath(live, 1))!.state).toEqual({ kind: 'done' });
	});

	it('does not touch other step kinds', () => {
		const steps = appliedSteps(push(base(), lookDraft({ kind: 'look', filter: 'reel', name: 'Reel', intensity: 50 })));
		expect(stepOutOfDate(steps[1], stepsBeneath(steps, 1))).toBe(false);
		expect(generateStepKind.shapeBasis!(genParams([]))).toBe(basisFor([], null).geometry);
	});
});

describe('reordering around a generated step', () => {
	it('refuses to drag it across a crop, so the shape beneath cannot be changed that way', () => {
		const stack = withGenerate(push(createStack(), cropOf(0, 0.5)));
		expect(canMove(stack, 's2', 0, IMAGE_PINNED_LANES)).toBe(false);
		expect(move(stack, 's2', 0, IMAGE_PINNED_LANES)).toBe(stack);
	});

	it('marks it out of date when two shaping steps beneath trade places', () => {
		let stack = push(push(createStack(), cropOf(0, 0.5)), resize(250));
		stack = withGenerate(stack);
		expect(outOfDate(stack)).toBe(false);
		const swapped = move(stack, 's2', 0, IMAGE_PINNED_LANES);
		expect(swapped.steps.map((step) => step.id)).toEqual(['s2', 's1', 's3']);
		expect(outOfDate(swapped)).toBe(true);
	});
});

describe('what the preview and Save do with an out of date step', () => {
	function base() {
		return push(createStack(), cropOf(0, 0.5));
	}
	const redrawn = (stack: EditStackState<unknown>) => apply(updatePending(reopen(stack, 's1'), cropOf(0.25, 0.5) as never));

	it('sends it hidden with its results intact and sends it normally while up to date', () => {
		const fresh = appliedSteps(withGenerate(base()));
		const place = (list: EditStep[]) => runSteps(list, { source: SOURCE }).filter((step) => step.tool === 'place_patch');
		expect(place(fresh)).toEqual([{ tool: 'place_patch', params: { patch: { item_id: 'p2' }, x: 0.1, y: 0.1, width: 0.3, height: 0.3 } }]);
		const stale = appliedSteps(redrawn(withGenerate(base())));
		const sent = place(stale);
		expect(sent).toHaveLength(1);
		expect(sent[0]).toMatchObject({ hidden: true, params: { patch: { item_id: 'p2' } } });
		expect(readGenerateParams(stale[1].params)!.results).toHaveLength(2);
	});

	describe('the preview', () => {
		beforeEach(() => {
			calls.decodes = [];
			vi.stubGlobal('document', { createElement: () => fakeCanvas() });
			vi.stubGlobal('ImageData', class {});
		});
		afterEach(() => vi.unstubAllGlobals());

		it('pastes an up to date step and skips an out of date one', async () => {
			const spy = vi.spyOn(registered(), 'preview').mockImplementation(async (frame) => frame);
			const source = { url: '/src.png', size: SOURCE, ref: null };
			await new ImageRenderer(source).render(appliedSteps(withGenerate(base())));
			expect(spy).toHaveBeenCalledTimes(1);
			spy.mockClear();
			await new ImageRenderer(source).render(appliedSteps(redrawn(withGenerate(base()))));
			expect(spy).not.toHaveBeenCalled();
			spy.mockRestore();
		});
	});

	it('keeps its results and basis through a save and a load', () => {
		const stack = redrawn(withGenerate(base()));
		const loaded = deserialize(JSON.parse(JSON.stringify(serialize(stack))));
		const params = readGenerateParams(generateOf(loaded).params)!;
		expect(params.results).toHaveLength(2);
		expect(params.basis).toEqual(readGenerateParams(generateOf(stack).params)!.basis);
		expect(outOfDate(loaded)).toBe(true);
	});
});

describe('which change is a plain resize', () => {
	const params = (stack: EditStackState<unknown>) => readGenerateParams(generateOf(stack).params)!;
	const beneath = (stack: EditStackState<unknown>) => appliedSteps(stack).filter((step) => step.tool !== GENERATE_TOOL);

	it('says none when nothing changed, and resize when only a resize beneath did', () => {
		const stack = withGenerate(push(createStack(), resize(500)));
		expect(shapeChange(params(stack), beneath(stack))).toBe('none');
		const first = stack.steps[0];
		const next = { ...first, params: { ...(first.params as object), width: 400, height: 320 } };
		expect(shapeChange(params(stack), [next])).toBe('resize');
		expect(shapeChange(params(stack), [])).toBe('resize');
	});

	it('allows a crop beside the resize, and anything else is another shape change', () => {
		let stack = push(createStack(), cropOf(0, 0.5));
		stack = push(stack, resize(250));
		stack = withGenerate(stack);
		const [crop, size] = stack.steps;
		const smaller = { ...size, params: { ...(size.params as object), width: 125, height: 100 } };
		expect(shapeChange(params(stack), [crop, smaller])).toBe('resize');
		const moved = { ...crop, params: JSON.parse(JSON.stringify(crop.params).replace('"x":0', '"x":0.25')) };
		expect(shapeChange(params(stack), [moved, size])).toBe('other');
		expect(shapeChange(params(stack), [size])).toBe('other');
		let canvased = push(createStack(), resize(500));
		canvased = withGenerate(canvased);
		const canvas = push(createStack(), catalogDraft('canvas', { width: 900, height: 900 })).steps[0];
		expect(shapeChange(params(canvased), [canvas])).toBe('other');
		const withCanvas = withGenerate(push(push(createStack(), resize(500)), catalogDraft('canvas', { width: 900, height: 900 })));
		const [r, c] = withCanvas.steps;
		expect(shapeChange(params(withCanvas), [{ ...r, params: { ...(r.params as object), width: 300 } }, c])).toBe('other');
	});

	it('says none for a step without a basis', () => {
		const stack = push(createStack(), generateDraft({ ...genParams([]), basis: undefined } as GenerateParams, 'x', 'sparkles') as never);
		expect(shapeChange(params(stack), [])).toBe('none');
	});
});

describe('remapping the marked area with the picture', () => {
	const area = { region: { item_id: 'mask1' }, size: { width: 400, height: 300 } };

	it('scales each side by the change in picture size and rounds to whole pixels', () => {
		expect(remapArea(area, { width: 1000, height: 800 }, { width: 500, height: 400 }).size).toEqual({ width: 200, height: 150 });
		expect(remapArea(area, { width: 1000, height: 800 }, { width: 333, height: 267 }).size).toEqual({ width: 133, height: 100 });
		expect(remapArea(area, { width: 1000, height: 800 }, { width: 2000, height: 1000 }).size).toEqual({ width: 800, height: 375 });
	});

	it('keeps at least one pixel and never grows past the new picture', () => {
		expect(remapArea({ region: null, size: { width: 3, height: 2 } }, { width: 1000, height: 800 }, { width: 100, height: 100 }).size).toEqual({ width: 1, height: 1 });
		expect(remapArea({ region: null, size: { width: 900, height: 700 } }, { width: 1000, height: 800 }, { width: 100, height: 40 }).size).toEqual({ width: 90, height: 35 });
		expect(remapArea({ region: null, size: { width: 1500, height: 100 } }, { width: 1000, height: 800 }, { width: 1000, height: 800 }).size).toEqual({ width: 1000, height: 100 });
	});

	it('leaves the area as it is when a size is not known', () => {
		expect(remapArea(area, null, { width: 10, height: 10 })).toBe(area);
		expect(remapArea({ region: null, size: null }, { width: 1, height: 1 }, { width: 2, height: 2 }).size).toBeNull();
	});
});

const offer = (): TaskOffer => ({ preset_id: 'qwen', preset_name: 'Qwen-Image 2.1', mode: 'editor:edit', from_mode: 'edit', mode_label: 'Edit', label: null, short_description: null, order: 0, region_strategy: 'mask', via: null, qualifies: true, reason: null });
const task = (): EditorTaskInfo => ({ id: 'edit_region', label: 'Edit a region', icon: 'sparkles', region: 'optional', prompt: 'required', fallback: null, offers: [offer()], default: null });
const run = (over: Partial<EditorRun> = {}): EditorRun => ({ id: 'er_1', task: 'edit_region', status: 'queued', edit_id: 'ed', step_id: 's1', generation_id: null, progress: 0, error: null, soft: false, rect: null, candidates: [], ...over });
const schema = { properties: { main: { children: [{ name: 'steps', type: 'number', default: 28 }, { name: 'seed', type: 'seed', default: 7 }] } } };
const finished = run({ status: 'done', rect: { x: 0.1, y: 0.1, width: 0.3, height: 0.3 }, candidates: [1, 2].map((n) => ({ patch: { item_id: `p${n}` }, url: `/p${n}.png`, seed: n })) });

function harness(initial: EditStackState<unknown>) {
	let stack = initial;
	const views = writable<RunViews>({});
	const ports: RunPorts = {
		start: vi.fn(async () => run()),
		get: vi.fn(async () => run({ status: 'running' })),
		cancel: vi.fn(async () => run({ status: 'cancelled' })),
		socket: () => ({ subscribe: () => () => undefined, connected: () => false, onConnection: () => () => undefined, close: () => undefined })
	};
	let controller!: ReturnType<typeof createGenerateController>;
	const engine = createRunEngine(ports, { views, pollMs: 1000, onSettled: (view) => controller.settle(view) });
	const deps: ControllerDeps = {
		getStack: () => stack,
		setStack: (next) => (stack = next),
		source: () => ({ ref: { item_id: 'pic' } }),
		editLink: async () => ({ id: 'ed_1', revision: 3 }),
		landRegion: async () => ({ item_id: 'mask1' }),
		areaSize: () => ({ width: 400, height: 300 }),
		pictureSize: (id) => {
			const list = appliedSteps(stack);
			const at = id ? list.findIndex((step) => step.id === id) : -1;
			return outputSize(at >= 0 ? list.slice(0, at) : list, SOURCE);
		},
		stepsBeneath: async () => [],
		ports: {
			tasks: async () => [task()],
			sessions: async () => [],
			formulas: async () => [],
			schema: async () => schema,
			setDefault: async () => undefined,
			clearDefault: async () => undefined,
			saveSession: async () => ({ id: 'new', name: 'New', version: 1, updated_at: null }),
			updateSession: async () => ({ id: 's1', name: 'Soft portrait', version: 2, updated_at: null })
		},
		engine,
		storage: { getItem: () => null, setItem: () => undefined }
	};
	controller = createGenerateController(deps);
	const start = ports.start as ReturnType<typeof vi.fn>;
	return { controller, start, stack: () => stack, setStack: (next: EditStackState<unknown>) => (stack = next) };
}

async function started(initial: EditStackState<unknown>) {
	const t = harness(initial);
	await t.controller.prepare('edit_region');
	t.controller.use('edit_region');
	t.controller.setPrompt('edit_region', 'a red sailboat');
	await t.controller.generate('edit_region', true);
	t.controller.settle({ stepId: 's2', status: 'done', error: null, run: finished });
	t.controller.choose('s2', 1);
	return t;
}

const generated = (t: { stack(): EditStackState<unknown> }) => readGenerateParams(t.stack().steps.find((step) => step.tool === GENERATE_TOOL)!.params)!;
const reshaped = (stack: EditStackState<unknown>, draft: Parameters<typeof beginEdit>[1]) => apply(updatePending(reopen(stack, 's1'), draft as never));

describe('Redo on the new picture', () => {
	beforeEach(() => vi.useFakeTimers());
	afterEach(() => vi.useRealTimers());

	it('records the basis and the picture size when a run starts', async () => {
		const t = await started(push(createStack(), resize(500)));
		expect(generated(t).basis).toEqual({ geometry: geometryKey(appliedSteps(t.stack()).slice(0, 1)), size: { width: 500, height: 400 } });
	});

	it('after a plain resize starts straight away with the area scaled, the chosen seed, and replaces the results in one undoable change', async () => {
		const t = await started(push(createStack(), resize(500)));
		t.setStack(reshaped(t.stack(), resize(250) as never));
		expect(outOfDate(t.stack())).toBe(true);
		const before = t.stack();
		t.start.mockResolvedValueOnce(run({ id: 'er_2', rect: { x: 0.1, y: 0.1, width: 0.3, height: 0.3 } }));
		const outcome = await t.controller.redo('s2');
		expect(outcome).toEqual({ kind: 'started' });
		const body = t.start.mock.calls[1][1];
		expect(body.region).toEqual({ item_id: 'mask1' });
		expect(body.seed).toBe(2);
		expect(body.prompt).toBe('a red sailboat');
		expect(body.edit.step_id).toBe('s2');
		const params = generated(t);
		expect(t.stack().steps.filter((step) => step.tool === GENERATE_TOOL)).toHaveLength(1);
		expect(params).toMatchObject({ results: [], run: { id: 'er_2', status: 'queued' }, area: { size: { width: 200, height: 150 } } });
		expect(params.basis).toEqual({ geometry: geometryKey(appliedSteps(t.stack()).slice(0, 1)), size: { width: 250, height: 200 } });
		expect(t.stack().revisions!.length).toBe((before.revisions ?? []).length + 1);
		const back = undo(commitLive(t.stack()));
		const old = readGenerateParams(generateOf(back).params)!;
		expect(old.results).toHaveLength(2);
		expect(old.run.id).toBe('er_1');
		expect(old.area.size).toEqual({ width: 400, height: 300 });
	});

	it('after a crop asks for the area first, then a Generate replaces the same step instead of adding one', async () => {
		const t = await started(push(createStack(), cropOf(0, 0.5)));
		t.setStack(reshaped(t.stack(), cropOf(0.25, 0.5) as never));
		const count = t.start.mock.calls.length;
		const outcome = await t.controller.redo('s2');
		expect(outcome).toEqual({ kind: 'area', task: 'edit_region' });
		expect(t.start.mock.calls.length).toBe(count);
		expect(get(t.controller.ui).redo).toMatchObject({ stepId: 's2', taskId: 'edit_region', seed: 2 });
		expect(get(t.controller.ui).prompts.edit_region).toBe('a red sailboat');
		t.controller.setPrompt('edit_region', 'a red sailboat at dusk');
		t.start.mockResolvedValueOnce(run({ id: 'er_2', rect: { x: 0.4, y: 0.4, width: 0.2, height: 0.2 } }));
		const before = t.stack();
		expect(await t.controller.generate('edit_region', true)).toBe(true);
		const body = t.start.mock.calls[count][1];
		expect(body.edit.step_id).toBe('s2');
		expect(body.seed).toBe(2);
		expect(body.prompt).toBe('a red sailboat at dusk');
		expect(body.setup.form_data).toMatchObject({ steps: 28, seed: 7 });
		expect(t.stack().steps).toHaveLength(2);
		const params = generated(t);
		expect(params).toMatchObject({ prompt: 'a red sailboat at dusk', results: [], run: { id: 'er_2' }, rect: { x: 0.4, y: 0.4, width: 0.2, height: 0.2 } });
		expect(params.basis!.geometry).toBe(geometryKey(appliedSteps(t.stack()).slice(0, 1)));
		expect(get(t.controller.ui).redo).toBeNull();
		expect((t.stack().revisions ?? []).length).toBe((before.revisions ?? []).length + 1);
		const back = undo(commitLive(t.stack()));
		expect(readGenerateParams(generateOf(back).params)!.results).toHaveLength(2);
		expect(outOfDate(back)).toBe(true);
	});

	it('forgets the pending Redo when the tool is left or the step is removed', async () => {
		const t = await started(push(createStack(), cropOf(0, 0.5)));
		t.setStack(reshaped(t.stack(), cropOf(0.25, 0.5) as never));
		await t.controller.redo('s2');
		t.controller.use(null);
		expect(get(t.controller.ui).redo).toBeNull();
		await t.controller.redo('s2');
		t.controller.stepRemoved('s2', t.stack());
		expect(get(t.controller.ui).redo).toBeNull();
	});

	it('runs a whole-picture step again straight away after any shape change', async () => {
		const t = harness(push(createStack(), cropOf(0, 0.5)));
		await t.controller.prepare('edit_region');
		t.controller.setPrompt('edit_region', 'a lantern');
		await t.controller.generate('edit_region', false);
		t.controller.settle({ stepId: 's2', status: 'done', error: null, run: finished });
		t.setStack(reshaped(t.stack(), cropOf(0.25, 0.5) as never));
		t.start.mockResolvedValueOnce(run({ id: 'er_2' }));
		expect(await t.controller.redo('s2')).toEqual({ kind: 'started' });
		expect(t.start.mock.calls[1][1].region).toBeNull();
	});

	it('lets a run finish after the shape beneath changed and lands it out of date', async () => {
		const t = harness(push(createStack(), cropOf(0, 0.5)));
		await t.controller.prepare('edit_region');
		t.controller.setPrompt('edit_region', 'a lantern');
		await t.controller.generate('edit_region', true);
		expect(outOfDate(t.stack(), 's2')).toBe(false);
		t.setStack(reshaped(t.stack(), cropOf(0.25, 0.5) as never));
		expect(imageRowInfo(t.stack().steps[1], stepsBeneath(t.stack().steps, 1))!.state).toMatchObject({ kind: 'busy', label: 'Waiting' });
		expect(outOfDate(t.stack(), 's2')).toBe(false);
		t.controller.settle({ stepId: 's2', status: 'done', error: null, run: finished });
		expect(generated(t).results).toHaveLength(2);
		expect(outOfDate(t.stack(), 's2')).toBe(true);
	});

	it('sends a failed step that changed shape through the same Redo path', async () => {
		const t = harness(push(createStack(), cropOf(0, 0.5)));
		await t.controller.prepare('edit_region');
		t.controller.setPrompt('edit_region', 'a lantern');
		await t.controller.generate('edit_region', true);
		t.controller.settle({ stepId: 's2', status: 'failed', error: 'x', run: run({ status: 'failed', error: 'x' }) });
		t.setStack(reshaped(t.stack(), cropOf(0.25, 0.5) as never));
		expect(await t.controller.redo('s2')).toEqual({ kind: 'area', task: 'edit_region' });
	});
});

describe('the summary of a redone step', () => {
	it('is the prompt start while it runs again', () => {
		expect(summaryOf(genParams([], { results: [], run: { id: 'er_2', status: 'queued', error: null } }))).toBe('a red');
	});
});
