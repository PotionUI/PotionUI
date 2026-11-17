import type { ImageEditPlan } from '$lib/media/imageEditPlan';
import type { ToolDTO } from '$lib/media-tools/types';
import type { EditStep, LanePair, StepDraft } from '$lib/tool-tabs/frame/editStack';
import type { RailGroup } from '$lib/tool-tabs/frame/types';
import { railFor, railTools } from '$lib/editors/railTools';
import { stepKinds } from '$lib/editors/stepKinds';
import { ensureCoreImageEditor } from './coreEditor';
import { imageEffects } from './shaderEffect';
import { sliceSummary, type SliceParams } from './slice/model';

export const GEOMETRY_LANE = 'geometry';
export const PAINT_LANE = 'paint';
export const OUTPUT_LANE = 'output';

export const IMAGE_PINNED_LANES: readonly LanePair[] = [[PAINT_LANE, GEOMETRY_LANE]];

export type CoreImageTool = 'crop' | 'resize' | 'canvas' | 'slice' | 'paint' | 'mask' | 'effects' | 'adjust' | 'colorKey' | 'subject';

export interface CropParams {
	plan: ImageEditPlan;
	aspect: string;
}

export interface ResizeParams {
	width: number;
	height: number;
	lock: boolean;
}

export interface PaintParams {
	layer: string;
	url: string;
	layers: number;
}

export interface LookParams {
	kind: 'look';
	filter: string;
	name: string;
	intensity: number;
}

export interface AdjustParams {
	kind: 'adjust';
	op: string;
	name: string;
	values: Record<string, number>;
}

export interface ToolEffectParams {
	kind: 'effect';
	tool: string;
	name: string;
	values: Record<string, unknown>;
}

export type EffectParams = LookParams | AdjustParams | ToolEffectParams;

export interface CatalogParams {
	values: Record<string, unknown>;
}

export const CATALOG_TOOL: Record<string, string> = {
	canvas: 'fit_canvas',
	colorKey: 'color_key',
	subject: 'crop_subject'
};

const HANDLED_CATALOG_IDS = new Set(['crop', 'resize', 'rotate', 'flip', 'fit_canvas', 'color_key', 'crop_subject', 'paint_layer', 'place_patch', 'slice']);

export const CORE_TOOLS: Record<CoreImageTool, { label: string; icon: string; lane?: string }> = {
	crop: { label: 'Crop', icon: 'editor-crop', lane: GEOMETRY_LANE },
	resize: { label: 'Resize', icon: 'editor-resize', lane: GEOMETRY_LANE },
	canvas: { label: 'Canvas', icon: 'editor-canvas', lane: GEOMETRY_LANE },
	slice: { label: 'Slice', icon: 'editor-slice', lane: OUTPUT_LANE },
	paint: { label: 'Paint', icon: 'editor-paint', lane: PAINT_LANE },
	mask: { label: 'Mask', icon: 'editor-mask' },
	effects: { label: 'Looks', icon: 'editor-effects' },
	adjust: { label: 'Adjust', icon: 'adjustments-horizontal' },
	colorKey: { label: 'Color key', icon: 'editor-color-key' },
	subject: { label: 'Subject', icon: 'editor-subject', lane: GEOMETRY_LANE }
};

export interface CoreRailGroup {
	id: string;
	label: string;
	fold?: number;
	tools: readonly CoreImageTool[];
}

export const CORE_RAIL: readonly CoreRailGroup[] = [
	{ id: 'shape', label: 'Transform', fold: 4, tools: ['crop', 'resize', 'canvas', 'slice'] },
	{ id: 'paint', label: 'Paint', tools: ['paint'] },
	{ id: 'look', label: 'Light and colour', fold: 2, tools: [] },
	{ id: 'fx', label: 'Effects', fold: 1, tools: [] },
	{ id: 'looks', label: 'Looks', tools: ['effects'] },
	{ id: 'tools', label: 'Cut out', fold: 3, tools: ['colorKey', 'subject'] }
];

