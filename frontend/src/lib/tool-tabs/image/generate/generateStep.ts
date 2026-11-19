import { editorToolIcons } from '$lib/utils/iconGlyphs';
import { get } from 'svelte/store';
import { canvasFromUrl } from '$lib/paint/loadPicked';
import { timeRangeOf, type EditorRun, type NormRect, type RunStatus, type TaskOffer, type TimeRange } from '$lib/services/api/editorTasks';
import { formatEditorTime } from '$lib/tool-tabs/video/videoState';
import type { StepKind, StepState } from '$lib/editors/stepKinds';
import { opacityOf, type EditStackState, type EditStep, type StepDraft } from '$lib/tool-tabs/frame/editStack';
import { geometryKey } from '../mask';
import { isTerminal, runViews } from './runEngine';
import { readOptions, type TaskOptions } from './taskOptions';

export const GENERATE_TOOL = 'generate';
const PAINT_LANE = 'paint';
export const GENERATE_VERSION = 2;
export const EDITOR_KEY_PREFIX = 'editor:';
export const PROMPT_ROW_LIMIT = 12;

export interface SetupSource {
	kind: 'session' | 'formula' | 'defaults';
	id: string | null;
	name: string | null;
}

export interface ResolvedSetup {
	preset_id: string;
	mode: string;
	form_data: Record<string, unknown>;
	source: SetupSource;
	label: string;
}

export interface GenerateResult {
	patch: { item_id: string };
	url: string;
	seed: number | null;
	clip?: boolean;
	range?: TimeRange | null;
	added?: { at_s: number; seconds: number } | null;
	duration_s?: number | null;
}

export interface GenerateArea {
	region: { item_id: string; url?: string } | null;
	size: { width: number; height: number } | null;
}

export interface GenerateRun {
	id: string;
	status: RunStatus;
	error: string | null;
}

export interface GenerateBasis {
	geometry: string;
	size: { width: number; height: number } | null;
}

