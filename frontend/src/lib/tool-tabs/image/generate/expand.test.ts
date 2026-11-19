import { get } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EditorRun, EditorTaskInfo, ExpandFormat, TaskOffer } from '$lib/services/api/editorTasks';
import { apply, beginEdit, canMove, createStack, type EditStackState, type StepDraft } from '$lib/tool-tabs/frame/editStack';
import { isLayerStep } from '../layers';
import { createGenerateController, type ControllerDeps } from './controller';
import {
	CUSTOM_ID,
	MAX_PICKS,
	anchorFor,
	containSize,
	expandUi,
	passesFor,
	placement,
	retarget,
	targetFor,
	targetsOf,
	togglePick,
	type ExpandUi
} from './expand';
import { generateDraft, generateStepKind, patchPlacement, readGenerateParams, type GenerateParams } from './generateStep';
import { IMAGE_PINNED_LANES } from '../steps';
import { createRunEngine, type RunPorts, type RunViews } from './runEngine';
import { writable } from 'svelte/store';

const FORMATS: ExpandFormat[] = [
	{ id: 'square', label: 'Square', ratio: '1:1', source: 'built_in', group_id: null },
	{ id: 'portrait', label: 'Portrait', ratio: '4:5', source: 'built_in', group_id: null },
	{ id: 'story', label: 'Story', ratio: '9:16', source: 'built_in', group_id: null },
	{ id: 'landscape', label: 'Landscape', ratio: '16:9', source: 'built_in', group_id: null },
	{ id: 'wide_banner', label: 'Wide banner', ratio: '3:1', source: 'built_in', group_id: null },
	{ id: 'house.team', label: 'Team banner', ratio: '5:2', source: 'house', group_id: 'marketing' }
];

function ui(picks: string[], over: Partial<ExpandUi> = {}): ExpandUi {
	return { formats: FORMATS, loaded: true, picks, custom: { width: 1024, height: 1024 }, anchor: { x: 0.5, y: 0.5 }, source: { width: 1024, height: 1024 }, ...over };
}

