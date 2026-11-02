import type { EditStep, StepDraft } from '$lib/tool-tabs/frame/editStack';
import type { RailGroup } from '$lib/tool-tabs/frame/types';
import type { MediaToolInputRef, MediaToolStep } from '$lib/media-tools/types';
import {
	FULL_CROP,
	clampCropRect,
	displaySize,
	isFullFrame,
	toCropOperation,
	type CropRect,
	type PixelSize,
	type Rotation
} from '$lib/media/editors/cropGeometry';
import { evenRound } from '$lib/media/editors/mediaSizeOptions';
import { outputDuration, safeSpeed, sourceToOutput, type TimeOp } from './timeMap';

export type VideoOp =
	| 'trim'
	| 'split'
	| 'crop'
	| 'resize'
	| 'speed'
	| 'mute'
	| 'volume'
	| 'replace_audio'
	| 'film_grain'
	| 'fix_colors';

export type RailToolId = 'trim' | 'split' | 'crop' | 'resize' | 'speed' | 'mute' | 'volume' | 'replace' | 'effects' | 'frame';

export interface TrimParams {
	start: number;
	end: number;
}

export interface SplitParams {
	at: number;
}

export interface CropParams {
	rect: CropRect;
	rotation: Rotation;
	flipH: boolean;
	flipV: boolean;
}

export type ResizeQuality = 'high' | 'balanced' | 'small';

export interface ResizeParams {
	short: number;
	quality: ResizeQuality;
}

export interface SpeedParams {
	speed: number;
	keep_pitch: boolean;
}

export interface VolumeParams {
	unit: 'percent' | 'db';
	percent: number;
	db: number;
	fade_in: number;
	fade_out: number;
}

export interface ReplaceParams {
	audio: MediaToolInputRef | null;
	name: string;
	start: number;
	loop: boolean;
}

export interface GrainParams {
	amount: number;
	size: number;
	color: boolean;
	seed: number;
}

export interface FixParams {
	white_balance: boolean;
	contrast: boolean;
	strength: number;
}

export type VideoParams = Record<string, unknown>;

export type VideoStep = EditStep<VideoParams>;

export interface ClipInfo {
	width: number;
	height: number;
	duration: number;
}

export const OP_LABELS: Record<VideoOp, string> = {
	trim: 'Trim',
	split: 'Split',
	crop: 'Crop',
	resize: 'Resize',
	speed: 'Speed',
	mute: 'Mute',
	volume: 'Volume',
	replace_audio: 'Replace audio',
	film_grain: 'Film grain',
	fix_colors: 'Fix colours'
};

export const OP_ICONS: Record<VideoOp, string> = {
	trim: 'editor-trim',
	split: 'editor-split',
	crop: 'editor-crop',
	resize: 'editor-resize',
	speed: 'editor-speed',
	mute: 'editor-mute',
	volume: 'editor-volume',
	replace_audio: 'editor-music',
	film_grain: 'editor-effects',
	fix_colors: 'editor-effects'
};

export const OP_LANES: Partial<Record<VideoOp, string>> = {
	trim: 'time',
	speed: 'time',
	split: 'time',
	crop: 'geometry',
	resize: 'geometry'
};

export const RAIL_GROUPS: RailGroup[] = [
	{
		id: 'shape',
		label: 'Shape',
		tools: [
			{ id: 'trim', label: 'Trim', icon: 'editor-trim' },
			{ id: 'split', label: 'Split', icon: 'editor-split' },
			{ id: 'crop', label: 'Crop', icon: 'editor-crop' },
			{ id: 'resize', label: 'Resize', icon: 'editor-resize' },
			{ id: 'speed', label: 'Speed', icon: 'editor-speed' }
		]
	},
	{
		id: 'audio',
		label: 'Audio',
		tools: [
			{ id: 'mute', label: 'Mute', icon: 'editor-mute' },
			{ id: 'volume', label: 'Volume', icon: 'editor-volume' },
			{ id: 'replace', label: 'Replace', icon: 'editor-music' }
		]
	},
	{ id: 'look', label: 'Look', tools: [{ id: 'effects', label: 'Effects', icon: 'editor-effects' }] },
	{ id: 'frame', label: 'Frame', tools: [{ id: 'frame', label: 'Frame', icon: 'editor-camera' }] }
];

