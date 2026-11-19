import { get, writable } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DetectionResult, EditorRun, EditorTaskInfo, TaskOffer } from '$lib/services/api/editorTasks';
import { createStack, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { createGenerateController, type ControllerDeps } from './controller';
import { boxPixels, clampStrength, paintBoxes, scoreText, strengthText, tickedBoxes } from './detections';
import { busyLabel, readGenerateParams } from './generateStep';
import { createRunEngine, type RunPorts, type RunViews } from './runEngine';

const offer: TaskOffer = { preset_id: 'sdxl', preset_name: 'SDXL', mode: 'editor:fix_faces', from_mode: 'fix_faces', mode_label: 'Fix faces', label: null, short_description: null, order: 0, region_strategy: 'crop', via: null, qualifies: true, reason: null, strength: { default: 0.35, min: 0.1, max: 0.8 } };
const task = { id: 'fix_faces', label: 'Fix faces and hands', icon: 'mode-refine-faces', region: 'required', prompt: 'none', fallback: null, offers: [offer], default: null } as EditorTaskInfo;
const run = (over: Partial<EditorRun> = {}): EditorRun => ({ id: 'er_1', task: 'fix_faces', status: 'queued', edit_id: 'ed', step_id: 's1', generation_id: null, progress: 0, error: null, soft: false, rect: { x: 0, y: 0, width: 1, height: 1 }, parts: { done: 0, total: 2 }, candidates: [], ...over });
const found: DetectionResult = {
	picture: { width: 1000, height: 800 },
	detections: [
		{ id: 'd1', kind: 'face', box: { x: 0.1, y: 0.1, width: 0.2, height: 0.25 }, score: 0.97 },
		{ id: 'd2', kind: 'hand', box: { x: 0.6, y: 0.5, width: 0.1, height: 0.1 }, score: 0.81 }
	]
};

function setup(detect: ControllerDeps['ports']['detect'] = vi.fn(async () => found)) {
	let stack: EditStackState<unknown> = createStack();
	const views = writable<RunViews>({});
	const enginePorts: RunPorts = {
		start: vi.fn(async () => run()),
		get: vi.fn(async () => run({ status: 'running' })),
		cancel: vi.fn(async () => run({ status: 'cancelled' })),
		socket: () => ({ subscribe: () => () => undefined, connected: () => false, onConnection: () => () => undefined, close: () => undefined })
	};
	const engine = createRunEngine(enginePorts, { views, pollMs: 1000 });
	const landRegion = vi.fn(async () => ({ item_id: 'region-1' }));
	const deps: ControllerDeps = {
		getStack: () => stack,
		setStack: (next) => (stack = next),
		source: () => ({ ref: { item_id: 'pic' } }),
		editLink: vi.fn(async () => ({ id: 'ed_1', revision: 1 })),
		landRegion,
		areaSize: () => null,
		stepsBeneath: vi.fn(async () => [{ tool: 'resize', params: { width: 1000 } }]),
		engine,
		ports: {
			detect,
			tasks: vi.fn(async () => [task]),
			sessions: vi.fn(async () => []),
			formulas: vi.fn(async () => []),
			schema: vi.fn(async () => ({ properties: {} })),
			setDefault: vi.fn(async () => undefined),
			clearDefault: vi.fn(async () => undefined),
			saveSession: vi.fn(async () => ({ id: 'n', name: 'n', version: 1, updated_at: null })),
			updateSession: vi.fn(async () => ({ id: 'n', name: 'n', version: 2, updated_at: null }))
		}
	};
	const controller = createGenerateController(deps);
	return { controller, deps, enginePorts, landRegion, stack: () => stack };
}

async function flush() {
	for (let i = 0; i < 40; i++) await Promise.resolve();
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe('finding faces and hands', () => {
	it('asks for the picture as it stands and ticks everything found', async () => {
		const detect = vi.fn(async () => found);
		const t = setup(detect);
		t.controller.use('fix_faces');
		await flush();
		expect(detect).toHaveBeenCalledWith('fix_faces', { input: { item_id: 'pic' }, steps: [{ tool: 'resize', params: { width: 1000 } }] });
		const state = get(t.controller.ui).detections.fix_faces;
		expect(state.status).toBe('ready');
		expect(state.items.map((item) => [item.id, item.kind, item.on])).toEqual([['d1', 'face', true], ['d2', 'hand', true]]);
		expect(t.controller.boxesFor('fix_faces')).toHaveLength(2);
	});

	it('skips what is unticked and ticks it back', async () => {
		const t = setup();
		t.controller.use('fix_faces');
		await flush();
		t.controller.tickDetection('fix_faces', 'd2', false);
		expect(t.controller.boxesFor('fix_faces')).toEqual([found.detections[0].box]);
		t.controller.tickAllDetections('fix_faces', false);
		expect(t.controller.boxesFor('fix_faces')).toEqual([]);
		t.controller.tickAllDetections('fix_faces', true);
		expect(t.controller.boxesFor('fix_faces')).toHaveLength(2);
		t.controller.hoverDetection('fix_faces', 'd1');
		expect(get(t.controller.ui).detections.fix_faces.hover).toBe('d1');
	});

	it('shows an unavailable detector as its own state and a failure as a failure', async () => {
		const gone = setup(vi.fn(async () => Promise.reject({ response: { status: 503, data: { detail: { error: 'detector_unavailable' } } } })));
		gone.controller.use('fix_faces');
		await flush();
		expect(get(gone.controller.ui).detections.fix_faces).toMatchObject({ status: 'unavailable', items: [] });
		const broken = setup(vi.fn(async () => Promise.reject({ response: { status: 500, data: {} } })));
		broken.controller.use('fix_faces');
		await flush();
		expect(get(broken.controller.ui).detections.fix_faces.status).toBe('failed');
	});

	it('finds again on request and leaves other tasks alone', async () => {
		const detect = vi.fn(async () => found);
		const t = setup(detect);
		t.controller.use('fix_faces');
		await flush();
		await t.controller.detect('fix_faces');
		expect(detect).toHaveBeenCalledTimes(2);
		await t.controller.detect('edit_region');
		expect(detect).toHaveBeenCalledTimes(2);
	});
});

describe('a Fix faces run', () => {
	it('sends the union of ticked boxes with the strength and no prompt, and stores the settings on a layer step', async () => {
		const t = setup();
		t.controller.use('fix_faces');
		await t.controller.prepare('fix_faces');
		await flush();
		t.controller.tickDetection('fix_faces', 'd2', false);
		t.controller.setStrength('fix_faces', 0.5);
		expect(await t.controller.generate('fix_faces', false)).toBe(true);
		expect(t.landRegion).toHaveBeenCalledWith(false, [found.detections[0].box]);
		const [id, body] = (t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[0];
		expect(id).toBe('fix_faces');
		expect(body).toMatchObject({ region: { item_id: 'region-1' }, prompt: null, strength: 0.5 });
		const step = t.stack().steps[0];
		expect(step).toMatchObject({ tool: 'generate', lane: 'paint', label: 'Fix faces and hands' });
		expect(readGenerateParams(step.params)?.options).toEqual({ strength: 0.5 });
	});

	it('clears the found boxes when the run lands and finds them again on request', async () => {
		const t = setup();
		t.controller.use('fix_faces');
		await t.controller.prepare('fix_faces');
		await flush();
		await t.controller.generate('fix_faces', false);
		await flush();
		expect(get(t.controller.ui).detections.fix_faces.status).toBe('ready');
		t.controller.settle({ stepId: 's1', status: 'done', error: null, run: run({ status: 'done', progress: 1, candidates: [{ patch: { item_id: 'p1' }, url: '/p1.png', seed: 1 }] }) });
		expect(get(t.controller.ui).detections.fix_faces).toMatchObject({ status: 'idle', items: [] });
		expect(t.controller.boxesFor('fix_faces')).toEqual([]);
		await t.controller.detect('fix_faces');
		expect(get(t.controller.ui).detections.fix_faces.items).toHaveLength(2);
	});

	it('runs on hand marks alone with no boxes ticked', async () => {
		const t = setup();
		t.controller.use('fix_faces');
		await t.controller.prepare('fix_faces');
		await flush();
		t.controller.tickAllDetections('fix_faces', false);
		expect(await t.controller.generate('fix_faces', true)).toBe(true);
		expect(t.landRegion).toHaveBeenCalledWith(false);
	});

	it('refuses with nothing ticked and nothing marked', async () => {
		const t = setup();
		t.controller.use('fix_faces');
		await t.controller.prepare('fix_faces');
		await flush();
		t.controller.tickAllDetections('fix_faces', false);
		expect(await t.controller.generate('fix_faces', false)).toBe(false);
		expect(get(t.controller.ui).error).toBe('Mark the area first.');
		expect(t.enginePorts.start).not.toHaveBeenCalled();
	});

	it('works when the detector is unavailable and an area is marked by hand', async () => {
		const t = setup(vi.fn(async () => Promise.reject({ response: { status: 503, data: { detail: { error: 'detector_unavailable' } } } })));
		t.controller.use('fix_faces');
		await t.controller.prepare('fix_faces');
		await flush();
		expect(await t.controller.generate('fix_faces', true)).toBe(true);
		expect((t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[0][1]).not.toHaveProperty('strength');
	});

	it('exposes the strength of the chosen setup', async () => {
		const t = setup();
		t.controller.use('fix_faces');
		await t.controller.prepare('fix_faces');
		expect(t.controller.offerFor('fix_faces')?.strength).toEqual({ default: 0.35, min: 0.1, max: 0.8 });
		expect(t.controller.strengthOf('fix_faces')).toBeUndefined();
	});
});

describe('faces helpers', () => {
	it('ticks only what is on and only when ready', () => {
		const items = found.detections.map((item, i) => ({ ...item, on: i === 0 }));
		expect(tickedBoxes({ status: 'ready', picture: null, items, hover: null, error: null })).toEqual([found.detections[0].box]);
		expect(tickedBoxes({ status: 'loading', picture: null, items, hover: null, error: null })).toEqual([]);
		expect(tickedBoxes(undefined)).toEqual([]);
	});

	it('turns a box into whole pixels inside the picture and paints it', () => {
		expect(boxPixels({ x: 0.1, y: 0.2, width: 0.5, height: 0.5 }, { width: 100, height: 50 })).toEqual({ x: 10, y: 10, width: 50, height: 25 });
		expect(boxPixels({ x: 0.9, y: 0.9, width: 0.5, height: 0.5 }, { width: 100, height: 50 })).toEqual({ x: 90, y: 45, width: 10, height: 5 });
		const fills: number[][] = [];
		paintBoxes({ fillStyle: '', fillRect: (...rect) => fills.push(rect) }, [{ x: 0, y: 0, width: 0.5, height: 1 }], { width: 10, height: 4 });
		expect(fills).toEqual([[0, 0, 5, 4]]);
	});

	it('formats and clamps', () => {
		expect(scoreText(0.973)).toBe('97%');
		expect(strengthText(0.355)).toBe('0.36');
		expect(clampStrength(2, { default: 0.35, min: 0.1, max: 0.8 })).toBe(0.8);
		expect(clampStrength(Number.NaN, { default: 0.35, min: 0.1, max: 0.8 })).toBe(0.35);
	});

	it('words the busy label for parts', () => {
		expect(busyLabel('fix_faces', { done: 1, total: 3 })).toBe('Fixing 2 of 3');
		expect(busyLabel('fix_faces', null)).toBe('Fixing');
	});
});
