import { editorToolIcons } from '$lib/utils/iconGlyphs';
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
	type TaskOffer,
	type TimeRange
} from '$lib/services/api/editorTasks';
import type { PresetInfo, SessionVersionDetail, SessionVersionSummary } from '$lib/types/api';
import { appliedSteps, apply, beginEdit, commitLive, liveEdit, type EditStackState, type EditStep } from '$lib/tool-tabs/frame/editStack';
import { missingModelMessage, type MissingModelField } from '$lib/generation/requiredModelField';
import type { FormulaApplication, FormulaHost } from '$lib/formulas/types';
import type { EditLink } from '../editLink';
import { expandUi, paramsOf, retarget, targetsOf, type ExpandTarget } from './expand';
import { addApplied, appliedFormulaOf, withoutApplied, type AppliedFormula } from './formulas';
import type { RunEngine } from './runEngine';
import {
	GENERATE_TOOL,
	GENERATE_VERSION,
	basisFor,
	generateDraft,
	generateSteps,
	migrateParams,
	readGenerateParams,
	remapArea,
	replaceStepParams,
	shapeChange,
	stepsBeneathOf,
	stepsToResume,
	withChosen,
	withFreshRun,
	withRun,
	type ExpandParams,
	type GenerateBasis,
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
	layerOf,
	firstChoiceOf,
	offersOfPreset,
	type FormulaChoice,
	type ModeChoices,
	type SessionChoice,
	type SetupChoice
} from './setup';