export const RAIL_TOOL_IDS: RailToolId[] = RAIL_GROUPS.flatMap((group) => group.tools.map((tool) => tool.id as RailToolId));

export function isRailToolId(value: unknown): value is RailToolId {
	return typeof value === 'string' && (RAIL_TOOL_IDS as string[]).includes(value);
}

export function railToolOf(op: string): RailToolId | null {
	if (op === 'film_grain' || op === 'fix_colors') return 'effects';
	if (op === 'replace_audio') return 'replace';
	return isRailToolId(op) ? op : null;
}

export function isVideoOp(value: unknown): value is VideoOp {
	return typeof value === 'string' && value in OP_LABELS;
}

function num(value: unknown, fallback: number): number {
	const parsed = typeof value === 'number' ? value : Number(value);
	return Number.isFinite(parsed) ? parsed : fallback;
}

function round3(value: number): number {
	return Math.round(value * 1000) / 1000;
}

export function fixed2(value: number): string {
	return (Math.round(value * 100) / 100).toFixed(2);
}

function fixed1(value: number): string {
	return (Math.round(value * 10) / 10).toFixed(1);
}

export function readTrim(params: VideoParams): TrimParams {
	return { start: num(params.start, 0), end: num(params.end, 0) };
}

export function readCrop(params: VideoParams): CropParams {
	const raw = (params.rect ?? {}) as Partial<CropRect>;
	const rect = clampCropRect({
		x: num(raw.x, 0),
		y: num(raw.y, 0),
		width: num(raw.width, 1),
		height: num(raw.height, 1)
	});
	const rotation = [0, 90, 180, 270].includes(num(params.rotation, 0)) ? (num(params.rotation, 0) as Rotation) : 0;
	return { rect, rotation, flipH: params.flipH === true, flipV: params.flipV === true };
}

export function readSpeed(params: VideoParams): SpeedParams {
	return { speed: safeSpeed(num(params.speed, 1)), keep_pitch: params.keep_pitch !== false };
}

export function readVolume(params: VideoParams): VolumeParams {
	return {
		unit: params.unit === 'db' ? 'db' : 'percent',
		percent: num(params.percent, 100),
		db: num(params.db, 0),
		fade_in: Math.max(0, num(params.fade_in, 0)),
		fade_out: Math.max(0, num(params.fade_out, 0))
	};
}

export function volumeGain(params: VolumeParams): number {
	return params.unit === 'db' ? 10 ** (params.db / 20) : params.percent / 100;
}

export function defaultParams(op: VideoOp, clip: ClipInfo, at = 0): VideoParams {
	switch (op) {
		case 'trim':
			return { start: 0, end: clip.duration };
		case 'split':
			return { at: round3(at) };
		case 'crop':
			return { rect: { ...FULL_CROP }, rotation: 0, flipH: false, flipV: false };
		case 'resize': {
			const short = Math.min(clip.width, clip.height);
			return { short: evenRound(short >= 1080 ? 720 : Math.max(2, short / 2)), quality: 'balanced' };
		}
		case 'speed':
			return { speed: 2, keep_pitch: true };
		case 'mute':
			return {};
		case 'volume':
			return { unit: 'percent', percent: 100, db: 0, fade_in: 0, fade_out: 0 };
		case 'replace_audio':
			return { audio: null, name: '', start: 0, loop: true };
		case 'film_grain':
			return { amount: 30, size: 2, color: false, seed: Math.floor(Math.random() * 9_999_999) };
		case 'fix_colors':
			return { white_balance: true, contrast: true, strength: 100 };
	}
}

