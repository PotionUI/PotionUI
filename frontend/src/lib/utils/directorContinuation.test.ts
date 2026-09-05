import { describe, it, expect } from 'vitest';
import { resolvePredecessorFrame, type PredecessorRunLike, type PredecessorOutputLike } from './directorContinuation';
import { parseDirectorCapabilities, normalizeDirectorValue } from './videoDirector';
import type { DirectorTimelineShot, VideoDirectorValue } from '$lib/types/videoDirector';

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

function run(overrides: Partial<PredecessorRunLike> = {}): PredecessorRunLike {
	return { status: 'done', generationId: 'gen-a', posterUrl: null, ...overrides };
}

describe('resolvePredecessorFrame', () => {
	it('a shot that does not continue from a previous shot reports so, without looking at runs at all', () => {
		const doc = timelineDoc([shot('a'), shot('b')]);
		const result = resolvePredecessorFrame(doc, caps, 'b', null, null);
		expect(result).toEqual({ ok: false, reason: 'This shot does not continue from a previous shot' });
	});

	it('the first shot in the film has no predecessor even if (incorrectly) marked continue_from_previous', () => {
		const doc = timelineDoc([shot('a', { continue_from_previous: true })]);
		const result = resolvePredecessorFrame(doc, caps, 'a', { 'a': run() }, null);
		expect(result).toEqual({ ok: false, reason: 'This shot does not continue from a previous shot' });
	});

	it('predecessor has no run at all yet', () => {
		const doc = timelineDoc([shot('a'), shot('b', { continue_from_previous: true })]);
		const result = resolvePredecessorFrame(doc, caps, 'b', null, null);
		expect(result).toEqual({ ok: false, reason: 'Its previous shot has not been generated yet' });
	});

	it('predecessor is still generating (pending, not a failure)', () => {
		const doc = timelineDoc([shot('a'), shot('b', { continue_from_previous: true })]);
		const result = resolvePredecessorFrame(doc, caps, 'b', { a: run({ status: 'generating' }) }, null);
		expect(result).toEqual({ ok: false, reason: 'Its previous shot is still generating' });
	});

	it('predecessor failed -- reported distinctly from "still generating"', () => {
		const doc = timelineDoc([shot('a'), shot('b', { continue_from_previous: true })]);
		const result = resolvePredecessorFrame(doc, caps, 'b', { a: run({ status: 'failed' }) }, null);
		expect(result).toEqual({ ok: false, reason: 'Its previous shot failed to generate' });
	});

	it('predecessor done but its output is nowhere to be found (no cached outputs, no posterUrl)', () => {
		const doc = timelineDoc([shot('a'), shot('b', { continue_from_previous: true })]);
		const result = resolvePredecessorFrame(doc, caps, 'b', { a: run({ status: 'done', posterUrl: null }) }, null);
		expect(result).toEqual({ ok: false, reason: "Its previous shot's output is not available" });
	});

	it('resolves the predecessor SHOT\'s own completed run -- never the first shot, never a different run', () => {
		const doc = timelineDoc([
			shot('a'),
			shot('b', { continue_from_previous: true }),
			shot('c', { continue_from_previous: true })
		]);
		const runs: Record<string, PredecessorRunLike> = {
			a: run({ generationId: 'gen-a', posterUrl: 'generations/gen-a/1.mp4' }),
			b: run({ generationId: 'gen-b', posterUrl: 'generations/gen-b/1.mp4' })
		};
		const outputs: Record<string, PredecessorOutputLike> = {
			'gen-a': { videos: [{ url: 'generations/gen-a/1.mp4' }] },
			'gen-b': { videos: [{ url: 'generations/gen-b/1.mp4' }] }
		};

		// Shot c continues from shot b (its immediate predecessor), not shot a.
		const result = resolvePredecessorFrame(doc, caps, 'c', runs, outputs);
		expect(result.ok).toBe(true);
		if (result.ok) {
			expect(result.media).toEqual({
				path: 'generations/gen-b/1.mp4',
				relative_path: 'generations/gen-b/1.mp4',
				url: 'generations/gen-b/1.mp4',
				type: 'video'
			});
			expect(result.predecessor).toEqual({ shotId: 'b', generationId: 'gen-b', outputKey: 'b' });
		}
	});

	it('prefers the cached generation output over a stale posterUrl on the same run', () => {
		const doc = timelineDoc([shot('a'), shot('b', { continue_from_previous: true })]);
		const runs: Record<string, PredecessorRunLike> = {
			a: run({ generationId: 'gen-a', posterUrl: 'stale/path.mp4' })
		};
		const outputs: Record<string, PredecessorOutputLike> = {
			'gen-a': { videos: [{ originalUrl: 'fresh/path.mp4' }] }
		};
		const result = resolvePredecessorFrame(doc, caps, 'b', runs, outputs);
		expect(result.ok).toBe(true);
		if (result.ok) expect(result.media).toMatchObject({ path: 'fresh/path.mp4' });
	});

	it('falls back to the persisted posterUrl once the transient output cache has been drained', () => {
		const doc = timelineDoc([shot('a'), shot('b', { continue_from_previous: true })]);
		const runs: Record<string, PredecessorRunLike> = {
			a: run({ generationId: 'gen-a', posterUrl: 'generations/gen-a/1.mp4' })
		};
		// outputsById has no entry at all for gen-a -- e.g. generation_complete
		// already called takeGenerationOutputs and cleared it.
		const result = resolvePredecessorFrame(doc, caps, 'b', runs, {});
		expect(result.ok).toBe(true);
		if (result.ok) expect(result.media).toMatchObject({ path: 'generations/gen-a/1.mp4' });
	});
});
