import { describe, it, expect } from 'vitest';
import {
	applyModelOverlay,
	describeEstimate,
	estimateShotsFor,
	directorOverlayFrom,
	modelFacts,
	modelFitNotices,
	parseCloudEstimate,
	planHostedRetry,
	hostedRetryKind,
	hostedRetryNotice,
	selectedCloudModelId,
	snapShotLengths,
	followModelDefaultLengths,
	modelDefaultLength,
	withDefaultLengthShot,
	withoutDefaultLength,
	hasLengthProblems,
	type DirectorModelOverlay
} from './cloudDirector';
import { buildDirectorSubmission, createDefaultDirectorValue, parseDirectorCapabilities, validateDirector } from './videoDirector';
import { planDirectorSelection } from './directorPlanner';
import type { ChainSegment, DirectorCapabilities, VideoDirectorValue } from '$lib/types/videoDirector';
import type { DirectorRunState } from '$lib/types/tabs';

const continuation = { source: 'last_frame', overlap_frames: 0, stitch: true };

const presetBlock = {
	preset_modes: ['txt2video', 'img2video'],
	segment_routing: true,
	modes: {
		t2v: {},
		i2v: {},
		flf: {},
		director: { keyframes: 'anywhere', max_segments: 8, audio: true, max_keyframes: 4, continuation, continue_from_video: true }
	},
	limits: { default_duration: 5, default_fps: 24, max_duration: 15 }
};

const fullOverlay: DirectorModelOverlay = {
	label: 'Fake Video',
	raw: {
		model_label: 'Fake Video',
		modes: { director: { max_segments: 6 } },
		limits: { default_duration: 5, default_fps: 24, max_duration: 10 }
	}
};

const liteOverlay: DirectorModelOverlay = {
	label: 'Fake Lite',
	raw: {
		model_label: 'Fake Lite',
		modes: { flf: null, director: { max_segments: 3, keyframes: 'first_only', audio: false } },
		limits: { default_duration: 4, default_fps: 24, max_duration: 8, durations: [4, 6, 8] }
	}
};

function capsFor(overlay: DirectorModelOverlay | null): DirectorCapabilities {
	const caps = parseDirectorCapabilities(applyModelOverlay(presetBlock, overlay));
	if (!caps) throw new Error('no capabilities');
	return caps;
}

function segment(id: string, duration: number, extra: Partial<ChainSegment> = {}): ChainSegment {
	return {
		id,
		prompt: `prompt ${id}`,
		prompt_segments: [],
		duration,
		loras: null,
		keyframe: null,
		keyframe_strength: 1,
		last_keyframe: null,
		last_keyframe_strength: 1,
		sub_type_override: null,
		steps: null,
		cfg: null,
		...extra
	};
}

function film(caps: DirectorCapabilities, segments: ChainSegment[]): VideoDirectorValue {
	const doc = createDefaultDirectorValue(caps);
	return { ...doc, mode: 'director', chain: { ...doc.chain, segments } };
}

const frame = { path: '/x.png', relative_path: 'x.png', url: '/x.png', type: 'image' as const };

function run(overrides: Partial<DirectorRunState>): DirectorRunState {
	return {
		generationId: 'gen-1',
		status: 'queued',
		progress: null,
		finishedAt: null,
		posterUrl: null,
		inputsHash: null,
		...overrides
	};
}