export function summarize(op: VideoOp, params: VideoParams): string {
	switch (op) {
		case 'trim': {
			const trim = readTrim(params);
			return `Keep ${fixed2(trim.start)} → ${fixed2(trim.end)} s`;
		}
		case 'split':
			return `At ${fixed2(num(params.at, 0))} s · two items`;
		case 'crop': {
			const crop = readCrop(params);
			const parts: string[] = [];
			if (!isFullFrame(crop.rect)) parts.push(`${Math.round(crop.rect.width * 100)}% × ${Math.round(crop.rect.height * 100)}%`);
			if (crop.rotation) parts.push(`${crop.rotation}°`);
			if (crop.flipH) parts.push('flip H');
			if (crop.flipV) parts.push('flip V');
			return parts.join(' · ') || 'Full frame';
		}
		case 'resize':
			return `${num(params.short, 720)}p · ${String(params.quality ?? 'balanced')}`;
		case 'speed': {
			const speed = readSpeed(params);
			return `${speed.speed}×${speed.keep_pitch ? ' · keep pitch' : ''}`;
		}
		case 'mute':
			return 'No sound';
		case 'volume': {
			const volume = readVolume(params);
			const parts = [volume.unit === 'db' ? `${volume.db} dB` : `${Math.round(volume.percent)}%`];
			if (volume.fade_in > 0) parts.push(`fade in ${fixed1(volume.fade_in)} s`);
			if (volume.fade_out > 0) parts.push(`fade out ${fixed1(volume.fade_out)} s`);
			return parts.join(' · ');
		}
		case 'replace_audio': {
			const parts = [String(params.name || 'No track')];
			if (num(params.start, 0) > 0) parts.push(`from ${fixed1(num(params.start, 0))} s`);
			if (params.loop !== false) parts.push('loop');
			return parts.join(' · ');
		}
		case 'film_grain':
			return `Amount ${Math.round(num(params.amount, 30))} · size ${num(params.size, 2)}`;
		case 'fix_colors': {
			const parts = [`Strength ${Math.round(num(params.strength, 100))}%`];
			if (params.white_balance !== false) parts.push('white balance');
			if (params.contrast !== false) parts.push('contrast');
			return parts.join(' · ');
		}
	}
}

export function draftFor(op: VideoOp, params: VideoParams): StepDraft<VideoParams> {
	const draft: StepDraft<VideoParams> = {
		tool: op,
		icon: OP_ICONS[op],
		label: OP_LABELS[op],
		summary: summarize(op, params),
		params
	};
	const lane = OP_LANES[op];
	if (lane) draft.lane = lane;
	return draft;
}

export interface ChainState {
	ops: TimeOp[];
	size: PixelSize;
	duration: number;
}

export interface CompileOptions {
	skipSplit?: boolean;
}

function shortEdgeResize(size: PixelSize, short: number): { params: Record<string, number>; size: PixelSize } {
	const target = evenRound(Math.max(2, short));
	if (size.width >= size.height) {
		const width = evenRound((size.width * target) / Math.max(1, size.height));
		return { params: { height: target }, size: { width, height: target } };
	}
	const height = evenRound((size.height * target) / Math.max(1, size.width));
	return { params: { width: target }, size: { width: target, height } };
}

export function compileSteps(
	steps: readonly EditStep<unknown>[],
	clip: ClipInfo,
	options: CompileOptions = {}
): { steps: MediaToolStep[]; chain: ChainState } {
	const ops: TimeOp[] = [];
	let size: PixelSize = { width: clip.width, height: clip.height };
	const out: MediaToolStep[] = [];
	for (const step of steps) {
		if (step.hidden || !isVideoOp(step.tool)) continue;
		const params = (step.params ?? {}) as VideoParams;
		const length = outputDuration(clip.duration, ops);
		switch (step.tool) {
			case 'trim': {
				const trim = readTrim(params);
				const start = sourceToOutput(trim.start, clip.duration, ops);
				const end = sourceToOutput(trim.end, clip.duration, ops);
				if (end - start <= 0) break;
				out.push({ tool: 'trim', params: { start: round3(start), end: round3(end) } });
				ops.push({ kind: 'trim', start, end });
				break;
			}
			case 'split': {
				if (options.skipSplit) break;
				const at = sourceToOutput(num(params.at, 0), clip.duration, ops);
				if (at <= 0 || at >= length) break;
				out.push({ tool: 'split', params: { mode: 'at', at: round3(at) } });
				break;
			}
			case 'crop': {
				const crop = readCrop(params);
				if (crop.rotation) out.push({ tool: 'rotate', params: { degrees: crop.rotation } });
				if (crop.flipH) out.push({ tool: 'flip', params: { axis: 'horizontal' } });
				if (crop.flipV) out.push({ tool: 'flip', params: { axis: 'vertical' } });
				size = displaySize(size, crop.rotation);
				if (!isFullFrame(crop.rect)) {
					const { type: _type, ...box } = toCropOperation(crop.rect, size);
					out.push({ tool: 'crop', params: box });
					size = { width: box.width, height: box.height };
				}
				break;
			}
			case 'resize': {
				const resized = shortEdgeResize(size, num(params.short, Math.min(size.width, size.height)));
				out.push({ tool: 'resize', params: { ...resized.params, quality: String(params.quality ?? 'balanced') } });
				size = resized.size;
				break;
			}
			case 'speed': {
				const speed = readSpeed(params);
				out.push({ tool: 'speed', params: { speed: speed.speed, keep_pitch: speed.keep_pitch } });
				ops.push({ kind: 'speed', speed: speed.speed });
				break;
			}
			case 'mute':
				out.push({ tool: 'mute', params: {} });
				break;
			case 'volume': {
				const volume = readVolume(params);
				const level = volume.unit === 'db' ? { db: volume.db } : { percent: volume.percent };
				out.push({ tool: 'volume', params: { unit: volume.unit, ...level, fade_in: volume.fade_in, fade_out: volume.fade_out } });
				break;
			}
			case 'replace_audio': {
				if (!params.audio) break;
				out.push({
					tool: 'replace_audio',
					params: { audio: params.audio, start: Math.max(0, num(params.start, 0)), loop: params.loop !== false }
				});
				break;
			}
			case 'film_grain':
				out.push({
					tool: 'film_grain',
					params: {
						amount: num(params.amount, 30),
						size: num(params.size, 2),
						color: params.color === true,
						seed: Math.max(0, Math.trunc(num(params.seed, 0)))
					}
				});
				break;
			case 'fix_colors':
				out.push({
					tool: 'fix_colors',
					params: {
						white_balance: params.white_balance !== false,
						contrast: params.contrast !== false,
						strength: num(params.strength, 100)
					}
				});
				break;
		}
	}
	return { steps: out, chain: { ops, size, duration: outputDuration(clip.duration, ops) } };
}

