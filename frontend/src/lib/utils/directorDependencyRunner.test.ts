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
		const submit = vi.fn().mockResolvedValue({ ok: true, generationId: 'gen-a' });
		const waitForTerminal = vi.fn();
		const onBlocked = vi.fn();

		await runDirectorDependencyPlan(['a'], doc, caps, {
			getRuns: () => ({}),
			getOutputs: () => ({}),
			submit,
			waitForTerminal,
			onBlocked
		});

		expect(submit).toHaveBeenCalledWith('a', null, null);
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
			if (shotId === 'a') {
				runs = { a: { status: 'generating', generationId: 'gen-a' } };
				return { ok: true as const, generationId: 'gen-a' };
			}
			return { ok: true as const, generationId: 'gen-b' };
		});
		const waitForTerminal = vi.fn(async (shotId: string, generationId: string) => {
			expect(shotId).toBe('a'); // only ever awaited for the shot this plan itself submitted
			expect(generationId).toBe('gen-a'); // the EXACT generation id submit() returned, not "whichever run occupies the shot"
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
		expect(waitForTerminal).toHaveBeenCalledWith('a', 'gen-a');
		expect(submit).toHaveBeenLastCalledWith('b', GEN_A_FRAME, { generationId: 'gen-a', outputKey: 'a' });
		expect(onBlocked).not.toHaveBeenCalled();
	});

	it('predecessor fails -> successor is never submitted, and is reported blocked (not silently dropped, not a fresh cut)', async () => {
		const doc = timelineDoc([shot('a'), shot('b', { continue_from_previous: true })]);
		const submit = vi.fn().mockResolvedValue({ ok: true, generationId: 'gen-a' });
		const waitForTerminal = vi.fn().mockResolvedValue('failed');
		const onBlocked = vi.fn();

		await runDirectorDependencyPlan(['a', 'b'], doc, caps, {
			getRuns: () => ({}),
			getOutputs: () => ({}),
			submit,
			waitForTerminal,
			onBlocked
		});

		expect(submit).toHaveBeenCalledTimes(1);
		expect(submit).toHaveBeenCalledWith('a', null, null);
		expect(waitForTerminal).toHaveBeenCalledWith('a', 'gen-a');
		expect(onBlocked).toHaveBeenCalledWith('b', 'Its previous shot failed to generate');
	});

	it('predecessor\'s wait is abandoned (tab closed / superseded) -> successor is blocked with a distinct reason, never left hanging', async () => {
		const doc = timelineDoc([shot('a'), shot('b', { continue_from_previous: true })]);
		const submit = vi.fn().mockResolvedValue({ ok: true, generationId: 'gen-a' });
		const waitForTerminal = vi.fn().mockResolvedValue('abandoned');
		const onBlocked = vi.fn();

		await runDirectorDependencyPlan(['a', 'b'], doc, caps, {
			getRuns: () => ({}),
			getOutputs: () => ({}),
			submit,
			waitForTerminal,
			onBlocked
		});

		expect(onBlocked).toHaveBeenCalledWith('b', "Its previous shot's generation was interrupted");
	});

	it('a same-batch predecessor whose OWN submission fails to even start blocks its dependant -- never resolved from a stale OLD "done" run for that shot', async () => {
		const doc = timelineDoc([shot('a'), shot('b', { continue_from_previous: true })]);
		// getRuns() reflects an OLD, unrelated 'done' run for shot 'a' (e.g. a
		// prior successful generation) that THIS plan's own resubmission of
		// 'a' never actually superseded, because that resubmission failed to
		// even start. The runner must never treat this stale snapshot as
		// proof that 'a' succeeded in THIS plan.
		const staleRuns: Record<string, PredecessorRunLike> = {
			a: { status: 'done', generationId: 'gen-a-OLD', posterUrl: 'generations/gen-a-OLD/1.mp4' }
		};
		const staleOutputs: Record<string, PredecessorOutputLike> = {
			'gen-a-OLD': { videos: [{ url: 'generations/gen-a-OLD/1.mp4' }] }
		};
		const submit = vi.fn(async (shotId: string) => {
			if (shotId === 'a') return { ok: false as const, reason: 'Shot failed to start.' };
			return { ok: true as const, generationId: 'gen-b' };
		});
		const waitForTerminal = vi.fn();
		const onBlocked = vi.fn();

		await runDirectorDependencyPlan(['a', 'b'], doc, caps, {
			getRuns: () => staleRuns,
			getOutputs: () => staleOutputs,
			submit,
			waitForTerminal,
			onBlocked
		});

		expect(submit).toHaveBeenCalledTimes(1); // 'b' never submitted
		expect(submit).toHaveBeenCalledWith('a', null, null);
		expect(waitForTerminal).not.toHaveBeenCalled(); // 'a' never even got a generation id to wait on
		expect(onBlocked).toHaveBeenCalledWith('b', 'Its previous shot failed to generate');
	});

	it('a predecessor OUTSIDE this plan that already finished earlier is resolved directly, with no wait', async () => {
		const doc = timelineDoc([shot('a'), shot('b', { continue_from_previous: true })]);
		const runs: Record<string, PredecessorRunLike> = {
			a: { status: 'done', generationId: 'gen-a', posterUrl: 'generations/gen-a/1.mp4' }
		};
		const submit = vi.fn().mockResolvedValue({ ok: true, generationId: 'gen-b' });
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
		expect(submit).toHaveBeenCalledWith('b', GEN_A_FRAME, { generationId: 'gen-a', outputKey: 'a' });
		expect(onBlocked).not.toHaveBeenCalled();
	});

	it('a shot presubmitted by the caller (the ordinary Generate button\'s primary tab) is never resubmitted, and its dependant waits on the seeded generation id', async () => {
		const doc = timelineDoc([shot('a'), shot('b', { continue_from_previous: true })]);
		const outputs: Record<string, PredecessorOutputLike> = { 'gen-a-primary': { videos: [{ url: 'generations/gen-a/1.mp4' }] } };
		const submit = vi.fn().mockResolvedValue({ ok: true, generationId: 'gen-b' });
		const waitForTerminal = vi.fn(async (shotId: string, generationId: string) => {
			expect(shotId).toBe('a');
			expect(generationId).toBe('gen-a-primary');
			return 'done' as const;
		});
		const onBlocked = vi.fn();

		await runDirectorDependencyPlan(
			['a', 'b'],
			doc,
			caps,
			{
				getRuns: () => ({ a: { status: 'done', generationId: 'gen-a-primary' } }),
				getOutputs: () => outputs,
				submit,
				waitForTerminal,
				onBlocked
			},
			{ a: 'gen-a-primary' }
		);

		expect(submit).toHaveBeenCalledTimes(1); // 'a' never resubmitted
		expect(submit).toHaveBeenCalledWith('b', GEN_A_FRAME, { generationId: 'gen-a-primary', outputKey: 'a' });
		expect(waitForTerminal).toHaveBeenCalledWith('a', 'gen-a-primary');
		expect(onBlocked).not.toHaveBeenCalled();
	});

	// The EXACT contract the ordinary Generate button's main path uses
	// (+page.svelte's `startGeneration`): the primary shot ('a') is submitted
	// OUTSIDE this runner entirely and is NEVER itself a member of
	// `shotsToSubmit` -- only `directorRemainingShotIds` (here, just ['b']) is
	// passed in, with 'a' seeded via `presubmittedGenerationIds`. Distinct from
	// the "shot presubmitted by the caller" control above, which (unrealistically
	// for that call site) still lists 'a' in `shotsToSubmit` too -- these three
	// cases prove the fix holds even when 'a' is ABSENT from that array, which is
	// what actually reaches this function from `startGeneration`.
	it('remaining-only contract (shotsToSubmit=[b], presubmitted={a}): b waits once on a\'s seeded generation, then submits with a\'s fresh frame', async () => {
		const doc = timelineDoc([shot('a'), shot('b', { continue_from_previous: true })]);
		const outputs: Record<string, PredecessorOutputLike> = { 'gen-a-primary': { videos: [{ url: 'generations/gen-a/1.mp4' }] } };
		const submit = vi.fn().mockResolvedValue({ ok: true, generationId: 'gen-b' });
		const waitForTerminal = vi.fn().mockResolvedValue('done');
		const onBlocked = vi.fn();

		await runDirectorDependencyPlan(
			['b'],
			doc,
			caps,
			{
				getRuns: () => ({ a: { status: 'done', generationId: 'gen-a-primary' } }),
				getOutputs: () => outputs,
				submit,
				waitForTerminal,
				onBlocked
			},
			{ a: 'gen-a-primary' }
		);

		expect(submit).toHaveBeenCalledTimes(1); // 'a' never (re)submitted -- it isn't even in shotsToSubmit
		expect(waitForTerminal).toHaveBeenCalledTimes(1);
		expect(waitForTerminal).toHaveBeenCalledWith('a', 'gen-a-primary');
		expect(submit).toHaveBeenCalledWith('b', GEN_A_FRAME, { generationId: 'gen-a-primary', outputKey: 'a' });
		expect(onBlocked).not.toHaveBeenCalled();
	});

	it('remaining-only contract: the presubmitted primary fails -> b is blocked, never submitted', async () => {
		const doc = timelineDoc([shot('a'), shot('b', { continue_from_previous: true })]);
		const submit = vi.fn().mockResolvedValue({ ok: true, generationId: 'gen-b' });
		const waitForTerminal = vi.fn().mockResolvedValue('failed');
		const onBlocked = vi.fn();

		await runDirectorDependencyPlan(
			['b'],
			doc,
			caps,
			{
				getRuns: () => ({ a: { status: 'generating', generationId: 'gen-a-primary' } }),
				getOutputs: () => ({}),
				submit,
				waitForTerminal,
				onBlocked
			},
			{ a: 'gen-a-primary' }
		);

		expect(waitForTerminal).toHaveBeenCalledWith('a', 'gen-a-primary');
		expect(submit).not.toHaveBeenCalled();
		expect(onBlocked).toHaveBeenCalledWith('b', 'Its previous shot failed to generate');
	});

	it('remaining-only contract: the presubmitted primary is abandoned (tab removed / superseded) -> b is blocked with the abandon reason, never submitted', async () => {
		const doc = timelineDoc([shot('a'), shot('b', { continue_from_previous: true })]);
		const submit = vi.fn().mockResolvedValue({ ok: true, generationId: 'gen-b' });
		const waitForTerminal = vi.fn().mockResolvedValue('abandoned');
		const onBlocked = vi.fn();

		await runDirectorDependencyPlan(
			['b'],
			doc,
			caps,
			{
				getRuns: () => ({ a: { status: 'generating', generationId: 'gen-a-primary' } }),
				getOutputs: () => ({}),
				submit,
				waitForTerminal,
				onBlocked
			},
			{ a: 'gen-a-primary' }
		);

		expect(waitForTerminal).toHaveBeenCalledWith('a', 'gen-a-primary');
		expect(submit).not.toHaveBeenCalled();
		expect(onBlocked).toHaveBeenCalledWith('b', "Its previous shot's generation was interrupted");
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
		const submitted: Array<[string, unknown, unknown]> = [];

		const submit = vi.fn(async (shotId: string, frame: unknown, predecessorRef: unknown) => {
			submitted.push([shotId, frame, predecessorRef]);
			runs = { ...runs, [shotId]: { status: 'generating', generationId: `gen-${shotId}` } };
			return { ok: true as const, generationId: `gen-${shotId}` };
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

		expect(submitted[0]).toEqual(['a', null, null]);
		expect(submitted[1]).toEqual(['b', GEN_A_FRAME, { generationId: 'gen-a', outputKey: 'a' }]);
		expect(submitted[2]).toEqual([
			'c',
			{ path: 'generations/gen-b/1.mp4', relative_path: 'generations/gen-b/1.mp4', url: 'generations/gen-b/1.mp4', type: 'video' },
			{ generationId: 'gen-b', outputKey: 'b' }
		]);
	});
});
