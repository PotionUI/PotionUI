import { describe, it, expect } from 'vitest';
import {
	groupStatusHistory,
	groupByPipe,
	artifactsForPipe,
	formatPipeTiming,
	findRunningPipeKey,
	buildPipeTimeline,
	resolveRunEnd
} from './runReport';
import type { RunReportArtifact, RunReportPipeTimer, RunReportStatusEntry } from '$lib/services/admin-api';

function entry(overrides: Partial<RunReportStatusEntry>): RunReportStatusEntry {
	return {
		at: '2026-08-14T00:00:00Z',
		pipe_id: 1,
		step: '',
		message: null,
		progress: 0,
		...overrides
	};
}

describe('groupStatusHistory', () => {
	it('collapses a run of progress updates for the same pipe into one progress_group', () => {
		const groups = groupStatusHistory([
			entry({ at: '2026-08-14T00:00:00Z', step: '<<PIPE:generator:bolt>> Sampling', progress: 0.1 }),
			entry({ at: '2026-08-14T00:00:01Z', step: '<<PIPE:generator:bolt>> Sampling', progress: 0.5 }),
			entry({ at: '2026-08-14T00:00:02Z', step: '<<PIPE:generator:bolt>> Sampling', progress: 0.9 })
		]);

		expect(groups).toHaveLength(1);
		expect(groups[0].type).toBe('progress_group');
		expect(groups[0].count).toBe(3);
		expect(groups[0].startProgress).toBeCloseTo(10);
		expect(groups[0].endProgress).toBeCloseTo(90);
		expect(groups[0].pipeLabel).toBe('generator');
	});

	it('starts a new group when progress resets from near-100% back near 0% on the same pipe', () => {
		const groups = groupStatusHistory([
			entry({ pipe_id: 2, step: '<<PIPE:upscale:bolt>> Pass 1', progress: 0.95 }),
			entry({ pipe_id: 2, step: '<<PIPE:upscale:bolt>> Pass 2', progress: 0.05 }),
			entry({ pipe_id: 2, step: '<<PIPE:upscale:bolt>> Pass 2', progress: 0.8 })
		]);

		expect(groups).toHaveLength(2);
		expect(groups[0].count).toBeUndefined(); // lone entry rendered as 'single'
		expect(groups[1].type).toBe('progress_group');
	});

	it('falls back to the raw pipe_id when no <<PIPE:...>> marker is present', () => {
		const groups = groupStatusHistory([entry({ pipe_id: 'decoder-3', step: 'Decoding', progress: 1 })]);
		expect(groups[0].pipeLabel).toBe('decoder-3');
		expect(groups[0].pipeKey).toBe('decoder-3');
	});

	it('returns entries sorted by first-seen timestamp across interleaved pipes', () => {
		const groups = groupStatusHistory([
			entry({ pipe_id: 'b', at: '2026-08-14T00:00:05Z', step: 'B step', progress: 1 }),
			entry({ pipe_id: 'a', at: '2026-08-14T00:00:00Z', step: 'A step', progress: 1 })
		]);
		expect(groups.map((g) => g.pipeKey)).toEqual(['a', 'b']);
	});
});

describe('groupByPipe', () => {
	it('buckets grouped entries by pipeKey preserving group order', () => {
		// The second 'a' entry resets progress from 1.0 back to near 0, so it
		// starts a new group rather than merging into the first (a bare repeat at
		// progress: 1 would collapse into a single count:2 group for 'a').
		const groups = groupStatusHistory([
			entry({ pipe_id: 'a', at: '2026-08-14T00:00:00Z', step: 'A1', progress: 1 }),
			entry({ pipe_id: 'b', at: '2026-08-14T00:00:01Z', step: 'B1', progress: 1 }),
			entry({ pipe_id: 'a', at: '2026-08-14T00:00:02Z', step: 'A2', progress: 0.05 })
		]);
		const byPipe = groupByPipe(groups);
		expect([...byPipe.keys()]).toEqual(['a', 'b']);
		expect(byPipe.get('a')).toHaveLength(2);
	});
});

describe('artifactsForPipe', () => {
	const artifacts: RunReportArtifact[] = [
		{ at: '2026-08-14T00:00:00Z', pipe_id: 1, artifact_type: 'seed', artifact_data: {} },
		{ at: '2026-08-14T00:00:01Z', pipe_id: 2, artifact_type: 'seed', artifact_data: {} },
		{ at: '2026-08-14T00:00:02Z', pipe_id: 1, artifact_type: 'models', artifact_data: {} }
	];

	it('filters by pipe_id, coerced to string, in original order', () => {
		expect(artifactsForPipe(artifacts, '1')).toEqual([artifacts[0], artifacts[2]]);
	});

	it('returns an empty array when nothing matches', () => {
		expect(artifactsForPipe(artifacts, 'missing')).toEqual([]);
	});
});