export function stepsSignature(steps: readonly MediaToolStep[]): string {
	return JSON.stringify(steps);
}

export function hasSplit(steps: readonly MediaToolStep[]): boolean {
	return steps.some((step) => step.tool === 'split');
}

export function hasPixelEffects(steps: readonly MediaToolStep[]): boolean {
	return steps.some((step) => step.tool === 'film_grain' || step.tool === 'fix_colors');
}

export interface PlaybackPreview {
	ops: TimeOp[];
	duration: number;
	rate: number;
	muted: boolean;
	gain: number;
	crop: CropParams | null;
	size: PixelSize;
}

export function playbackPreview(steps: readonly EditStep<unknown>[], clip: ClipInfo): PlaybackPreview {
	const { chain } = compileSteps(steps, clip, { skipSplit: true });
	let muted = false;
	let gain = 1;
	let crop: CropParams | null = null;
	for (const step of steps) {
		if (step.hidden) continue;
		const params = (step.params ?? {}) as VideoParams;
		if (step.tool === 'mute') muted = true;
		else if (step.tool === 'volume') gain *= volumeGain(readVolume(params));
		else if (step.tool === 'crop') crop = readCrop(params);
		else if (step.tool === 'replace_audio' && params.audio) muted = true;
	}
	const rate = chain.ops.reduce((value, op) => (op.kind === 'speed' ? value * safeSpeed(op.speed) : value), 1);
	return { ops: chain.ops, duration: chain.duration, rate, muted, gain: Math.max(0, gain), crop, size: chain.size };
}

export function isNoop(op: VideoOp, params: VideoParams, clip: ClipInfo, kept: { start: number; end: number }): boolean {
	switch (op) {
		case 'trim': {
			const trim = readTrim(params);
			return trim.end - trim.start <= 0.01 || (Math.abs(trim.start - kept.start) < 0.005 && Math.abs(trim.end - kept.end) < 0.005);
		}
		case 'split': {
			const at = num(params.at, 0);
			return at <= kept.start + 0.05 || at >= kept.end - 0.05;
		}
		case 'crop': {
			const crop = readCrop(params);
			return isFullFrame(crop.rect) && crop.rotation === 0 && !crop.flipH && !crop.flipV;
		}
		case 'resize':
			return num(params.short, 0) >= Math.min(clip.width, clip.height);
		case 'speed':
			return readSpeed(params).speed === 1;
		case 'volume': {
			const volume = readVolume(params);
			return Math.abs(volumeGain(volume) - 1) < 1e-6 && volume.fade_in <= 0 && volume.fade_out <= 0;
		}
		case 'replace_audio':
			return !params.audio;
		default:
			return false;
	}
}