import { runOptionsOf, withOption, type TaskOptions } from './taskOptions';
import { FIND_SUBJECT_TASK, backgroundDraft, directBlockedReason, initialBackground, paramsFor, pictureTarget, type BackgroundChoice, type BackgroundPicture } from './backgroundStep';
import { SUBJECT_FAILED, SUBJECT_NEEDS_MODEL, SUBJECT_NOT_MARKED, idleSubject, subjectChoice, type SubjectState } from './subject';
import { isTerminal, type RunView } from './runEngine';
import type { MediaRef } from '$lib/types/tabs';
import { getSchemaDefaults } from '$lib/form/defaults';
import {
	editTweak,
	isChanged,
	loadedTweak,
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

export const MAX_RESULTS = 4;
export const NOT_SAVED_MESSAGE = 'Your edit is not saved yet. Check your connection and try again.';
export const NO_PICTURE_MESSAGE = 'This picture has to be in your Library or History first.';
export const CHOOSE_SETUP = 'Choose a setup';

export interface ControllerPorts {
	tasks(): Promise<EditorTaskInfo[]>;
	sessions(presetId: string): Promise<EditorSessionEntry[]>;
	formulas(presetId: string, mode: string): Promise<Array<{ id: string; name: string; values: Record<string, unknown> }>>;
	schema(presetId: string, mode: string): Promise<{ properties?: Record<string, any> } | null>;
	presets?(): Promise<PresetInfo[]>;
	pinSession?(sessionId: string, pinned: boolean): Promise<void>;
	deleteSession?(sessionId: string): Promise<void>;
	versions?(sessionId: string): Promise<SessionVersionSummary[]>;
	version?(sessionId: string, versionNumber: number): Promise<SessionVersionDetail | null>;
	seed?(input: { presetId: string; schema: { properties?: Record<string, any> }; incoming: Record<string, unknown> }): Promise<Record<string, unknown> | null>;
	missingModel?(schema: { properties?: Record<string, any> }, values: Record<string, unknown>): MissingModelField | null;
	setDefault(taskId: string, body: { preset_id: string; mode: string; setup: SetupRef }): Promise<void>;
	clearDefault(taskId: string): Promise<void>;
	saveSession(body: SaveEditorSessionBody): Promise<SavedEditorSession>;
	updateSession(sessionId: string, body: UpdateEditorSessionBody): Promise<SavedEditorSession>;
}

export interface ControllerDeps {
	getStack(): EditStackState<unknown>;
	setStack(next: EditStackState<unknown>): void;
	source(): { ref: MediaToolInputRef | null };
	editLink(): Promise<EditLink | null>;
	landRegion(soft?: boolean): Promise<{ item_id: string; url?: string } | null>;
	areaSize(): { width: number; height: number } | null;
	pictureSize?(stepId: string | null): { width: number; height: number } | null;
	stepsBeneath(stepId: string | null): Promise<MediaToolStep[]>;
	ports: ControllerPorts;
	engine: RunEngine;
	editor?: 'image' | 'video';
	range?(): TimeRange | null;
	inputFor?(stepId: string | null): MediaToolInputRef | null;
	basisOf?(stepId: string | null): GenerateBasis;
	beneath?(stepId: string | null): EditStep[];
	onTasks?(tasks: EditorTaskInfo[]): void;
	applyMatte?(url: string): Promise<boolean>;
	landPicture?(source: MediaRef | File): Promise<{ item_id: string; url: string | null; name: string | null } | null>;
	spawnEdit?(stack: EditStackState<unknown>, name: string): Promise<EditLink | null>;
	saveSpawned?(link: EditLink, stack: EditStackState<unknown>): Promise<boolean>;
}

export interface PickerState {
	open: boolean;
	why: 'first_use' | 'broken_default' | 'nothing_qualifies' | 'change' | null;
	notice: string | null;
}

export interface RedoState {
	stepId: string;
	taskId: string;
	setup: ResolvedSetup;
	choice: string | null;
	seed: number | null;
	candidates: number;
}

export type RedoOutcome = { kind: 'started' } | { kind: 'area'; task: string } | { kind: 'failed' };

export const REDO_AREA_MESSAGE = 'Mark the area again: the picture beneath changed shape';

export interface GenerateUi {
	loaded: boolean;
	tasks: EditorTaskInfo[];
	catalog: PresetInfo[];
	reloads: number;
	formulas: Record<string, AppliedFormula[]>;
	prompts: Record<string, string>;
	options: Record<string, TaskOptions>;
	background: BackgroundChoice;
	subject: SubjectState;
	choice: Record<string, SetupChoice | null>;
	choices: Record<string, ModeChoices>;
	picker: PickerState;
	starting: boolean;
	error: string | null;
	activeStepId: string | null;
	taskId: string | null;
	tweaks: Record<string, Tweak>;
	redo: RedoState | null;
	spawned: string[];
}

export interface SettingsContext {
	offer: TaskOffer;
	choice: SetupChoice;
	key: string;
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

export function countOf(offer: Pick<TaskOffer, 'quantity_field'> | null, values: Record<string, unknown>): number {
	const field = offer?.quantity_field;
	return field ? clampResults(Number(values[field])) : 1;
}

export function clampResults(value: number): number {
	return Number.isFinite(value) ? Math.max(1, Math.min(MAX_RESULTS, Math.trunc(value))) : 1;
}

function failureText(error: unknown): string {
	return editorTaskFailureMessage(describeEditorTaskFailure(error));
}

export const NOT_SENT_MESSAGE = 'The marked area or the picture could not be sent. Try again.';
export const AREA_UNREADABLE_MESSAGE = 'The marked area could not be read. Mark it again and try again.';
export const NOT_STARTED_MESSAGE = 'This edit could not be started. Try again.';

function isTransportFailure(error: unknown): boolean {
	if (!error || typeof error !== 'object') return false;
	const candidate = error as { isAxiosError?: unknown; request?: unknown; code?: unknown; message?: unknown; response?: unknown };
	if (candidate.response) return false;
	return candidate.isAxiosError === true || candidate.request !== undefined || candidate.code === 'ERR_NETWORK' || candidate.message === 'Network Error';
}

function hasResponse(error: unknown): boolean {
	return typeof (error as { response?: { status?: unknown } } | null)?.response?.status === 'number';
}

function startFailureText(error: unknown, otherwise: string): string {
	if (hasResponse(error)) return failureText(error);
	if (isTransportFailure(error)) return NOT_SENT_MESSAGE;
	console.error('[image editor] a run could not be started', error);
	return otherwise;
}

function missingModelText(missing: MissingModelField): string {
	return `${missingModelMessage(missing)}. Open Settings to choose it.`;
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
		catalog: [],
		reloads: 0,
		formulas: {},
		prompts: {},
		options: {},
		background: initialBackground(),
		subject: idleSubject(),
		choice: {},
		choices: {},
		picker: closedPicker(),
		starting: false,
		error: null,
		activeStepId: null,
		taskId: null,
		tweaks: {},
		redo: null,
		spawned: []
	});
	let disposed = false;
	let tasksLoad: Promise<void> | null = null;
	const choiceLoads = new Map<string, Promise<void>>();

	function patch(change: Partial<GenerateUi>): void {
		state.update((current) => ({ ...current, ...change }));
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
					return data ? [{ id: entry.id, name: entry.name, createdAt: entry.created_at ?? null, updatedAt: entry.updated_at, values: data.formData ?? {}, version: entry.version ?? null, pinned: entry.pinned === true }] : [];
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

	async function loadCatalog(): Promise<void> {
		if (!deps.ports.presets || get(state).catalog.length) return;
		const list = await deps.ports.presets().catch(() => []);
		if (!disposed && list.length) patch({ catalog: list });
	}
	
	async function prepare(taskId: string): Promise<void> {
		await loadTasks();
		void loadCatalog();
		const task = taskOf(taskId);
		if (!task) return;
		const usable = task.offers.filter((offer) => offer.qualifies);
		await Promise.all(usable.map(loadChoices));
		if (disposed) return;
		const plan = planStart({
			task,
			offers: task.offers,
			taskDefault: task.default,
			picked: get(state).choice[taskId] ?? null
			});
		if (plan.action === 'run') {
			state.update((current) => ({ ...current, choice: { ...current.choice, [taskId]: plan.choice } }));
			return;
		}
		state.update((current) => ({ ...current, choice: { ...current.choice, [taskId]: null }, picker: { open: plan.why === 'first_use', why: plan.why, notice: plan.notice } }));
 }

	function use(taskId: string | null): void {
		if (get(state).taskId === taskId) return;
		const redo = get(state).redo;
		patch({ taskId, error: null, picker: closedPicker(), activeStepId: null, ...(redo && redo.taskId !== taskId ? { redo: null } : {}) });
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

	function capFor(taskId: string): number | null {
		const current = get(state);
		const choice = current.choice[taskId];
		const task = taskOf(taskId);
		if (!choice || !task) return null;
		return offerOf(task.offers, choice)?.max_range_s ?? null;
	}

	function setPrompt(taskId: string, text: string): void {
		state.update((current) => ({ ...current, prompts: { ...current.prompts, [taskId]: text }, error: null }));
	}

	function setOption(taskId: string, name: string, on: boolean): void {
		state.update((current) => ({ ...current, options: { ...current.options, [taskId]: withOption(current.options[taskId], name, on) }, error: null }));
	}

	function askForPicker(why: PickerState['why'] = 'change'): void {
		patch({ picker: { open: true, why, notice: get(state).picker.notice } });
	}
	
	function pickerHandled(): void {
		state.update((current) => (current.picker.open ? { ...current, picker: { ...current.picker, open: false } } : current));
	}
	
	async function pick(taskId: string, choice: SetupChoice): Promise<void> {
		const task = taskOf(taskId);
		const offer = task ? offerOf(task.offers, choice) : null;
		if (offer) await loadChoices(offer);
		state.update((current) => {
			const tweaks = { ...current.tweaks };
			const formulas = { ...current.formulas };
			if (tweaks[taskId]?.key !== choiceKey(choice)) {
				delete tweaks[taskId];
				delete formulas[taskId];
			}
			return { ...current, choice: { ...current.choice, [taskId]: choice }, tweaks, formulas, picker: closedPicker(), error: null };
		});
	}
	
	function choiceForPreset(taskId: string, presetId: string): SetupChoice | null {
		const task = taskOf(taskId);
		if (!task) return null;
		const remembered = task.default;
		if (remembered?.health.ok && remembered.preset_id === presetId) return { preset_id: presetId, mode: remembered.mode, setup: remembered.setup };
		return firstChoiceOf(task.offers, presetId);
	}
	
	function choiceForMode(taskId: string, mode: string): SetupChoice | null {
		const task = taskOf(taskId);
		const current = get(state).choice[taskId];
		const offer = task && current ? offersOfPreset(task.offers, current.preset_id).find((entry) => entry.mode === mode && entry.qualifies) : null;
		return offer ? defaultsChoice(offer) : null;
	}
	
	function isDefault(taskId: string): boolean {
		const task = taskOf(taskId);
		const choice = get(state).choice[taskId];
		const remembered = task?.default;
		if (!choice || !remembered) return false;
		return sameChoice(choice, { preset_id: remembered.preset_id, mode: remembered.mode, setup: remembered.setup });
	}
	
	async function setAsDefault(taskId: string, on: boolean): Promise<void> {
		if (!on) {
			if (isDefault(taskId)) await clearDefault(taskId);
			return;
		}
		const choice = get(state).choice[taskId];
		if (!choice) return;
		try {
			await deps.ports.setDefault(taskId, { preset_id: choice.preset_id, mode: choice.mode, setup: choice.setup });
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
		const schema = await deps.ports.schema(offer.preset_id, offer.mode).catch(() => null);
		const choices = choicesOf(offer);
		let resolved = resolveSetup({ offer, setup: choice.setup, schema, choices });
		if (schema && deps.ports.seed) {
			const seeded = await deps.ports.seed({ presetId: offer.preset_id, schema, incoming: layerOf(choice.setup, choices) }).catch(() => null);
			if (seeded) resolved = { ...resolved, form_data: seeded };
		}
		return { offer, schema, resolved };
	}

	async function resolveFor(taskId: string, choice: SetupChoice): Promise<ResolvedSetup | { error: string }> {
		const found = await baseFor(taskId, choice);
		if (!found) return { error: 'This setup cannot do that any more. Pick another.' };
		const { resolved, schema } = found;
		const form_data = runValues(resolved.form_data, get(state).tweaks[taskId], choiceKey(choice));
		const missing = schema ? (deps.ports.missingModel?.(schema, form_data) ?? null) : null;
		if (missing) return { error: missingModelText(missing) };
		return { ...resolved, form_data };
	}

	function changedFor(taskId: string): boolean {
		return isChanged(get(state).tweaks[taskId]);
	}

	function setTweak(taskId: string, next: Tweak | null): void {
		state.update((current) => {
			const tweaks = { ...current.tweaks };
			const formulas = { ...current.formulas };
			if (next) tweaks[taskId] = next;
			else {
				delete tweaks[taskId];
				delete formulas[taskId];
			}
			return { ...current, tweaks, formulas };
		});
	}

	async function settings(taskId: string): Promise<SettingsContext | null> {
		const choice = get(state).choice[taskId];
		if (!choice) return null;
		const found = await baseFor(taskId, choice);
		if (!found || disposed) return null;
		const { offer, schema, resolved } = found;
		const key = choiceKey(choice);
		const existing = get(state).tweaks[taskId];
		let tweak = existing && existing.key === key ? existing : null;
		if (!tweak) {
			const defaults = getSchemaDefaults(schema as never) as Record<string, unknown>;
			tweak = startTweak(key, defaults, resolved.form_data);
			setTweak(taskId, tweak);
		}
		return { offer, choice, key, base: resolved, schema, values: tweak.values, source: resolved.source, setupName: resolved.source.name ?? DEFAULTS_NAME };
	}

	function tweak(taskId: string, data: Record<string, unknown>): void {
		const current = get(state).tweaks[taskId];
		const choice = get(state).choice[taskId];
		if (!current || !choice || current.key !== choiceKey(choice)) return;
		setTweak(taskId, editTweak(current, data));
	}

	async function applyFormula(taskId: string, application: FormulaApplication): Promise<void> {
		if (!get(state).tweaks[taskId] && !(await settings(taskId))) return;
		const open = get(state).tweaks[taskId];
		if (!open) return;
		setTweak(taskId, loadedTweak(open, application.formData));
		state.update((current) => ({
			...current,
			formulas: { ...current.formulas, [taskId]: addApplied(current.formulas[taskId] ?? [], appliedFormulaOf(application)) },
			reloads: current.reloads + 1
		}));
	}

	function removeAppliedFormula(taskId: string, formulaId: string): void {
		const open = get(state).tweaks[taskId];
		const entry = get(state).formulas[taskId]?.find((item) => item.id === formulaId);
		if (!open || !entry) return;
		setTweak(taskId, loadedTweak(open, withoutApplied(open.values, entry)));
		state.update((current) => ({
			...current,
			formulas: { ...current.formulas, [taskId]: (current.formulas[taskId] ?? []).filter((item) => item.id !== formulaId) },
			reloads: current.reloads + 1
		}));
	}

	function formulaHost(taskId: string): FormulaHost {
		return { apply: (application) => void applyFormula(taskId, application) };
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

	async function saveTo(taskId: string, rename?: string): Promise<SaveOutcome> {
		const current = get(state);
		const choice = current.choice[taskId];
		const open = current.tweaks[taskId];
		const task = taskOf(taskId);
		const offer = task && choice ? offerOf(task.offers, choice) : null;
		if (!choice || !offer || choice.setup.kind !== 'session') return { ok: false, message: 'This setup cannot be saved to. Save it as a new setup instead.' };
		const session = sessionChoiceOf(offer, choice.setup.id);
		if (!session) return { ok: false, message: 'This setup no longer exists. Save it as a new setup instead.' };
		const name = rename === undefined ? session.name : rename.trim();
		if (rename !== undefined) {
			const invalid = validateSetupName(name, choicesOf(offer).sessions.filter((entry) => entry.id !== session.id).map((entry) => entry.name));
			if (invalid) return { ok: false, message: invalid };
		}
		if (!open && rename === undefined) return { ok: true };
		const values = open ? open.values : session.values;
		try {
			const saved = await deps.ports.updateSession(session.id, saveToBody({ name, version: session.version ?? null, target: offer, values }));
			patchSessions(offer, (sessions) => sessions.map((entry) => (entry.id === session.id ? { ...entry, name, values, version: saved.version ?? entry.version ?? null, updatedAt: saved.updated_at ?? entry.updatedAt } : entry)));
			const latest = get(state).tweaks[taskId];
			if (latest) setTweak(taskId, savedTweak(latest));
			return { ok: true };
		} catch (error) {
			if (saveErrorCode(error) === 'session_version_conflict') await refreshChoices(offer);
			return { ok: false, message: sessionSaveMessage(error) };
		}
	}

	async function saveAsNew(taskId: string, rawName: string): Promise<SaveOutcome> {
		if (!get(state).tweaks[taskId]) await settings(taskId);
		const current = get(state);
		const choice = current.choice[taskId];
		const open = current.tweaks[taskId];
		const task = taskOf(taskId);
		const offer = task && choice ? offerOf(task.offers, choice) : null;
		if (!choice || !offer) return { ok: false, message: 'This setup cannot be saved right now. Pick a setup first.' };
		const invalid = validateSetupName(rawName, choicesOf(offer).sessions.map((entry) => entry.name));
		if (invalid) return { ok: false, message: invalid };
		const name = rawName.trim();
		const values = open ? open.values : layerOf(choice.setup, choicesOf(offer));
		try {
			const saved = await deps.ports.saveSession(saveAsNewBody({ presetId: offer.preset_id, name, target: offer, values }));
			patchSessions(offer, (sessions) => [{ id: saved.id, name: saved.name || name, createdAt: saved.updated_at, updatedAt: saved.updated_at, values, version: saved.version, pinned: false }, ...sessions.filter((entry) => entry.id !== saved.id)]);
			const next: SetupChoice = { preset_id: choice.preset_id, mode: choice.mode, setup: { kind: 'session', id: saved.id } };
			const key = choiceKey(next);
			const start = startTweak(key, open?.defaults ?? {}, values);
			state.update((latest) => ({
				...latest,
				choice: { ...latest.choice, [taskId]: next },
				tweaks: { ...latest.tweaks, [taskId]: savedTweak({ ...start, values, settled: true }) }
			}));
			return { ok: true };
		} catch (error) {
			return { ok: false, message: sessionSaveMessage(error) };
		}
	}

	async function removeSession(taskId: string): Promise<SaveOutcome> {
		const current = get(state);
		const choice = current.choice[taskId];
		const task = taskOf(taskId);
		const offer = task && choice ? offerOf(task.offers, choice) : null;
		if (!choice || !offer || choice.setup.kind !== 'session' || !deps.ports.deleteSession) return { ok: false, message: 'This setup cannot be deleted.' };
		const id = choice.setup.id;
		try {
			await deps.ports.deleteSession(id);
		} catch (error) {
			return { ok: false, message: sessionSaveMessage(error) };
		}
		patchSessions(offer, (sessions) => sessions.filter((entry) => entry.id !== id));
		await pick(taskId, defaultsChoice(offer));
		return { ok: true };
	}

	async function pinSession(taskId: string, sessionId: string): Promise<boolean> {
		const choice = get(state).choice[taskId];
		const task = taskOf(taskId);
		const offer = task && choice ? offerOf(task.offers, choice) : null;
		const found = offer ? sessionChoiceOf(offer, sessionId) : null;
		if (!offer || !found || !deps.ports.pinSession) return true;
		const pinned = !found.pinned;
		patchSessions(offer, (sessions) => sessions.map((entry) => (entry.id === sessionId ? { ...entry, pinned } : entry)));
		try {
			await deps.ports.pinSession(sessionId, pinned);
			return true;
		} catch {
			patchSessions(offer, (sessions) => sessions.map((entry) => (entry.id === sessionId ? { ...entry, pinned: !pinned } : entry)));
			return false;
		}
	}

	async function loadVersion(taskId: string, sessionId: string, versionNumber: number): Promise<boolean> {
		const choice = get(state).choice[taskId];
		const task = taskOf(taskId);
		const offer = task && choice ? offerOf(task.offers, choice) : null;
		if (!offer || !deps.ports.version) return false;
		const detail = await deps.ports.version(sessionId, versionNumber).catch(() => null);
		const values = detail?.data?.[offer.mode]?.formData;
		if (!values || typeof values !== 'object') return false;
		if (choice!.setup.kind !== 'session' || choice!.setup.id !== sessionId) await pick(taskId, { preset_id: offer.preset_id, mode: offer.mode, setup: { kind: 'session', id: sessionId } });
		const context = await settings(taskId);
		const open = get(state).tweaks[taskId];
		if (!context || !open) return false;
		setTweak(taskId, loadedTweak(open, values as Record<string, unknown>));
		patch({ reloads: get(state).reloads + 1 });
		return true;
	}

	async function listVersions(sessionId: string): Promise<SessionVersionSummary[]> {
		return deps.ports.versions ? await deps.ports.versions(sessionId) : [];
	}

	function reloadSnapshot(taskId: string): void {
		discardTweaks(taskId);
		patch({ reloads: get(state).reloads + 1 });
	}

	function beneathOf(stepId: string | null): EditStep[] {
		return deps.beneath ? deps.beneath(stepId) : stepsBeneathOf(appliedSteps(deps.getStack()), stepId);
	}

	function nextStepId(): string {
		return `s${deps.getStack().seq + 1}`;
	}

	async function submit(taskId: string, stepId: string | null, input: {
		prompt: string;
		setup: ResolvedSetup;
		region: { item_id: string; url?: string } | null;
		range?: TimeRange | null;
		candidates: number;
		seed: number | null;
		options?: TaskOptions;
		expand?: ExpandParams | null;
		link?: EditLink;
	}): Promise<{ run: EditorRun; basis: GenerateBasis } | { error: string }> {
		const ref = deps.inputFor ? deps.inputFor(stepId) : deps.source().ref;
		if (!ref) return { error: NO_PICTURE_MESSAGE };
		const link = input.link ?? (await deps.editLink());
		if (!link) return { error: NOT_SAVED_MESSAGE };
		const task = taskOf(taskId);
		const basis = deps.basisOf ? deps.basisOf(stepId) : basisFor(beneathOf(stepId), deps.pictureSize?.(stepId) ?? null);
		const steps = await deps.stepsBeneath(stepId);
		const body: StartRunBody = {
			edit: { id: link.id, step_id: stepId },
			input: ref,
			steps,
			region: task?.region === 'none' || !input.region ? null : { item_id: input.region.item_id },
			...(deps.range ? { range: task?.range === 'none' ? null : (input.range ?? null) } : {}),
			...(input.expand ? { expand: { width: input.expand.width, height: input.expand.height, x: input.expand.x, y: input.expand.y } } : {}),
			prompt: task?.prompt === 'none' ? null : input.prompt.trim() || null,
			setup: { preset_id: input.setup.preset_id, mode: input.setup.mode, form_data: input.setup.form_data },
			candidates: input.candidates,
			seed: input.seed
		};
		const options = runOptionsOf(input.options);
		if (options) body.options = options;
		try {
			return { run: await deps.engine.start(taskId, body, stepId), basis };
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
			askForPicker('change');
			return false;
		}
		if (task.grows) return generateExpand(taskId, choice);
		const prompt = current.prompts[taskId] ?? '';
		const options = runOptionsOf(current.options[taskId]);
		if (task.prompt === 'required' && !prompt.trim()) return fail('Describe the change first.');
		if (task.region === 'required' && !areaMarked) return fail('Mark the area first.');
		patch({ starting: true, error: null });
		try {
			let region: { item_id: string; url?: string } | null = null;
			if (task.region !== 'none' && areaMarked) {
				try {
					region = await deps.landRegion(task.matte === true);
				} catch (error) {
					return fail(startFailureText(error, AREA_UNREADABLE_MESSAGE));
				}
			}
			if (task.region === 'required' && !region) return fail('Mark the area first.');
			const redo = redoTarget(taskId);
			const reused = redo && redo.choice === choiceKey(choice) && !changedFor(taskId) ? redo.state.setup : null;
			const resolved = reused ?? (await resolveFor(taskId, choice));
			if ('error' in resolved) return fail(resolved.error);
			const stepId = redo ? redo.step.step.id : nextStepId();
			const candidates = countOf(offerOf(task.offers, choice), resolved.form_data);
			const range = deps.range ? deps.range() : undefined;
			const submitted = await submit(taskId, stepId, { prompt, setup: resolved, region, range, candidates, seed: redo ? redo.state.seed : null, options });
			if (!('run' in submitted)) return fail(submitted.error);
			const run = submitted.run;
			if (redo) {
				const next = withFreshRun(
					{ ...redo.step.params, v: GENERATE_VERSION, prompt: task.prompt === 'none' ? '' : prompt.trim(), setup: resolved, area: { region, size: region ? deps.areaSize() : null }, candidates, rect: run.rect ?? redo.step.params.rect, options },
					{ id: run.id, status: run.status },
					submitted.basis
				);
				if (run.id !== redo.step.params.run.id) deps.engine.release(redo.step.params.run.id);
				const draft = generateDraft(next, redo.step.step.label || task.label, redo.step.step.icon || task.icon);
				deps.setStack(commitLive(liveEdit(deps.getStack(), draft, (entry) => entry.id === stepId)));
				patch({ activeStepId: stepId, redo: null });
				if (run.status === 'done' || run.status === 'failed' || run.status === 'cancelled') settle(get(deps.engine.views)[run.id]);
				return true;
			}
			const params: GenerateParams = {
				v: GENERATE_VERSION,
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
				run: { id: run.id, status: run.status, error: null },
				basis: submitted.basis,
				...(range === undefined ? {} : { range }),
				...(options ? { options } : {})
			};
			const stack = apply(beginEdit(deps.getStack(), generateDraft(params, task.label, task.icon)));
			deps.setStack(stack);
			const created = stack.steps[stack.steps.length - 1];
			if (created && created.id !== stepId) deps.engine.adopt(run, created.id);
			patch({ activeStepId: created?.id ?? stepId });
			if (run.status === 'done' || run.status === 'failed' || run.status === 'cancelled') settle(get(deps.engine.views)[run.id]);
			return true;
		} catch (error) {
			return fail(startFailureText(error, NOT_STARTED_MESSAGE));
		} finally {
			patch({ starting: false });
		}
	}

	function setBackground(change: Partial<BackgroundChoice>): void {
		state.update((current) => ({ ...current, background: { ...current.background, ...change }, error: null }));
	}

	async function chooseBackgroundPicture(source: MediaRef | File): Promise<boolean> {
		let picture: BackgroundPicture | null = source instanceof File ? null : pictureTarget(source);
		if (!picture && deps.landPicture) picture = await deps.landPicture(source).catch(() => null);
		if (!picture) return fail('That picture could not be used. Try another.');
		setBackground({ picture, mode: 'picture' });
		return true;
	}

	function subjectFailed(message: string): false {
		patch({ subject: { status: 'failed', progress: 0, error: message } });
		return false;
	}

	function untilSettled(runId: string): Promise<RunView> {
		return new Promise((resolve) => {
			let off: (() => void) | null = null;
			let done = false;
			off = deps.engine.views.subscribe((all) => {
				const view = all[runId];
				if (!view || done) return;
				if (isTerminal(view.status)) {
					done = true;
					off?.();
					resolve(view);
					return;
				}
				const subject = get(state).subject;
				if (subject.status === 'running' && subject.progress !== view.progress) patch({ subject: { ...subject, progress: view.progress } });
			});
			if (done) off();
		});
	}

	async function findSubject(): Promise<boolean> {
		if (get(state).subject.status === 'running') return false;
		await loadTasks();
		const info = taskOf(FIND_SUBJECT_TASK);
		const choice = subjectChoice(info);
		if (!info || !choice) return subjectFailed(SUBJECT_NEEDS_MODEL);
		patch({ subject: { status: 'running', progress: 0, error: null }, error: null });
		try {
			const resolved = await resolveFor(FIND_SUBJECT_TASK, choice);
			if ('error' in resolved) return subjectFailed(resolved.error);
			const submitted = await submit(FIND_SUBJECT_TASK, null, { prompt: '', setup: resolved, region: null, candidates: 1, seed: null });
			if (!('run' in submitted)) return subjectFailed(submitted.error);
			const runId = submitted.run.id;
			const view = await untilSettled(runId);
			const url = view.status === 'done' ? (view.run?.candidates[0]?.url ?? null) : null;
			deps.engine.release(runId);
			if (view.status === 'cancelled') {
				patch({ subject: idleSubject() });
				return false;
			}
			if (!url) return subjectFailed(SUBJECT_FAILED);
			if (!deps.applyMatte || !(await deps.applyMatte(url))) return subjectFailed(SUBJECT_NOT_MARKED);
			patch({ subject: { status: 'done', progress: 1, error: null } });
			return true;
		} catch (error) {
			return subjectFailed(startFailureText(error, SUBJECT_FAILED));
		}
	}

	async function applyBackground(areaMarked: boolean): Promise<boolean> {
		const current = get(state);
		if (current.starting) return false;
		const reason = directBlockedReason(current.background, areaMarked);
		if (reason) return fail(reason);
		patch({ starting: true, error: null });
		try {
			let region: { item_id: string; url?: string } | null = null;
			try {
				region = await deps.landRegion(true);
			} catch (error) {
				return fail(startFailureText(error, AREA_UNREADABLE_MESSAGE));
			}
			const params = region ? paramsFor(current.background, { item_id: region.item_id, url: region.url ?? null }) : null;
			if (!params) return fail(directBlockedReason(current.background, !!region) ?? AREA_UNREADABLE_MESSAGE);
			deps.setStack(apply(beginEdit(deps.getStack(), backgroundDraft(params))));
			return true;
		} catch (error) {
			return fail(startFailureText(error, NOT_STARTED_MESSAGE));
		} finally {
			patch({ starting: false });
		}
	}

	function expandParamsOf(taskId: string, prompt: string, setup: ResolvedSetup, expand: ExpandParams, run: EditorRun, basis: GenerateBasis): GenerateParams {
		return {
			v: GENERATE_VERSION,
			task: taskId,
			prompt: prompt.trim(),
			setup,
			area: { region: null, size: null },
			candidates: 1,
			results: [],
			chosen: 0,
			seed: null,
			rect: run.rect,
			soft: false,
			run: { id: run.id, status: run.status, error: null },
			basis,
			expand
		};
	}

	async function spawnExpand(taskId: string, base: EditStackState<unknown>, prompt: string, setup: ResolvedSetup, target: ExpandTarget, label: string, icon: string): Promise<string | null> {
		if (!deps.spawnEdit || !deps.saveSpawned) return null;
		const stepId = `s${base.seq + 1}`;
		const link = await deps.spawnEdit(base, target.label);
		if (!link) return null;
		const submitted = await submit(taskId, stepId, { prompt, setup, region: null, candidates: 1, seed: null, expand: paramsOf(target), link });
		if (!('run' in submitted)) return null;
		const params = expandParamsOf(taskId, prompt, setup, paramsOf(target), submitted.run, submitted.basis);
		const stack = apply(beginEdit(base, generateDraft(params, label, icon)));
		return (await deps.saveSpawned(link, stack)) ? target.label : null;
	}

	async function generateExpand(taskId: string, choice: SetupChoice): Promise<boolean> {
		const task = taskOf(taskId);
		if (!task) return false;
		const targets = targetsOf(get(expandUi), deps.pictureSize?.(null) ?? get(expandUi).source);
		if (!targets.length) return fail('Choose a format first.');
		patch({ starting: true, error: null, spawned: [] });
		try {
			const prompt = get(state).prompts[taskId] ?? '';
			const resolved = await resolveFor(taskId, choice);
			if ('error' in resolved) return fail(resolved.error);
			const base = deps.getStack();
			const [first, ...rest] = targets;
			const stepId = nextStepId();
			const submitted = await submit(taskId, stepId, { prompt, setup: resolved, region: null, candidates: 1, seed: null, expand: paramsOf(first) });
			if (!('run' in submitted)) return fail(submitted.error);
			const run = submitted.run;
			const params = expandParamsOf(taskId, prompt, resolved, paramsOf(first), run, submitted.basis);
			const stack = apply(beginEdit(deps.getStack(), generateDraft(params, task.label, task.icon)));
			deps.setStack(stack);
			const created = stack.steps[stack.steps.length - 1];
			if (created && created.id !== stepId) deps.engine.adopt(run, created.id);
			patch({ activeStepId: created?.id ?? stepId });
			if (run.status === 'done' || run.status === 'failed' || run.status === 'cancelled') settle(get(deps.engine.views)[run.id]);
			const made: string[] = [];
			const missed: string[] = [];
			for (const target of rest) {
				try {
					const name = await spawnExpand(taskId, base, prompt, resolved, target, task.label, task.icon);
					(name ? made : missed).push(name ?? target.label);
				} catch {
					missed.push(target.label);
				}
			}
			patch({ spawned: made, ...(missed.length ? { error: `${missed.join(', ')} could not be started. Try them again.` } : {}) });
			return true;
		} catch (error) {
			return fail(startFailureText(error, NOT_STARTED_MESSAGE));
		} finally {
			patch({ starting: false });
		}
	}

	function redoTarget(taskId: string): { state: RedoState; step: { step: EditStep; params: GenerateParams }; choice: string | null } | null {
		const redo = get(state).redo;
		if (!redo || redo.taskId !== taskId) return null;
		const step = generateSteps(deps.getStack()).find((entry) => entry.step.id === redo.stepId);
		if (!step) {
			patch({ redo: null });
			return null;
		}
		return { state: redo, step, choice: redo.choice };
	}

	function fail(message: string): false {
		patch({ error: message, starting: false });
		return false;
	}

	function setupChoiceOf(params: GenerateParams): SetupChoice {
		const source = params.setup.source;
		const setup = source.kind !== 'defaults' && source.id ? { kind: source.kind, id: source.id } : { kind: 'defaults' as const };
		return { preset_id: params.setup.preset_id, mode: params.setup.mode, setup };
	}

	function askForArea(entry: { step: EditStep; params: GenerateParams }): RedoOutcome {
		const task = taskOf(entry.params.task);
		const params = (task ? migrateParams(entry.params, task.offers) : null) ?? entry.params;
		const choice = setupChoiceOf(params);
		const known = task ? offerOf(task.offers, choice) : null;
		state.update((current) => ({
			...current,
			prompts: { ...current.prompts, [params.task]: params.prompt },
			options: { ...current.options, [params.task]: params.options ?? {} },
			choice: known ? { ...current.choice, [params.task]: choice } : current.choice,
			redo: { stepId: entry.step.id, taskId: params.task, setup: params.setup, choice: known ? choiceKey(choice) : (current.choice[params.task] ? choiceKey(current.choice[params.task]!) : null), seed: params.seed, candidates: params.candidates },
			error: null
		}));
		return { kind: 'area', task: params.task };
	}

	async function rerun(stepId: string, keepSeed: boolean): Promise<RedoOutcome> {
		const found = generateSteps(deps.getStack()).find((entry) => entry.step.id === stepId);
		if (!found || get(state).starting) return { kind: 'failed' };
		const { step } = found;
			await loadTasks();
			const task = taskOf(found.params.task);
			const params = migrateParams(found.params, task?.offers ?? []);
			if (!params) {
				fail(editorTaskFailureMessage({ kind: 'task_not_offered' }));
				return { kind: 'failed' };
			}
			const change = deps.basisOf ? 'none' : shapeChange(params, beneathOf(stepId));
			if (change === 'other' && params.area.region) return askForArea({ step, params });
			patch({ starting: true, error: null });
			try {
				const form = { ...params.setup.form_data };
				if (!keepSeed) {
					const schema = await deps.ports.schema(params.setup.preset_id, params.setup.mode).catch(() => null);
				const field = seedFieldOf(schema);
				if (field) form[field] = -1;
			}
			const setup: ResolvedSetup = { ...params.setup, form_data: form };
			const area = change === 'resize' ? remapArea(params.area, params.basis?.size ?? null, deps.pictureSize?.(stepId) ?? null) : params.area;
			let expand = params.expand;
			if (expand) {
				const picture = deps.pictureSize?.(stepId) ?? null;
				const next = picture ? retarget(expand, picture) : expand;
				if (!next) {
					fail('This picture already has that shape.');
					return { kind: 'failed' };
				}
				expand = next;
			}
			const submitted = await submit(params.task, stepId, { prompt: params.prompt, setup, region: area.region, range: params.range, candidates: params.candidates, seed: keepSeed ? params.seed : null, expand, options: params.options });
			if (!('run' in submitted)) {
				fail(submitted.error);
				return { kind: 'failed' };
			}
			const run = submitted.run;
			const old = params.run.id;
			if (old !== run.id) deps.engine.release(old);
			const next = withFreshRun({ ...params, area, rect: run.rect ?? params.rect, ...(expand ? { expand } : {}) }, { id: run.id, status: run.status }, submitted.basis);
			const draft = generateDraft(next, step.label || task?.label || 'Generate', step.icon || task?.icon || editorToolIcons.edit_region);
			deps.setStack(commitLive(liveEdit(deps.getStack(), draft, (entry) => entry.id === stepId)));
			patch({ activeStepId: stepId });
			return { kind: 'started' };
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
		if (get(state).redo?.stepId === stepId) patch({ redo: null });
	}

	function allRemoved(stack: EditStackState<unknown>): void {
		for (const entry of generateSteps(stack)) void deps.engine.remove(entry.params.run.id);
		patch({ activeStepId: null, redo: null });
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
		capFor,
		changedFor,
		settings,
		tweak,
		discardTweaks,
		applyFormula,
		removeAppliedFormula,
		formulaHost,
		saveTo,
		saveAsNew,
		setPrompt,
		setOption,
		setBackground,
		chooseBackgroundPicture,
		findSubject,
		applyBackground,
		askForPicker,
		pickerHandled,
		pick,
		choiceForPreset,
		choiceForMode,
		isDefault,
		setAsDefault,
		removeSession,
		pinSession,
		loadVersion,
		reloadSnapshot,
		listVersions,
		clearDefault,
		generate,
		retry: async (stepId: string) => (await rerun(stepId, true)).kind !== 'failed',
		tryAgain: async (stepId: string) => (await rerun(stepId, false)).kind !== 'failed',
		redo: (stepId: string) => rerun(stepId, true),
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
		dispose,
		editor: deps.editor ?? 'image'
	};
}

export type GenerateController = ReturnType<typeof createGenerateController>;
export const GENERATE_CONTEXT = Symbol('image-editor-generate');
