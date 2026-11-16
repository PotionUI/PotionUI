import { get, writable, type Readable } from 'svelte/store';
import type { MediaToolInputRef, MediaToolStep } from '$lib/media-tools/types';
import {
	describeEditorTaskFailure,
	editorTaskFailureMessage,
	type EditorRun,
	type EditorSessionEntry,
	type EditorTaskInfo,
	type SaveEditorSessionBody,
	type SavedEditorSession,
	type SetupRef,
	type StartRunBody,
	type UpdateEditorSessionBody,
	type TaskDefault,
	type TaskOffer
} from '$lib/services/api/editorTasks';
import { apply, beginEdit, commitLive, liveEdit, type EditStackState } from '$lib/tool-tabs/frame/editStack';
import type { EditLink } from '../editLink';
import type { RunEngine } from './runEngine';
import {
	GENERATE_TOOL,
	generateDraft,
	generateSteps,
	readGenerateParams,
	replaceStepParams,
	stepsToResume,
	withChosen,
	withFreshRun,
	withRun,
	type GenerateParams,
	type ResolvedSetup
} from './generateStep';
import {
	DEFAULTS_NAME,
	choiceKey,
	defaultsChoice,
	offerOf,
	offerKey,
	planStart,
	resolveSetup,
	sameChoice,
	sourceOf,
	chipLabel,
	ownedFieldsOf,
	withoutOwned,
	type FormulaChoice,
	type ModeChoices,
	type SessionChoice,
	type SetupChoice
} from './setup';

import { getSchemaDefaults } from '$lib/form/defaults';
import {
	editTweak,
	isChanged,
	runValues,
	saveAsNewBody,
	saveErrorCode,
	saveToBody,
	savedTweak,
	sessionSaveMessage,
	startTweak,
	validateSetupName,
	type Tweak
} from './tweaks';

export const RESULTS_KEY_PREFIX = 'potionui_editor_results_';
export const MAX_RESULTS = 4;
export const NOT_SAVED_MESSAGE = 'Your edit is not saved yet. Check your connection and try again.';
export const NO_PICTURE_MESSAGE = 'This picture has to be in your Library or History first.';
export const CHOOSE_SETUP = 'Choose a setup';

export interface ControllerPorts {
	tasks(): Promise<EditorTaskInfo[]>;
	sessions(presetId: string): Promise<EditorSessionEntry[]>;
	formulas(presetId: string, mode: string): Promise<Array<{ id: string; name: string; values: Record<string, unknown> }>>;
	schema(presetId: string, mode: string, variant: string | null): Promise<{ properties?: Record<string, any> } | null>;
	setDefault(taskId: string, body: { preset_id: string; mode: string; variant: string | null; setup: SetupRef }): Promise<void>;
	clearDefault(taskId: string): Promise<void>;
	saveSession(body: SaveEditorSessionBody): Promise<SavedEditorSession>;
	updateSession(sessionId: string, body: UpdateEditorSessionBody): Promise<SavedEditorSession>;
}

export interface ControllerDeps {
	getStack(): EditStackState<unknown>;
	setStack(next: EditStackState<unknown>): void;
	source(): { ref: MediaToolInputRef | null };
	editLink(): Promise<EditLink | null>;
	landRegion(): Promise<{ item_id: string } | null>;
	areaSize(): { width: number; height: number } | null;
	stepsBeneath(stepId: string | null): Promise<MediaToolStep[]>;
	ports: ControllerPorts;
	engine: RunEngine;
	storage?: Pick<Storage, 'getItem' | 'setItem'>;
	onTasks?(tasks: EditorTaskInfo[]): void;
}

export interface PickerState {
	open: boolean;
	why: 'first_use' | 'broken_default' | 'nothing_qualifies' | 'change' | null;
	notice: string | null;
}

export interface GenerateUi {
	loaded: boolean;
	tasks: EditorTaskInfo[];
	prompts: Record<string, string>;
	results: Record<string, number>;
	choice: Record<string, SetupChoice | null>;
	choices: Record<string, ModeChoices>;
	picker: PickerState;
	starting: boolean;
	error: string | null;
	activeStepId: string | null;
	taskId: string | null;
	tweaks: Record<string, Tweak>;
}

export interface SettingsContext {
	offer: TaskOffer;
	choice: SetupChoice;
	key: string;
	owned: string[];
	base: ResolvedSetup;
	schema: { properties?: Record<string, any> } | null;
	values: Record<string, unknown>;
	source: ResolvedSetup['source'];
	setupName: string;
}