export const LEGACY_TOOL_ALIAS: Readonly<Record<string, string>> = { adjust: 'light', mask: 'edit_region' };

export function isRailTool(id: string | null | undefined, catalog: readonly ToolDTO[] = []): id is string {
	return !!id && (isCoreTool(id) || !!railTools.get('image', id) || editorCatalogTools(catalog).some((tool) => tool.id === id));
}

export function isCoreTool(id: string): id is CoreImageTool {
	return id in CORE_TOOLS;
}

export function catalogToolId(tool: string): string {
	return CATALOG_TOOL[tool] ?? tool;
}

export function toolMeta(id: string, catalog: readonly ToolDTO[] = []): { label: string; icon: string; lane?: string } {
	if (isCoreTool(id)) return CORE_TOOLS[id];
	const registered = railTools.get('image', id);
	if (registered) {
		const lane = stepKinds.get('image', id)?.lane;
		return lane ? { label: registered.label, icon: registered.icon, lane } : { label: registered.label, icon: registered.icon };
	}
	const entry = catalog.find((tool) => tool.id === id);
	return { label: entry?.label ?? id, icon: entry?.icon ?? 'extension' };
}

export function editorCatalogTools(catalog: readonly ToolDTO[]): ToolDTO[] {
	return catalog.filter(
		(tool) =>
			!HANDLED_CATALOG_IDS.has(tool.id) &&
			tool.scopes.includes('editor') &&
			tool.accepts.includes('image') &&
			tool.cardinality === 'one_to_one' &&
			(tool.produces === 'same' || tool.produces === 'image') &&
			tool.runnable
	);
}

export function imageRail(catalog: readonly ToolDTO[] = []): RailGroup[] {
	ensureCoreImageEditor();
	const catalogTools = editorCatalogTools(catalog)
		.filter((tool) => !railTools.get('image', tool.id))
		.map((tool) => ({ id: tool.id, label: tool.label, icon: tool.icon || 'extension' }));
	const rail = railFor('image', { editor: 'image', hasSource: true }, { tools: catalogTools });
	if (!catalog.length) return rail;
	const present = new Set(imageEffects(catalog).map((effect) => effect.id));
	return rail.map((group) => ({
		...group,
		tools: group.tools.map((tool) => (EFFECT_BACKED.has(tool.id) && !present.has(tool.id) ? { ...tool, disabled: true, reason: 'Not available on this server' } : tool))
	}));
}

const EFFECT_BACKED: ReadonlySet<string> = new Set(['lut', 'film_grain', 'vignette']);

export function pluralLayers(count: number): string {
	return `${count} ${count === 1 ? 'layer' : 'layers'}`;
}

function draft<P>(tool: string, label: string, summary: string, params: P, catalog: readonly ToolDTO[] = []): StepDraft<P> {
	const meta = toolMeta(tool, catalog);
	const next: StepDraft<P> = { tool, icon: meta.icon, label, summary, params };
	if (meta.lane) next.lane = meta.lane;
	return next;
}

export function cropDraft(params: CropParams, aspectLabel: string, output: { width: number; height: number }): StepDraft<CropParams> {
	return draft('crop', 'Crop', `${aspectLabel} · ${output.width}×${output.height}`, params);
}

export function resizeDraft(params: ResizeParams): StepDraft<ResizeParams> {
	return draft('resize', 'Resize', `${params.width}×${params.height} · Lanczos`, params);
}

export function paintDraft(params: PaintParams, size: { width: number; height: number }): StepDraft<PaintParams> {
	return draft('paint', `Paint · ${pluralLayers(params.layers)}`, `${size.width}×${size.height}`, params);
}

export function sliceDraft(params: SliceParams): StepDraft<SliceParams> {
	return draft('slice', 'Slice', sliceSummary(params), params);
}

export function lookDraft(params: LookParams): StepDraft<LookParams> {
	return draft('effects', params.name, `Intensity ${Math.round(params.intensity)}`, params);
}

