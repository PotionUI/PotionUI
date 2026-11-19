import { get, writable } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EditorRun, EditorTaskInfo, TaskOffer } from '$lib/services/api/editorTasks';
import { apply, beginEdit, createStack, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import { createGenerateController, type ControllerDeps } from './controller';
import { generateDraft, readGenerateParams, type GenerateParams } from './generateStep';
import legacy from './fixtures/generateStepV1.json';
import { createRunEngine, type RunPorts, type RunViews } from './runEngine';

const offer = (over: Partial<TaskOffer> = {}): TaskOffer => ({ preset_id: 'qwen', preset_name: 'Qwen-Image 2.1', mode: 'editor:edit', from_mode: 'edit', mode_label: 'Edit', label: null, short_description: null, order: 0, region_strategy: 'mask', via: null, qualifies: true, reason: null, ...over });
const task = (over: Partial<EditorTaskInfo> = {}): EditorTaskInfo => ({ id: 'edit_region', label: 'Edit a region', icon: 'sparkles', region: 'optional', prompt: 'required', fallback: null, offers: [offer()], default: null, ...over });
const run = (over: Partial<EditorRun> = {}): EditorRun => ({ id: 'er_1', task: 'edit_region', status: 'queued', edit_id: 'ed', step_id: 's1', generation_id: null, progress: 0, error: null, soft: false, rect: null, candidates: [], ...over });
const schema = { properties: { main: { children: [{ name: 'steps', type: 'number', default: 28 }, { name: 'seed', type: 'seed', default: 7 }] } } };

function setup(tasks: EditorTaskInfo[] = [task()], extra: Partial<ControllerDeps> = {}, saved = false) {
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
	const ports = {
		tasks: vi.fn(async () => tasks),
		sessions: vi.fn(async () => (saved ? [{ id: 's1', name: 'Soft portrait', updated_at: null, data: { 'editor:edit': { formData: { steps: 20 } } } }] : [])),
		formulas: vi.fn(async () => []),
		schema: vi.fn(async () => schema),
		setDefault: vi.fn(async () => undefined),
		clearDefault: vi.fn(async () => undefined),
		saveSession: vi.fn(async () => ({ id: 'new', name: 'New', version: 1, updated_at: null })),
		updateSession: vi.fn(async () => ({ id: 's1', name: 'Soft portrait', version: 2, updated_at: null }))
	};
	const deps: ControllerDeps = {
		getStack: () => stack,
		setStack: (next) => (stack = next),
		source: () => ({ ref: { item_id: 'pic' } }),
		editLink: vi.fn(async () => ({ id: 'ed_1', revision: 3 })),
		landRegion: vi.fn(async () => ({ item_id: 'mask1' })),
		areaSize: () => ({ width: 412, height: 380 }),
		stepsBeneath: vi.fn(async () => []),
		ports,
		engine,
		...extra
	};
	controller = createGenerateController(deps);
	return { controller, deps, ports, enginePorts, engine, views, stack: () => stack };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

async function ready(t: ReturnType<typeof setup>, prompt = 'a red sailboat') {
	await t.controller.prepare('edit_region');
	t.controller.setPrompt('edit_region', prompt);
}

describe('generate controller: starting', () => {
	it('runs a task with a single qualifying mode on its defaults and creates the step', async () => {
		const t = setup();
		await ready(t);
		expect(get(t.controller.ui).choice.edit_region).toMatchObject({ setup: { kind: 'defaults' } });
		expect(get(t.controller.ui).picker.open).toBe(false);
		expect(await t.controller.generate('edit_region', true)).toBe(true);
		const body = (t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[0];
		expect(body[0]).toBe('edit_region');
		expect(body[1]).toMatchObject({ edit: { id: 'ed_1', step_id: 's1' }, input: { item_id: 'pic' }, region: { item_id: 'mask1' }, prompt: 'a red sailboat', candidates: 1, seed: null, setup: { preset_id: 'qwen', mode: 'editor:edit', form_data: { steps: 28, seed: 7 } } });
		const step = t.stack().steps[0];
		expect(step).toMatchObject({ tool: 'generate', label: 'Edit a region', lane: 'paint' });
		const params = readGenerateParams(step.params) as GenerateParams;
		expect(params.run).toEqual({ id: 'er_1', status: 'queued', error: null });
		expect(params.setup.source.kind).toBe('defaults');
		expect(params.candidates).toBe(1);
		expect(get(t.controller.ui).activeStepId).toBe('s1');
		expect(t.engine.tracking('er_1')).toBe(true);
	});

	it('sends the whole picture when nothing is marked and no prompt for a promptless task', async () => {
		const remove = task({ id: 'remove_object', label: 'Remove an object', region: 'required', prompt: 'none' });
		const t = setup([remove]);
		await t.controller.prepare('remove_object');
		expect(await t.controller.generate('remove_object', false)).toBe(false);
		expect(get(t.controller.ui).error).toBe('Mark the area first.');
		expect(t.enginePorts.start).not.toHaveBeenCalled();
		expect(await t.controller.generate('remove_object', true)).toBe(true);
		expect((t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[0][1]).toMatchObject({ prompt: null, region: { item_id: 'mask1' } });

		const free = setup();
		await ready(free);
		await free.controller.generate('edit_region', false);
		expect((free.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[0][1].region).toBeNull();
	});

	it('does not start without a prompt for a task that needs one', async () => {
		const t = setup();
		await ready(t, '  ');
		expect(await t.controller.generate('edit_region', true)).toBe(false);
		expect(get(t.controller.ui).error).toBe('Describe the change first.');
	});

	it('says so when the edit could not be saved first and starts nothing', async () => {
		const t = setup([task()], { editLink: vi.fn(async () => null) });
		await ready(t);
		expect(await t.controller.generate('edit_region', true)).toBe(false);
		expect(get(t.controller.ui).error).toContain('not saved yet');
		expect(t.enginePorts.start).not.toHaveBeenCalled();
		expect(t.stack().steps).toHaveLength(0);
	});

	it('shows a refusal in plain words and creates no step', async () => {
		const t = setup();
		(t.enginePorts.start as ReturnType<typeof vi.fn>).mockRejectedValueOnce({ response: { status: 422, data: { detail: { error: 'task_not_offered' } } } });
		await ready(t);
		expect(await t.controller.generate('edit_region', true)).toBe(false);
		expect(get(t.controller.ui).error).toBe('This setup cannot do that any more. Pick another.');
		expect(t.stack().steps).toHaveLength(0);
		expect(get(t.controller.ui).starting).toBe(false);
	});

	it('sends the count from the form field the offer names, defaults it like the form, and clamps it', async () => {
		const withCount = { properties: { main: { children: [{ name: 'steps', type: 'number', default: 28 }, { name: 'quantity', type: 'stepper', default: 1 }] } } };
		const t = setup([task({ offers: [offer({ quantity_field: 'quantity' })] })]);
		t.ports.schema.mockResolvedValue(withCount as never);
		await ready(t);
		expect(await t.controller.generate('edit_region', true)).toBe(true);
		let body = (t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[0][1];
		expect(body.candidates).toBe(1);
		expect(body.setup.form_data.quantity).toBe(1);
		await t.controller.settings('edit_region');
		t.controller.tweak('edit_region', { steps: 28, quantity: 1 });
		t.controller.tweak('edit_region', { steps: 28, quantity: 9 });
		t.engine.release('er_1');
		expect(await t.controller.generate('edit_region', true)).toBe(true);
		body = (t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[1][1];
		expect(body.candidates).toBe(4);
		expect(body.setup.form_data.quantity).toBe(9);
		expect(get(t.controller.ui)).not.toHaveProperty('results');
	});
});

describe('generate controller: formulas applied to the run', () => {
	const formulaApplication = (id: string, before: Record<string, unknown>, after: Record<string, unknown>, skipped = 0) => ({
		formData: { ...before, ...after },
		applied: {
			formulaId: id,
			name: `Formula ${id}`,
			key: 'k',
			changed: Object.fromEntries(Object.keys(after).map((field) => [field, { old: before[field], new: after[field], label: field }])),
			skipped,
			snapshot: { values: Object.fromEntries(Object.keys(after).filter((field) => field in before).map((field) => [field, before[field]])), absent: Object.keys(after).filter((field) => !(field in before)) }
		}
	});

	async function applied() {
		const t = setup();
		await ready(t);
		const first = formulaApplication('f1', { steps: 28, seed: 7 }, { steps: 8 }, 3);
		await t.controller.applyFormula('edit_region', first);
		return t;
	}

	it('sends the formula values in the run and lists the formula with what it skipped', async () => {
		const t = await applied();
		expect(get(t.controller.ui).formulas.edit_region).toMatchObject([{ id: 'f1', changed: 1, skipped: 3 }]);
		expect(t.controller.changedFor('edit_region')).toBe(true);
		expect(await t.controller.generate('edit_region', true)).toBe(true);
		expect((t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[0][1].setup.form_data).toEqual({ steps: 8, seed: 7 });
	});

	it('stacks a second formula, then removes one and keeps the other', async () => {
		const t = await applied();
		await t.controller.applyFormula('edit_region', formulaApplication('f2', { steps: 8, seed: 7 }, { seed: 99 }));
		expect(get(t.controller.ui).formulas.edit_region?.map((entry) => entry.id)).toEqual(['f1', 'f2']);
		t.controller.removeAppliedFormula('edit_region', 'f1');
		expect(get(t.controller.ui).formulas.edit_region?.map((entry) => entry.id)).toEqual(['f2']);
		expect(get(t.controller.ui).tweaks.edit_region?.values).toEqual({ steps: 28, seed: 99 });
		t.controller.removeAppliedFormula('edit_region', 'f2');
		expect(get(t.controller.ui).formulas.edit_region).toEqual([]);
		expect(t.controller.changedFor('edit_region')).toBe(false);
		expect(await t.controller.generate('edit_region', true)).toBe(true);
		expect((t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[0][1].setup.form_data).toEqual({ steps: 28, seed: 7 });
	});

	it('forgets the applied formulas when another setup is picked', async () => {
		const t = await applied();
		await t.controller.pick('edit_region', { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'formula', id: 'other' } });
		expect(get(t.controller.ui).formulas.edit_region).toBeUndefined();
	});
});

describe('generate controller: setups', () => {
	it('preselects the one preset that qualifies even when it has saved setups, and asks when several presets qualify', async () => {
		const saved = setup([task()], {}, true);
		await saved.controller.prepare('edit_region');
		expect(get(saved.controller.ui).picker.open).toBe(false);
		expect(get(saved.controller.ui).choice.edit_region).toEqual({ preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'defaults' } });
		const two = setup([task({ offers: [offer(), offer({ preset_id: 'flux', preset_name: 'Flux Fill', mode: 'editor:fill' })] })]);
		await two.controller.prepare('edit_region');
		expect(get(two.controller.ui).picker).toMatchObject({ open: true, why: 'first_use' });
		expect(get(two.controller.ui).choice.edit_region).toBeNull();
		expect(two.controller.chipFor('edit_region')).toBeNull();
	});

	it('never substitutes a broken default and names what went away', async () => {
		const broken = task({ default: { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'session', id: 'gone' }, health: { ok: false, reason: 'session_deleted', name: 'Soft portrait', mode_label: 'Edit' } } });
		const t = setup([broken]);
		await t.controller.prepare('edit_region');
		const ui = get(t.controller.ui);
		expect(ui.choice.edit_region).toBeNull();
		expect(ui.picker).toEqual({ open: false, why: 'broken_default', notice: 'Your default setup "Soft portrait" was deleted. Pick another.' });
	});

	it('uses a healthy default and a setup picked from the list', async () => {
		const healthy = task({ offers: [offer(), offer({ mode: 'editor:other', mode_label: 'Other' })], default: { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'session', id: 's1' }, health: { ok: true, reason: null, name: null, mode_label: null } } });
		const t = setup([healthy], {}, true);
		await t.controller.prepare('edit_region');
		expect(t.controller.chipFor('edit_region')).toBe('Qwen-Image 2.1 · Edit · Soft portrait');
		await t.controller.pick('edit_region', t.controller.defaultsChoice(healthy.offers[1]));
		expect(t.controller.chipFor('edit_region')).toBe('Qwen-Image 2.1 · Other · Model defaults');
		expect(t.ports.setDefault).not.toHaveBeenCalled();
	});

	it('stores a default only when asked to and then treats it as healthy', async () => {
		const t = setup([task({ offers: [offer(), offer({ mode: 'editor:other' })] })]);
		await t.controller.prepare('edit_region');
		const choice = { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'session' as const, id: 's1' } };
		await t.controller.pick('edit_region', choice);
		expect(t.ports.setDefault).not.toHaveBeenCalled();
		expect(t.controller.isDefault('edit_region')).toBe(false);
		await t.controller.setAsDefault('edit_region', true);
		expect(t.ports.setDefault).toHaveBeenCalledWith('edit_region', choice);
		expect(t.controller.isDefault('edit_region')).toBe(true);
		expect(get(t.controller.ui).tasks[0].default?.health.ok).toBe(true);
		await t.controller.setAsDefault('edit_region', false);
		expect(t.ports.clearDefault).toHaveBeenCalled();
		expect(get(t.controller.ui).tasks[0].default).toBeNull();
	});

	it('stores the values it resolved, not a reference to the session', async () => {
		const t = setup([task({ offers: [offer(), offer({ mode: 'editor:other' })] })], {}, true);
		await t.controller.prepare('edit_region');
		await t.controller.pick('edit_region', { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'session', id: 's1' } });
		t.controller.setPrompt('edit_region', 'a boat');
		await t.controller.generate('edit_region', true);
		const params = readGenerateParams(t.stack().steps[0].params) as GenerateParams;
		expect(params.setup.form_data).toMatchObject({ steps: 20, seed: 7 });
		expect(params.setup.source).toEqual({ kind: 'session', id: 's1', name: 'Soft portrait' });
	});
});

describe('generate controller: a step through its life', () => {
	async function withStep() {
		const t = setup();
		await ready(t);
		await t.controller.generate('edit_region', true);
		return t;
	}
	const done = run({ status: 'done', rect: { x: 0.1, y: 0.1, width: 0.3, height: 0.3 }, candidates: [1, 2].map((n) => ({ patch: { item_id: `p${n}` }, url: `/p${n}.png`, seed: n })) });

	it('settles a finished run into its step and lets a result be chosen as one undo step', async () => {
		const t = await withStep();
		t.controller.settle({ stepId: 's1', status: 'done', error: null, run: done });
		let params = readGenerateParams(t.stack().steps[0].params) as GenerateParams;
		expect(params.results).toHaveLength(2);
		expect(t.stack().steps[0].summary).toBe('1 of 2 · a red');
		t.controller.choose('s1', 1);
		params = readGenerateParams(t.stack().steps[0].params) as GenerateParams;
		expect(params.chosen).toBe(1);
		expect(t.stack().revisions).toHaveLength(1);
	});

	it('settles a failure with the reason', async () => {
		const t = await withStep();
		t.controller.settle({ stepId: 's1', status: 'failed', error: 'timed out', run: run({ status: 'failed', error: 'timed out' }) });
		expect((readGenerateParams(t.stack().steps[0].params) as GenerateParams).run).toEqual({ id: 'er_1', status: 'failed', error: 'timed out' });
	});

	it('tries again with a new seed and the same settings, replacing the results in one revision', async () => {
		const t = await withStep();
		t.controller.settle({ stepId: 's1', status: 'done', error: null, run: done });
		(t.enginePorts.start as ReturnType<typeof vi.fn>).mockResolvedValueOnce(run({ id: 'er_2' }));
		expect(await t.controller.tryAgain('s1')).toBe(true);
		const body = (t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[1][1];
		expect(body.setup.form_data.seed).toBe(-1);
		expect(body.setup.form_data.steps).toBe(28);
		expect(body.region).toEqual({ item_id: 'mask1' });
		expect(body.edit.step_id).toBe('s1');
		const params = readGenerateParams(t.stack().steps[0].params) as GenerateParams;
		expect(params).toMatchObject({ run: { id: 'er_2', status: 'queued' }, results: [] });
		expect(t.stack().revisions).toHaveLength(1);
		expect(t.engine.tracking('er_1')).toBe(false);
	});

	it('retries a failed run with the same seed handling', async () => {
		const t = await withStep();
		t.controller.settle({ stepId: 's1', status: 'failed', error: 'x', run: run({ status: 'failed', error: 'x' }) });
		(t.enginePorts.start as ReturnType<typeof vi.fn>).mockResolvedValueOnce(run({ id: 'er_3' }));
		await t.controller.retry('s1');
		expect((t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[1][1].setup.form_data.seed).toBe(7);
		expect((readGenerateParams(t.stack().steps[0].params) as GenerateParams).run.id).toBe('er_3');
	});

	it('cancels, and says so when the cancel is refused', async () => {
		const t = await withStep();
		(t.enginePorts.cancel as ReturnType<typeof vi.fn>).mockRejectedValueOnce({ response: { status: 400, data: { detail: { error: 'cancel_failed' } } } });
		await t.controller.cancel('s1');
		expect(get(t.controller.ui).error).toBe('It could not be cancelled. Try again.');
		await t.controller.cancel('s1');
		expect((readGenerateParams(t.stack().steps[0].params) as GenerateParams).run.status).toBe('cancelled');
	});

	it('cancels the run when its step is removed and when everything is cleared', async () => {
		const t = await withStep();
		t.controller.stepRemoved('s1', t.stack());
		await vi.advanceTimersByTimeAsync(0);
		expect(t.enginePorts.cancel).toHaveBeenCalledWith('er_1');
		const clear = await withStep();
		clear.controller.allRemoved(clear.stack());
		await vi.advanceTimersByTimeAsync(0);
		expect(clear.enginePorts.cancel).toHaveBeenCalledWith('er_1');
	});

	it('re-attaches a run left in flight in the stack, once', async () => {
		const t = await withStep();
		const stack = t.stack();
		t.engine.release('er_1');
		t.controller.resume(stack);
		t.controller.resume(stack);
		await vi.advanceTimersByTimeAsync(0);
		expect(t.enginePorts.get).toHaveBeenCalledTimes(1);
		expect(t.engine.tracking('er_1')).toBe(true);
	});

	it('writes a run the server lost into its step as failed', async () => {
		const t = await withStep();
		const stack = t.stack();
		t.engine.release('er_1');
		(t.enginePorts.get as ReturnType<typeof vi.fn>).mockRejectedValueOnce({ response: { status: 404, data: { detail: { error: 'run_not_found' } } } });
		t.controller.resume(stack);
		await vi.advanceTimersByTimeAsync(0);
		expect((readGenerateParams(t.stack().steps[0].params) as GenerateParams).run.status).toBe('failed');
	});

	it('stops the engine on dispose', async () => {
		const t = await withStep();
		t.controller.dispose();
		await vi.advanceTimersByTimeAsync(60000);
		expect(t.enginePorts.get).not.toHaveBeenCalled();
	});
});

describe('generate controller: settings tweaks', () => {
	const pickSession = { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'session' as const, id: 's1' } };
	const two = () => task({ offers: [offer(), offer({ mode: 'editor:other', mode_label: 'Other' })] });

	async function open(saved = true) {
		const t = setup([two()], {}, saved);
		t.ports.sessions.mockImplementation(async () => [{ id: 's1', name: 'Soft portrait', updated_at: null, version: 3, data: { 'editor:edit': { formData: { steps: 20 } } } }]);
		await t.controller.prepare('edit_region');
		await t.controller.pick('edit_region', pickSession);
		const context = await t.controller.settings('edit_region');
		return { t, context };
	}

	it('describes the setup and the starting values from the form the server serves', async () => {
		const { context, t } = await open();
		expect(context).not.toHaveProperty('owned');
		expect(t.ports.schema).toHaveBeenCalledWith('qwen', 'editor:edit');
		expect(context?.values).toMatchObject({ steps: 20 });
		expect(context?.setupName).toBe('Soft portrait');
	});

	it('says Changed only after an edit that differs, and runs with the form values', async () => {
		const { t } = await open();
		t.controller.tweak('edit_region', { steps: 20, seed: 7 });
		expect(t.controller.changedFor('edit_region')).toBe(false);
		t.controller.tweak('edit_region', { steps: 12, seed: 7, resolution: '2048' });
		expect(t.controller.changedFor('edit_region')).toBe(true);
		t.controller.setPrompt('edit_region', 'a boat');
		await t.controller.generate('edit_region', true);
		const params = readGenerateParams(t.stack().steps[0].params) as GenerateParams;
		expect(params.setup.form_data).toEqual({ steps: 12, seed: 7, resolution: '2048' });
		expect(params.setup.source).toEqual({ kind: 'session', id: 's1', name: 'Soft portrait' });
	});

	it('keeps the tweaks when the same setup is picked again and drops them for another', async () => {
		const { t } = await open();
		t.controller.tweak('edit_region', { steps: 20, seed: 7 });
		t.controller.tweak('edit_region', { steps: 12, seed: 7 });
		await t.controller.pick('edit_region', pickSession);
		expect(t.controller.changedFor('edit_region')).toBe(true);
		await t.controller.pick('edit_region', t.controller.defaultsChoice(two().offers[1]));
		expect(t.controller.changedFor('edit_region')).toBe(false);
		expect(get(t.controller.ui).tweaks).toEqual({});
	});

	it('saves to the session with its version, then clears Changed and uses the new values', async () => {
		const { t } = await open();
		t.controller.tweak('edit_region', { steps: 20, seed: 7 });
		t.controller.tweak('edit_region', { steps: 12, seed: 7 });
		const outcome = await t.controller.saveTo('edit_region');
		expect(outcome).toEqual({ ok: true });
		expect(t.ports.updateSession).toHaveBeenCalledWith('s1', {
			name: 'Soft portrait',
			data: { 'editor:edit': { formData: { steps: 12, seed: 7 } } },
			mode: 'editor:edit',
			base_version: 3,
			kind: 'editor'
		});
		expect(t.controller.changedFor('edit_region')).toBe(false);
		expect(t.controller.choicesOf(two().offers[0]).sessions[0]).toMatchObject({ values: { steps: 12, seed: 7 }, version: 2 });
	});

	it('refuses to save to a formula or the model defaults', async () => {
		const t = setup([two()]);
		await t.controller.prepare('edit_region');
		await t.controller.pick('edit_region', t.controller.defaultsChoice(two().offers[0]));
		await t.controller.settings('edit_region');
		t.controller.tweak('edit_region', { steps: 28 });
		t.controller.tweak('edit_region', { steps: 5 });
		const outcome = await t.controller.saveTo('edit_region');
		expect(outcome.ok).toBe(false);
		expect(t.ports.updateSession).not.toHaveBeenCalled();
	});

	it('reports a conflict in plain words and reloads the setup', async () => {
		const { t } = await open();
		t.ports.updateSession.mockRejectedValueOnce({ response: { status: 409, data: { detail: { error: 'session_version_conflict' } } } });
		t.controller.tweak('edit_region', { steps: 20 });
		t.controller.tweak('edit_region', { steps: 12 });
		const loads = t.ports.sessions.mock.calls.length;
		const outcome = await t.controller.saveTo('edit_region');
		expect(outcome).toMatchObject({ ok: false });
		expect((outcome as { message: string }).message).toContain('changed somewhere else');
		expect(t.ports.sessions.mock.calls.length).toBe(loads + 1);
		expect(t.controller.changedFor('edit_region')).toBe(true);
	});

	it('saves as a new setup, makes it the one this task uses and leaves the default alone', async () => {
		const { t } = await open();
		t.controller.tweak('edit_region', { steps: 20, seed: 7 });
		t.controller.tweak('edit_region', { steps: 12, seed: 7 });
		const outcome = await t.controller.saveAsNew('edit_region', '  Crisp  ');
		expect(outcome).toEqual({ ok: true });
		expect(t.ports.saveSession).toHaveBeenCalledWith({ preset_id: 'qwen', name: 'Crisp', data: { 'editor:edit': { formData: { steps: 12, seed: 7 } } }, mode: 'editor:edit', kind: 'editor' });
		expect(get(t.controller.ui).choice.edit_region?.setup).toEqual({ kind: 'session', id: 'new' });
		expect(t.controller.changedFor('edit_region')).toBe(false);
		expect(t.ports.setDefault).not.toHaveBeenCalled();
		expect(t.controller.chipFor('edit_region')).toBe('Qwen-Image 2.1 · Edit · New');
	});

	it('does not send a name that is empty or already used, and words a Generate page clash', async () => {
		const { t } = await open();
		t.controller.tweak('edit_region', { steps: 20 });
		t.controller.tweak('edit_region', { steps: 12 });
		expect((await t.controller.saveAsNew('edit_region', ' ')).ok).toBe(false);
		expect(await t.controller.saveAsNew('edit_region', 'soft portrait')).toEqual({ ok: false, message: 'You already have a setup with that name. Choose another name.' });
		expect(t.ports.saveSession).not.toHaveBeenCalled();
		t.ports.saveSession.mockRejectedValueOnce({ response: { status: 409, data: { detail: { error: 'session_kind_mismatch' } } } });
		expect(await t.controller.saveAsNew('edit_region', 'Held by Generate')).toEqual({
			ok: false,
			message: 'That name is already used by one of your sessions on the Generate page. Choose another name.'
		});
		expect(get(t.controller.ui).choice.edit_region?.setup).toEqual({ kind: 'session', id: 's1' });
	});
});

describe('generate controller: steps saved before editor modes', () => {
	function withLegacy(offers: TaskOffer[] = [offer()]) {
		const t = setup([task({ offers })]);
		const params = readGenerateParams({ ...legacy, run: { id: 'er_old', status: 'done', error: null } }) as GenerateParams;
		t.deps.setStack(apply(beginEdit(createStack(), generateDraft(params, 'Edit a region', 'sparkles'))));
		return { t, before: structuredClone(t.stack().steps[0].params) };
	}

	it('tries again with the editor key and rewrites the step to version 2 as one revision', async () => {
		const { t } = withLegacy();
		(t.enginePorts.start as ReturnType<typeof vi.fn>).mockResolvedValueOnce(run({ id: 'er_2' }));
		const revisions = (t.stack().revisions ?? []).length;
		expect(await t.controller.tryAgain('s1')).toBe(true);
		const body = (t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[0][1];
		expect(body.setup).toMatchObject({ preset_id: 'qwen', mode: 'editor:edit' });
		expect(body.setup).not.toHaveProperty('variant');
		const params = readGenerateParams(t.stack().steps[0].params) as GenerateParams;
		expect(params).toMatchObject({ v: 2, setup: { mode: 'editor:edit' }, run: { id: 'er_2', status: 'queued' }, results: [] });
		expect(t.stack().revisions).toHaveLength(revisions + 1);
	});

	it('keeps the step valid and says the setup is gone when no editor mode derives from its mode', async () => {
		const { t, before } = withLegacy([offer({ from_mode: 'other', mode: 'editor:other' })]);
		expect(await t.controller.tryAgain('s1')).toBe(false);
		expect(get(t.controller.ui).error).toBe('This setup cannot do that any more. Pick another.');
		expect(t.enginePorts.start).not.toHaveBeenCalled();
		expect(t.stack().steps[0].params).toEqual(before);
		expect(readGenerateParams(t.stack().steps[0].params)).not.toBeNull();
	});

	it('does not rewrite the step while the member only opens the edit', async () => {
		const { t, before } = withLegacy();
		await t.controller.prepare('edit_region');
		t.controller.resume(t.stack());
		expect(t.stack().steps[0].params).toEqual(before);
	});

	it('retries a failed legacy step the same way', async () => {
		const { t } = withLegacy();
		t.controller.settle({ stepId: 's1', status: 'failed', error: 'x', run: run({ id: 'er_old', status: 'failed', error: 'x' }) });
		(t.enginePorts.start as ReturnType<typeof vi.fn>).mockResolvedValueOnce(run({ id: 'er_3' }));
		expect(await t.controller.retry('s1')).toBe(true);
		expect((t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[0][1].setup.mode).toBe('editor:edit');
	});
});

describe('generate controller: the three pickers', () => {
	const flux = () => offer({ preset_id: 'flux', preset_name: 'Flux Fill', mode: 'editor:fill', from_mode: 'inpaint' });
	const modes = () => task({ offers: [offer(), offer({ mode: 'editor:edit2', mode_label: 'Edit 2', order: 1 }), flux()] });

	it('picks the first usable mode of a preset on its defaults, or the default of the member when it is that preset', async () => {
		const t = setup([modes()]);
		await t.controller.prepare('edit_region');
		expect(t.controller.choiceForPreset('edit_region', 'flux')).toEqual({ preset_id: 'flux', mode: 'editor:fill', setup: { kind: 'defaults' } });
		expect(t.controller.choiceForPreset('edit_region', 'qwen')).toEqual({ preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'defaults' } });
		const remembered = setup([task({ offers: modes().offers, default: { preset_id: 'qwen', mode: 'editor:edit2', setup: { kind: 'session', id: 's1' }, health: { ok: true, reason: null, name: null, mode_label: null } } })], {}, true);
		await remembered.controller.prepare('edit_region');
		expect(remembered.controller.choiceForPreset('edit_region', 'qwen')).toEqual({ preset_id: 'qwen', mode: 'editor:edit2', setup: { kind: 'session', id: 's1' } });
	});

	it('switches mode inside the chosen preset and refuses a mode of another preset or one that cannot run', async () => {
		const bad = task({ offers: [offer(), offer({ mode: 'editor:edit2', qualifies: false, reason: 'models_missing' }), flux()] });
		const t = setup([bad]);
		await t.controller.prepare('edit_region');
		await t.controller.pick('edit_region', t.controller.defaultsChoice(offer()));
		expect(t.controller.choiceForMode('edit_region', 'editor:edit2')).toBeNull();
		expect(t.controller.choiceForMode('edit_region', 'editor:fill')).toBeNull();
		const ok = setup([modes()]);
		await ok.controller.prepare('edit_region');
		await ok.controller.pick('edit_region', ok.controller.defaultsChoice(offer()));
		expect(ok.controller.choiceForMode('edit_region', 'editor:edit2')).toEqual({ preset_id: 'qwen', mode: 'editor:edit2', setup: { kind: 'defaults' } });
	});

	it('lists the formulas of the editor key and the sessions of that key only', async () => {
		const t = setup([modes()], {}, true);
		t.ports.formulas.mockImplementation(async () => [{ id: 'f1', name: 'Film look', values: { steps: 40 } }] as never);
		await t.controller.prepare('edit_region');
		expect(t.ports.formulas).toHaveBeenCalledWith('qwen', 'editor:edit');
		expect(t.controller.choicesOf(offer()).formulas).toEqual([{ id: 'f1', name: 'Film look', values: { steps: 40 } }]);
		expect(t.controller.choicesOf(offer()).sessions.map((entry) => entry.id)).toEqual(['s1']);
		expect(t.controller.choicesOf(offer({ mode: 'editor:edit2' })).sessions).toEqual([]);
	});

	it('loads the preset catalogue once for the picker', async () => {
		const presets = vi.fn(async () => [{ id: 'qwen', name: 'Qwen-Image 2.1', version: '1', tags: [] }]);
		const t = setup([modes()], { ports: { ...setup().ports, presets } });
		await t.controller.prepare('edit_region');
		await t.controller.prepare('edit_region');
		expect(presets).toHaveBeenCalledTimes(1);
		expect(get(t.controller.ui).catalog.map((entry) => entry.id)).toEqual(['qwen']);
	});

	it('opens the picker once on first use and keeps the reason after it was handled', async () => {
		const t = setup([modes()]);
		await t.controller.prepare('edit_region');
		expect(get(t.controller.ui).picker).toMatchObject({ open: true, why: 'first_use' });
		t.controller.pickerHandled();
		expect(get(t.controller.ui).picker).toMatchObject({ open: false, why: 'first_use' });
		await t.controller.generate('edit_region', false);
		expect(get(t.controller.ui).picker.open).toBe(true);
	});

	it('refuses Generate with nothing picked', async () => {
		const t = setup([modes()]);
		await ready(t);
		expect(await t.controller.generate('edit_region', false)).toBe(false);
		expect(t.enginePorts.start).not.toHaveBeenCalled();
	});

	it('keeps a failed default write off the member setup and says why', async () => {
		const t = setup();
		t.ports.setDefault.mockRejectedValueOnce({ message: 'Network Error' });
		await t.controller.prepare('edit_region');
		await t.controller.setAsDefault('edit_region', true);
		expect(t.controller.isDefault('edit_region')).toBe(false);
		expect(get(t.controller.ui).error).toBe('The connection was lost. Try again.');
	});
});

describe('generate controller: sessions of the editor', () => {
	const open = async (saved = true) => {
		const t = setup([task({ offers: [offer(), offer({ mode: 'editor:other' })] })], {}, saved);
		t.ports.sessions.mockImplementation(async () => [{ id: 's1', name: 'Soft portrait', updated_at: '2026-11-14T10:00:00Z', version: 3, pinned: false, data: { 'editor:edit': { formData: { steps: 20 } } } }] as never);
		await t.controller.prepare('edit_region');
		return t;
	};

	it('saves the defaults as a new setup without opening Settings first', async () => {
		const t = await open();
		const outcome = await t.controller.saveAsNew('edit_region', 'Mine');
		expect(outcome).toEqual({ ok: true });
		expect(t.ports.saveSession).toHaveBeenCalledWith({ preset_id: 'qwen', name: 'Mine', data: { 'editor:edit': { formData: { steps: 28, seed: 7 } } }, mode: 'editor:edit', kind: 'editor' });
		expect(get(t.controller.ui).choice.edit_region?.setup).toEqual({ kind: 'session', id: 'new' });
	});

	it('renames the selected session and keeps its values', async () => {
		const t = await open();
		await t.controller.pick('edit_region', { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'session', id: 's1' } });
		expect(await t.controller.saveTo('edit_region', 'Softer')).toEqual({ ok: true });
		expect(t.ports.updateSession).toHaveBeenCalledWith('s1', { name: 'Softer', data: { 'editor:edit': { formData: { steps: 20 } } }, mode: 'editor:edit', base_version: 3, kind: 'editor' });
		expect(t.controller.choicesOf(offer()).sessions[0].name).toBe('Softer');
		expect((await t.controller.saveTo('edit_region', ' ')).ok).toBe(false);
	});

	it('deletes the selected session and falls back to the model defaults', async () => {
		const t = await open();
		const deleteSession = vi.fn(async () => undefined);
		const ports = { ...t.ports, deleteSession };
		const c = createGenerateController({ ...t.deps, ports });
		await c.prepare('edit_region');
		await c.pick('edit_region', { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'session', id: 's1' } });
		expect(await c.removeSession('edit_region')).toEqual({ ok: true });
		expect(deleteSession).toHaveBeenCalledWith('s1');
		expect(c.choicesOf(offer()).sessions).toEqual([]);
		expect(get(c.ui).choice.edit_region?.setup).toEqual({ kind: 'defaults' });
	});

	it('pins and unpins, and undoes the change when the server refuses', async () => {
		const t = await open();
		const pinSession = vi.fn(async () => undefined);
		const c = createGenerateController({ ...t.deps, ports: { ...t.ports, pinSession } });
		await c.prepare('edit_region');
		await c.pick('edit_region', { preset_id: 'qwen', mode: 'editor:edit', setup: { kind: 'session', id: 's1' } });
		await c.pinSession('edit_region', 's1');
		expect(pinSession).toHaveBeenCalledWith('s1', true);
		expect(c.choicesOf(offer()).sessions[0].pinned).toBe(true);
		pinSession.mockRejectedValueOnce(new Error('offline'));
		await c.pinSession('edit_region', 's1');
		expect(c.choicesOf(offer()).sessions[0].pinned).toBe(true);
	});

	it('loads an older version as unsaved changes on that session', async () => {
		const t = await open();
		const version = vi.fn(async () => ({ version_number: 1, created_at: 'x', summary: 's', data: { 'editor:edit': { formData: { steps: 9 } } } }));
		const c = createGenerateController({ ...t.deps, ports: { ...t.ports, version } });
		await c.prepare('edit_region');
		expect(await c.loadVersion('edit_region', 's1', 1)).toBe(true);
		expect(get(c.ui).choice.edit_region?.setup).toEqual({ kind: 'session', id: 's1' });
		expect(c.changedFor('edit_region')).toBe(true);
		expect(get(c.ui).tweaks.edit_region.values).toEqual({ steps: 9 });
		const reloads = get(c.ui).reloads;
		c.reloadSnapshot('edit_region');
		expect(c.changedFor('edit_region')).toBe(false);
		expect(get(c.ui).reloads).toBe(reloads + 1);
	});

	it('says so when a version cannot be loaded', async () => {
		const t = await open();
		const c = createGenerateController({ ...t.deps, ports: { ...t.ports, version: vi.fn(async () => null) } });
		await c.prepare('edit_region');
		expect(await c.loadVersion('edit_region', 's1', 1)).toBe(false);
	});
});

describe('generate controller: replacing the background', () => {
	const finder = (over: Partial<EditorTaskInfo> = {}) => task({ id: 'find_subject', label: 'Find the subject', region: 'none', prompt: 'none', internal: true, offers: [offer({ preset_id: 'matting', mode: 'editor:matte' })], ...over });
	const matted = (over: Partial<EditorRun> = {}) => run({ id: 'er_m', task: 'find_subject', status: 'done', candidates: [{ patch: { item_id: 'cand1' }, url: '/api/media/cand1.png', seed: null }], ...over });
	const startMock = (t: ReturnType<typeof setup>) => t.enginePorts.start as ReturnType<typeof vi.fn>;

	it('finds the subject with no region and no prompt, marks the background and leaves no step', async () => {
		const applyMatte = vi.fn(async () => true);
		const t = setup([finder()], { applyMatte });
		startMock(t).mockResolvedValueOnce(matted());
		expect(await t.controller.findSubject()).toBe(true);
		const [taskId, body] = startMock(t).mock.calls[0];
		expect(taskId).toBe('find_subject');
		expect(body).toMatchObject({ edit: { id: 'ed_1', step_id: null }, input: { item_id: 'pic' }, region: null, prompt: null, setup: { preset_id: 'matting', mode: 'editor:matte' } });
		expect(applyMatte).toHaveBeenCalledWith('/api/media/cand1.png');
		expect(t.stack().steps).toHaveLength(0);
		expect(get(t.controller.ui).subject).toEqual({ status: 'done', progress: 1, error: null });
		expect(t.engine.tracking('er_m')).toBe(false);
	});

	it('says the matting model is missing when nothing offers the task', async () => {
		const t = setup([finder({ offers: [] })], { applyMatte: vi.fn(async () => true) });
		expect(await t.controller.findSubject()).toBe(false);
		expect(get(t.controller.ui).subject.error).toContain('Remove background');
		expect(startMock(t)).not.toHaveBeenCalled();
	});

	it('shows a plain failure when the run does not finish and marks nothing', async () => {
		const applyMatte = vi.fn(async () => true);
		const t = setup([finder()], { applyMatte });
		startMock(t).mockResolvedValueOnce(matted({ status: 'failed', error: 'CUDA out of memory', candidates: [] }));
		expect(await t.controller.findSubject()).toBe(false);
		expect(get(t.controller.ui).subject).toEqual({ status: 'failed', progress: 0, error: 'The subject could not be found. Try again.' });
		expect(applyMatte).not.toHaveBeenCalled();
	});

	it('says so when the mask could not take the subject', async () => {
		const t = setup([finder()], { applyMatte: vi.fn(async () => false) });
		startMock(t).mockResolvedValueOnce(matted());
		expect(await t.controller.findSubject()).toBe(false);
		expect(get(t.controller.ui).subject.status).toBe('failed');
	});

	it('adds a step for a colour with no model, taking the mask as the matte', async () => {
		const t = setup([finder()]);
		t.controller.setBackground({ mode: 'color', color: '#336699' });
		expect(await t.controller.applyBackground(true)).toBe(true);
		expect(startMock(t)).not.toHaveBeenCalled();
		expect(t.stack().steps[0]).toMatchObject({ tool: 'replace_background', label: 'Replace background', summary: '#336699', params: { kind: 'color', color: '#336699', matte: { item_id: 'mask1' } } });
	});

	it('refuses with a plain reason until there is a mark or a picture', async () => {
		const t = setup([finder()]);
		t.controller.setBackground({ mode: 'color' });
		expect(await t.controller.applyBackground(false)).toBe(false);
		expect(get(t.controller.ui).error).toBe('Find the subject or mark the background first');
		t.controller.setBackground({ mode: 'picture' });
		expect(await t.controller.applyBackground(true)).toBe(false);
		expect(get(t.controller.ui).error).toBe('Choose a picture first');
		expect(t.stack().steps).toHaveLength(0);
	});

	it('lands the soft matte for a described background and for a direct one, the plain mask for other tasks', async () => {
		const replace = task({ id: 'replace_background', label: 'Replace the background', region: 'required', prompt: 'required', matte: true });
		const t = setup([replace, task()]);
		await t.controller.prepare('replace_background');
		t.controller.setPrompt('replace_background', 'a beach at dusk');
		await t.controller.generate('replace_background', true);
		expect(t.deps.landRegion).toHaveBeenLastCalledWith(true);
		t.controller.setBackground({ mode: 'transparent' });
		await t.controller.applyBackground(true);
		expect(t.deps.landRegion).toHaveBeenLastCalledWith(true);
		await t.controller.prepare('edit_region');
		t.controller.setPrompt('edit_region', 'a red sailboat');
		await t.controller.generate('edit_region', true);
		expect(t.deps.landRegion).toHaveBeenLastCalledWith(false);
	});

	it('takes a library item as the picture and uploads anything else', async () => {
		const landPicture = vi.fn(async () => ({ item_id: 'up1', url: '/up1.png', name: 'Harbour' }));
		const t = setup([finder()], { landPicture });
		expect(await t.controller.chooseBackgroundPicture({ path: 'p', url: '/lib.png', name: 'Sea', type: 'image', item_id: 'lib1' } as never)).toBe(true);
		expect(get(t.controller.ui).background).toMatchObject({ mode: 'picture', picture: { item_id: 'lib1', name: 'Sea' } });
		expect(landPicture).not.toHaveBeenCalled();
		expect(await t.controller.chooseBackgroundPicture({ path: 'p', url: '/h.png', name: 'h', type: 'image', generation_file_id: '9' } as never)).toBe(true);
		expect(get(t.controller.ui).background.picture?.item_id).toBe('up1');
	});
});

describe('generate controller: task options', () => {
	const remove = () => task({ id: 'remove_object', label: 'Remove an object', region: 'required', prompt: 'none' });
	const startBody = (t: ReturnType<typeof setup>, index = 0) => (t.enginePorts.start as ReturnType<typeof vi.fn>).mock.calls[index][1];

	it('sends the shadow option only when it is on and keeps it in the step', async () => {
		const t = setup([remove()]);
		await t.controller.prepare('remove_object');
		expect(await t.controller.generate('remove_object', true)).toBe(true);
		expect('options' in startBody(t)).toBe(false);
		expect(readGenerateParams(t.stack().steps[0].params)?.options).toBeUndefined();

		const on = setup([remove()]);
		await on.controller.prepare('remove_object');
		on.controller.setOption('remove_object', 'include_shadow', true);
		expect(await on.controller.generate('remove_object', true)).toBe(true);
		expect(startBody(on).options).toEqual({ include_shadow: true });
		expect(readGenerateParams(on.stack().steps[0].params)?.options).toEqual({ include_shadow: true });
	});

	it('drops the option again when it is switched off', async () => {
		const t = setup([remove()]);
		await t.controller.prepare('remove_object');
		t.controller.setOption('remove_object', 'include_shadow', true);
		t.controller.setOption('remove_object', 'include_shadow', false);
		await t.controller.generate('remove_object', true);
		expect('options' in startBody(t)).toBe(false);
	});

	it('runs again with the options the step was made with', async () => {
		const t = setup([remove()]);
		await t.controller.prepare('remove_object');
		t.controller.setOption('remove_object', 'include_shadow', true);
		await t.controller.generate('remove_object', true);
		t.controller.setOption('remove_object', 'include_shadow', false);
		(t.enginePorts.start as ReturnType<typeof vi.fn>).mockResolvedValueOnce(run({ id: 'er_2' }));
		await t.controller.tryAgain('s1');
		expect(startBody(t, 1).options).toEqual({ include_shadow: true });
	});
});