export interface GenerateParams {
	v: number;
	task: string;
	prompt: string;
	setup: ResolvedSetup;
	area: GenerateArea;
	candidates: number;
	results: GenerateResult[];
	chosen: number;
	seed: number | null;
	rect: NormRect | null;
	soft: boolean;
	run: GenerateRun;
	basis?: GenerateBasis;
	range?: TimeRange | null;
	options?: TaskOptions;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

const STATUSES: readonly RunStatus[] = ['queued', 'running', 'done', 'failed', 'cancelled'];

function resultsOf(raw: unknown): GenerateResult[] {
	return (Array.isArray(raw) ? raw : []).flatMap((entry): GenerateResult[] => {
		if (!isRecord(entry) || typeof entry.url !== 'string') return [];
		const clipId = isRecord(entry.clip) && typeof entry.clip.item_id === 'string' ? entry.clip.item_id : null;
		const patchId = isRecord(entry.patch) && typeof entry.patch.item_id === 'string' ? entry.patch.item_id : clipId;
		if (!patchId) return [];
		const result: GenerateResult = { patch: { item_id: patchId }, url: entry.url, seed: typeof entry.seed === 'number' ? entry.seed : null };
		if (clipId !== null || entry.clip === true) {
			const added = isRecord(entry.added) && typeof entry.added.at_s === 'number' && typeof entry.added.seconds === 'number' ? { at_s: entry.added.at_s, seconds: entry.added.seconds } : null;
			Object.assign(result, { clip: true, range: timeRangeOf(entry.range), added, duration_s: typeof entry.duration_s === 'number' ? entry.duration_s : null });
		}
		return [result];
	});
}

function rectOf(raw: unknown): NormRect | null {
	if (!isRecord(raw)) return null;
	const { x, y, width, height } = raw;
	return [x, y, width, height].every((value) => typeof value === 'number' && Number.isFinite(value)) ? { x: x as number, y: y as number, width: width as number, height: height as number } : null;
}

function basisOf(raw: unknown): GenerateBasis | null {
	if (!isRecord(raw) || typeof raw.geometry !== 'string') return null;
	const size = isRecord(raw.size) && typeof raw.size.width === 'number' && typeof raw.size.height === 'number' ? { width: raw.size.width, height: raw.size.height } : null;
	return { geometry: raw.geometry, size };
}

export function readGenerateParams(raw: unknown): GenerateParams | null {
	if (!isRecord(raw) || !isRecord(raw.run) || typeof raw.run.id !== 'string' || !STATUSES.includes(raw.run.status as RunStatus)) return null;
	const setup = isRecord(raw.setup) ? raw.setup : {};
	const source = isRecord(setup.source) ? setup.source : {};
	const area = isRecord(raw.area) ? raw.area : {};
	const results = resultsOf(raw.results);
	const chosen = typeof raw.chosen === 'number' && raw.chosen >= 0 && raw.chosen < results.length ? Math.trunc(raw.chosen) : 0;
	const size = isRecord(area.size) && typeof area.size.width === 'number' && typeof area.size.height === 'number' ? { width: area.size.width, height: area.size.height } : null;
	const basis = basisOf(raw.basis);
	const options = readOptions(raw.options);
	return {
		v: raw.v === GENERATE_VERSION ? GENERATE_VERSION : 1,
		task: typeof raw.task === 'string' ? raw.task : '',
		prompt: typeof raw.prompt === 'string' ? raw.prompt : '',
		setup: {
			preset_id: typeof setup.preset_id === 'string' ? setup.preset_id : '',
			mode: typeof setup.mode === 'string' ? setup.mode : '',
			form_data: isRecord(setup.form_data) ? setup.form_data : {},
			source: {
				kind: source.kind === 'session' || source.kind === 'formula' ? source.kind : 'defaults',
				id: typeof source.id === 'string' ? source.id : null,
				name: typeof source.name === 'string' ? source.name : null
			},
			label: typeof setup.label === 'string' ? setup.label : ''
		},
		area: { region: isRecord(area.region) && typeof area.region.item_id === 'string' ? { item_id: area.region.item_id, ...(typeof area.region.url === 'string' && area.region.url ? { url: area.region.url } : {}) } : null, size },
		candidates: typeof raw.candidates === 'number' && raw.candidates >= 1 ? Math.min(4, Math.trunc(raw.candidates)) : 1,
		results,
		chosen,
		seed: typeof raw.seed === 'number' ? raw.seed : null,
		rect: rectOf(raw.rect),
		soft: raw.soft === true,
		run: { id: raw.run.id, status: raw.run.status as RunStatus, error: typeof raw.run.error === 'string' ? raw.run.error : null },
		...(basis ? { basis } : {}),
		...(options ? { options } : {}),
		...('range' in raw ? { range: timeRangeOf(raw.range) } : {})
	};
}

export function isEditorKey(mode: string): boolean {
	return mode.startsWith(EDITOR_KEY_PREFIX);
}

export function setupModeOf(params: GenerateParams, offers: readonly TaskOffer[]): string | null {
	const { preset_id, mode } = params.setup;
	if (isEditorKey(mode)) return mode;
	const of = offers.filter((offer) => offer.preset_id === preset_id);
	const derived = of.filter((offer) => offer.from_mode === mode);
	const found = derived.length ? derived : of.filter((offer) => offer.mode === `${EDITOR_KEY_PREFIX}${mode}`);
	return found.length === 1 ? found[0].mode : null;
}

export function migrateParams(params: GenerateParams, offers: readonly TaskOffer[]): GenerateParams | null {
	const mode = setupModeOf(params, offers);
	if (!mode) return null;
	if (mode === params.setup.mode && params.v === GENERATE_VERSION) return params;
	return { ...params, v: GENERATE_VERSION, setup: { ...params.setup, mode } };
}

export function basisFor(beneath: readonly EditStep[], size: { width: number; height: number } | null): GenerateBasis {
	return { geometry: geometryKey(beneath), size };
}

export type ShapeChange = 'none' | 'resize' | 'other';

function geometryList(key: string): Array<[string, unknown]> | null {
	try {
		const parsed = JSON.parse(key);
		return Array.isArray(parsed) ? (parsed as Array<[string, unknown]>) : null;
	} catch {
		return null;
	}
}

export function shapeChange(params: GenerateParams, beneath: readonly EditStep[]): ShapeChange {
	if (!params.basis) return 'none';
	const now = geometryKey(beneath);
	if (now === params.basis.geometry) return 'none';
	const before = geometryList(params.basis.geometry);
	const after = geometryList(now);
	if (!before || !after) return 'other';
	const rest = (list: Array<[string, unknown]>) => list.filter((entry) => entry[0] !== 'resize');
	const kept = rest(before);
	const same = JSON.stringify(kept) === JSON.stringify(rest(after)) && kept.every((entry) => entry[0] === 'crop');
	return same ? 'resize' : 'other';
}

function scaled(value: number, from: number, to: number): number {
	return Math.min(to, Math.max(1, Math.round((value * to) / from)));
}

export function remapArea(area: GenerateArea, from: { width: number; height: number } | null, to: { width: number; height: number } | null): GenerateArea {
	if (!area.size || !from || !to || from.width <= 0 || from.height <= 0 || to.width <= 0 || to.height <= 0) return area;
	return { ...area, size: { width: scaled(area.size.width, from.width, to.width), height: scaled(area.size.height, from.height, to.height) } };
}

export function stepsBeneathOf(steps: readonly EditStep[], id: string | null): EditStep[] {
	const at = id ? steps.findIndex((step) => step.id === id) : -1;
	return (at >= 0 ? steps.slice(0, at) : steps.slice()).filter((step) => !step.hidden);
}

export function promptStart(prompt: string): string {
	const text = prompt.trim().replace(/\s+/g, ' ');
	if (text.length <= PROMPT_ROW_LIMIT) return text;
	return text.slice(0, PROMPT_ROW_LIMIT).replace(/\s+\S*$/, '') || text.slice(0, PROMPT_ROW_LIMIT);
}

export function rangeText(range: TimeRange | null | undefined): string {
	return range ? `${formatEditorTime(range.start_s)} → ${formatEditorTime(range.end_s)}` : 'Whole clip';
}

export function summaryOf(params: GenerateParams): string {
	if (params.range !== undefined) {
		const where = rangeText(params.range);
		return params.run.status === 'done' && params.results.length ? `${params.chosen + 1} of ${params.results.length} · ${where}` : where;
	}
	if (params.run.status === 'done' && params.results.length) {
		const count = `${params.chosen + 1} of ${params.results.length}`;
		const start = promptStart(params.prompt);
		return start ? `${count} · ${start}` : count;
	}
	return promptStart(params.prompt);
}

export function generateDraft(params: GenerateParams, label: string, icon: string): StepDraft<GenerateParams> {
	return { tool: GENERATE_TOOL, icon, label, summary: summaryOf(params), params, lane: PAINT_LANE };
}

export function chosenResult(params: GenerateParams): GenerateResult | null {
	return params.results[params.chosen] ?? null;
}

export function inFlight(params: GenerateParams): boolean {
	return params.run.status === 'queued' || params.run.status === 'running';
}

export function withRun(params: GenerateParams, run: EditorRun): GenerateParams {
	const results = run.status === 'done' ? resultsOf(run.candidates) : [];
	const chosen = params.chosen < results.length ? params.chosen : 0;
	const next: GenerateParams = {
		...params,
		run: { id: run.id || params.run.id, status: run.status, error: run.error },
		results,
		chosen,
		rect: run.rect ?? params.rect,
		soft: run.soft,
		seed: results[chosen]?.seed ?? params.seed
	};
	return next;
}

export function withChosen(params: GenerateParams, index: number): GenerateParams {
	if (index < 0 || index >= params.results.length || index === params.chosen) return params;
	return { ...params, chosen: index, seed: params.results[index].seed ?? params.seed };
}

export function withFreshRun(params: GenerateParams, run: { id: string; status: RunStatus }, basis?: GenerateBasis): GenerateParams {
	const next: GenerateParams = { ...params, run: { id: run.id, status: run.status, error: null }, results: [], chosen: 0, seed: null, soft: false };
	if (basis) next.basis = basis;
	return next;
}

export function generateStateOf(params: GenerateParams): StepState {
	const view = get(runViews)[params.run.id];
	const status = view?.status ?? params.run.status;
	if (status === 'queued') return { kind: 'busy', label: 'Waiting' };
	if (status === 'running') return { kind: 'busy', label: 'Generating', progress: Math.round((view?.progress ?? 0) * 100) };
	if (status === 'failed') return { kind: 'skipped', tone: 'danger', label: `Did not finish: ${view?.error ?? params.run.error ?? 'something went wrong'}` };
	if (status === 'cancelled') return { kind: 'skipped', tone: 'muted', label: 'Cancelled' };
	return { kind: 'done' };
}

export function replaceStepParams(stack: EditStackState<unknown>, id: string, update: (params: GenerateParams) => GenerateParams, label?: string): EditStackState<unknown> {
	let changed = false;
	const steps = stack.steps.map((step) => {
		if (step.id !== id || step.tool !== GENERATE_TOOL) return step;
		const current = readGenerateParams(step.params);
		if (!current) return step;
		const next = update(current);
		if (next === current) return step;
		changed = true;
		return { ...step, params: next, summary: summaryOf(next), ...(label ? { label } : {}) };
	});
	return changed ? { ...stack, steps } : stack;
}

export function generateSteps(stack: EditStackState<unknown>): Array<{ step: EditStep<unknown>; params: GenerateParams }> {
	return stack.steps.flatMap((step) => {
		if (step.tool !== GENERATE_TOOL) return [];
		const params = readGenerateParams(step.params);
		return params ? [{ step, params }] : [];
	});
}

export function stepsToResume(stack: EditStackState<unknown>): Array<{ id: string; runId: string; status: RunStatus }> {
	return generateSteps(stack)
		.filter(({ params }) => !isTerminal(params.run.status))
		.map(({ step, params }) => ({ id: step.id, runId: params.run.id, status: params.run.status }));
}

export function leftOutCount(steps: readonly EditStep[]): number {
	return steps.filter((step) => {
		if (step.tool !== GENERATE_TOOL || step.hidden) return false;
		const params = readGenerateParams(step.params);
		return !!params && inFlight(params);
	}).length;
}

export function leftOutNotice(count: number): string | null {
	if (count <= 0) return null;
	return `${count} ${count === 1 ? 'edit is' : 'edits are'} not included: ${count === 1 ? 'it is' : 'they are'} still generating.`;
}

export function patchPlacement(params: GenerateParams): { x: number; y: number; width: number; height: number } | null {
	const result = chosenResult(params);
	if (params.run.status !== 'done' || !result || !params.rect) return null;
	return { x: params.rect.x, y: params.rect.y, width: params.rect.width, height: params.rect.height };
}

export const generateStepKind: StepKind<GenerateParams> = {
	id: GENERATE_TOOL,
	editor: 'image',
	label: 'Generate',
	icon: editorToolIcons.edit_region,
	lane: PAINT_LANE,
	version: GENERATE_VERSION,
	summary: (_params, ctx) => ctx.step.summary,
	outputSize: (_params, ctx) => ctx.input,
	payload(raw) {
		const params = readGenerateParams(raw);
		const result = params ? chosenResult(params) : null;
		const place = params ? patchPlacement(params) : null;
		if (!params || !result || !place) return [{ tool: 'place_patch', params: {}, hidden: true }];
		return [{ tool: 'place_patch', params: { patch: { item_id: result.patch.item_id }, ...place } }];
	},
	async preview(frame, raw, ctx, signal) {
		const params = readGenerateParams(raw);
		const result = params ? chosenResult(params) : null;
		const place = params ? patchPlacement(params) : null;
		if (!result || !place) return frame;
		const patch = await canvasFromUrl(result.url);
		if (signal.aborted) return frame;
		const out = document.createElement('canvas');
		out.width = frame.canvas.width;
		out.height = frame.canvas.height;
		const context = out.getContext('2d', { willReadFrequently: true });
		if (!context) return frame;
		context.imageSmoothingQuality = 'high';
		context.drawImage(frame.canvas, 0, 0);
		context.globalAlpha = opacityOf(ctx.step);
		context.drawImage(patch, place.x * out.width, place.y * out.height, place.width * out.width, place.height * out.height);
		return { canvas: out, size: frame.size };
	},
	state(step) {
		const params = readGenerateParams(step.params);
		return params ? generateStateOf(params) : { kind: 'done' };
	},
	shapeBasis(raw) {
		const params = readGenerateParams(raw);
		if (!params?.basis) return null;
		const status = get(runViews)[params.run.id]?.status ?? params.run.status;
		return status === 'done' ? params.basis.geometry : null;
	},
	extras(step, state) {
		const params = readGenerateParams(step.params);
		const status = params ? (get(runViews)[params.run.id]?.status ?? params.run.status) : 'done';
		const thumb = status === 'done' && params ? (chosenResult(params)?.url ?? null) : null;
		if (state.kind === 'busy') return { actions: [{ id: 'cancel', label: 'Cancel', icon: 'close' }], hideToggle: false, removable: false };
		if (state.kind === 'skipped' && state.tone === 'warning') return { actions: [{ id: 'redo', label: 'Redo on the new picture', icon: 'refresh' }], hideToggle: step.hidden };
		if (state.kind === 'skipped') return { actions: [{ id: 'retry', label: 'Retry', icon: 'refresh' }], hideToggle: false };
		return { actions: [{ id: 'again', label: 'Try again', icon: 'refresh' }], thumb };
	},
	reopenTool: (raw) => readGenerateParams(raw)?.task || null
};
