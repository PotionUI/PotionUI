import { MIN_TRIM_SECONDS, clamp, safeDuration, type TrimPoints } from './trimPoints';

const SECONDS_SEGMENT = /^\d+(\.\d+)?$/;
const WHOLE_SEGMENT = /^\d+$/;

export const TRIM_TIME_INPUT_ERROR = 'Enter a time as seconds or m:ss.';
export const TRIM_IN_AFTER_OUT_ERROR = 'In must be before Out.';
export const TRIM_OUT_BEFORE_IN_ERROR = 'Out must be after In.';

export interface TrimEntryResult {
	points: TrimPoints;
	error: string | null;
}

export function parseTimeInput(text: string): number | null {
	const trimmed = text.trim();
	if (!trimmed) return null;

	const segments = trimmed.split(':');
	if (segments.length > 3) return null;

	const secondsSegment = segments[segments.length - 1];
	const unitSegments = segments.slice(0, -1);

	if (!SECONDS_SEGMENT.test(secondsSegment)) return null;
	for (const segment of unitSegments) {
		if (!WHOLE_SEGMENT.test(segment)) return null;
	}

	const seconds = Number(secondsSegment);
	const minutes = unitSegments.length >= 1 ? Number(unitSegments[unitSegments.length - 1]) : 0;
	const hours = unitSegments.length >= 2 ? Number(unitSegments[unitSegments.length - 2]) : 0;

	const total = hours * 3600 + minutes * 60 + seconds;
	return Number.isFinite(total) ? total : null;
}

export function commitTrimIn(
	text: string,
	points: TrimPoints,
	duration: number | null | undefined
): TrimEntryResult {
	const parsed = parseTimeInput(text);
	if (parsed === null) return { points, error: TRIM_TIME_INPUT_ERROR };

	const start = clamp(parsed, 0, safeDuration(duration));
	if (start >= points.end - MIN_TRIM_SECONDS) return { points, error: TRIM_IN_AFTER_OUT_ERROR };

	return { points: { start, end: points.end }, error: null };
}

export function commitTrimOut(
	text: string,
	points: TrimPoints,
	duration: number | null | undefined
): TrimEntryResult {
	const parsed = parseTimeInput(text);
	if (parsed === null) return { points, error: TRIM_TIME_INPUT_ERROR };

	const end = clamp(parsed, 0, safeDuration(duration));
	if (end <= points.start + MIN_TRIM_SECONDS) return { points, error: TRIM_OUT_BEFORE_IN_ERROR };

	return { points: { start: points.start, end }, error: null };
}
