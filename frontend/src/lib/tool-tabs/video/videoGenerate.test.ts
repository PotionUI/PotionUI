import { get, writable } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { railGroups, railTools, railFor } from '$lib/editors/railTools';
import { tasks } from '$lib/editors/tasks';
import { normalizeRun, normalizeTask, type EditorRun, type EditorTaskInfo } from '$lib/services/api/editorTasks';
import { appliedSteps, createStack, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { createGenerateController, type ControllerDeps } from '../image/generate/controller';
import { generateSteps, rangeText, readGenerateParams } from '../image/generate/generateStep';
import { GENERATE_GROUP, syncVideoTasks, videoTaskInfos } from '../image/generate/register';
import { createRunEngine, runViews, type RunPorts, type RunViews } from '../image/generate/runEngine';
import { toRunRange, type AiRange } from './aiRange';
import { basisBeneath, rebase } from './generateChain';

const ORIGINAL = { item_id: 'orig' };

function offer(mode: string, over: Record<string, unknown> = {}) {
	return { preset_id: 'ltx', preset_name: 'LTX-2.5', mode, from_mode: mode.replace('editor:', ''), mode_label: 'Restore', label: null, short_description: null, order: 0, region_strategy: 'whole', quantity_field: 'quantity', via: null, qualifies: true, reason: null, ...over };
}

function raw(id: string, over: Record<string, unknown> = {}) {
	return { id, label: id, icon: `mode-${id}`, editors: ['video'], region: 'none', prompt: 'optional', range: 'optional', result: 'replace', audio: 'keep', resizes: false, fallback: null, offers: [offer(`editor:${id}`)], default: null, ...over };
}

const info = (id: string, over: Record<string, unknown> = {}) => normalizeTask(raw(id, over)) as EditorTaskInfo;
const schema = { properties: { main: { children: [{ name: 'quantity', type: 'stepper', default: 1 }, { name: 'duration', type: 'slider', default: 5 }, { name: 'seed', type: 'seed', default: 7 }] } } };

function doneRun(over: Partial<EditorRun> = {}): EditorRun {
	return normalizeRun({
		id: 'er_1',
		task: 'restore',
		status: 'done',
		edit_id: 'video-tab',
		step_id: 's1',
		progress: 1,
		range: { start_s: 2, end_s: 4, whole: false },
		candidates: [
			{ clip: { item_id: 'clip_a' }, url: '/uploads/a.mp4', seed: 11, generation_file_id: 'gf_a', range: { start_s: 2, end_s: 4 }, duration_s: 6.4 },
			{ clip: { item_id: 'clip_b' }, url: '/uploads/b.mp4', seed: 12, generation_file_id: 'gf_b', range: { start_s: 2, end_s: 4 }, duration_s: 6.4 }
		],
		...over
	}) as EditorRun;
}

function setup(infos: EditorTaskInfo[], marked: AiRange | null = { start: 2, end: 4 }, extra: Partial<ControllerDeps> = {}) {
	let stack: EditStackState<unknown> = createStack();
	let range: AiRange | null = marked;
	const views = writable<RunViews>({});
	const enginePorts: RunPorts = {
		start: vi.fn(async () => doneRun()),
		get: vi.fn(async () => doneRun()),
		cancel: vi.fn(async () => doneRun({ status: 'cancelled' })),
		socket: () => ({ subscribe: () => () => undefined, connected: () => false, onConnection: () => () => undefined, close: () => undefined })
	};
	let controller!: ReturnType<typeof createGenerateController>;
	const engine = createRunEngine(enginePorts, { views, pollMs: 1000, onSettled: (view) => controller.settle(view) });
	const ports = {
		tasks: vi.fn(async () => infos),
		sessions: vi.fn(async () => []),
		formulas: vi.fn(async () => []),
		schema: vi.fn(async () => schema),
		setDefault: vi.fn(async () => undefined),
		clearDefault: vi.fn(async () => undefined),
		saveSession: vi.fn(async () => ({ id: 'new', name: 'New', version: 1, updated_at: null })),
		updateSession: vi.fn(async () => ({ id: 's1', name: 'S', version: 2, updated_at: null }))
	};
	const deps: ControllerDeps = {
		getStack: () => stack,
		setStack: (next) => (stack = next),
		source: () => ({ ref: ORIGINAL }),
		editLink: async () => ({ id: 'video-tab', revision: 1 }),
		landRegion: vi.fn(async () => null),
		areaSize: () => null,
		stepsBeneath: vi.fn(async () => [{ tool: 'speed', params: { speed: 2 } }]),
		range: () => toRunRange(range, 6.4, []),
		inputFor: (id) => basisBeneath(appliedSteps(stack), ORIGINAL, id).input,
		basisOf: (id) => basisBeneath(appliedSteps(stack), ORIGINAL, id).basis,
		editor: 'video',
		ports,
		engine,
		...extra
	};
	controller = createGenerateController(deps);
	return { controller, deps, ports, enginePorts, engine, stack: () => stack, mark: (next: AiRange | null) => (range = next) };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
	vi.useRealTimers();
	runViews.set({});
	syncVideoTasks([]);
});

