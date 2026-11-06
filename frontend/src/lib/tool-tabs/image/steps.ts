import type { ImageEditPlan } from '$lib/media/editors/imageEditPlan';
import type { ToolDTO } from '$lib/media-tools/types';
import type { EditStep, LanePair, StepDraft } from '$lib/tool-tabs/frame/editStack';
import type { RailGroup } from '$lib/tool-tabs/frame/types';
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

const HANDLED_CATALOG_IDS = new Set(['crop', 'resize', 'rotate', 'flip', 'fit_canvas', 'color_key', 'crop_subject', 'paint_layer', 'slice']);

const CORE_TOOLS: Record<CoreImageTool, { label: string; icon: string; lane?: string }> = {
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

export function isRailTool(id: string | null | undefined, catalog: readonly ToolDTO[] = []): id is string {
	return !!id && (isCoreTool(id) || editorCatalogTools(catalog).some((tool) => tool.id === id));
}

export function isCoreTool(id: string): id is CoreImageTool {
	return id in CORE_TOOLS;
}

export function catalogToolId(tool: string): string {
	return CATALOG_TOOL[tool] ?? tool;
}

export function toolMeta(id: string, catalog: readonly ToolDTO[] = []): { label: string; icon: string; lane?: string } {
	if (isCoreTool(id)) return CORE_TOOLS[id];
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
	const rail = (id: CoreImageTool) => ({ id, label: CORE_TOOLS[id].label, icon: CORE_TOOLS[id].icon });
	return [
		{ id: 'shape', label: 'Shape', tools: [rail('crop'), rail('resize'), rail('canvas'), rail('slice')] },
		{ id: 'paint', label: 'Paint', tools: [rail('paint'), rail('mask')] },
		{ id: 'look', label: 'Look', tools: [rail('effects'), rail('adjust')] },
		{
			id: 'tools',
			label: 'Tools',
			tools: [rail('colorKey'), rail('subject'), ...editorCatalogTools(catalog).map((tool) => ({ id: tool.id, label: tool.label, icon: tool.icon || 'extension' }))]
		}
	];
}

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