describe('expand planning', () => {
	it('puts a square picture on the smallest canvas of each shape', () => {
		expect(containSize({ width: 1024, height: 1024 }, [9, 16])).toEqual({ width: 1024, height: 1821 });
		expect(containSize({ width: 1024, height: 1024 }, [16, 9])).toEqual({ width: 1821, height: 1024 });
		expect(containSize({ width: 1000, height: 500 }, [3, 1])).toEqual({ width: 1500, height: 500 });
		expect(containSize({ width: 500, height: 1000 }, [3, 1])).toEqual({ width: 3000, height: 1000 });
	});

	it('centres the picture by default and follows the anchor', () => {
		const source = { width: 1024, height: 1024 };
		expect(placement(source, { width: 1024, height: 1821 }, { x: 0.5, y: 0.5 })).toEqual({ x: 0, y: 399 });
		expect(placement(source, { width: 1024, height: 1821 }, { x: 0, y: 0 })).toEqual({ x: 0, y: 0 });
		expect(placement(source, { width: 1024, height: 1821 }, { x: 1, y: 1 })).toEqual({ x: 0, y: 797 });
		expect(anchorFor(source, { width: 1024, height: 1821 }, { x: 0, y: 797 })).toEqual({ x: 0.5, y: 1 });
		expect(anchorFor(source, { width: 1024, height: 1024 }, { x: 0, y: 0 })).toEqual({ x: 0.5, y: 0.5 });
	});

	it('plans three formats from one source and skips a format the picture already has', () => {
		const targets = targetsOf(ui(['story', 'landscape', 'wide_banner', 'square']));

		expect(targets.map((target) => [target.id, target.width, target.height])).toEqual([
			['story', 1024, 1821],
			['landscape', 1821, 1024],
			['wide_banner', 3072, 1024]
		]);
		expect(targets[2]).toMatchObject({ x: 1024, y: 0, label: 'Wide banner', ratio: [3, 1] });
	});

	it('takes a custom size at least as large as the picture and refuses an oversized canvas', () => {
		const custom = targetsOf(ui([CUSTOM_ID], { custom: { width: 800, height: 2000 } }))[0];
		expect(custom).toMatchObject({ id: CUSTOM_ID, width: 1024, height: 2000, ratio: null });
		expect(targetsOf(ui([CUSTOM_ID], { custom: { width: 9000, height: 1024 } }))).toEqual([]);
		expect(targetFor({ width: 6000, height: 6000 }, 'x', 'X', [2, 1], { width: 0, height: 0 }, { x: 0.5, y: 0.5 })).toBeNull();
	});

	it('plans nothing without a picture or a format', () => {
		expect(targetsOf(ui([], { source: { width: 100, height: 100 } }))).toEqual([]);
		expect(targetsOf(ui(['story'], { source: null }))).toEqual([]);
		expect(targetsOf(ui(['unknown']))).toEqual([]);
	});

	it('counts the passes a large expansion takes', () => {
		const square = { width: 1024, height: 1024 };
		expect(passesFor(square, { width: 1024, height: 1821 })).toBe(1);
		expect(passesFor(square, { width: 1821, height: 1024 })).toBe(1);
		expect(passesFor(square, { width: 3072, height: 1024 })).toBe(2);
		expect(passesFor(square, { width: 6000, height: 1024 })).toBeGreaterThan(2);
	});

	it('limits the picks and toggles them', () => {
		let state = ui([]);
		for (const format of FORMATS) state = togglePick(state, format.id);
		state = togglePick(state, CUSTOM_ID);
		expect(state.picks).toHaveLength(MAX_PICKS);
		expect(togglePick(state, 'story').picks).not.toContain('story');
	});

	it('moves a finished step to the new shape when the picture changed size', () => {
		const target = targetFor({ width: 1024, height: 1024 }, 'story', 'Story', [9, 16], { width: 0, height: 0 }, { x: 0.5, y: 0.5 })!;
		const next = retarget({ width: target.width, height: target.height, x: target.x, y: target.y, label: 'Story', ratio: [9, 16], anchor: { x: 0.5, y: 0.5 } }, { width: 512, height: 512 });
		expect(next).toMatchObject({ width: 512, height: 911, label: 'Story' });
		expect(retarget({ ...target, label: 'Story', ratio: [1, 1], anchor: target.anchor }, { width: 512, height: 512 })).toBeNull();
	});
});

const expandParams = (over: Partial<GenerateParams> = {}): GenerateParams => ({
	v: 2,
	task: 'expand',
	prompt: '',
	setup: { preset_id: 'zimage', mode: 'editor:expand', form_data: {}, source: { kind: 'defaults', id: null, name: null }, label: '' },
	area: { region: null, size: null },
	candidates: 1,
	results: [{ patch: { item_id: 'patch1' }, url: '/p.png', seed: 4 }],
	chosen: 0,
	seed: 4,
	rect: { x: 0, y: 0, width: 1, height: 1 },
	soft: false,
	run: { id: 'er_1', status: 'done', error: null },
	expand: { width: 1024, height: 1821, x: 0, y: 399, label: 'Story', ratio: [9, 16], anchor: { x: 0.5, y: 0.5 } },
	...over
});

