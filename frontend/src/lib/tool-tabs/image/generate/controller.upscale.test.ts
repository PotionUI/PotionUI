import { get, writable } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EditorRun, EditorTaskInfo, TaskOffer } from '$lib/services/api/editorTasks';
import { createStack, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { createGenerateController, type ControllerDeps } from './controller';
import { readGenerateParams } from './generateStep';
import { createRunEngine, type RunPorts, type RunViews } from './runEngine';

const offer: TaskOffer = { preset_id: 'upres', preset_name: 'Upres', mode: 'editor:upscale', from_mode: 'upscale', mode_label: 'Upscale', label: null, short_description: null, order: 0, region_strategy: 'whole', via: null, qualifies: true, reason: null };
const task: EditorTaskInfo = { id: 'upscale', label: 'Upscale', icon: 'mode-upscale', region: 'none', prompt: 'none', resizes: true, editors: ['image'], fallback: null, offers: [offer], default: null } as EditorTaskInfo;
const run = (over: Partial<EditorRun> = {}): EditorRun => ({ id: 'er_1', task: 'upscale', status: 'queued', edit_id: 'ed', step_id: 's1', generation_id: null, progress: 0, error: null, soft: false, rect: null, candidates: [], ...over });
const schema = { properties: { main: { children: [{ name: 'seed', type: 'seed', default: 7 }] } } };
const done = (size = { width: 2048, height: 1536 }) => run({ status: 'done', progress: 1, parts: { done: 4, total: 4 }, size, candidates: [{ patch: { item_id: 'patch-1' }, url: '/patch-1.png', seed: 3, size }] });

function setup(picture = { width: 1024, height: 768 }) {
	let stack: EditStackState<unknown> = createStack();
	const views = writable<RunViews>({});
	const enginePorts: RunPorts = {
		start: vi.fn(async () => run()),
		get: vi.fn(async () => run({ status: 'running' })),
		cancel: vi.fn(async () => run({ status: 'cancelled' })),
		socket: () => ({ subscribe: () => () => undefined, connected: () => false, onConnection: () => () => undefined, close: () => undefined })
	};
	let controller!: ReturnType<typeof createGenerateController>;
	const engine = createRunEngine(enginePorts, { views, pollMs: 1000, onSettled: (view) => controller.settle(view) });
	const deps: ControllerDeps = {
		getStack: () => stack,
		setStack: (next) => (stack = next),
		source: () => ({ ref: { item_id: 'pic' } }),
		editLink: vi.fn(async () => ({ id: 'ed_1', revision: 3 })),
		landRegion: vi.fn(async () => null),
		areaSize: () => null,
		pictureSize: () => picture,
		stepsBeneath: vi.fn(async () => []),
		engine,
		ports: {
			tasks: vi.fn(async () => [task]),
			sessions: vi.fn(async () => []),
			formulas: vi.fn(async () => []),
			schema: vi.fn(async () => schema),
			setDefault: vi.fn(async () => undefined),
			clearDefault: vi.fn(async () => undefined),
			saveSession: vi.fn(async () => ({ id: 'n', name: 'n', version: 1, updated_at: null })),
			updateSession: vi.fn(async () => ({ id: 'n', name: 'n', version: 2, updated_at: null }))
		}
	};
	controller = createGenerateController(deps);
	return { controller, enginePorts, engine, stack: () => stack, setStack: (next: EditStackState<unknown>) => (stack = next) };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe('upscale runs', () => {
	it('starts a 2x run by default with no prompt and no region, and lands a geometry step', async () => {
		const t = setup();
		await t.controller.prepare('upscale');
		expect(await t.controller.generate('upscale', false)).toBe(true);
		const [id, body] = (t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[0];
		expect(id).toBe('upscale');
		expect(body).toMatchObject({ scale: 2, prompt: null, region: null, candidates: 1 });
		expect(body.target).toBeUndefined();
		const step = t.stack().steps[0];
		expect(step).toMatchObject({ tool: 'generate', lane: 'geometry', label: 'Upscale', summary: '2×' });
		expect(readGenerateParams(step.params)?.options).toEqual({ scale: 2 });
	});

	it('sends the chosen target instead of a factor', async () => {
		const t = setup();
		await t.controller.prepare('upscale');
		t.controller.setOptions('upscale', { target: { width: 3000, height: 2250 } });
		await t.controller.generate('upscale', false);
		const body = (t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[0][1];
		expect(body.target).toEqual({ width: 3000, height: 2250 });
		expect(body.scale).toBeUndefined();
		expect(t.stack().steps[0].summary).toBe('Custom size');
	});

	it('refuses a size that is not larger without calling the server', async () => {
		const t = setup();
		await t.controller.prepare('upscale');
		t.controller.setOptions('upscale', { target: { width: 800, height: 600 } });
		expect(await t.controller.generate('upscale', false)).toBe(false);
		expect(get(t.controller.ui).error).toBe('Pick a size larger than the picture.');
		expect(t.enginePorts.start).not.toHaveBeenCalled();
		expect(t.stack().steps).toHaveLength(0);
	});

	it('refuses an oversized request', async () => {
		const t = setup({ width: 9000, height: 9000 });
		await t.controller.prepare('upscale');
		expect(await t.controller.generate('upscale', false)).toBe(false);
		expect(get(t.controller.ui).error).toContain('too large');
	});

	it('shows the server refusal in plain words', async () => {
		const t = setup();
		(t.enginePorts.start as ReturnType<typeof vi.fn>).mockRejectedValueOnce({ response: { status: 422, data: { detail: { error: 'upscale_too_large' } } } });
		await t.controller.prepare('upscale');
		expect(await t.controller.generate('upscale', false)).toBe(false);
		expect(get(t.controller.ui).error).toBe('That size is too large. Pick a smaller factor or size.');
	});

	it('records the size when the run finishes, scales what sits above, and keeps the options for Try again', async () => {
		const t = setup();
		await t.controller.prepare('upscale');
		await t.controller.generate('upscale', false);
		const above = { id: 's2', tool: 'resize', icon: 'x', label: 'Resize', summary: '500×400 · Lanczos', params: { width: 500, height: 400, lock: false }, hidden: false };
		t.setStack({ ...t.stack(), steps: [...t.stack().steps, above], cursor: 2, seq: 2 });
		(t.enginePorts.get as ReturnType<typeof vi.fn>).mockResolvedValue(done());
		await vi.advanceTimersByTimeAsync(1100);
		const params = readGenerateParams(t.stack().steps[0].params)!;
		expect(params.run.status).toBe('done');
		expect(params.results[0].size).toEqual({ width: 2048, height: 1536 });
		expect(params.out).toEqual({ width: 2048, height: 1536 });
		expect(params.options).toEqual({ scale: 2 });
		expect(t.stack().steps[1].params).toEqual({ width: 1000, height: 800, lock: false });
		expect(t.stack().steps[0].summary).toBe('2× · 2048×1536');

		(t.enginePorts.start as ReturnType<typeof vi.fn>).mockResolvedValueOnce(run({ id: 'er_2' }));
		expect(await t.controller.tryAgain('s1')).toBe(true);
		expect((t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[1][1]).toMatchObject({ scale: 2 });
		expect(readGenerateParams(t.stack().steps[0].params)?.out).toEqual({ width: 2048, height: 1536 });
	});
});