export type SaveOutcome = { ok: true } | { ok: false; message: string };

function closedPicker(): PickerState {
	return { open: false, why: null, notice: null };
}

export function clampResults(value: number): number {
	return Number.isFinite(value) ? Math.max(1, Math.min(MAX_RESULTS, Math.trunc(value))) : 1;
}

function failureText(error: unknown): string {
	return editorTaskFailureMessage(describeEditorTaskFailure(error));
}

function seedFieldOf(schema: { properties?: Record<string, any> } | null): string | null {
	let found: string | null = null;
	const walk = (nodes: any[]) => {
		for (const node of nodes) {
			if (found) return;
			if (node?.name && node.type === 'seed') found = node.name;
			else if (node?.name === 'seed' && !found) found = 'seed';
			if (Array.isArray(node?.children)) walk(node.children);
		}
	};
	for (const root of Object.values(schema?.properties ?? {})) walk(root?.children ?? []);
	return found;
}

export function createGenerateController(deps: ControllerDeps) {
	const state = writable<GenerateUi>({
		loaded: false,
		tasks: [],
		prompts: {},
		results: {},
		choice: {},
		choices: {},
		picker: closedPicker(),
		starting: false,
		error: null,
		activeStepId: null,
		taskId: null,
		tweaks: {}
	});
	let disposed = false;
	let tasksLoad: Promise<void> | null = null;
	const choiceLoads = new Map<string, Promise<void>>();

	function patch(change: Partial<GenerateUi>): void {
		state.update((current) => ({ ...current, ...change }));
	}

	function storedResults(taskId: string): number {
		try {
			const raw = deps.storage?.getItem(`${RESULTS_KEY_PREFIX}${taskId}`);
			return raw ? clampResults(Number(raw)) : 1;
		} catch {
			return 1;
		}
	}

	function taskOf(taskId: string): EditorTaskInfo | null {
		return get(state).tasks.find((entry) => entry.id === taskId) ?? null;
	}

	function choicesOf(offer: TaskOffer): ModeChoices {
		return get(state).choices[offerKey(offer)] ?? { sessions: [], formulas: [] };
	}

	async function loadChoices(offer: TaskOffer): Promise<void> {
		const key = offerKey(offer);
		if (get(state).choices[key]) return;
		let pending = choiceLoads.get(key);
		if (!pending) {
			pending = (async () => {
				const [sessions, formulas] = await Promise.all([
					deps.ports.sessions(offer.preset_id).catch(() => []),
					deps.ports.formulas(offer.preset_id, offer.mode).catch(() => [])
				]);
				const sessionChoices: SessionChoice[] = sessions.flatMap((entry) => {
					const data = entry.data?.[offer.mode];
					return data ? [{ id: entry.id, name: entry.name, updatedAt: entry.updated_at, values: data.formData ?? {}, version: entry.version ?? null }] : [];
				});
				const formulaChoices: FormulaChoice[] = formulas.map((entry) => ({ id: entry.id, name: entry.name, values: entry.values ?? {} }));
				if (!disposed) state.update((current) => ({ ...current, choices: { ...current.choices, [key]: { sessions: sessionChoices, formulas: formulaChoices } } }));
			})().finally(() => choiceLoads.delete(key));
			choiceLoads.set(key, pending);
		}
		await pending;
	}

	async function loadTasks(force = false): Promise<void> {
		if (tasksLoad && !force) return tasksLoad;
		tasksLoad = (async () => {
			try {
				const list = await deps.ports.tasks();
				if (disposed) return;
				deps.onTasks?.(list);
				patch({ tasks: list, loaded: true });
			} catch {
				if (!disposed) patch({ loaded: true });
			}
		})();
		return tasksLoad;
	}

	async function prepare(taskId: string): Promise<void> {
		await loadTasks();
		const task = taskOf(taskId);
		if (!task) return;
		const usable = task.offers.filter((offer) => offer.qualifies);
		await Promise.all((usable.length ? usable : []).map(loadChoices));
		if (disposed) return;
		state.update((current) => ({ ...current, results: { ...current.results, [taskId]: current.results[taskId] ?? storedResults(taskId) } }));
		const plan = planStart({
			task,
			offers: task.offers,
			taskDefault: task.default,
			picked: get(state).choice[taskId] ?? null,
			choicesOf
		});
		if (plan.action === 'run') {
			state.update((current) => ({ ...current, choice: { ...current.choice, [taskId]: plan.choice } }));
			return;
		}
		state.update((current) => ({ ...current, choice: { ...current.choice, [taskId]: null }, picker: { open: true, why: plan.why, notice: plan.notice } }));
	}

	function use(taskId: string | null): void {
		if (get(state).taskId === taskId) return;
		patch({ taskId, error: null, picker: closedPicker(), activeStepId: null });
		if (taskId) void prepare(taskId);
	}

	function chipFor(taskId: string): string | null {
		const current = get(state);
		const choice = current.choice[taskId];
		const task = taskOf(taskId);
		if (!choice || !task) return null;
		const offer = offerOf(task.offers, choice);
		if (!offer) return null;
		return chipLabel(offer, sourceOf(choice.setup, choicesOf(offer)));
	}

	function setPrompt(taskId: string, text: string): void {
		state.update((current) => ({ ...current, prompts: { ...current.prompts, [taskId]: text }, error: null }));
	}

	function setResults(taskId: string, count: number): void {
		const value = clampResults(count);
		state.update((current) => ({ ...current, results: { ...current.results, [taskId]: value } }));
		try {
			deps.storage?.setItem(`${RESULTS_KEY_PREFIX}${taskId}`, String(value));
		} catch {
			return;
		}
	}

	function openPicker(why: PickerState['why'] = 'change'): void {
		patch({ picker: { open: true, why, notice: null } });
		const current = get(state);
		for (const entry of current.tasks) for (const offer of entry.offers) void loadChoices(offer);
	}

	function closePicker(): void {
		patch({ picker: closedPicker() });
	}

	async function pick(taskId: string, choice: SetupChoice, makeDefault: boolean): Promise<void> {
		state.update((current) => {
			const tweaks = { ...current.tweaks };
			if (tweaks[taskId]?.key !== choiceKey(choice)) delete tweaks[taskId];
			return { ...current, choice: { ...current.choice, [taskId]: choice }, tweaks, picker: closedPicker(), error: null };
		});
		if (!makeDefault) return;
		try {
			await deps.ports.setDefault(taskId, { preset_id: choice.preset_id, mode: choice.mode, variant: choice.variant, setup: choice.setup });
			const fresh: TaskDefault = { ...choice, health: { ok: true, reason: null, name: null, mode_label: null } };
			state.update((current) => ({ ...current, tasks: current.tasks.map((entry) => (entry.id === taskId ? { ...entry, default: fresh } : entry)) }));
		} catch (error) {
			patch({ error: failureText(error) });
		}
	}

	async function clearDefault(taskId: string): Promise<void> {
		try {
			await deps.ports.clearDefault(taskId);
			state.update((current) => ({ ...current, tasks: current.tasks.map((entry) => (entry.id === taskId ? { ...entry, default: null } : entry)) }));
		} catch (error) {
			patch({ error: failureText(error) });
		}
	}

	async function baseFor(taskId: string, choice: SetupChoice): Promise<{ offer: TaskOffer; schema: { properties?: Record<string, any> } | null; resolved: ResolvedSetup } | null> {
		const task = taskOf(taskId);
		const offer = task ? offerOf(task.offers, choice) : null;
		if (!offer) return null;
		await loadChoices(offer);
		const schema = await deps.ports.schema(offer.preset_id, offer.mode, offer.variant).catch(() => null);
		return { offer, schema, resolved: resolveSetup({ offer, setup: choice.setup, schema, choices: choicesOf(offer) }) };
	}

	async function resolveFor(taskId: string, choice: SetupChoice): Promise<ResolvedSetup | { error: string }> {
		const found = await baseFor(taskId, choice);
		if (!found) return { error: 'This setup cannot do that any more. Pick another.' };
		const { resolved } = found;
		return { ...resolved, form_data: runValues(resolved.form_data, get(state).tweaks[taskId], choiceKey(choice)) };
	}

	function changedFor(taskId: string): boolean {
		return isChanged(get(state).tweaks[taskId]);
	}

	function setTweak(taskId: string, next: Tweak | null): void {
		state.update((current) => {
			const tweaks = { ...current.tweaks };
			if (next) tweaks[taskId] = next;
			else delete tweaks[taskId];
			return { ...current, tweaks };
		});
	}

	async function settings(taskId: string): Promise<SettingsContext | null> {
		const choice = get(state).choice[taskId];
		if (!choice) return null;
		const found = await baseFor(taskId, choice);
		if (!found || disposed) return null;
		const { offer, schema, resolved } = found;
		const key = choiceKey(choice);
		const owned = [...ownedFieldsOf(offer, schema)];
		const existing = get(state).tweaks[taskId];
		let tweak = existing && existing.key === key ? existing : null;
		if (!tweak) {
			const defaults = withoutOwned(getSchemaDefaults(schema as never) as Record<string, unknown>, new Set(owned));
			tweak = startTweak(key, owned, defaults, resolved.form_data);
			setTweak(taskId, tweak);
		}
		return { offer, choice, key, owned, base: resolved, schema, values: tweak.values, source: resolved.source, setupName: resolved.source.name ?? DEFAULTS_NAME };
	}

	function tweak(taskId: string, data: Record<string, unknown>): void {
		const current = get(state).tweaks[taskId];
		const choice = get(state).choice[taskId];
		if (!current || !choice || current.key !== choiceKey(choice)) return;
		setTweak(taskId, editTweak(current, data));
	}

	function discardTweaks(taskId: string): void {
		if (get(state).tweaks[taskId]) setTweak(taskId, null);
	}

	function sessionChoiceOf(offer: TaskOffer, id: string): SessionChoice | null {
		return choicesOf(offer).sessions.find((entry) => entry.id === id) ?? null;
	}

	function patchSessions(offer: TaskOffer, change: (sessions: SessionChoice[]) => SessionChoice[]): void {
		const key = offerKey(offer);
		state.update((current) => {
			const existing = current.choices[key] ?? { sessions: [], formulas: [] };
			return { ...current, choices: { ...current.choices, [key]: { ...existing, sessions: change(existing.sessions) } } };
		});
	}

	async function refreshChoices(offer: TaskOffer): Promise<void> {
		state.update((current) => {
			const choices = { ...current.choices };
			delete choices[offerKey(offer)];
			return { ...current, choices };
		});
		await loadChoices(offer);
	}

	async function saveTo(taskId: string): Promise<SaveOutcome> {
		const current = get(state);
		const choice = current.choice[taskId];
		const open = current.tweaks[taskId];
		const task = taskOf(taskId);
		const offer = task && choice ? offerOf(task.offers, choice) : null;
		if (!choice || !open || !offer || choice.setup.kind !== 'session') return { ok: false, message: 'This setup cannot be saved to. Save it as a new setup instead.' };
		const session = sessionChoiceOf(offer, choice.setup.id);
		if (!session) return { ok: false, message: 'This setup no longer exists. Save it as a new setup instead.' };
		try {
			const saved = await deps.ports.updateSession(session.id, saveToBody({ name: session.name, version: session.version ?? null, target: offer, values: open.values, owned: open.owned }));
			const values = withoutOwned(open.values, new Set(open.owned));
			patchSessions(offer, (sessions) => sessions.map((entry) => (entry.id === session.id ? { ...entry, values, version: saved.version ?? entry.version ?? null, updatedAt: saved.updated_at ?? entry.updatedAt } : entry)));
			const latest = get(state).tweaks[taskId];
			if (latest) setTweak(taskId, savedTweak(latest));
			return { ok: true };
		} catch (error) {
			if (saveErrorCode(error) === 'session_version_conflict') await refreshChoices(offer);
			return { ok: false, message: sessionSaveMessage(error) };
		}
	}

	async function saveAsNew(taskId: string, rawName: string): Promise<SaveOutcome> {
		const current = get(state);
		const choice = current.choice[taskId];
		const open = current.tweaks[taskId];
		const task = taskOf(taskId);
		const offer = task && choice ? offerOf(task.offers, choice) : null;
		if (!choice || !open || !offer) return { ok: false, message: 'This setup cannot be saved right now. Pick a setup first.' };
		const invalid = validateSetupName(rawName, choicesOf(offer).sessions.map((entry) => entry.name));
		if (invalid) return { ok: false, message: invalid };
		const name = rawName.trim();
		try {
			const saved = await deps.ports.saveSession(saveAsNewBody({ presetId: offer.preset_id, name, target: offer, values: open.values, owned: open.owned }));
			const values = withoutOwned(open.values, new Set(open.owned));
			patchSessions(offer, (sessions) => [{ id: saved.id, name: saved.name || name, updatedAt: saved.updated_at, values, version: saved.version }, ...sessions.filter((entry) => entry.id !== saved.id)]);
			const next: SetupChoice = { preset_id: choice.preset_id, mode: choice.mode, variant: choice.variant, setup: { kind: 'session', id: saved.id } };
			const key = choiceKey(next);
			state.update((latest) => ({
				...latest,
				choice: { ...latest.choice, [taskId]: next },
				tweaks: { ...latest.tweaks, [taskId]: savedTweak({ ...open, key }) }
			}));
			return { ok: true };
		} catch (error) {
			return { ok: false, message: sessionSaveMessage(error) };
		}
	}

	function nextStepId(): string {
		return `s${deps.getStack().seq + 1}`;
	}

	async function submit(taskId: string, stepId: string, input: {
		prompt: string;
		setup: ResolvedSetup;
		region: { item_id: string } | null;
		candidates: number;
		seed: number | null;
	}): Promise<{ run: EditorRun } | { error: string }> {
		const ref = deps.source().ref;
		if (!ref) return { error: NO_PICTURE_MESSAGE };
		const link = await deps.editLink();
		if (!link) return { error: NOT_SAVED_MESSAGE };
		const task = taskOf(taskId);
		const steps = await deps.stepsBeneath(stepId);
		const body: StartRunBody = {
			edit: { id: link.id, step_id: stepId },
			input: ref,
			steps,
			region: task?.region === 'none' ? null : input.region,
			prompt: task?.prompt === 'none' ? null : input.prompt.trim() || null,
			setup: { preset_id: input.setup.preset_id, mode: input.setup.mode, variant: input.setup.variant, form_data: input.setup.form_data },
			candidates: input.candidates,
			seed: input.seed
		};
		try {
			return { run: await deps.engine.start(taskId, body, stepId) };
		} catch (error) {
			return { error: failureText(error) };
		}
	}

	async function generate(taskId: string, areaMarked: boolean): Promise<boolean> {
		const current = get(state);
		if (current.starting) return false;
		const task = taskOf(taskId);
		const choice = current.choice[taskId];
		if (!task || !choice) {
			patch({ picker: { open: true, why: 'change', notice: null } });
			return false;
		}
		const prompt = current.prompts[taskId] ?? '';
		if (task.prompt === 'required' && !prompt.trim()) return fail('Describe the change first.');
		if (task.region === 'required' && !areaMarked) return fail('Mark the area first.');
		patch({ starting: true, error: null });
		try {
			const region = task.region === 'none' || !areaMarked ? null : await deps.landRegion();
			if (task.region === 'required' && !region) return fail('Mark the area first.');
			const resolved = await resolveFor(taskId, choice);
			if ('error' in resolved) return fail(resolved.error);
			const stepId = nextStepId();
			const candidates = current.results[taskId] ?? 1;
			const submitted = await submit(taskId, stepId, { prompt, setup: resolved, region, candidates, seed: null });
			if (!('run' in submitted)) return fail(submitted.error);
			const run = submitted.run;
			const params: GenerateParams = {
				v: 1,
				task: taskId,
				prompt: task.prompt === 'none' ? '' : prompt.trim(),
				setup: resolved,
				area: { region, size: region ? deps.areaSize() : null },
				candidates,
				results: [],
				chosen: 0,
				seed: null,
				rect: run.rect,
				soft: false,
				run: { id: run.id, status: run.status, error: null }
			};
			const stack = apply(beginEdit(deps.getStack(), generateDraft(params, task.label, task.icon)));
			deps.setStack(stack);
			const created = stack.steps[stack.steps.length - 1];
			if (created && created.id !== stepId) deps.engine.adopt(run, created.id);
			patch({ activeStepId: created?.id ?? stepId });
			if (run.status === 'done' || run.status === 'failed' || run.status === 'cancelled') settle(get(deps.engine.views)[run.id]);
			return true;
		} catch (error) {
			return fail(failureText(error) === editorTaskFailureMessage({ kind: 'network' }) ? 'The marked area or the picture could not be sent. Try again.' : failureText(error));
		} finally {
			patch({ starting: false });
		}
	}

	function fail(message: string): false {
		patch({ error: message, starting: false });
		return false;
	}

	async function rerun(stepId: string, keepSeed: boolean): Promise<boolean> {
		const found = generateSteps(deps.getStack()).find((entry) => entry.step.id === stepId);
		if (!found || get(state).starting) return false;
		const { params, step } = found;
		const task = taskOf(params.task);
		patch({ starting: true, error: null });
		try {
			const form = { ...params.setup.form_data };
			if (!keepSeed) {
				const schema = await deps.ports.schema(params.setup.preset_id, params.setup.mode, params.setup.variant).catch(() => null);
				const field = seedFieldOf(schema);
				if (field) form[field] = -1;
			}
			const setup: ResolvedSetup = { ...params.setup, form_data: form };
			const submitted = await submit(params.task, stepId, { prompt: params.prompt, setup, region: params.area.region, candidates: params.candidates, seed: keepSeed ? params.seed : null });
			if (!('run' in submitted)) return fail(submitted.error);
			const run = submitted.run;
			const old = params.run.id;
			if (old !== run.id) deps.engine.release(old);
			const next = withFreshRun({ ...params, rect: run.rect ?? params.rect }, { id: run.id, status: run.status });
			const draft = generateDraft(next, step.label || task?.label || 'Generate', step.icon || task?.icon || 'sparkles');
			deps.setStack(commitLive(liveEdit(deps.getStack(), draft, (entry) => entry.id === stepId)));
			patch({ activeStepId: stepId });
			return true;
		} finally {
			patch({ starting: false });
		}
	}

	async function cancel(stepId: string): Promise<void> {
		const found = generateSteps(deps.getStack()).find((entry) => entry.step.id === stepId);
		if (!found) return;
		const outcome = await deps.engine.cancel(found.params.run.id);
		if (!outcome.ok) patch({ error: outcome.reason === 'refused' ? 'It could not be cancelled. Try again.' : 'The connection was lost. Try again.' });
	}

	function choose(stepId: string, index: number): void {
		const stack = deps.getStack();
		const found = generateSteps(stack).find((entry) => entry.step.id === stepId);
		if (!found) return;
		const next = withChosen(found.params, index);
		if (next === found.params) return;
		const draft = generateDraft(next, found.step.label, found.step.icon);
		deps.setStack(commitLive(liveEdit(stack, draft, (entry) => entry.id === stepId)));
	}

	function settle(view: { stepId: string | null; status: string; error: string | null; run: EditorRun | null } | undefined): void {
		if (!view?.stepId || disposed) return;
		const stack = deps.getStack();
		const next = replaceStepParams(stack, view.stepId, (params) => {
			if (view.run) return withRun(params, view.run);
			return { ...params, run: { ...params.run, status: view.status as GenerateParams['run']['status'], error: view.error } };
		});
		if (next !== stack) deps.setStack(next);
	}

	function resume(stack: EditStackState<unknown>): void {
		if (disposed) return;
		for (const entry of stepsToResume(stack)) {
			if (!deps.engine.tracking(entry.runId)) void deps.engine.attach(entry.runId, entry.id, entry.status);
		}
	}

	function stepRemoved(stepId: string, stack: EditStackState<unknown>): void {
		const step = stack.steps.find((entry) => entry.id === stepId);
		if (!step || step.tool !== GENERATE_TOOL) return;
		const params = readGenerateParams(step.params);
		if (params) void deps.engine.remove(params.run.id);
		if (get(state).activeStepId === stepId) patch({ activeStepId: null });
	}

	function allRemoved(stack: EditStackState<unknown>): void {
		for (const entry of generateSteps(stack)) void deps.engine.remove(entry.params.run.id);
		patch({ activeStepId: null });
	}

	function dispose(): void {
		disposed = true;
		deps.engine.dispose();
	}

	return {
		ui: { subscribe: state.subscribe } as Readable<GenerateUi>,
		loadTasks,
		prepare,
		use,
		chipFor,
		changedFor,
		settings,
		tweak,
		discardTweaks,
		saveTo,
		saveAsNew,
		setPrompt,
		setResults,
		openPicker,
		closePicker,
		pick,
		clearDefault,
		generate,
		retry: (stepId: string) => rerun(stepId, true),
		tryAgain: (stepId: string) => rerun(stepId, false),
		cancel,
		choose,
		settle,
		resume,
		stepRemoved,
		allRemoved,
		activate: (stepId: string | null) => patch({ activeStepId: stepId }),
		clearError: () => patch({ error: null }),
		loadChoices,
		choicesOf,
		taskOf,
		defaultsChoice,
		sameChoice,
		dispose
	};
}

export type GenerateController = ReturnType<typeof createGenerateController>;
export const GENERATE_CONTEXT = Symbol('image-editor-generate');