describe('applyModelOverlay', () => {
	it('leaves the preset block alone without an overlay', () => {
		expect(applyModelOverlay(presetBlock, null)).toBe(presetBlock);
	});

	it('keeps only the composition modes the model supports', () => {
		expect(capsFor(fullOverlay).enabledModes).toEqual(['t2v', 'i2v', 'flf', 'director']);
		expect(capsFor(liteOverlay).enabledModes).toEqual(['t2v', 'i2v', 'director']);
	});

	it('removes a mode the model names as unsupported', () => {
		const overlay: DirectorModelOverlay = { label: 'X', raw: { modes: { t2v: null, flf: null } } };
		expect(capsFor(overlay).enabledModes).toEqual(['i2v', 'director']);
	});

	it('never adds a mode the preset mode does not have', () => {
		const caps = parseDirectorCapabilities(
			applyModelOverlay({ ...presetBlock, modes: { t2v: {}, director: presetBlock.modes.director } }, { label: 'X', raw: { modes: { flf: {}, i2v: {} } } })
		)!;
		expect(caps.enabledModes).toEqual(['t2v', 'director']);
	});

	it('merges a mode the model describes onto the preset mode and keeps the rest of it', () => {
		const director = capsFor({ label: 'X', raw: { modes: { director: { max_segments: 2 } } } }).modes.director!;
		expect(director.maxSegments).toBe(2);
		expect(director.keyframes).toBe('anywhere');
		expect(director.audio).toBe(true);
	});

	it('reads the shot count and keyframe reach the model sets', () => {
		const lite = capsFor(liteOverlay).modes.director!;
		expect(lite.maxSegments).toBe(3);
		expect(lite.keyframes).toBe('first_only');
		expect(capsFor(fullOverlay).modes.director!.maxSegments).toBe(6);
		expect(capsFor(fullOverlay).modes.director!.keyframes).toBe('anywhere');
	});

	it('lets the model switch a capability off', () => {
		expect(capsFor(fullOverlay).modes.director!.audio).toBe(true);
		expect(capsFor(liteOverlay).modes.director!.audio).toBe(false);
	});

	it('replaces limits with the model limits and keeps the rest of the preset limits', () => {
		const caps = capsFor(liteOverlay);
		expect(caps.maxDuration).toBe(8);
		expect(caps.durations).toEqual([4, 6, 8]);
		expect(caps.defaultDuration).toBe(4);
		expect(capsFor(fullOverlay).durations).toBeNull();
	});

	it('records the model name and keeps the preset mode scoping', () => {
		const caps = capsFor(liteOverlay);
		expect(caps.modelLabel).toBe('Fake Lite');
		expect(caps.presetModes).toEqual(['txt2video', 'img2video']);
		expect(capsFor(null).modelLabel).toBeNull();
	});

	it('locks fps when the model fixes it', () => {
		const caps = capsFor({ label: 'Fixed', raw: { modes: { director: { fps_locked: true } } } });
		expect(caps.modes.director!.fpsLocked).toBe(true);
	});

	it('leaves no composition mode when the model supports none', () => {
		const none: DirectorModelOverlay = { label: 'Stills', raw: { modes: { t2v: null, i2v: null, flf: null, director: null } } };
		expect(parseDirectorCapabilities(applyModelOverlay(presetBlock, none))).toBeNull();
	});

	it('uses the catalog label when the overlay carries no name', () => {
		expect(capsFor({ label: 'Catalog Name', raw: {} }).modelLabel).toBe('Catalog Name');
		expect(capsFor({ label: 'Catalog Name', raw: { model_label: 'Own Name' } }).modelLabel).toBe('Own Name');
	});
});

describe('directorOverlayFrom', () => {
	it('reads the video_director block of a capabilities payload', () => {
		const overlay = directorOverlayFrom({ model_id: 'm', label: ' Fake Video ', params: [], inputs: [], video_director: { modes: {} } } as never);
		expect(overlay).toEqual({ label: 'Fake Video', raw: { modes: {} } });
	});

	it('is null for a model with no video block', () => {
		expect(directorOverlayFrom({ model_id: 'm', params: [], inputs: [], video_director: null } as never)).toBeNull();
		expect(directorOverlayFrom(undefined)).toBeNull();
	});
});

describe('selectedCloudModelId', () => {
	it('reads the model field', () => {
		expect(selectedCloudModelId({ model: { modelPath: 'model:abc' } })).toBe('abc');
	});

	it('finds a cloud model under another field name', () => {
		expect(selectedCloudModelId({ aspect: '16:9', engine_model: 'model:xyz' })).toBe('xyz');
	});

	it('is null for a local model or an empty form', () => {
		expect(selectedCloudModelId({ model: { modelPath: 'checkpoints/a.safetensors' } })).toBeNull();
		expect(selectedCloudModelId(null)).toBeNull();
	});
});