describe('formatPipeTiming', () => {
	it('formats the wall-clock gap between started_at and ended_at', () => {
		const text = formatPipeTiming({
			started_at: '2026-08-14T00:00:00.000Z',
			ended_at: '2026-08-14T00:00:05.000Z'
		});
		expect(text).toBe('5.0s');
	});

	it('returns "-" when the timer never closed (still running or lost)', () => {
		expect(formatPipeTiming({ started_at: '2026-08-14T00:00:00Z', ended_at: null })).toBe('-');
	});

	it('returns "-" when there is no timer at all', () => {
		expect(formatPipeTiming(undefined)).toBe('-');
	});
});

describe('resolveRunEnd', () => {
	it('prefers completed_at when present', () => {
		expect(
			resolveRunEnd({ completedAt: '2026-08-14T00:00:05Z', statusHistory: [], fallback: '2026-08-14T00:00:00Z' })
		).toBe('2026-08-14T00:00:05Z');
	});

	it('falls back to the last status timestamp when the run never closed', () => {
		const statusHistory = [entry({ at: '2026-08-14T00:00:01Z' }), entry({ at: '2026-08-14T00:00:03Z' })];
		expect(resolveRunEnd({ completedAt: null, statusHistory, fallback: '2026-08-14T00:00:00Z' })).toBe(
			'2026-08-14T00:00:03Z'
		);
	});

	it('falls back to the given fallback when there is no completion or status history', () => {
		expect(resolveRunEnd({ completedAt: undefined, statusHistory: [], fallback: '2026-08-14T00:00:00Z' })).toBe(
			'2026-08-14T00:00:00Z'
		);
	});
});

describe('findRunningPipeKey', () => {
	it('returns the key of the pipe whose timer opened but never closed', () => {
		const timers: Record<string, RunReportPipeTimer> = {
			generator: { started_at: '2026-08-14T00:00:00Z', ended_at: '2026-08-14T00:00:05Z' },
			upscale: { started_at: '2026-08-14T00:00:05Z', ended_at: null }
		};
		expect(findRunningPipeKey(timers)).toBe('upscale');
	});

	it('returns null when every timer closed', () => {
		const timers: Record<string, RunReportPipeTimer> = {
			generator: { started_at: '2026-08-14T00:00:00Z', ended_at: '2026-08-14T00:00:05Z' }
		};
		expect(findRunningPipeKey(timers)).toBeNull();
	});
});

