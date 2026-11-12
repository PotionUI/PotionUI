import type { DirectorCapabilities, VideoDirectorValue } from '$lib/types/videoDirector';
import { normalizeDirectorValue } from '$lib/utils/videoDirector';

export const LONG_MODE = 'long';
export const DIRECTOR_MODE = 'video';

const CHUNK_FRAMES = 17;
const LATTICE_FRAMES = 5;
const OVERLAP_FRAMES = 17;
const DEFAULT_FPS = 24;
const DEFAULT_TOTAL_SECONDS = 30;
const DEFAULT_PART_SECONDS = 10;

const CONSUMED_KEYS = new Set(['total_seconds', 'piece_seconds', 'piece_prompts', 'start_image', 'soundtrack']);

function aligned(frames: number): number {
	return Math.max(Math.ceil((frames - LATTICE_FRAMES) / CHUNK_FRAMES) * CHUNK_FRAMES + LATTICE_FRAMES, CHUNK_FRAMES + LATTICE_FRAMES);
}

export function longVideoParts(totalSeconds: number, partSeconds: number, fps = DEFAULT_FPS, overlapFrames = OVERLAP_FRAMES): number[] {
	const total = Math.round(totalSeconds * fps);
	const part = aligned(Math.round(partSeconds * fps));
	if (total <= part) return [aligned(total)];
	const stride = part - overlapFrames;
	const count = 1 + Math.ceil((total - part) / stride);
	const remaining = total - (part + (count - 2) * stride) + overlapFrames;
	return [...Array<number>(count - 1).fill(part), aligned(remaining)];
}

export interface LongVideoSource {
	prompt?: string | null;
	formData?: Record<string, unknown> | null;
}

export interface LongVideoConversion {
	directorValue: VideoDirectorValue;
	formPatch: Record<string, unknown>;
	notes: string[];
	shots: number;
	finishedSeconds: number;
}

export interface LongVideoOptions {
	caps?: DirectorCapabilities;
	maxShots?: number | null;
	fps?: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function positive(value: unknown, fallback: number): number {
	const parsed = typeof value === 'string' ? Number(value) : value;
	return typeof parsed === 'number' && Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function mediaRef(value: unknown, type: 'image' | 'audio'): Record<string, unknown> | null {
	if (typeof value === 'string' && value.trim() !== '') return { path: value, type };
	if (isRecord(value) && (value.path || value.relative_path)) return { type, ...value };
	return null;
}

function seconds(value: number): string {
	return `${Math.round(value * 10) / 10}`;
}

export function isLongVideoForm(formData: unknown): boolean {
	return isRecord(formData) && ('total_seconds' in formData || 'piece_seconds' in formData || 'piece_prompts' in formData);
}

export function convertLongVideo(source: LongVideoSource, options: LongVideoOptions = {}): LongVideoConversion {
	const formData = isRecord(source.formData) ? source.formData : {};
	const fps = options.fps ?? options.caps?.defaultFps ?? DEFAULT_FPS;
	const total = positive(formData.total_seconds, DEFAULT_TOTAL_SECONDS);
	const piece = positive(formData.piece_seconds, DEFAULT_PART_SECONDS);
	const mainPrompt = (source.prompt ?? '').trim();
	const lines = typeof formData.piece_prompts === 'string' ? formData.piece_prompts.split('\n') : [];

	const parts = longVideoParts(total, piece, fps);
	const maxShots = options.maxShots ?? options.caps?.modes.director?.maxSegments ?? null;
	const kept = maxShots != null && maxShots > 0 ? parts.slice(0, maxShots) : parts;
	const notes: string[] = [];

	const dropped = parts.slice(kept.length);
	if (dropped.length > 0) {
		const lostSeconds = dropped.reduce((sum, frames) => sum + (frames - OVERLAP_FRAMES) / fps, 0);
		notes.push(
			`Video Director allows up to ${maxShots} shots, so the last ${dropped.length} of ${parts.length} parts ` +
				`(about ${seconds(lostSeconds)} s of the ${seconds(total)} s) were left out.`
		);
	}

	const unusedLines = lines.slice(parts.length).filter((line) => line.trim() !== '');
	if (unusedLines.length > 0) {
		notes.push(
			`${unusedLines.length === 1 ? 'A "Prompt per part" line had' : `${unusedLines.length} "Prompt per part" lines had`} ` +
				`no part to belong to and ${unusedLines.length === 1 ? 'was' : 'were'} left out: ${unusedLines.map((line) => `"${line.trim()}"`).join(', ')}.`
		);
	}

	const finishedFrames = kept.reduce((sum, frames, index) => sum + (index === 0 ? frames : frames - OVERLAP_FRAMES), 0);
	const finishedSeconds = finishedFrames / fps;
	if (dropped.length === 0 && seconds(finishedSeconds) !== seconds(total)) {
		notes.push(
			`Long video cut the end to exactly ${seconds(total)} s; Video Director keeps whole shots, ` +
				`so the film runs ${seconds(finishedSeconds)} s.`
		);
	}

	const startPicture = mediaRef(formData.start_image, 'image');
	if (formData.start_image && !startPicture) {
		notes.push('The start picture could not be read from the saved setup and was left out.');
	}
	const soundtrack = mediaRef(formData.soundtrack, 'audio');
	if (formData.soundtrack && !soundtrack) {
		notes.push('The soundtrack could not be read from the saved setup and was left out.');
	}

	const segments = kept.map((frames, index) => ({
		id: `shot-${index + 1}`,
		prompt: (lines[index] ?? '').trim() || mainPrompt,
		duration: frames / fps,
		keyframe: index === 0 ? startPicture : null
	}));

	const raw = {
		schema_version: 1,
		mode: 'director',
		global_prompt: '',
		negative_prompt: '',
		chain: {
			fps,
			segments,
			continuation: { overlap_frames: OVERLAP_FRAMES, stitch: true },
			keyframes: [],
			audio: soundtrack
				? [{ id: 'audio-1', start: 0, trim_start: 0, length: finishedSeconds, media: soundtrack, role: 'mux' }]
				: []
		}
	};

	const formPatch: Record<string, unknown> = {};
	for (const [key, value] of Object.entries(formData)) {
		if (!CONSUMED_KEYS.has(key)) formPatch[key] = value;
	}

	return {
		directorValue: options.caps ? normalizeDirectorValue(raw, options.caps) : (raw as unknown as VideoDirectorValue),
		formPatch,
		notes,
		shots: kept.length,
		finishedSeconds
	};
}
