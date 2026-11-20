export interface PixelDims {
	width: number;
	height: number;
}

export const UPSCALE_TASK = 'upscale';
export const UPSCALE_FACTORS: readonly number[] = [2, 4];
export const MAX_OUTPUT_SIDE = 16384;
export const MAX_OUTPUT_PIXELS = (MAX_OUTPUT_SIDE * MAX_OUTPUT_SIDE) / 4;
export const GEOMETRY_LANE_ID = 'geometry';

export type RunOptions = {
	scale?: number;
	target?: PixelDims;
	strength?: number;
};

export type UpscaleOutcome = { ok: true; size: PixelDims } | { ok: false; reason: 'invalid' | 'not_larger' | 'too_large' };

const resizing = new Set<string>([UPSCALE_TASK]);

export function isResizingTask(taskId: string | null | undefined): boolean {
	return !!taskId && resizing.has(taskId);
}

export function markResizingTasks(ids: Iterable<string>): void {
	for (const id of ids) resizing.add(id);
}

function positive(value: unknown): number | null {
	return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;
}

export function dimsOf(raw: unknown): PixelDims | null {
	if (typeof raw !== 'object' || raw === null) return null;
	const width = positive((raw as Record<string, unknown>).width);
	const height = positive((raw as Record<string, unknown>).height);
	return width !== null && height !== null ? { width: Math.round(width), height: Math.round(height) } : null;
}

export function readRunOptions(raw: unknown): RunOptions | null {
	if (typeof raw !== 'object' || raw === null) return null;
	const record = raw as Record<string, unknown>;
	const scale = positive(record.scale);
	const target = dimsOf(record.target);
	const strength = typeof record.strength === 'number' && Number.isFinite(record.strength) && record.strength >= 0 ? record.strength : null;
	const options: RunOptions = {};
	if (scale !== null) options.scale = scale;
	else if (target) options.target = target;
	if (strength !== null) options.strength = strength;
	return Object.keys(options).length ? options : null;
}

export function upscaleSize(source: PixelDims, options: RunOptions): UpscaleOutcome {
	if (source.width < 1 || source.height < 1) return { ok: false, reason: 'invalid' };
	let factor: number;
	if (options.scale !== undefined) {
		if (!Number.isFinite(options.scale) || options.scale <= 0) return { ok: false, reason: 'invalid' };
		factor = options.scale;
	} else if (options.target) {
		if (options.target.width < 1 || options.target.height < 1) return { ok: false, reason: 'invalid' };
		factor = Math.min(options.target.width / source.width, options.target.height / source.height);
	} else {
		return { ok: false, reason: 'invalid' };
	}
	const size = { width: Math.max(1, Math.round(source.width * factor)), height: Math.max(1, Math.round(source.height * factor)) };
	if (size.width <= source.width && size.height <= source.height) return { ok: false, reason: 'not_larger' };
	if (size.width > MAX_OUTPUT_SIDE || size.height > MAX_OUTPUT_SIDE || size.width * size.height > MAX_OUTPUT_PIXELS) return { ok: false, reason: 'too_large' };
	return { ok: true, size };
}

export function upscaleReasonText(reason: 'invalid' | 'not_larger' | 'too_large'): string {
	if (reason === 'not_larger') return 'Pick a size larger than the picture.';
	if (reason === 'too_large') return 'That size is too large. Pick a smaller factor or size.';
	return 'Enter a width and a height.';
}

export function targetFromWidth(source: PixelDims, width: number): PixelDims {
	const wide = Math.max(1, Math.round(width));
	return { width: wide, height: Math.max(1, Math.round((wide * source.height) / Math.max(1, source.width))) };
}

export function targetFromHeight(source: PixelDims, height: number): PixelDims {
	const high = Math.max(1, Math.round(height));
	return { width: Math.max(1, Math.round((high * source.width) / Math.max(1, source.height))), height: high };
}

export function scaleText(options: RunOptions | undefined): string {
	if (options?.scale) return `${Math.round(options.scale * 100) / 100}×`;
	return options?.target ? 'Custom size' : '';
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function resultOf(params: Record<string, unknown>): Record<string, unknown> | null {
	const results = Array.isArray(params.results) ? params.results : [];
	const chosen = typeof params.chosen === 'number' ? params.chosen : 0;
	const entry = results[chosen] ?? results[0];
	return isRecord(entry) ? entry : null;
}

export function resizedPictureOf(raw: unknown): { size: PixelDims; patch: string } | null {
	if (!isRecord(raw) || !isRecord(raw.run) || raw.run.status !== 'done') return null;
	const result = resultOf(raw);
	const size = result ? dimsOf(result.size) : null;
	const patch = result && isRecord(result.patch) && typeof result.patch.item_id === 'string' ? result.patch.item_id : null;
	return size && patch ? { size, patch } : null;
}

export function resizedPictureEntry(raw: unknown): [string, unknown] | null {
	const landed = resizedPictureOf(raw);
	return landed ? ['upscale', { width: landed.size.width, height: landed.size.height, patch: landed.patch }] : null;
}

export function isResizedParams(raw: unknown): boolean {
	return isRecord(raw) && (isResizingTask(typeof raw.task === 'string' ? raw.task : null) || resizedPictureOf(raw) !== null);
}

interface KeyStep {
	tool: string;
	params: unknown;
	hidden: boolean;
	layer?: { opacity: number };
}

function inFlight(step: KeyStep): boolean {
	if (step.tool !== 'generate' || !isRecord(step.params) || !isRecord(step.params.run)) return false;
	return step.params.run.status === 'queued' || step.params.run.status === 'running';
}

export function pictureKey(beneath: readonly KeyStep[]): string {
	return JSON.stringify(beneath.filter((step) => !step.hidden && step.tool !== 'slice' && !inFlight(step)).map((step) => [step.tool, step.params, step.layer?.opacity ?? 1]));
}
