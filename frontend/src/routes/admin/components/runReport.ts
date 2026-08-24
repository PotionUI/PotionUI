/** Pure derivations for `GenerationRunReport.svelte`, kept out of the
 *  component so the grouping/formatting logic is unit-testable without
 *  mounting Svelte - mirrors `GenerationPanelHistory`'s in-markup grouping,
 *  adapted to the persisted `RunReport` shape (`pipe_id`-keyed, `at` instead
 *  of `timestamp`). */
import { extractPipeName } from '$lib/utils/templateProcessor';
import { formatDurationMs } from '$lib/components/generation-panel/barState';
import type { RunReportArtifact, RunReportPipeTimer, RunReportStatusEntry } from '$lib/services/admin-api';

export interface GroupedStatusEntry {
	type: 'single' | 'progress_group';
	firstAt: string;
	lastAt: string;
	step: string;
	message?: string;
	startProgress: number;
	endProgress: number;
	count?: number;
	pipeKey: string;
	pipeLabel: string;
}

/** The human label for a status/artifact entry's pipe: the `<<PIPE:name:...>>`
 *  marker embedded in its own text if present (matches what the live drawer
 *  showed), else the raw `pipe_id`. */
function pipeLabelFor(pipeId: string | number | null, step: string, message: string | null): string {
	return extractPipeName(step) || extractPipeName(message || '') || (pipeId != null ? String(pipeId) : 'unknown');
}

function pipeKeyFor(pipeId: string | number | null): string {
	return pipeId != null ? String(pipeId) : 'unknown';
}

/** Group consecutive status entries for the same pipe into progress runs,
 *  starting a new group whenever progress resets from near-100% back near 0%
 *  (a new step within the same pipe) - identical rule to the live drawer. */
export function groupStatusHistory(entries: RunReportStatusEntry[]): GroupedStatusEntry[] {
	if (entries.length === 0) return [];

	const grouped: GroupedStatusEntry[] = [];
	const active = new Map<string, RunReportStatusEntry[]>();

	const flush = (key: string, items: RunReportStatusEntry[]) => {
		if (items.length === 0) return;
		const sorted = [...items].sort((a, b) => a.progress - b.progress);
		const first = sorted[0];
		const last = sorted[sorted.length - 1];
		const pipeLabel = pipeLabelFor(first.pipe_id, first.step, first.message);
		if (sorted.length > 1) {
			grouped.push({
				type: 'progress_group',
				firstAt: first.at,
				lastAt: last.at,
				step: first.step.replace(/<<PROGRESS:\d+%>>/g, ''),
				message: first.message ?? undefined,
				startProgress: first.progress * 100,
				endProgress: last.progress * 100,
				count: sorted.length,
				pipeKey: key,
				pipeLabel
			});
		} else {
			grouped.push({
				type: 'single',
				firstAt: first.at,
				lastAt: first.at,
				step: first.step,
				message: first.message ?? undefined,
				startProgress: first.progress * 100,
				endProgress: first.progress * 100,
				pipeKey: key,
				pipeLabel
			});
		}
	};

	for (const entry of entries) {
		const key = pipeKeyFor(entry.pipe_id);
		const current = active.get(key);
		let startNewGroup = false;
		if (current && current.length > 0) {
			const lastProgress = current[current.length - 1].progress;
			if (lastProgress >= 0.9 && entry.progress < 0.1) startNewGroup = true;
		}
		if (startNewGroup) {
			flush(key, current!);
			active.set(key, [entry]);
		} else {
			if (!current) active.set(key, []);
			active.get(key)!.push(entry);
		}
	}
	active.forEach((items, key) => flush(key, items));

	return grouped.sort((a, b) => new Date(a.firstAt).getTime() - new Date(b.firstAt).getTime());
}

/** `GroupedStatusEntry[]` bucketed by pipe, in first-seen order - the order
 *  the timeline renders its pipe sections in. */
