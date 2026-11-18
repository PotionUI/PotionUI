import type { TimeRange } from '$lib/services/api/editorTasks';
import { sourceToOutput, type TimeOp } from './timeMap';

export interface AiRange {
	start: number;
	end: number;
}

export const MIN_RANGE = 0.1;
export const DEFAULT_SPAN = 2;

function round3(value: number): number {
	return Math.round(value * 1000) / 1000;
}

export function clampRange(range: AiRange, duration: number): AiRange {
	const length = Math.max(0, duration);
	const start = Math.max(0, Math.min(range.start, length));
	const end = Math.max(start, Math.min(range.end, length));
	return { start: round3(start), end: round3(end) };
}

export function validRange(range: AiRange | null, duration: number): AiRange | null {
	if (!range || !(duration > 0)) return null;
	const clamped = clampRange(range, duration);
	return clamped.end - clamped.start >= MIN_RANGE - 1e-9 ? clamped : null;
}

export function defaultRange(playhead: number, duration: number, span = DEFAULT_SPAN): AiRange | null {
	if (!(duration > 0)) return null;
	const length = Math.min(span, duration);
	const start = Math.max(0, Math.min(playhead, duration - length));
	return clampRange({ start, end: start + length }, duration);
}

export function withStart(range: AiRange, seconds: number, duration: number): AiRange {
	const start = Math.max(0, Math.min(seconds, range.end - MIN_RANGE));
	return clampRange({ start, end: range.end }, duration);
}

export function withEnd(range: AiRange, seconds: number, duration: number): AiRange {
	const end = Math.min(duration, Math.max(seconds, range.start + MIN_RANGE));
	return clampRange({ start: range.start, end }, duration);
}

export function fromDrag(anchor: number, point: number, duration: number): AiRange {
	return clampRange({ start: Math.min(anchor, point), end: Math.max(anchor, point) }, duration);
}

export function lengthOf(range: AiRange | null, duration: number): number {
	return range ? range.end - range.start : Math.max(0, duration);
}

export function toRunRange(range: AiRange | null, duration: number, ops: readonly TimeOp[]): TimeRange | null {
	const valid = validRange(range, duration);
	if (!valid) return null;
	const start = sourceToOutput(valid.start, duration, ops);
	const end = sourceToOutput(valid.end, duration, ops);
	if (end - start < MIN_RANGE - 1e-9) return null;
	return { start_s: round3(start), end_s: round3(end) };
}

export function parseSeconds(text: string): number | null {
	const value = Number(text.trim().replace(',', '.'));
	return text.trim() !== '' && Number.isFinite(value) && value >= 0 ? value : null;
}