describe('the video task tools on the rail', () => {
	it('reads the editors, the range need, the result kind and the audio rule from the endpoint', () => {
		const extend = info('extend', { result: 'append', audio: 'result' });
		expect(extend.editors).toEqual(['video']);
		expect(extend.range).toBe('optional');
		expect(extend.result).toBe('append');
		expect(extend.audio).toBe('result');
		expect(info('upscale', { resizes: true }).resizes).toBe(true);
		expect(normalizeTask({ id: 'edit_region', label: 'x', offers: [] })?.editors).toBeUndefined();
	});

	it('shows one tool per task the instance offers, and none for a task without an editor mode to run', () => {
		const all = [info('extend'), info('upscale'), info('colorize', { offers: [] }), info('relight', { offers: [offer('editor:relight', { qualifies: false })] }), info('restore', { editors: ['image'] })];
		expect(videoTaskInfos(all).map((entry) => entry.id)).toEqual(['extend', 'upscale']);
		syncVideoTasks(all);
		const list = railTools.list('video').filter((tool) => tool.task);
		expect(list.map((tool) => tool.task)).toEqual(['extend', 'upscale']);
		expect(list.every((tool) => tool.group === GENERATE_GROUP && tool.ai === true)).toBe(true);
		expect(list.map((tool) => tool.icon)).toEqual(['mode-extend', 'mode-upscale']);
		expect(tasks.get('video', 'extend')).toMatchObject({ range: 'optional', result: 'replace', prompt: 'optional', region: 'none' });
	});

	it('puts the Generate group first on the rail and drops a tool when the server stops offering it', () => {
		syncVideoTasks([info('extend'), info('upscale')]);
		const group = railGroups.list('video').find((entry) => entry.id === GENERATE_GROUP);
		expect(group?.label).toBe('Generate');
		const rail = railFor('video', { editor: 'video', hasSource: true });
		expect(rail[0].id).toBe(GENERATE_GROUP);
		syncVideoTasks([info('upscale')]);
		expect(railTools.get('video', 'extend')).toBeUndefined();
		expect(railTools.get('video', 'upscale')?.task).toBe('upscale');
	});
});