describe('modelFitNotices', () => {
	it('is quiet when the film fits', () => {
		const caps = capsFor(liteOverlay);
		expect(modelFitNotices(film(caps, [segment('a', 4), segment('b', 6)]), caps)).toEqual([]);
	});

	it('says which shots would not be made when there are too many', () => {
		const caps = capsFor(liteOverlay);
		const notices = modelFitNotices(film(caps, [segment('a', 4), segment('b', 4), segment('c', 4), segment('d', 4), segment('e', 4)]), caps);
		expect(notices).toEqual(['Fake Lite makes up to 3 shots in one film. Your film has 5, so shots 4 and 5 would not be made.']);
	});

	it('names the shots with an end picture the model cannot use', () => {
		const caps = capsFor(liteOverlay);
		const notices = modelFitNotices(film(caps, [segment('a', 4), segment('b', 4, { keyframe: frame, last_keyframe: frame })]), caps);
		expect(notices).toEqual(['Fake Lite cannot end a shot on a picture, so the end picture on shot 2 would not be used.']);
	});

	it('explains audio the model does not take', () => {
		const caps = capsFor(liteOverlay);
		const doc = film(caps, [segment('a', 4)]);
		doc.chain.audio = [{ id: 'aud', start: 0, trim_start: 0, length: 4, media: frame as never }];
		expect(modelFitNotices(doc, caps)).toEqual(['Fake Lite does not take an audio track, so the audio you added would not be used.']);
	});

	it('lists the lengths a model renders when a shot has a different one', () => {
		const caps = capsFor(liteOverlay);
		const notices = modelFitNotices(film(caps, [segment('a', 4), segment('b', 7), segment('c', 5)]), caps);
		expect(notices).toEqual(['Fake Lite renders shots of 4, 6 and 8 s. Shots 2 and 3 are a different length.']);
	});

	it('names the longest length when the model has a ceiling only', () => {
		const caps = capsFor(fullOverlay);
		const notices = modelFitNotices(film(caps, [segment('a', 12)]), caps);
		expect(notices).toEqual(['Fake Video renders up to 10 s a shot. Shot 1 is a different length.']);
	});

	it('says nothing without a hosted model', () => {
		const caps = capsFor(null);
		expect(modelFitNotices(film(caps, [segment('a', 30)]), caps)).toEqual([]);
	});
});

describe('validation under a model', () => {
	it('blocks a shot length the model does not render, in plain words', () => {
		const caps = capsFor(liteOverlay);
		const result = validateDirector(film(caps, [segment('a', 4), segment('b', 7)]), caps);
		expect(result.ok).toBe(false);
		expect(result.reasons).toContain('Shot 2: 7 s is not a length Fake Lite renders. Use 4, 6 or 8 s.');
	});

	it('blocks a shot over the model ceiling', () => {
		const caps = capsFor(fullOverlay);
		const result = validateDirector(film(caps, [segment('a', 12)]), caps);
		expect(result.reasons).toContain('12 s is longer than Fake Video renders. The most it does is 10 s.');
	});

	it('blocks too many shots and names the model', () => {
		const caps = capsFor(liteOverlay);
		const result = validateDirector(film(caps, [segment('a', 4), segment('b', 4), segment('c', 4), segment('d', 4)]), caps);
		expect(result.reasons).toContain('Fake Lite makes up to 3 shots in one film, this one has 4.');
	});

	it('passes a film that fits', () => {
		const caps = capsFor(liteOverlay);
		expect(validateDirector(film(caps, [segment('a', 4), segment('b', 6)]), caps).ok).toBe(true);
	});

	it('keeps the generic wording without a model', () => {
		const caps = capsFor(null);
		const tooMany = Array.from({ length: 9 }, (_, i) => segment(`s${i}`, 4));
		expect(validateDirector(film(caps, tooMany), caps).reasons).toContain('Too many segments (max 8)');
	});
});

