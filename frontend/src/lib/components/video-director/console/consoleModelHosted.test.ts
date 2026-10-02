import { describe, it, expect } from 'vitest';
import { deriveConsoleModel } from './consoleModel';
import { applyModelOverlay } from '$lib/utils/cloudDirector';
import { createDefaultDirectorValue, parseDirectorCapabilities } from '$lib/utils/videoDirector';
import type { ChainSegment, DirectorCapabilities, VideoDirectorValue } from '$lib/types/videoDirector';
import type { DirectorRunState } from '$lib/types/tabs';

const continuation = { source: 'last_frame', overlap_frames: 0, stitch: true };

function capsFor(label: string | null): DirectorCapabilities {
	const preset = {
		segment_routing: true,
		modes: { t2v: {}, i2v: {}, director: { keyframes: 'first_only', max_segments: 6, continuation } },
		limits: { default_duration: 4, default_fps: 24, max_duration: 8 }
	};
	const overlay = label ? { label, raw: { modes: { t2v: {}, i2v: {}, director: { keyframes: 'first_only', continuation } } } } : null;
	return parseDirectorCapabilities(applyModelOverlay(preset, overlay))!;
}

function segment(id: string): ChainSegment {
	return {
		id,
		prompt: `prompt ${id}`,
		prompt_segments: [],
		duration: 4,
		loras: null,
		keyframe: null,
		keyframe_strength: 1,
		last_keyframe: null,
		last_keyframe_strength: 1,
		sub_type_override: null,
		steps: null,
		cfg: null
	};
}

function film(caps: DirectorCapabilities): VideoDirectorValue {
	const doc = createDefaultDirectorValue(caps);
	return { ...doc, mode: 'director', chain: { ...doc.chain, segments: [segment('a'), segment('b'), segment('c')] } };
}

function run(overrides: Partial<DirectorRunState>): DirectorRunState {
	return { generationId: 'gen-1', status: 'queued', progress: null, finishedAt: null, posterUrl: null, inputsHash: null, ...overrides };
}

describe('console model for a hosted model', () => {
	it('lets a failed shot start again from the previous clip', () => {
		const caps = capsFor('Fake Video');
		const runs = { a: run({ status: 'done', posterUrl: '/clips/a.mp4', outputPath: 'generations/d/g/a.mp4' }), b: run({ status: 'failed' }), c: run({ status: 'failed' }) };
		const model = deriveConsoleModel(film(caps), caps, { activeShotId: null }, null, runs);
		expect(model.shots[1].badge).toBe('input-ready');
	});

	it('does not offer the hand-off when the previous shot left no clip', () => {
		const caps = capsFor('Fake Video');
		const runs = { a: run({ status: 'done', posterUrl: '/clips/a.mp4' }), b: run({ status: 'failed' }) };
		const model = deriveConsoleModel(film(caps), caps, { activeShotId: null }, null, runs);
		expect(model.shots[1].badge).toBe('needs-previous');
	});

	it('keeps the native behaviour without a model', () => {
		const caps = capsFor(null);
		const runs = { a: run({ status: 'done', posterUrl: '/clips/a.mp4', outputPath: 'generations/d/g/a.mp4' }), b: run({ status: 'failed' }) };
		const model = deriveConsoleModel(film(caps), caps, { activeShotId: null }, null, runs);
		expect(model.shots[1].badge).toBe('needs-previous');
	});

	it('does not warn about a missing predecessor when the finished clip can be reused', () => {
		const caps = capsFor('Fake Video');
		const runs = { a: run({ status: 'done', posterUrl: '/clips/a.mp4', outputPath: 'generations/d/g/a.mp4' }), b: run({ status: 'failed' }) };
		const model = deriveConsoleModel(film(caps), caps, { activeShotId: null }, null, runs, new Set(['b']));
		expect(model.joins.find((j) => j.beforeShotId === 'b')?.kind).not.toBe('missing');
	});

	it('carries the failure reason on the shot row', () => {
		const caps = capsFor('Fake Video');
		const runs = { b: run({ status: 'failed', message: 'The provider refused this prompt.' }), c: run({ status: 'failed' }) };
		const model = deriveConsoleModel(film(caps), caps, { activeShotId: null }, null, runs);
		expect(model.shots[1].run).toEqual({ kind: 'failed', message: 'The provider refused this prompt.' });
		expect(model.shots[2].run).toEqual({ kind: 'failed' });
	});

	it('reads not ready with the model-aware reason when the film is too long', () => {
		const caps = capsFor('Fake Video');
		const doc = film(caps);
		doc.chain.segments = Array.from({ length: 7 }, (_, i) => segment(`s${i}`));
		const header = deriveConsoleModel(doc, caps, { activeShotId: null }).header;
		expect(header.readiness).toEqual({ ok: false, text: 'Fake Video makes up to 6 shots in one film, this one has 7.' });
	});
});