describe('a video run', () => {
	it('sends the marked range in seconds with the setup, the count and the clip, and no region', async () => {
		const t = setup([info('restore')]);
		await t.controller.prepare('restore');
		t.controller.setPrompt('restore', 'clean the grain');
		expect(await t.controller.generate('restore', false)).toBe(true);
		const [task, body] = (t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[0];
		expect(task).toBe('restore');
		expect(body).toMatchObject({
			edit: { id: 'video-tab', step_id: 's1' },
			input: { item_id: 'orig' },
			steps: [{ tool: 'speed', params: { speed: 2 } }],
			region: null,
			range: { start_s: 2, end_s: 4 },
			prompt: 'clean the grain',
			setup: { preset_id: 'ltx', mode: 'editor:restore' },
			candidates: 1
		});
		expect(t.deps.landRegion).not.toHaveBeenCalled();
	});

	it('sends no range for the whole clip, and no prompt for a task that takes none', async () => {
		const t = setup([info('upscale', { prompt: 'none' })], null);
		await t.controller.prepare('upscale');
		expect(await t.controller.generate('upscale', false)).toBe(true);
		const body = (t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[0][1];
		expect(body.range).toBeNull();
		expect(body.prompt).toBeNull();
	});

	it('asks nothing of a task that takes no range', async () => {
		const t = setup([info('restore', { range: 'none' })]);
		await t.controller.prepare('restore');
		await t.controller.generate('restore', false);
		expect((t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[0][1].range).toBeNull();
	});

	it('extends: the range end is where the new time goes and the seconds field reaches the setup', async () => {
		const t = setup([info('extend', { result: 'append', audio: 'result' })], { start: 0, end: 4 });
		await t.controller.prepare('extend');
		await t.controller.settings('extend');
		t.controller.tweak('extend', { quantity: 1, duration: 5, seed: 7 });
		t.controller.tweak('extend', { quantity: 2, duration: 8, seed: 7 });
		t.controller.setPrompt('extend', 'the road turns toward the sea');
		await t.controller.generate('extend', false);
		const [task, body] = (t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[0];
		expect(task).toBe('extend');
		expect(body.range).toEqual({ start_s: 0, end_s: 4 });
		expect(body.setup.form_data.duration).toBe(8);
		expect(body.candidates).toBe(2);
		expect(body.prompt).toBe('the road turns toward the sea');
	});

	it('extends the whole clip when nothing is marked', async () => {
		const t = setup([info('extend', { result: 'append' })], null);
		await t.controller.prepare('extend');
		await t.controller.generate('extend', false);
		expect((t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[0][1].range).toBeNull();
	});

	it('says so when the server refuses the range', async () => {
		const t = setup([info('restore')]);
		(t.enginePorts.start as ReturnType<typeof vi.fn>).mockRejectedValueOnce({ response: { status: 422, data: { detail: { error: 'range_outside_clip', message: 'The range ends (9s) past the end of the clip (6.400s)' } } } });
		await t.controller.prepare('restore');
		expect(await t.controller.generate('restore', false)).toBe(false);
		expect(get(t.controller.ui).error).toBe('The range ends (9s) past the end of the clip (6.400s)');
		expect(t.stack().steps).toHaveLength(0);
	});
});

describe('a settled candidate as a step', () => {
	it('becomes a generate step carrying the clips, the range and a basis, and rebases the clip', async () => {
		const t = setup([info('restore')]);
		await t.controller.prepare('restore');
		expect(await t.controller.generate('restore', false)).toBe(true);
		const [entry] = generateSteps(t.stack());
		expect(entry.step.label).toBe('restore');
		expect(entry.step.summary).toBe('1 of 2 · 00:02.00 → 00:04.00');
		expect(entry.params.range).toEqual({ start_s: 2, end_s: 4 });
		expect(entry.params.results.map((result) => [result.patch.item_id, result.url, result.clip, result.duration_s])).toEqual([
			['clip_a', '/uploads/a.mp4', true, 6.4],
			['clip_b', '/uploads/b.mp4', true, 6.4]
		]);
		expect(rangeText(entry.params.range)).toBe('00:02.00 → 00:04.00');
		const found = rebase(appliedSteps(t.stack()), ORIGINAL);
		expect(found.input).toEqual({ item_id: 'clip_a' });
		expect(found.url).toBe('/uploads/a.mp4');
		expect(found.stale.size).toBe(0);
	});

	it('takes the clips of a run that arrives over the socket as the server sent it', async () => {
		const t = setup([info('restore')]);
		(t.enginePorts.start as ReturnType<typeof vi.fn>).mockResolvedValueOnce(doneRun({ status: 'running', candidates: [] }));
		await t.controller.prepare('restore');
		await t.controller.generate('restore', false);
		const socketRun = { id: 'er_1', status: 'done', candidates: [{ clip: { item_id: 'clip_w' }, url: '/uploads/w.mp4', seed: 3, range: { start_s: 2, end_s: 4 }, added: { at_s: 4, seconds: 5 }, duration_s: 11.4 }] };
		t.controller.settle({ stepId: 's1', status: 'done', error: null, run: socketRun as never });
		const [entry] = generateSteps(t.stack());
		expect(entry.params.results).toEqual([{ patch: { item_id: 'clip_w' }, url: '/uploads/w.mp4', seed: 3, clip: true, range: { start_s: 2, end_s: 4 }, added: { at_s: 4, seconds: 5 }, duration_s: 11.4 }]);
		expect(rebase(appliedSteps(t.stack()), ORIGINAL).url).toBe('/uploads/w.mp4');
	});

	it('plays the result picked in the strip', async () => {
		const t = setup([info('restore')]);
		await t.controller.prepare('restore');
		await t.controller.generate('restore', false);
		const id = generateSteps(t.stack())[0].step.id;
		t.controller.choose(id, 1);
		expect(rebase(appliedSteps(t.stack()), ORIGINAL).url).toBe('/uploads/b.mp4');
		expect(generateSteps(t.stack())[0].params.seed).toBe(12);
	});

	it('goes out of date when a step beneath it changes, and the redo runs on the new clip beneath', async () => {
		const t = setup([info('restore')]);
		await t.controller.prepare('restore');
		await t.controller.generate('restore', false);
		const id = generateSteps(t.stack())[0].step.id;
		const trimmed = { id: 'sx', tool: 'trim', icon: '', label: 'Trim', summary: '', params: { start: 0, end: 3 }, hidden: false };
		const stack = t.stack();
		const before = { ...stack, steps: [trimmed, ...stack.steps], cursor: stack.cursor + 1 };
		expect(rebase(appliedSteps(before), ORIGINAL).stale.has(id)).toBe(true);
		expect(rebase(appliedSteps(before), ORIGINAL).input).toEqual(ORIGINAL);
		t.deps.setStack(before);
		const again = await t.controller.redo(id);
		expect(again.kind).toBe('started');
		const body = (t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls.at(-1)![1];
		expect(body.range).toEqual({ start_s: 2, end_s: 4 });
		expect(body.input).toEqual(ORIGINAL);
		expect(readGenerateParams(generateSteps(t.stack())[0].step.params)?.basis?.geometry).toContain('"trim"');
	});

	it('keeps an unfinished run out of the clip until it settles', async () => {
		const t = setup([info('restore')]);
		(t.enginePorts.start as ReturnType<typeof vi.fn>).mockResolvedValueOnce(doneRun({ status: 'running', candidates: [] }));
		await t.controller.prepare('restore');
		await t.controller.generate('restore', false);
		const [entry] = generateSteps(t.stack());
		expect(entry.params.run.status).toBe('running');
		expect(rebase(appliedSteps(t.stack()), ORIGINAL).base).toBeNull();
		expect(rebase(appliedSteps(t.stack()), ORIGINAL).left.has(entry.step.id)).toBe(true);
	});
});