export function groupByPipe(groups: GroupedStatusEntry[]): Map<string, GroupedStatusEntry[]> {
	const byPipe = new Map<string, GroupedStatusEntry[]>();
	for (const group of groups) {
		if (!byPipe.has(group.pipeKey)) byPipe.set(group.pipeKey, []);
		byPipe.get(group.pipeKey)!.push(group);
	}
	return byPipe;
}

/** The artifacts belonging to one pipe, in emission order. There is no
 *  persisted per-image `index` on `RunReportArtifact` (unlike the live
 *  `ArtifactData`), so - unlike the drawer - artifacts for a pipe are not
 *  further split into per-output subgroups. */
export function artifactsForPipe(artifacts: RunReportArtifact[], pipeKey: string): RunReportArtifact[] {
	return artifacts.filter((artifact) => pipeKeyFor(artifact.pipe_id) === pipeKey);
}

/** Formatted wall-clock duration for a pipe's timer, or "-" when the pipe has
 *  no timer or is still missing an end (never actually finished). */
export function formatPipeTiming(timer: RunReportPipeTimer | undefined): string {
	if (!timer?.started_at || !timer?.ended_at) return '-';
	const ms = new Date(timer.ended_at).getTime() - new Date(timer.started_at).getTime();
	if (!Number.isFinite(ms) || ms < 0) return '-';
	return formatDurationMs(ms);
}

/** The timeline's right edge: the generation's own `completed_at` when it
 *  has one, else the last recorded status timestamp (a run still in flight
 *  or one that died mid-report), else `fallback` (the caller's last resort,
 *  typically `updated_at`/`created_at`) so the span is never zero-length
 *  just because nothing closed cleanly. */
export function resolveRunEnd(params: {
	completedAt: string | null | undefined;
	statusHistory: RunReportStatusEntry[];
	fallback: string;
}): string {
	if (params.completedAt) return params.completedAt;
	if (params.statusHistory.length > 0) return params.statusHistory[params.statusHistory.length - 1].at;
	return params.fallback;
}

/** The pipe whose timer started but never closed - the best-effort "this is
 *  where it broke" signal for a failed generation, since no pipe-level
 *  status is persisted on `RunReport` itself. */
export function findRunningPipeKey(pipeTimers: Record<string, RunReportPipeTimer>): string | null {
	for (const [key, timer] of Object.entries(pipeTimers)) {
		if (timer.started_at && !timer.ended_at) return key;
	}
	return null;
}

export interface TimelineTick {
	pct: number;
	atMs: number;
}

export interface TimelineBar {
	pipeKey: string;
	pipeLabel: string;
	startPct: number;
	widthPct: number;
	durationMs: number | null;
	running: boolean;
	failed: boolean;
	ticks: TimelineTick[];
}

export interface TimelineAxisTick {
	pct: number;
	label: string;
}

export interface Timeline {
	spanMs: number;
	bars: TimelineBar[];
	axisTicks: TimelineAxisTick[];
}

const MIN_BAR_WIDTH_PCT = 0.6;
const AXIS_STEP_COUNT = 5;

/** Pure layout math for the pipe-timeline gantt: bar extents from
 *  `pipe_timers`, tick marks at each status-group boundary (from the same
 *  `groupStatusHistory` output the status log renders), everything expressed
 *  as percentages of the [runStart, runEnd] span. Kept out of the Svelte
 *  component so positioning/scaling is unit-testable without mounting.
 *  Pipes are ordered by whichever signal saw them first - a timer start or a
 *  status group - so a pipe carrying only one of the two still gets a row. */