describe('the expand step', () => {
	it('survives a save and load, and old steps without it read as before', () => {
		const params = expandParams();
		const again = readGenerateParams(JSON.parse(JSON.stringify(params)));
		expect(again?.expand).toEqual(params.expand);
		expect(readGenerateParams({ ...params, expand: undefined })?.expand).toBeUndefined();
		expect(readGenerateParams({ ...params, expand: { width: 0, height: 5, x: 0, y: 0 } })?.expand).toBeUndefined();
	});

	it('changes the canvas size once it landed and asks the server to place the picture on it', () => {
		const input = { width: 1024, height: 1024 };
		const ctx = { input } as never;
		expect(generateStepKind.outputSize!(expandParams(), ctx)).toEqual({ width: 1024, height: 1821 });
		expect(generateStepKind.outputSize!(expandParams({ run: { id: 'er_1', status: 'running', error: null } }), ctx)).toEqual(input);
		expect(generateStepKind.payload(expandParams(), ctx)).toEqual([
			{ tool: 'place_patch', params: { patch: { item_id: 'patch1' }, x: 0, y: 0, width: 1, height: 1, canvas_width: 1024, canvas_height: 1821, canvas_x: 0, canvas_y: 399 } }
		]);
		expect(generateStepKind.payload(expandParams({ run: { id: 'er_1', status: 'failed', error: 'no' } }), ctx)).toEqual([{ tool: 'place_patch', params: {}, hidden: true }]);
	});

	it('is a geometry step, not a layer or a patch on the picture', () => {
		const draft = generateDraft(expandParams(), 'Expand', 'expand');
		expect(draft.lane).toBe('geometry');
		expect(draft.summary).toBe('Story · 1024×1821');
		expect(patchPlacement(expandParams())).toBeNull();
		expect(isLayerStep({ tool: 'generate', params: expandParams() })).toBe(false);
		expect(isLayerStep({ tool: 'generate', params: expandParams({ expand: undefined }) })).toBe(true);
		expect(generateDraft(expandParams({ expand: undefined }), 'Edit', 'edit').lane).toBe('paint');
	});

	it('is a barrier: painted layers cannot move across it', () => {
		const paint: StepDraft<unknown> = { tool: 'paint', icon: 'brush', label: 'Paint', summary: '', params: {}, lane: 'paint' };
		let stack: EditStackState<unknown> = createStack();
		stack = apply(beginEdit(stack, paint));
		stack = apply(beginEdit(stack, generateDraft(expandParams(), 'Expand', 'expand')));
		stack = apply(beginEdit(stack, { ...paint, label: 'Paint 2' }));
		const [first, expand, last] = stack.steps;

		expect(canMove(stack, first.id, 1, IMAGE_PINNED_LANES)).toBe(false);
		expect(canMove(stack, expand.id, 0, IMAGE_PINNED_LANES)).toBe(false);
		expect(canMove(stack, expand.id, 2, IMAGE_PINNED_LANES)).toBe(false);
		expect(canMove(stack, last.id, 1, IMAGE_PINNED_LANES)).toBe(false);
	});
});

const offer = (): TaskOffer => ({ preset_id: 'zimage', preset_name: 'Z-Image', mode: 'editor:expand', from_mode: 'inpaint', mode_label: 'Expand', label: null, short_description: null, order: 0, region_strategy: 'mask', via: null, qualifies: true, reason: null });
const expandTask = (): EditorTaskInfo => ({ id: 'expand', label: 'Expand', icon: 'expand', region: 'none', prompt: 'optional', grows: true, fallback: { task: 'edit_region', prompt: 'x' }, offers: [offer()], default: null });
const run = (id: string, over: Partial<EditorRun> = {}): EditorRun => ({ id, task: 'expand', status: 'queued', edit_id: 'ed', step_id: 's1', generation_id: null, progress: 0, error: null, soft: false, rect: { x: 0, y: 0, width: 1, height: 1 }, candidates: [], ...over });

function harness(extra: Partial<ControllerDeps> = {}) {
	let stack: EditStackState<unknown> = createStack();
	let counter = 0;
	const views = writable<RunViews>({});
	const start = vi.fn(async () => run(`er_${++counter}`));
	const enginePorts: RunPorts = {
		start,
		get: vi.fn(async () => run('er_1', { status: 'running' })),
		cancel: vi.fn(async () => run('er_1', { status: 'cancelled' })),
		socket: () => ({ subscribe: () => () => undefined, connected: () => false, onConnection: () => () => undefined, close: () => undefined })
	};
	let controller!: ReturnType<typeof createGenerateController>;
	const engine = createRunEngine(enginePorts, { views, pollMs: 1000, onSettled: (view) => controller.settle(view) });
	const spawnEdit = vi.fn(async (_base: EditStackState<unknown>, name: string) => ({ id: `ed_${name}`, revision: 1 }));
	const saveSpawned = vi.fn(async () => true);
	const deps: ControllerDeps = {
		getStack: () => stack,
		setStack: (next) => (stack = next),
		source: () => ({ ref: { item_id: 'pic' } }),
		editLink: vi.fn(async () => ({ id: 'ed_main', revision: 3 })),
		landRegion: vi.fn(async () => null),
		areaSize: () => null,
		pictureSize: () => ({ width: 1024, height: 1024 }),
		stepsBeneath: vi.fn(async () => []),
		spawnEdit,
		saveSpawned,
		ports: {
			tasks: vi.fn(async () => [expandTask()]),
			sessions: vi.fn(async () => []),
			formulas: vi.fn(async () => []),
			schema: vi.fn(async () => ({ properties: { main: { children: [{ name: 'seed', type: 'seed', default: 7 }] } } })),
			setDefault: vi.fn(async () => undefined),
			clearDefault: vi.fn(async () => undefined),
			saveSession: vi.fn(async () => ({ id: 'n', name: 'N', version: 1, updated_at: null })),
			updateSession: vi.fn(async () => ({ id: 'n', name: 'N', version: 1, updated_at: null }))
		},
		engine,
		...extra
	};
	controller = createGenerateController(deps);
	return { controller, start, spawnEdit, saveSpawned, stack: () => stack };
}

