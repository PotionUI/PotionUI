import { get } from 'svelte/store';
import { afterEach, describe, expect, it } from 'vitest';
import type { EditorRun } from '$lib/services/api/editorTasks';
import { apply, beginEdit, canUndo, commitLive, createStack, deserialize, liveEdit, serialize, undo, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { runViews } from './runEngine';
import legacy from './fixtures/generateStepV1.json';
import {
	GENERATE_TOOL,
	generateDraft,
	generateStateOf,
	generateStepKind,
	isEditorKey,
	leftOutCount,
	migrateParams,
	leftOutNotice,
	readGenerateParams,
	replaceStepParams,
	setupModeOf,
	stepsToResume,
	summaryOf,
	withChosen,
	withFreshRun,
	withRun,
	type GenerateParams
} from './generateStep';

function params(over: Partial<GenerateParams> = {}): GenerateParams {
	return {
		v: 2,
		task: 'edit_region',
		prompt: 'a small red sailboat, one white sail, calm water',
		setup: { preset_id: 'qwen', mode: 'editor:edit', form_data: { steps: 28 }, source: { kind: 'session', id: 's1', name: 'Soft portrait' }, label: 'Qwen-Image 2.1 · Edit · Soft portrait' },
		area: { region: { item_id: 'mask1' }, size: { width: 412, height: 380 } },
		candidates: 3,
		results: [],
		chosen: 0,
		seed: null,
		rect: null,
		soft: false,
		run: { id: 'er_1', status: 'queued', error: null },
		...over
	};
}

const run = (over: Partial<EditorRun> = {}): EditorRun => ({
	id: 'er_1', task: 'edit_region', status: 'done', edit_id: 'ed', step_id: 's1', generation_id: 'g', progress: 1, error: null, soft: false,
	rect: { x: 0.3, y: 0.2, width: 0.3, height: 0.4 },
	candidates: [1, 2, 3].map((n) => ({ patch: { item_id: `p${n}` }, url: `/p${n}.png`, seed: 100 + n })),
	...over
});

const step = (p: GenerateParams, hidden = false) => ({ id: 's1', tool: GENERATE_TOOL, icon: 'sparkles', label: 'Edit a region', summary: summaryOf(p), params: p, hidden, lane: 'paint' });

afterEach(() => runViews.set({}));

describe('generate step state', () => {
	it('maps each run status to a row state', () => {
		expect(generateStepKind.state!(step(params()), {} as never)).toEqual({ kind: 'busy', label: 'Waiting' });
		runViews.set({ er_1: { id: 'er_1', stepId: 's1', status: 'running', progress: 0.4, preview: null, run: null, error: null } });
		expect(generateStateOf(params())).toEqual({ kind: 'busy', label: 'Generating', progress: 40 });
		expect(generateStateOf(params({ run: { id: 'er_1', status: 'done', error: null } }))).toEqual({ kind: 'busy', label: 'Generating', progress: 40 });
		runViews.set({});
		expect(generateStateOf(params({ run: { id: 'er_1', status: 'done', error: null } }))).toEqual({ kind: 'done' });
		expect(generateStateOf(params({ run: { id: 'er_1', status: 'failed', error: 'timed out' } }))).toEqual({ kind: 'skipped', tone: 'danger', label: 'Did not finish: timed out' });
		expect(generateStateOf(params({ run: { id: 'er_1', status: 'cancelled', error: null } }))).toEqual({ kind: 'skipped', tone: 'muted', label: 'Cancelled' });
	});

	it('prefers the live view over the stored status', () => {
		runViews.set({ er_1: { id: 'er_1', stepId: 's1', status: 'failed', progress: 0, preview: null, run: null, error: 'out of memory' } });
		expect(generateStateOf(params({ run: { id: 'er_1', status: 'running', error: null } }))).toEqual({ kind: 'skipped', tone: 'danger', label: 'Did not finish: out of memory' });
	});

	it('offers the row actions of each state', () => {
		const extras = (p: GenerateParams) => generateStepKind.extras!(step(p), generateStateOf(p));
		expect(extras(params()).actions?.map((a) => a.id)).toEqual(['cancel']);
		expect(extras(params()).hideToggle).toBe(false);
		expect(extras(params({ run: { id: 'er_1', status: 'failed', error: null } })).actions?.map((a) => a.id)).toEqual(['retry']);
		const done = withRun(params(), run());
		expect(extras(done)).toMatchObject({ actions: [{ id: 'again' }], thumb: '/p1.png' });
	});
});

describe('generate step results', () => {
	it('stores every result, the chosen one and its seed when the run is done', () => {
		const done = withRun(params(), run());
		expect(done.results).toHaveLength(3);
		expect(done.run.status).toBe('done');
		expect(done.seed).toBe(101);
		expect(done.rect).toEqual({ x: 0.3, y: 0.2, width: 0.3, height: 0.4 });
		expect(summaryOf(withChosen(done, 1))).toBe('2 of 3 · a small red');
		expect(withChosen(done, 1).seed).toBe(102);
		expect(withChosen(done, 9)).toBe(done);
	});

	it('words a remove row without a prompt as just the count', () => {
		expect(summaryOf(withRun(params({ prompt: '' }), run()))).toBe('1 of 3');
	});

	it('keeps no results for a failed run and clears them for a fresh one', () => {
		expect(withRun(params(), run({ status: 'failed', error: 'x', candidates: [] })).results).toEqual([]);
		const fresh = withFreshRun(withRun(params(), run()), { id: 'er_2', status: 'queued' });
		expect(fresh).toMatchObject({ results: [], chosen: 0, seed: null, run: { id: 'er_2', status: 'queued', error: null } });
	});

	it('pastes the chosen result at its place when saving, and sends nothing before that', () => {
		const ctx = { editor: 'image', input: null, beneath: [], catalog: [], step: step(params()) } as never;
		expect(generateStepKind.payload(params(), ctx)).toEqual([{ tool: 'place_patch', params: {}, hidden: true }]);
		const done = withChosen(withRun(params(), run()), 2);
		expect(generateStepKind.payload(done, ctx)).toEqual([{ tool: 'place_patch', params: { patch: { item_id: 'p3' }, x: 0.3, y: 0.2, width: 0.3, height: 0.4 } }]);
		expect(generateStepKind.payload({ junk: true } as never, ctx)).toEqual([{ tool: 'place_patch', params: {}, hidden: true }]);
	});

	it('leaves the picture unchanged by size and sits in the paint lane', () => {
		const ctx = { editor: 'image', input: { width: 10, height: 20 }, beneath: [], catalog: [], step: step(params()) } as never;
		expect(generateStepKind.outputSize!(params(), ctx)).toEqual({ width: 10, height: 20 });
		expect(generateStepKind.lane).toBe('paint');
		expect(generateDraft(params(), 'Edit a region', 'sparkles').lane).toBe('paint');
	});

	it('opens the tool of its task when the row is clicked', () => {
		const wanted = generateStepKind.reopenTool as (value: unknown) => string | null;
		expect(wanted(params({ task: 'remove_object' }))).toBe('remove_object');
		expect(wanted({})).toBeNull();
	});
});

describe('generate step in the stack', () => {
	function stackWith(p: GenerateParams): EditStackState<unknown> {
		return apply(beginEdit(createStack(), generateDraft(p, 'Edit a region', 'sparkles')));
	}

	it('switches the chosen result as one undoable change', () => {
		const done = withRun(params(), run());
		let stack = stackWith(done);
		const before = stack.steps[0];
		const next = withChosen(done, 2);
		stack = commitLive(liveEdit(stack, generateDraft(next, 'Edit a region', 'sparkles'), (s) => s.id === 's1'));
		expect((stack.steps[0].params as GenerateParams).chosen).toBe(2);
		expect(canUndo(stack)).toBe(true);
		stack = undo(stack);
		expect(stack.steps[0].params).toEqual(before.params);
		expect(stack.cursor).toBe(1);
	});

	it('settles a run into its step without making an undo step', () => {
		const stack = stackWith(params());
		const settled = replaceStepParams(stack, 's1', (p) => withRun(p, run()));
		expect((settled.steps[0].params as GenerateParams).results).toHaveLength(3);
		expect(settled.steps[0].summary).toContain('1 of 3');
		expect(settled.revisions ?? []).toEqual(stack.revisions ?? []);
		expect(replaceStepParams(stack, 's1', (p) => p)).toBe(stack);
		expect(replaceStepParams(stack, 'nope', (p) => withRun(p, run()))).toBe(stack);
	});

	it('round-trips every state through serialize and deserialize', () => {
		const states: GenerateParams[] = [
			params(),
			params({ run: { id: 'er_1', status: 'running', error: null } }),
			withRun(params(), run()),
			params({ run: { id: 'er_1', status: 'failed', error: 'timed out' } }),
			params({ run: { id: 'er_1', status: 'cancelled', error: null } })
		];
		for (const p of states) {
			const stack = stackWith(p);
			const back = deserialize(JSON.parse(JSON.stringify(serialize(stack))));
			expect(readGenerateParams(back.steps[0].params)).toEqual(p);
			expect(back.steps[0].tool).toBe('generate');
		}
	});

	it('loads a stack that has no generate steps unchanged', () => {
		const stack = apply(beginEdit(createStack(), { tool: 'crop', icon: 'editor-crop', label: 'Crop', summary: '3:2', params: { aspect: 'free' }, lane: 'geometry' }));
		const back = deserialize(JSON.parse(JSON.stringify(serialize(stack))));
		expect(back.steps).toEqual(stack.steps);
		expect(stepsToResume(back)).toEqual([]);
	});

	it('finds the steps whose run has to be re-attached after a reload', () => {
		let stack = stackWith(params({ run: { id: 'er_1', status: 'running', error: null } }));
		stack = apply(beginEdit(stack, generateDraft(withRun(params({ run: { id: 'er_2', status: 'queued', error: null } }), run({ id: 'er_2' })), 'Edit a region', 'sparkles')));
		expect(stepsToResume(stack)).toEqual([{ id: 's1', runId: 'er_1', status: 'running' }]);
	});
});

describe('save notice', () => {
	it('counts the generating steps that are left out and words them', () => {
		const flying = step(params({ run: { id: 'er_1', status: 'running', error: null } }));
		const done = step(withRun(params(), run()));
		const failed = step(params({ run: { id: 'er_1', status: 'failed', error: 'x' } }));
		expect(leftOutCount([done, failed])).toBe(0);
		expect(leftOutCount([flying, done, failed, step(params(), true)])).toBe(1);
		expect(leftOutNotice(0)).toBeNull();
		expect(leftOutNotice(1)).toBe('1 edit is not included: it is still generating.');
		expect(leftOutNotice(2)).toBe('2 edits are not included: they are still generating.');
	});

	it('reads garbage as no params', () => {
		expect(readGenerateParams(null)).toBeNull();
		expect(readGenerateParams({ run: { id: 'x', status: 'weird' } })).toBeNull();
		expect(get(runViews)).toEqual({});
	});
});

describe('a step saved before editor modes', () => {
	const offers = [
		{ preset_id: 'qwen', preset_name: 'Qwen', mode: 'editor:edit', from_mode: 'edit', mode_label: 'Edit', label: null, short_description: null, order: 0, region_strategy: 'mask', via: null, qualifies: true, reason: null },
		{ preset_id: 'flux', preset_name: 'Flux', mode: 'editor:fill', from_mode: 'inpaint', mode_label: 'Fill', label: null, short_description: null, order: 0, region_strategy: 'crop', via: null, qualifies: true, reason: null }
	];

	it('reads as version 1, keeps its results and drops the variant', () => {
		const read = readGenerateParams(legacy)!;
		expect(read.v).toBe(1);
		expect(read.setup.mode).toBe('edit');
		expect(read.setup).not.toHaveProperty('variant');
		expect(read.results).toHaveLength(1);
		expect(read.run).toEqual({ id: 'er_old', status: 'done', error: null });
	});

	it('moves to version 2 with the editor mode derived from its generation mode', () => {
		const next = migrateParams(readGenerateParams(legacy)!, offers)!;
		expect(next.v).toBe(2);
		expect(next.setup).toMatchObject({ preset_id: 'qwen', mode: 'editor:edit', form_data: { steps: 20, seed: 7 }, source: { kind: 'session', id: 's1' } });
		expect(next.results).toEqual(readGenerateParams(legacy)!.results);
	});

	it('does not touch the stored step when it is only read', () => {
		const raw = structuredClone(legacy);
		readGenerateParams(raw);
		migrateParams(readGenerateParams(raw)!, offers);
		expect(raw).toEqual(legacy);
	});

	it('resolves a mode named after the editor mode when no mode derives from it', () => {
		const standalone = [{ ...offers[0], from_mode: null }];
		expect(setupModeOf(readGenerateParams(legacy)!, standalone)).toBe('editor:edit');
	});

	it('is unresolvable when no offer derives from the mode, or two do', () => {
		expect(migrateParams(readGenerateParams({ ...legacy, setup: { ...legacy.setup, mode: 'txt2img' } })!, offers)).toBeNull();
		expect(migrateParams(readGenerateParams(legacy)!, [])).toBeNull();
		expect(migrateParams(readGenerateParams(legacy)!, [offers[0], { ...offers[0], mode: 'editor:edit2' }])).toBeNull();
	});

	it('leaves a step that already carries an editor key alone', () => {
		const current = params();
		expect(migrateParams(current, [])).toEqual(current);
		expect(isEditorKey(current.setup.mode)).toBe(true);
	});
});