describe('modelFacts', () => {
	it('describes a full model', () => {
		expect(modelFacts(capsFor(fullOverlay))).toEqual([
			'Starts from a picture',
			'Ends on a picture',
			'Up to 10 s a shot',
			'Up to 6 shots',
			'Takes audio'
		]);
	});

	it('describes a smaller model with its reasons', () => {
		expect(modelFacts(capsFor(liteOverlay))).toEqual([
			'Starts from a picture',
			'No end picture',
			'Shots of 4, 6, 8 s',
			'Up to 3 shots',
			'No audio'
		]);
	});

	it('is empty without a model', () => {
		expect(modelFacts(capsFor(null))).toEqual([]);
	});
});

describe('estimate text', () => {
	it('parses the estimate payload', () => {
		expect(parseCloudEstimate({ data: { shot_count: 4, total_usd: '0.40', known: true } })).toEqual({ shots: 4, total_usd: '0.40', known: true });
	});

	it('rejects an empty or malformed payload', () => {
		expect(parseCloudEstimate(null)).toBeNull();
		expect(parseCloudEstimate({ data: { shot_count: 0 } })).toBeNull();
		expect(parseCloudEstimate({ data: { shot_count: 'x' } })).toBeNull();
	});

	it('says the price in plain words', () => {
		expect(describeEstimate(parseCloudEstimate({ shot_count: 4, total_usd: '0.40', known: true }))).toBe('About $0.40 for 4 shots');
	});

	it('says the price is unknown when the backend does not know it', () => {
		expect(describeEstimate(parseCloudEstimate({ shot_count: 3, total_usd: null, known: false }))).toBe('Price unknown for 3 shots');
	});

	it('mentions shots without a price', () => {
		expect(describeEstimate(parseCloudEstimate({ shot_count: 4, total_usd: '0.30', known: false }))).toBe(
			'About $0.30 for 4 shots, some not priced'
		);
	});

	it('stays silent for a single paid call or no estimate', () => {
		expect(describeEstimate(parseCloudEstimate({ shot_count: 1, total_usd: '0.10', known: true }))).toBeNull();
		expect(describeEstimate(null)).toBeNull();
	});
});

describe('estimateShotsFor', () => {
	const caps = capsFor(fullOverlay);
	const shots = [segment('a', 4), segment('b', 6), segment('c', 4, { keyframe: frame })];
	const doc = film(caps, shots);
	const form = { resolution: '720p', aspect_ratio: '16:9', generate_audio: false, seed: 4, model: { modelPath: 'model:x' } };

	it('prices every shot with its own length and the form settings that change the price', () => {
		expect(estimateShotsFor(doc, form, [])).toEqual([
			{ task: 'txt2video', params: { resolution: '720p', aspect_ratio: '16:9', generate_audio: false, duration_s: 4 } },
			{ task: 'img2video', params: { resolution: '720p', aspect_ratio: '16:9', generate_audio: false, duration_s: 6 } },
			{ task: 'img2video', params: { resolution: '720p', aspect_ratio: '16:9', generate_audio: false, duration_s: 4 } }
		]);
	});

	it('covers only the selected shots', () => {
		expect(estimateShotsFor(doc, form, ['c']).map((s) => s.params.duration_s)).toEqual([4]);
	});

	it('leaves out form settings that are not set', () => {
		expect(estimateShotsFor(doc, {}, ['a'])).toEqual([{ task: 'txt2video', params: { duration_s: 4 } }]);
	});
});