describe('buildPipeTimeline', () => {
	const runStart = '2026-08-14T00:00:00.000Z';
	const runEnd = '2026-08-14T00:00:10.000Z'; // 10s span

	it('positions and scales bars as percentages of the run span', () => {
		const timers: Record<string, RunReportPipeTimer> = {
			generator: { started_at: '2026-08-14T00:00:00.000Z', ended_at: '2026-08-14T00:00:04.000Z' }, // 0-40%
			upscale: { started_at: '2026-08-14T00:00:05.000Z', ended_at: '2026-08-14T00:00:10.000Z' } // 50-100%
		};
		const groups = groupStatusHistory([
			entry({ pipe_id: 'generator', at: '2026-08-14T00:00:00Z', step: '<<PIPE:generator:bolt>> Sampling', progress: 1 }),
			entry({ pipe_id: 'upscale', at: '2026-08-14T00:00:05Z', step: '<<PIPE:upscale:bolt>> Pass', progress: 1 })
		]);

		const timeline = buildPipeTimeline(timers, groups, runStart, runEnd);

		expect(timeline.spanMs).toBe(10_000);
		expect(timeline.bars.map((b) => b.pipeKey)).toEqual(['generator', 'upscale']);

		const [generator, upscale] = timeline.bars;
		expect(generator.startPct).toBeCloseTo(0);
		expect(generator.widthPct).toBeCloseTo(40);
		expect(generator.durationMs).toBe(4000);
		expect(generator.running).toBe(false);

		expect(upscale.startPct).toBeCloseTo(50);
		expect(upscale.widthPct).toBeCloseTo(50);
	});

	it('extends a still-running pipe (started, never closed) to the run end', () => {
		const timers: Record<string, RunReportPipeTimer> = {
			generator: { started_at: '2026-08-14T00:00:02.000Z', ended_at: null }
		};
		const groups = groupStatusHistory([
			entry({ pipe_id: 'generator', at: '2026-08-14T00:00:02Z', step: '<<PIPE:generator:bolt>> Sampling', progress: 0.5 })
		]);

		const timeline = buildPipeTimeline(timers, groups, runStart, runEnd);

		expect(timeline.bars[0].running).toBe(true);
		expect(timeline.bars[0].durationMs).toBeNull();
		expect(timeline.bars[0].startPct).toBeCloseTo(20);
		expect(timeline.bars[0].widthPct).toBeCloseTo(80); // 20% -> 100%
	});

	it('floors a near-instant pipe to a visible minimum bar width', () => {
		const timers: Record<string, RunReportPipeTimer> = {
			decoder: { started_at: '2026-08-14T00:00:05.000Z', ended_at: '2026-08-14T00:00:05.010Z' }
		};
		const groups = groupStatusHistory([
			entry({ pipe_id: 'decoder', at: '2026-08-14T00:00:05Z', step: '<<PIPE:decoder:bolt>> Decode', progress: 1 })
		]);

		const timeline = buildPipeTimeline(timers, groups, runStart, runEnd);

		expect(timeline.bars[0].widthPct).toBeGreaterThanOrEqual(0.6);
		expect(timeline.bars[0].widthPct).toBeCloseTo(0.6);
	});

	it('places a tick at each status-group boundary, positioned by elapsed time', () => {
		const timers: Record<string, RunReportPipeTimer> = {
			upscale: { started_at: '2026-08-14T00:00:00.000Z', ended_at: '2026-08-14T00:00:10.000Z' }
		};
		const groups = groupStatusHistory([
			entry({ pipe_id: 'upscale', at: '2026-08-14T00:00:00Z', step: '<<PIPE:upscale:bolt>> Pass 1', progress: 0.95 }),
			entry({ pipe_id: 'upscale', at: '2026-08-14T00:00:05Z', step: '<<PIPE:upscale:bolt>> Pass 2', progress: 0.05 }),
			entry({ pipe_id: 'upscale', at: '2026-08-14T00:00:05Z', step: '<<PIPE:upscale:bolt>> Pass 2', progress: 0.8 })
		]);

		const timeline = buildPipeTimeline(timers, groups, runStart, runEnd);

		expect(timeline.bars[0].ticks).toHaveLength(2); // two boundary groups (0% reset splits them)
		expect(timeline.bars[0].ticks[0].pct).toBeCloseTo(0);
		expect(timeline.bars[0].ticks[1].pct).toBeCloseTo(50);
	});

	it('marks the bar matching failedPipeKey as failed', () => {
		const timers: Record<string, RunReportPipeTimer> = {
			generator: { started_at: '2026-08-14T00:00:00.000Z', ended_at: null }
		};
		const groups = groupStatusHistory([
			entry({ pipe_id: 'generator', at: '2026-08-14T00:00:00Z', step: '<<PIPE:generator:bolt>> Sampling', progress: 0.5 })
		]);

		const timeline = buildPipeTimeline(timers, groups, runStart, runEnd, { failedPipeKey: 'generator' });

		expect(timeline.bars[0].failed).toBe(true);
	});

	it('gives a pipe with only status entries (no timer) a marker row instead of dropping it', () => {
		const groups = groupStatusHistory([
			entry({ pipe_id: 'validator', at: '2026-08-14T00:00:03Z', step: 'Validating', progress: 1 })
		]);

		const timeline = buildPipeTimeline({}, groups, runStart, runEnd);

		expect(timeline.bars).toHaveLength(1);
		expect(timeline.bars[0].pipeKey).toBe('validator');
		expect(timeline.bars[0].startPct).toBeCloseTo(30);
		expect(timeline.bars[0].widthPct).toBeGreaterThan(0);
	});

	it('generates evenly spaced axis ticks labeled with elapsed time from the run start', () => {
		const timeline = buildPipeTimeline({}, [], runStart, runEnd);

		expect(timeline.axisTicks).toHaveLength(6); // 5 steps + origin
		expect(timeline.axisTicks[0]).toEqual({ pct: 0, label: '0s' });
		expect(timeline.axisTicks[5].pct).toBeCloseTo(100);
		expect(timeline.axisTicks[5].label).toBe('+10.0s');
	});

	it('produces a zero-width, zero-position timeline when the run span collapses', () => {
		const timeline = buildPipeTimeline({}, [], runStart, runStart);
		expect(timeline.spanMs).toBe(0);
		expect(timeline.axisTicks.every((t) => t.label === '0s' || t.label === '+0.0s')).toBe(true);
	});
});
