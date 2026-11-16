import { get, writable } from 'svelte/store';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EditorRun, EditorTaskInfo, TaskOffer } from '$lib/services/api/editorTasks';
import { missingModelIn, seedFormValues } from '$lib/form/formStateSeed';
import type { AutopickSearchResult, AutopickSlot } from '$lib/form/modelSlotAutopick';
import { createStack, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import schema from './fixtures/qwenImage21EditSchema.json';
import { createGenerateController, type ControllerDeps, type ControllerPorts } from './controller';
import { createRunEngine, type RunPorts, type RunViews } from './runEngine';

const PRESET = 'qwen21';
const MODELS = ['diffusion_model', 'text_encoder', 'vae'];

const offer: TaskOffer = { preset_id: PRESET, preset_name: 'Qwen-Image 2.1', mode: 'edit', mode_label: 'Edit', label: null, variant: null, region_strategy: 'mask', via: null, qualifies: true, reason: null, owned: [] };
const editTask: EditorTaskInfo = { id: 'edit_region', label: 'Edit a region', icon: 'sparkles', region: 'optional', prompt: 'required', fallback: null, offers: [offer], default: null };
const run: EditorRun = { id: 'er_1', task: 'edit_region', status: 'queued', edit_id: 'ed', step_id: 's1', generation_id: null, progress: 0, error: null, soft: false, rect: null, candidates: [] };

const oneModelEach = async (slot: AutopickSlot): Promise<AutopickSearchResult> => ({ models: [{ id: `${slot.modelType}-1` }], total: 1 });
const twoModelsEach = async (slot: AutopickSlot): Promise<AutopickSearchResult> => ({ models: [{ id: `${slot.modelType}-1` }, { id: `${slot.modelType}-2` }], total: 2 });

function build(search: (slot: AutopickSlot) => Promise<AutopickSearchResult | null>, sessions: unknown[] = [], formulas: unknown[] = []) {
	let stack: EditStackState<unknown> = createStack();
	const start = vi.fn(async () => run);
	const enginePorts: RunPorts = {
		start,
		get: vi.fn(async () => run),
		cancel: vi.fn(async () => run),
		socket: () => ({ subscribe: () => () => undefined, connected: () => false, onConnection: () => () => undefined, close: () => undefined })
	};
	const capabilityOf = () => undefined;
	let controller!: ReturnType<typeof createGenerateController>;
	const engine = createRunEngine(enginePorts, { views: writable<RunViews>({}), pollMs: 1000, onSettled: (view) => controller.settle(view) });
	const ports: ControllerPorts = {
		tasks: async () => [editTask],
		sessions: async () => sessions as never,
		formulas: async () => formulas as never,
		schema: async () => schema as never,
		seed: ({ presetId, schema: given, incoming }) =>
			seedFormValues(
				{ schema: given, presetId, tabId: 'test-setup', incoming, audience: 'advanced', videoDirectorActive: false },
				{ search, loadCapabilities: async () => undefined, capabilityOf }
			),
		missingModel: (given, values) => missingModelIn(given, values, capabilityOf),
		setDefault: async () => undefined,
		clearDefault: async () => undefined,
		saveSession: async () => ({ id: 'new', name: 'New', version: 1, updated_at: null }),
		updateSession: async () => ({ id: 's1', name: 'S', version: 2, updated_at: null })
	};
	const deps: ControllerDeps = {
		getStack: () => stack,
		setStack: (next) => (stack = next),
		source: () => ({ ref: { item_id: 'pic' } }),
		editLink: async () => ({ id: 'ed_1', revision: 1 }),
		landRegion: async () => ({ item_id: 'mask1' }),
		areaSize: () => ({ width: 400, height: 300 }),
		stepsBeneath: async () => [],
		ports,
		engine
	};
	controller = createGenerateController(deps);
	return { controller, start };
}

async function submit(t: ReturnType<typeof build>, pick?: { kind: 'session' | 'formula'; id: string }) {
	await t.controller.prepare('edit_region');
	if (pick) {
		await t.controller.pick('edit_region', { preset_id: PRESET, mode: 'edit', variant: null, setup: pick }, false);
	}
	t.controller.setPrompt('edit_region', 'a red sailboat');
	const started = await t.controller.generate('edit_region', true);
	const sent = t.start.mock.calls.length ? ((t.start.mock.calls[0] as unknown[])[1] as { setup: { form_data: Record<string, unknown> } }).setup.form_data : null;
	return { started, sent };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe('the model fields a run sends', () => {
	it('are the models the Generate page would pick, as plain paths, on Model defaults', async () => {
		const t = build(oneModelEach);
		const { started, sent } = await submit(t);
		expect(started).toBe(true);
		for (const name of MODELS) expect(sent?.[name]).toBe(`model:${name}-1`);
		expect(Object.values(sent ?? {}).some((value) => typeof value === 'object' && value !== null && 'modelPath' in value)).toBe(false);
	});

	it('fill the models a saved setup left out', async () => {
		const sessions = [{ id: 's1', name: 'Soft', updated_at: null, version: 1, data: { edit: { formData: { steps: 12 } } } }];
		const t = build(oneModelEach, sessions);
		const { sent } = await submit(t, { kind: 'session', id: 's1' });
		expect(sent?.steps).toBe(12);
		for (const name of MODELS) expect(sent?.[name]).toBe(`model:${name}-1`);
	});

	it('fill the models a formula left out and keep the ones it chose', async () => {
		const formulas = [{ id: 'f1', name: 'Mine', values: { vae: 'model:my-vae' } }];
		const t = build(oneModelEach, [], formulas);
		const { sent } = await submit(t, { kind: 'formula', id: 'f1' });
		expect(sent?.vae).toBe('model:my-vae');
		expect(sent?.diffusion_model).toBe('model:diffusion_model-1');
	});

	it('refuse the run before it is queued when a model cannot be chosen for the member', async () => {
		const t = build(twoModelsEach);
		const { started } = await submit(t);
		expect(started).toBe(false);
		expect(t.start).not.toHaveBeenCalled();
		expect(get(t.controller.ui).error).toBe('Select Diffusion Model to generate. Open Settings to choose it.');
	});
});