describe('planHostedRetry', () => {
	const caps = capsFor(fullOverlay);
	const doc = film(caps, [segment('a', 4), segment('b', 4), segment('c', 4)]);

	it('starts the failed shot from the previous clip and takes the skipped shots with it', () => {
		const runs = {
			a: run({ status: 'done', posterUrl: '/clips/a.mp4', outputPath: 'generations/d/g/a.mp4' }),
			b: run({ status: 'failed' }),
			c: run({ status: 'failed' })
		};
		const plan = planHostedRetry(doc, caps, runs, ['b']);
		expect(plan.shotIds).toEqual(['b', 'c']);
		expect(plan.handoffFrom).toBe('a');
		expect(plan.doc.chain.segments[1].keyframe).toMatchObject({ path: 'generations/d/g/a.mp4', relative_path: 'generations/d/g/a.mp4', url: '/clips/a.mp4', type: 'video' });
		expect(plan.doc.chain.segments[0].keyframe).toBeNull();
	});

	it('retries from the start without a hand-off', () => {
		const runs = { a: run({ status: 'failed' }), b: run({ status: 'failed' }), c: run({ status: 'failed' }) };
		const plan = planHostedRetry(doc, caps, runs, ['a']);
		expect(plan.shotIds).toEqual(['a', 'b', 'c']);
		expect(plan.handoffFrom).toBeNull();
		expect(plan.doc).toBe(doc);
	});

	it('keeps a start picture the user chose', () => {
		const own = film(caps, [segment('a', 4), segment('b', 4, { keyframe: frame })]);
		const runs = { a: run({ status: 'done', posterUrl: '/clips/a.mp4', outputPath: 'generations/d/g/a.mp4' }), b: run({ status: 'failed' }) };
		const plan = planHostedRetry(own, caps, runs, ['b']);
		expect(plan.handoffFrom).toBeNull();
		expect(plan.doc.chain.segments[1].keyframe).toBe(frame);
	});

	it('makes the whole film again when the previous clip was not saved', () => {
		const runs = { a: run({ status: 'done', posterUrl: '/clips/a.mp4' }), b: run({ status: 'failed' }) };
		const plan = planHostedRetry(doc, caps, runs, ['b']);
		expect(plan.kind).toBe('restart');
		expect(plan.shotIds).toEqual(['a', 'b', 'c']);
		expect(plan.handoffFrom).toBeNull();
	});

	it('starts from the first shot when the shots before it failed too', () => {
		const runs = { a: run({ status: 'failed' }), b: run({ status: 'failed' }) };
		const plan = planHostedRetry(doc, caps, runs, ['b']);
		expect(plan.kind).toBe('plain');
		expect(plan.shotIds).toEqual(['a', 'b']);
	});

	it('retries a skipped shot from the first shot that failed before it', () => {
		const runs = {
			a: run({ status: 'done', posterUrl: '/clips/a.mp4', outputPath: 'generations/d/g/a.mp4' }),
			b: run({ status: 'failed' }),
			c: run({ status: 'failed' })
		};
		const plan = planHostedRetry(doc, caps, runs, ['c']);
		expect(plan.shotIds).toEqual(['b', 'c']);
		expect(plan.handoffFrom).toBe('a');
		expect(hostedRetryKind(doc, caps, runs, 'c')).toBe('handoff');
	});

	it('only retries the shots asked for when the later ones succeeded', () => {
		const runs = {
			a: run({ status: 'done', posterUrl: '/clips/a.mp4', outputPath: 'generations/d/g/a.mp4' }),
			b: run({ status: 'failed' }),
			c: run({ status: 'done', posterUrl: '/clips/c.mp4' })
		};
		expect(planHostedRetry(doc, caps, runs, ['b']).shotIds).toEqual(['b']);
	});

	it('changes nothing without a hosted model', () => {
		const native = capsFor(null);
		const plan = planHostedRetry(doc, native, { a: run({ status: 'done', posterUrl: '/a.mp4' }), b: run({ status: 'failed' }) }, ['b']);
		expect(plan.doc).toBe(doc);
		expect(plan.shotIds).toEqual(['b']);
	});
});

