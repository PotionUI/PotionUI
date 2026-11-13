import type { GenerationHistoryItem } from '$lib/types/history';

export const QUEUE_WAIT_MIN_SECONDS = 5;

function clock(totalSeconds: number): string {
	const hours = Math.floor(totalSeconds / 3600);
	const minutes = Math.floor((totalSeconds % 3600) / 60);
	const seconds = totalSeconds % 60;
	const ss = String(seconds).padStart(2, '0');
	if (hours > 0) return `${hours}:${String(minutes).padStart(2, '0')}:${ss}`;
	return `${minutes}:${ss}`;
}

export function formatGenerationTime(seconds: number | null | undefined): string | null {
	if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return null;
	const whole = Math.round(seconds);
	if (whole < 1) return '<1 s';
	if (whole < 60) return `${whole} s`;
	return clock(whole);
}

export function generationRunLabel(
	generation: Pick<GenerationHistoryItem, 'status' | 'run_seconds' | 'completed_at'>
): string | null {
	if (generation.status !== 'completed' || !generation.completed_at) return null;
	return formatGenerationTime(generation.run_seconds);
}

export function generationQueueLabel(
	generation: Pick<GenerationHistoryItem, 'status' | 'queued_seconds' | 'completed_at'>
): string | null {
	if (generation.status !== 'completed' || !generation.completed_at) return null;
	const queued = generation.queued_seconds;
	if (typeof queued !== 'number' || !(queued >= QUEUE_WAIT_MIN_SECONDS)) return null;
	return formatGenerationTime(queued);
}

export function generationImageCount(generation: Pick<GenerationHistoryItem, 'files'>): number {
	return (generation.files ?? []).filter(
		(file) => file.is_final && !file.is_derived && file.file_type?.toLowerCase() === 'image'
	).length;
}

export function generationRunSummary(
	generation: Pick<GenerationHistoryItem, 'status' | 'run_seconds' | 'completed_at' | 'files'>
): string | null {
	const label = generationRunLabel(generation);
	if (!label) return null;
	const images = generationImageCount(generation);
	return images > 1 ? `${label} · ${images} images` : label;
}

export function formatHumanDuration(seconds: number | null | undefined): string | null {
	if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return null;
	const whole = Math.round(seconds);
	if (whole < 1) return 'under 1 s';
	if (whole < 60) return `${whole} s`;
	const minutes = Math.floor(whole / 60);
	const rest = whole % 60;
	if (minutes < 60) return rest ? `${minutes} min ${rest} s` : `${minutes} min`;
	const hours = Math.floor(minutes / 60);
	const restMinutes = minutes % 60;
	return restMinutes ? `${hours} h ${restMinutes} min` : `${hours} h`;
}

export interface RunLine {
	when: string;
	detail: string | null;
}

export interface RunLineOptions {
	locale?: string;
	timeZone?: string;
	now?: Date;
}

export function formatShortWhen(iso: string | undefined, options: RunLineOptions = {}): string {
	if (!iso) return '';
	const date = new Date(iso);
	if (Number.isNaN(date.getTime())) return '';
	const now = options.now ?? new Date();
	const format = (withYear: boolean) =>
		new Intl.DateTimeFormat(options.locale, {
			day: 'numeric',
			month: 'short',
			...(withYear ? { year: 'numeric' } : {}),
			hour: '2-digit',
			minute: '2-digit',
			timeZone: options.timeZone
		}).format(date);
	const sameYear =
		new Intl.DateTimeFormat(options.locale, { year: 'numeric', timeZone: options.timeZone }).format(date) ===
		new Intl.DateTimeFormat(options.locale, { year: 'numeric', timeZone: options.timeZone }).format(now);
	return format(!sameYear);
}

export function formatShortDay(iso: string | undefined, options: RunLineOptions = {}): string {
	if (!iso) return '';
	const date = new Date(iso);
	if (Number.isNaN(date.getTime())) return '';
	const now = options.now ?? new Date();
	const year = (d: Date) =>
		new Intl.DateTimeFormat(options.locale, { year: 'numeric', timeZone: options.timeZone }).format(d);
	return new Intl.DateTimeFormat(options.locale, {
		day: 'numeric',
		month: 'short',
		...(year(date) === year(now) ? {} : { year: 'numeric' }),
		timeZone: options.timeZone
	}).format(date);
}

export function formatRunLine(
	generation: Pick<
		GenerationHistoryItem,
		'status' | 'run_seconds' | 'queued_seconds' | 'completed_at' | 'created_at' | 'files'
	>,
	options: RunLineOptions = {}
): RunLine {
	const when = formatShortWhen(generation.completed_at ?? generation.created_at, options);
	const parts: string[] = [];
	if (generation.status === 'completed' && generation.completed_at) {
		const took = formatHumanDuration(generation.run_seconds);
		if (took) parts.push(`took ${took}`);
		const images = generationImageCount(generation);
		if (images > 1) parts.push(`${images} images`);
		const queued = generationQueueLabel(generation);
		if (queued) parts.push(`queued ${formatHumanDuration(generation.queued_seconds)}`);
	} else if (generation.status === 'failed' || generation.status === 'cancelled') {
		const after = formatHumanDuration(generation.run_seconds);
		if (after) parts.push(`${generation.status} after ${after}`);
	}
	return { when, detail: parts.length ? parts.join(' · ') : null };
}
