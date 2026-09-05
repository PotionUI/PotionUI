import { describe, it, expect } from 'vitest';
import { assembleDirectorRequest } from './requestAssembly';
import { parseDirectorCapabilities } from '$lib/utils/videoDirector';
import { parseMusicDirectorCapabilities } from '$lib/utils/musicDirector';
import type { DirectorRunState } from '$lib/types/tabs';
import type { DirectorTimelineShot, VideoDirectorValue } from '$lib/types/videoDirector';

// `assembleDirectorRequest` normalizes `videoDirectorValue` itself (exactly
// as the generate page's own `normalizeDirectorValue(currentTab.videoDirector,
// caps)` does) - fixtures below pass the RAW, un-normalized shape, never a
// pre-normalized `VideoDirectorValue`: `normalizeDirectorValue` is not
// idempotent on its own output for every capability shape, so normalizing
// twice (once here, once inside the helper) would silently corrupt the doc.

// Wan-shaped routed chain director (segment_routing: true) - same fixture
// shape as directorPlanner.test.ts's own WAN_RAW_CAPS.
const WAN_RAW_CAPS = {
	preset_modes: ['video'],
	segment_routing: true,
	modes: {
		director: { keyframes: 'first_only', max_segments: 8 }
	},
	limits: { default_duration: 5, default_fps: 16, max_duration: 60 }
};
const wanCaps = parseDirectorCapabilities(WAN_RAW_CAPS)!;

// LTX-shaped timeline director (no segment_routing) - one generation per shot.
const LTX_RAW_CAPS = {
	preset_modes: ['video'],
	modes: {
		t2v: {},
		i2v: {},
		flf: {},
		director: { audio: true, ic_lora: true, max_keyframes: 8 }
	},
	limits: { default_duration: 5, default_fps: 24, max_duration: 30 }
};
const ltxCaps = parseDirectorCapabilities(LTX_RAW_CAPS)!;

function chainDoc(segments: Array<Record<string, unknown>>): VideoDirectorValue {
	return { mode: 'director', chain: { segments } } as unknown as VideoDirectorValue;
}

function timelineShot(id: string, overrides: Partial<DirectorTimelineShot> = {}): DirectorTimelineShot {
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
	return { mode: 'director', global_prompt: 'anchor', timeline: { fps: 24, shots } } as unknown as VideoDirectorValue;
}

const musicCaps = parseMusicDirectorCapabilities({
	modes: { t2m: {} },
	limits: { default_duration: 30, max_duration: 240 }
})!;