describe('generating several formats at once', () => {
	beforeEach(() => vi.useFakeTimers());
	afterEach(() => vi.useRealTimers());

	it('runs the first format in this edit and every other format in an edit of its own', async () => {
		const t = harness();
		expandUi.set(ui(['story', 'landscape', 'wide_banner']));
		await t.controller.prepare('expand');

		expect(await t.controller.generate('expand', false)).toBe(true);

		const bodies = t.start.mock.calls.map((call) => (call as unknown as [string, { edit: { id: string }; expand: { width: number; height: number; x: number; y: number }; region: unknown; candidates: number; prompt: string | null }])[1]);
		expect(bodies.map((body) => [body.edit.id, body.expand.width, body.expand.height])).toEqual([
			['ed_main', 1024, 1821],
			['ed_Landscape', 1821, 1024],
			['ed_Wide banner', 3072, 1024]
		]);
		expect(bodies.every((body) => body.region === null && body.candidates === 1 && body.prompt === null)).toBe(true);
		expect(t.stack().steps).toHaveLength(1);
		expect(t.stack().steps[0].lane).toBe('geometry');
		expect(readGenerateParams(t.stack().steps[0].params)?.expand).toMatchObject({ label: 'Story', width: 1024, height: 1821 });
		expect(t.spawnEdit).toHaveBeenCalledTimes(2);
		expect(get(t.controller.ui).spawned).toEqual(['Landscape', 'Wide banner']);
		const saved = (t.saveSpawned.mock.calls as unknown as Array<[{ id: string }, EditStackState<unknown>]>).map(([link, stack]) => [link.id, stack.steps.length, readGenerateParams(stack.steps[0].params)?.expand?.label]);
		expect(saved).toEqual([
			['ed_Landscape', 1, 'Landscape'],
			['ed_Wide banner', 1, 'Wide banner']
		]);
	});

	it('sends the prompt as typed and nothing when it is empty', async () => {
		const t = harness();
		expandUi.set(ui(['story']));
		await t.controller.prepare('expand');
		t.controller.setPrompt('expand', '  a quiet beach ');

		await t.controller.generate('expand', false);

		expect((t.start.mock.calls[0] as unknown as [string, { prompt: string }])[1].prompt).toBe('a quiet beach');
	});

	it('asks for a format when none is chosen and starts nothing', async () => {
		const t = harness();
		expandUi.set(ui([]));
		await t.controller.prepare('expand');

		expect(await t.controller.generate('expand', false)).toBe(false);

		expect(get(t.controller.ui).error).toBe('Choose a format first.');
		expect(t.start).not.toHaveBeenCalled();
	});

	it('says which formats could not be started and keeps the ones that did', async () => {
		const t = harness({ spawnEdit: vi.fn(async () => null) });
		expandUi.set(ui(['story', 'landscape']));
		await t.controller.prepare('expand');

		expect(await t.controller.generate('expand', false)).toBe(true);

		expect(t.stack().steps).toHaveLength(1);
		expect(get(t.controller.ui).spawned).toEqual([]);
		expect(get(t.controller.ui).error).toBe('Landscape could not be started. Try them again.');
	});
});