function formatValue(value: unknown): string {
	if (typeof value === 'number') return Number.isInteger(value) ? String(value) : String(Math.round(value * 100) / 100);
	if (typeof value === 'boolean') return value ? 'on' : 'off';
	return String(value);
}

export function adjustSummary(values: Record<string, unknown>, labels: Record<string, string> = {}): string {
	const parts = Object.entries(values)
		.filter(([, value]) => typeof value === 'number' || typeof value === 'boolean' || typeof value === 'string')
		.slice(0, 3)
		.map(([key, value]) => `${labels[key] ?? key} ${formatValue(value)}`);
	return parts.join(' · ') || 'On';
}

export function adjustDraft(params: AdjustParams, labels: Record<string, string> = {}): StepDraft<AdjustParams> {
	return draft('effects', params.name, adjustSummary(params.values, labels), params);
}

export function toolEffectDraft(params: ToolEffectParams, catalog: readonly ToolDTO[] = []): StepDraft<ToolEffectParams> {
	const entry = catalog.find((candidate) => candidate.id === params.tool);
	const labels = Object.fromEntries((entry?.params ?? []).map((param) => [param.name, param.label ?? param.name]));
	return draft('effects', params.name, adjustSummary(params.values, labels), params);
}

export function isToolEffect(params: unknown): params is ToolEffectParams {
	return (params as EffectParams | null)?.kind === 'effect';
}

export function catalogDraft(tool: string, values: Record<string, unknown>, catalog: readonly ToolDTO[] = []): StepDraft<CatalogParams> {
	const meta = toolMeta(tool, catalog);
	const entry = catalog.find((candidate) => candidate.id === catalogToolId(tool));
	const labels = Object.fromEntries((entry?.params ?? []).map((param) => [param.name, param.label ?? param.name]));
	return draft(tool, meta.label, adjustSummary(values, labels), { values }, catalog);
}

export function isEffect(step: Pick<EditStep, 'tool'>): boolean {
	return step.tool === 'effects';
}

export const ADJUST_OPS = ['tone', 'exposure', 'white_balance', 'curves', 'vibrance', 'split_tone', 'fade', 'vignette', 'grain', 'grayscale', 'invert'];

export const CURVE_POINTS = [
	{ id: 'shadows', label: 'Shadows', x: 0.25 },
	{ id: 'midtones', label: 'Midtones', x: 0.5 },
	{ id: 'highlights', label: 'Highlights', x: 0.75 }
] as const;

export function curveMaster(values: Record<string, number>): [number, number][] {
	const lift = (x: number, id: string) => Math.min(1, Math.max(0, x + (Number(values[id]) || 0) / 400));
	return [[0, 0], ...CURVE_POINTS.map((point): [number, number] => [point.x, lift(point.x, point.id)]), [1, 1]];
}

export function adjustRecipeStep(params: AdjustParams): Record<string, unknown> & { op: string } {
	if (params.op === 'curves') return { op: 'curves', master: curveMaster(params.values) };
	return { op: params.op, ...params.values };
}

export const BUNDLE_OPS: Readonly<Record<string, readonly string[]>> = {
	light: ['exposure', 'tone', 'fade'],
	colour: ['white_balance', 'tone', 'vibrance']
};

export function isBundleOp(op: string): boolean {
	return op in BUNDLE_OPS;
}

export function adjustRecipeSteps(params: AdjustParams): (Record<string, unknown> & { op: string })[] {
	const ops = BUNDLE_OPS[params.op];
	if (!ops) return [adjustRecipeStep(params)];
	return ops.map((op) => {
		const step: Record<string, unknown> & { op: string } = { op };
		for (const [key, value] of Object.entries(params.values)) {
			const at = key.indexOf('.');
			if (at > 0 && key.slice(0, at) === op) step[key.slice(at + 1)] = value;
		}
		return step;
	});
}