describe('snapShotLengths', () => {
	it('moves a shot to the nearest length the model renders and leaves the others', () => {
		const caps = capsFor(liteOverlay);
		const doc = film(caps, [segment('a', 4), segment('b', 5), segment('c', 9), segment('d', 7.5)]);
		expect(hasLengthProblems(doc, caps)).toBe(true);
		const fixed = snapShotLengths(doc, caps);
		expect(fixed.chain.segments.map((s) => s.duration)).toEqual([4, 4, 8, 8]);
		expect(fixed.chain.segments[0]).toBe(doc.chain.segments[0]);
		expect(hasLengthProblems(fixed, caps)).toBe(false);
	});

	it('shortens a shot to the ceiling when the model has no list of lengths', () => {
		const caps = capsFor(fullOverlay);
		const fixed = snapShotLengths(film(caps, [segment('a', 12), segment('b', 3)]), caps);
		expect(fixed.chain.segments.map((s) => s.duration)).toEqual([10, 3]);
	});

	it('returns the same film when nothing needs fixing', () => {
		const caps = capsFor(liteOverlay);
		const doc = film(caps, [segment('a', 4)]);
		expect(snapShotLengths(doc, caps)).toBe(doc);
	});
});

describe('retry for a model that cannot continue from a clip', () => {
	const noClip = capsFor({ label: 'Fake Text', raw: { modes: { director: { continue_from_video: false } } } });
	const doc = film(noClip, [segment('a', 4), segment('b', 4), segment('c', 4)]);
	const runs = {
		a: run({ status: 'done', posterUrl: '/clips/a.mp4', outputPath: 'generations/d/g/a.mp4' }),
		b: run({ status: 'failed' }),
		c: run({ status: 'failed' })
	};

	it('reads continue_from_video from the effective capabilities', () => {
		expect(noClip.modes.director?.continueFromVideo).toBe(false);
		expect(capsFor(fullOverlay).modes.director?.continueFromVideo).toBe(true);
	});

	it('redoes the film from the first shot and says so', () => {
		expect(hostedRetryKind(doc, noClip, runs, 'b')).toBe('restart');
		const plan = planHostedRetry(doc, noClip, runs, ['b']);
		expect(plan.shotIds).toEqual(['a', 'b', 'c']);
		expect(plan.doc).toBe(doc);
		expect(hostedRetryNotice(noClip)).toBe('Fake Text cannot carry on from a finished shot, so the whole film is made again from the first shot.');
	});

	it('restarts just the shot when it already starts fresh', () => {
		const fresh = film(noClip, [segment('a', 4), segment('b', 4, { keyframe: frame }), segment('c', 4)]);
		expect(hostedRetryKind(fresh, noClip, runs, 'b')).toBe('plain');
		expect(planHostedRetry(fresh, noClip, runs, ['b']).shotIds).toEqual(['b', 'c']);
	});

	it('restarts just the shot when the model makes every shot a hard cut', () => {
		const cuts = capsFor({ label: 'Fake Text', raw: { modes: { director: { keyframes: null, continuation: null } } } });
		const hardCuts = film(cuts, [segment('a', 4), segment('b', 4)]);
		expect(hostedRetryKind(hardCuts, cuts, runs, 'b')).toBe('plain');
	});

	it('starts the retry from a plain first shot', () => {
		expect(hostedRetryKind(doc, noClip, runs, 'a')).toBe('plain');
	});
});

describe('the request built for a retry from shot k', () => {
	const caps = capsFor(fullOverlay);
	const doc = film(caps, [segment('a', 4), segment('b', 4), segment('c', 4)]);
	const runs = {
		a: run({ status: 'done', posterUrl: '/api/media/generations/g/a.mp4', outputPath: 'generations/2026-10-02/g/a.mp4' }),
		b: run({ status: 'failed' }),
		c: run({ status: 'failed' })
	};

	it('passes the plan and sends the saved clip as the start media of shot k', () => {
		const plan = planHostedRetry(doc, caps, runs, ['b']);
		expect(planDirectorSelection(plan.doc, caps, runs, plan.shotIds).blockingReasons).toEqual([]);
		const [wire] = buildDirectorSubmission(plan.doc, caps, new Set(plan.shotIds));
		const first = wire.media.filter((entry) => entry.role === 'first');
		expect(first).toHaveLength(1);
		expect(first[0]).toMatchObject({
			role: 'first',
			segment_id: 'b',
			media: { type: 'video', path: 'generations/2026-10-02/g/a.mp4', relative_path: 'generations/2026-10-02/g/a.mp4' }
		});
		expect(wire.render).toEqual({ scope: 'shots', shot_ids: ['b', 'c'] });
		expect(wire.segments.map((s) => s.id)).toEqual(['a', 'b', 'c']);
	});

	it('is rejected by the planner without the hand-off', () => {
		expect(planDirectorSelection(doc, caps, runs, ['b', 'c']).blockingReasons.length).toBeGreaterThan(0);
	});
});

