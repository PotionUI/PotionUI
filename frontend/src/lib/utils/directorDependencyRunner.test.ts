import { describe, it, expect, vi } from 'vitest';
import { runDirectorDependencyPlan } from './directorDependencyRunner';
import { parseDirectorCapabilities, normalizeDirectorValue } from './videoDirector';
import type { DirectorTimelineShot, VideoDirectorValue } from '$lib/types/videoDirector';
import type { PredecessorRunLike, PredecessorOutputLike } from './directorContinuation';

// LTX-style timeline director (no segment_routing) -- same fixture shape as
// directorPlanner.test.ts's own RAW_CAPS.
const RAW_CAPS = {
	preset_modes: ['video'],
	modes: {
		t2v: {},
		i2v: {},
		flf: {},
		director: { audio: true, ic_lora: true, max_keyframes: 8 }
	},
	limits: { default_duration: 5, default_fps: 24, max_duration: 30 }
};

const caps = parseDirectorCapabilities(RAW_CAPS)!;

function shot(id: string, overrides: Partial<DirectorTimelineShot> = {}): DirectorTimelineShot {
	return {
		id,
		duration: 3,
		continue_from_previous: false,
		segments: [{ id: `${id}-seg`, start: 0, end: 3, text: '', prompt_segments: [] }],
		keyframes: [],
		audio: [],
		ic_lora: [],
		...overrides
	};
}

function timelineDoc(shots: DirectorTimelineShot[]): VideoDirectorValue {
	return {
		...normalizeDirectorValue({}, caps),
		mode: 'director',
		global_prompt: 'anchor',
		timeline: { fps: 24, shots }
	};
}

const GEN_A_FRAME = { path: 'generations/gen-a/1.mp4', relative_path: 'generations/gen-a/1.mp4', url: 'generations/gen-a/1.mp4', type: 'video' };