describe('assembleDirectorRequest', () => {
	it('neither Director active -> inactive (caller keeps its own formData as-is)', () => {
		const result = assembleDirectorRequest({
			formData: { steps: 20 },
			videoDirectorActive: false,
			videoDirectorCaps: null,
			videoDirectorValue: null,
			directorRuns: null,
			directorChecked: new Set(),
			predecessorOutputs: null,
			musicDirectorActive: false,
			musicDirectorCaps: null,
			musicDirectorValue: null
		});
		expect(result).toEqual({ kind: 'inactive' });
	});

	it('a valid single-segment chain document embeds video_director in form_data', () => {
		const doc = chainDoc([{ id: 'c1', prompt: 'a cat' }]);

		const result = assembleDirectorRequest({
			formData: { checkpoint: 'x' },
			videoDirectorActive: true,
			videoDirectorCaps: wanCaps,
			videoDirectorValue: doc,
			directorRuns: null,
			directorChecked: new Set(),
			predecessorOutputs: null,
			musicDirectorActive: false,
			musicDirectorCaps: null,
			musicDirectorValue: null
		});

		expect(result.kind).toBe('video');
		if (result.kind !== 'video' || !result.ok) throw new Error('expected ok:true');
		expect(result.formData.checkpoint).toBe('x');
		expect(result.formData.video_director).toBeTruthy();
		expect(result.primaryShotIds).toHaveLength(1); // normalizeDirectorValue assigns its own canonical shot id
		expect(result.remainingShotIds).toEqual([]);
	});

	it('switching the selected shots changes the assembled wire document', () => {
		const doc = chainDoc([{ id: 'c1', prompt: 'a' }, { id: 'c2', prompt: 'b' }]);
		const base = { formData: {}, videoDirectorActive: true as const, videoDirectorCaps: wanCaps, videoDirectorValue: doc, directorRuns: null, predecessorOutputs: null, musicDirectorActive: false as const, musicDirectorCaps: null, musicDirectorValue: null };

		const oneSelected = assembleDirectorRequest({ ...base, directorChecked: new Set(['c1']) });
		const bothSelected = assembleDirectorRequest({ ...base, directorChecked: new Set(['c1', 'c2']) });

		if (oneSelected.kind !== 'video' || !oneSelected.ok) throw new Error('expected ok:true');
		if (bothSelected.kind !== 'video' || !bothSelected.ok) throw new Error('expected ok:true');

		expect(oneSelected.primaryShotIds).toEqual(['c1']);
		expect(bothSelected.primaryShotIds).toEqual(['c1', 'c2']);
		expect(JSON.stringify(oneSelected.formData.video_director)).not.toBe(
			JSON.stringify(bothSelected.formData.video_director)
		);
	});

	it('switching the document itself (t2v opener only vs. a chain needing continuation) changes the wire document', () => {
		const t2vOnly = chainDoc([{ id: 'c1', prompt: 'establishing shot' }]);
		const chainMixed = chainDoc([
			{ id: 'c1', prompt: 'establishing shot' },
			{ id: 'c2', prompt: 'the story continues' }
		]);
		const base = { formData: {}, videoDirectorActive: true as const, videoDirectorCaps: wanCaps, directorRuns: null, directorChecked: new Set<string>(), predecessorOutputs: null, musicDirectorActive: false as const, musicDirectorCaps: null, musicDirectorValue: null };

		const a = assembleDirectorRequest({ ...base, videoDirectorValue: t2vOnly });
		const b = assembleDirectorRequest({ ...base, videoDirectorValue: chainMixed });

		if (a.kind !== 'video' || !a.ok) throw new Error('expected ok:true');
		if (b.kind !== 'video' || !b.ok) throw new Error('expected ok:true');
		expect(JSON.stringify(a.formData.video_director)).not.toBe(JSON.stringify(b.formData.video_director));
	});

	it('an invalid document reports an explicit unresolved reason, never throws', () => {
		const doc = chainDoc([{ id: 'c1', prompt: 'ok' }, { id: 'c2', prompt: '' }]); // every segment needs a prompt

		const result = assembleDirectorRequest({
			formData: {},
			videoDirectorActive: true,
			videoDirectorCaps: wanCaps,
			videoDirectorValue: doc,
			directorRuns: null,
			directorChecked: new Set(),
			predecessorOutputs: null,
			musicDirectorActive: false,
			musicDirectorCaps: null,
			musicDirectorValue: null
		});

		expect(result).toEqual({ kind: 'video', ok: false, reason: expect.any(String) });
	});

	it('a continuation shot whose predecessor "finished" but has no resolvable output reports unresolved, not a throw', () => {
		const a = timelineShot('shot-a');
		const b = timelineShot('shot-b', { continue_from_previous: true });
		const doc = timelineDoc([a, b]);
		const runs: Record<string, DirectorRunState> = {
			'shot-a': { generationId: 'g1', status: 'done', progress: null, finishedAt: 1, posterUrl: null, inputsHash: null }
		};

		const result = assembleDirectorRequest({
			formData: {},
			videoDirectorActive: true,
			videoDirectorCaps: ltxCaps,
			videoDirectorValue: doc,
			directorRuns: runs,
			directorChecked: new Set(['shot-b']),
			predecessorOutputs: {}, // no entry for g1 -> outputUrlFor finds nothing
			musicDirectorActive: false,
			musicDirectorCaps: null,
			musicDirectorValue: null
		});

		expect(result.kind).toBe('video');
		if (result.kind !== 'video') throw new Error('expected kind: video');
		expect(result.ok).toBe(false);
		if (!result.ok) {
			expect(result.reason.toLowerCase()).toContain('output');
		}
	});

	it('Music Director active assembles form_data.music_director and a representative prompt', () => {
		const value = { mode: 't2m', description: 'a calm piano piece', sections: [] } as any;

		const result = assembleDirectorRequest({
			formData: { seed: -1 },
			videoDirectorActive: false,
			videoDirectorCaps: null,
			videoDirectorValue: null,
			directorRuns: null,
			directorChecked: new Set(),
			predecessorOutputs: null,
			musicDirectorActive: true,
			musicDirectorCaps: musicCaps,
			musicDirectorValue: value
		});

		expect(result.kind).toBe('music');
		if (result.kind !== 'music') throw new Error('expected kind: music');
		expect(result.formData.seed).toBe(-1);
		expect(result.formData.music_director).toBeTruthy();
		expect(result.prompts[0].positive).toBe('a calm piano piece');
	});

	it('Video Director takes priority over Music Director when both are somehow active', () => {
		const doc = chainDoc([{ id: 'c1', prompt: 'a' }]);
		const musicValue = { mode: 't2m', description: 'song', sections: [] } as any;

		const result = assembleDirectorRequest({
			formData: {},
			videoDirectorActive: true,
			videoDirectorCaps: wanCaps,
			videoDirectorValue: doc,
			directorRuns: null,
			directorChecked: new Set(),
			predecessorOutputs: null,
			musicDirectorActive: true,
			musicDirectorCaps: musicCaps,
			musicDirectorValue: musicValue
		});

		expect(result.kind).toBe('video');
	});
});