describe('model default lengths', () => {
	const lite = capsFor(liteOverlay);
	const full = capsFor(fullOverlay);
	const native = capsFor(null);

	function blank(caps: DirectorCapabilities, duration = 5): VideoDirectorValue {
		return film(caps, [{ ...segment('a', duration), prompt: '' }]);
	}

	it('takes the catalog default, else the nearest length the model renders', () => {
		expect(modelDefaultLength(lite)).toBe(4);
		expect(modelDefaultLength(full)).toBe(5);
		const odd = capsFor({ label: 'Odd', raw: { limits: { durations: [2, 6], default_duration: 5, max_duration: 6 } } });
		expect(modelDefaultLength(odd)).toBe(6);
		const short = capsFor({ label: 'Short', raw: { limits: { default_duration: 5, max_duration: 3 } } });
		expect(modelDefaultLength(short)).toBe(3);
	});

	it('moves the length of an untouched blank film to the model default', () => {
		const next = followModelDefaultLengths(blank(lite, 5), lite);
		expect(next.chain.segments[0].duration).toBe(4);
		expect(next.ui?.defaultLengths).toEqual(['a']);
		expect(validateDirector({ ...next, global_prompt: 'x' }, lite).reasons.some((r) => r.includes('not a length'))).toBe(false);
	});

	it('follows a new model again while the length is still the default', () => {
		const start = capsFor({ label: 'Start', raw: { limits: { durations: [3, 5], default_duration: 3, max_duration: 5 } } });
		const first = followModelDefaultLengths(blank(lite, 5), lite);
		const second = followModelDefaultLengths(first, start);
		expect(second.chain.segments[0].duration).toBe(3);
		expect(followModelDefaultLengths(second, start)).toBe(second);
	});

	it('never moves a length the person set', () => {
		const first = followModelDefaultLengths(blank(lite, 5), lite);
		const edited = withoutDefaultLength({ ...first, chain: { ...first.chain, segments: [{ ...first.chain.segments[0], duration: 7 }] } }, 'a');
		const start = capsFor({ label: 'Start', raw: { limits: { durations: [3, 5], default_duration: 3, max_duration: 5 } } });
		const after = followModelDefaultLengths(edited, start);
		expect(after.chain.segments[0].duration).toBe(7);
		expect(modelFitNotices(after, start).length).toBeGreaterThan(0);
	});

	it('leaves a film made before the model alone', () => {
		const legacy = film(lite, [segment('a', 5)]);
		expect(followModelDefaultLengths(legacy, lite)).toBe(legacy);
		expect(modelFitNotices(legacy, lite).length).toBeGreaterThan(0);
	});

	it('gives a new shot the model default and remembers it is a default', () => {
		const withShot = withDefaultLengthShot(film(lite, [segment('a', 5), segment('b', 5)]), lite, 'b');
		expect(withShot.chain.segments.map((s) => s.duration)).toEqual([5, 4]);
		expect(withShot.ui?.defaultLengths).toEqual(['b']);
		const moved = followModelDefaultLengths(withShot, full);
		expect(moved.chain.segments.map((s) => s.duration)).toEqual([5, 5]);
	});

	it('does nothing without a hosted model', () => {
		const doc = blank(native, 5);
		expect(followModelDefaultLengths(doc, native)).toBe(doc);
		expect(withDefaultLengthShot(doc, native, 'a')).toBe(doc);
	});
});