describe('runDirectorDependencyPlan', () => {
	it('submits an independent shot immediately -- no predecessor frame, no wait', async () => {
		const doc = timelineDoc([shot('a')]);
		const submit = vi.fn().mockResolvedValue(undefined);
		const waitForTerminal = vi.fn();
		const onBlocked = vi.fn();

		await runDirectorDependencyPlan(['a'], doc, caps, {
			getRuns: () => ({}),
			getOutputs: () => ({}),
			submit,
			waitForTerminal,
			onBlocked
		});

		expect(submit).toHaveBeenCalledWith('a', null);
		expect(waitForTerminal).not.toHaveBeenCalled();
		expect(onBlocked).not.toHaveBeenCalled();
	});

	it('predecessor completes -> successor is submitted with the resolved predecessor frame, in order', async () => {
		const doc = timelineDoc([shot('a'), shot('b', { continue_from_previous: true })]);
		let runs: Record<string, PredecessorRunLike> = {};
		const outputs: Record<string, PredecessorOutputLike> = { 'gen-a': { videos: [{ url: 'generations/gen-a/1.mp4' }] } };
		const submitOrder: string[] = [];

		const submit = vi.fn(async (shotId: string) => {
			submitOrder.push(shotId);
			if (shotId === 'a') runs = { a: { status: 'generating', generationId: 'gen-a' } };
		});
		const waitForTerminal = vi.fn(async (shotId: string) => {
			expect(shotId).toBe('a'); // only ever awaited for the shot this plan itself submitted
			runs = { a: { status: 'done', generationId: 'gen-a' } };
			return 'done' as const;
		});
		const onBlocked = vi.fn();

		await runDirectorDependencyPlan(['a', 'b'], doc, caps, {
			getRuns: () => runs,
			getOutputs: () => outputs,
			submit,
			waitForTerminal,
			onBlocked
		});

		expect(submitOrder).toEqual(['a', 'b']);
		expect(waitForTerminal).toHaveBeenCalledWith('a');
		expect(submit).toHaveBeenLastCalledWith('b', GEN_A_FRAME);
		expect(onBlocked).not.toHaveBeenCalled();
	});

	it('predecessor fails -> successor is never submitted, and is reported blocked (not silently dropped, not a fresh cut)', async () => {
		const doc = timelineDoc([shot('a'), shot('b', { continue_from_previous: true })]);
		let runs: Record<string, PredecessorRunLike> = {};

		const submit = vi.fn(async (shotId: string) => {
			if (shotId === 'a') runs = { a: { status: 'generating', generationId: 'gen-a' } };
		});
		const waitForTerminal = vi.fn(async () => {
			runs = { a: { status: 'failed', generationId: 'gen-a' } };
			return 'failed' as const;
		});
		const onBlocked = vi.fn();

		await runDirectorDependencyPlan(['a', 'b'], doc, caps, {
			getRuns: () => runs,
			getOutputs: () => ({}),
			submit,
			waitForTerminal,
			onBlocked
		});

		expect(submit).toHaveBeenCalledTimes(1);
		expect(submit).toHaveBeenCalledWith('a', null);
		expect(onBlocked).toHaveBeenCalledWith('b', 'Its previous shot failed to generate');
	});

	it('a predecessor OUTSIDE this plan that already finished earlier is resolved directly, with no wait', async () => {
		const doc = timelineDoc([shot('a'), shot('b', { continue_from_previous: true })]);
		const runs: Record<string, PredecessorRunLike> = {
			a: { status: 'done', generationId: 'gen-a', posterUrl: 'generations/gen-a/1.mp4' }
		};
		const submit = vi.fn().mockResolvedValue(undefined);
		const waitForTerminal = vi.fn();
		const onBlocked = vi.fn();

		// Only 'b' is in this plan -- 'a' finished before this plan ever ran
		// (planDirectorSelection only lets this through when 'a' already reads
		// 'done').
		await runDirectorDependencyPlan(['b'], doc, caps, {
			getRuns: () => runs,
			getOutputs: () => ({}),
			submit,
			waitForTerminal,
			onBlocked
		});

		expect(waitForTerminal).not.toHaveBeenCalled();
		expect(submit).toHaveBeenCalledWith('b', GEN_A_FRAME);
		expect(onBlocked).not.toHaveBeenCalled();
	});

	it('three-shot chain: b waits on a, c waits on b, each inheriting its OWN immediate predecessor (never a\'s)', async () => {
		const doc = timelineDoc([
			shot('a'),
			shot('b', { continue_from_previous: true }),
			shot('c', { continue_from_previous: true })
		]);
		let runs: Record<string, PredecessorRunLike> = {};
		const outputs: Record<string, PredecessorOutputLike> = {
			'gen-a': { videos: [{ url: 'generations/gen-a/1.mp4' }] },
			'gen-b': { videos: [{ url: 'generations/gen-b/1.mp4' }] }
		};
		const submitted: Array<[string, unknown]> = [];

		const submit = vi.fn(async (shotId: string, frame: unknown) => {
			submitted.push([shotId, frame]);
			runs = { ...runs, [shotId]: { status: 'generating', generationId: `gen-${shotId}` } };
		});
		const waitForTerminal = vi.fn(async (shotId: string) => {
			runs = { ...runs, [shotId]: { status: 'done', generationId: `gen-${shotId}` } };
			return 'done' as const;
		});

		await runDirectorDependencyPlan(['a', 'b', 'c'], doc, caps, {
			getRuns: () => runs,
			getOutputs: () => outputs,
			submit,
			waitForTerminal,
			onBlocked: vi.fn()
		});

		expect(submitted[0]).toEqual(['a', null]);
		expect(submitted[1]).toEqual(['b', GEN_A_FRAME]);
		expect(submitted[2]).toEqual([
			'c',
			{ path: 'generations/gen-b/1.mp4', relative_path: 'generations/gen-b/1.mp4', url: 'generations/gen-b/1.mp4', type: 'video' }
		]);
	});
});