export function buildPipeTimeline(
	pipeTimers: Record<string, RunReportPipeTimer>,
	groupedEntries: GroupedStatusEntry[],
	runStart: string,
	runEnd: string,
	options: { failedPipeKey?: string | null } = {}
): Timeline {
	const startMs = new Date(runStart).getTime();
	const endMs = new Date(runEnd).getTime();
	const spanMs = Number.isFinite(startMs) && Number.isFinite(endMs) && endMs > startMs ? endMs - startMs : 0;

	const pctOf = (ms: number): number => {
		if (spanMs <= 0 || !Number.isFinite(ms)) return 0;
		return Math.min(100, Math.max(0, ((ms - startMs) / spanMs) * 100));
	};

	const firstSeenMs = new Map<string, number>();
	const labelByKey = new Map<string, string>();
	const ticksByKey = new Map<string, TimelineTick[]>();

	for (const [key, timer] of Object.entries(pipeTimers)) {
		if (!timer.started_at) continue;
		const ms = new Date(timer.started_at).getTime();
		if (!Number.isFinite(ms)) continue;
		firstSeenMs.set(key, Math.min(firstSeenMs.get(key) ?? Infinity, ms));
	}
	for (const group of groupedEntries) {
		const ms = new Date(group.firstAt).getTime();
		labelByKey.set(group.pipeKey, group.pipeLabel);
		if (!Number.isFinite(ms)) continue;
		firstSeenMs.set(group.pipeKey, Math.min(firstSeenMs.get(group.pipeKey) ?? Infinity, ms));
		if (!ticksByKey.has(group.pipeKey)) ticksByKey.set(group.pipeKey, []);
		ticksByKey.get(group.pipeKey)!.push({ pct: pctOf(ms), atMs: ms });
	}

	const pipeKeys = [...firstSeenMs.keys()].sort((a, b) => firstSeenMs.get(a)! - firstSeenMs.get(b)!);

	const bars: TimelineBar[] = pipeKeys.map((pipeKey) => {
		const timer = pipeTimers[pipeKey];
		const hasStart = !!timer?.started_at;
		const startAtMs = hasStart ? new Date(timer!.started_at as string).getTime() : NaN;
		const hasEnd = !!timer?.ended_at;
		const endAtMs = hasEnd ? new Date(timer!.ended_at as string).getTime() : NaN;
		const running = hasStart && Number.isFinite(startAtMs) && !hasEnd;

		let startPct = 0;
		let widthPct = 0;
		let durationMs: number | null = null;

		if (hasStart && Number.isFinite(startAtMs)) {
			startPct = pctOf(startAtMs);
			const effectiveEndMs = hasEnd && Number.isFinite(endAtMs) ? endAtMs : endMs;
			widthPct = Math.max(0, Math.min(100 - startPct, Math.max(MIN_BAR_WIDTH_PCT, pctOf(effectiveEndMs) - startPct)));
			if (hasEnd && Number.isFinite(endAtMs) && endAtMs >= startAtMs) {
				durationMs = endAtMs - startAtMs;
			}
		} else {
			// No timer at all for this pipe - still give it a marker at its
			// first status-group boundary so it isn't silently missing a row.
			const ticks = ticksByKey.get(pipeKey) ?? [];
			if (ticks.length > 0) {
				startPct = ticks[0].pct;
				widthPct = Math.min(MIN_BAR_WIDTH_PCT, 100 - startPct);
			}
		}

		return {
			pipeKey,
			pipeLabel: labelByKey.get(pipeKey) ?? pipeKey,
			startPct,
			widthPct,
			durationMs,
			running,
			failed: options.failedPipeKey != null && options.failedPipeKey === pipeKey,
			ticks: ticksByKey.get(pipeKey) ?? []
		};
	});

	const axisTicks: TimelineAxisTick[] = Array.from({ length: AXIS_STEP_COUNT + 1 }, (_, i) => {
		const pct = (i / AXIS_STEP_COUNT) * 100;
		const atMs = (pct / 100) * spanMs;
		return { pct, label: i === 0 ? '0s' : `+${formatDurationMs(atMs)}` };
	});

	return { spanMs, bars, axisTicks };
}
