import { describe, it, expect } from 'vitest';
import type { ChainSegment, DirectorCapabilities, VideoDirectorValue } from '$lib/types/videoDirector';
import type { Segment } from '$lib/types/segments';
import type { PromptResourceSpec } from './promptResources';
import { shotReferenceOverview, shotResourceNumbering, withMarkerAppendedToShot } from './shotReferences';

const specs: PromptResourceSpec[] = [
	{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' },
	{ field: 'reference_videos', kind: 'video', label: 'Videos', token: '<Video @>' },
	{ field: 'reference_audios', kind: 'audio', label: 'Audio', token: '<Audio @>' }
];

const formData = {
	references: [
		{ path: '/pool/a.png', url: '/u/a.png', type: 'image', label: 'Alice' },
		{ path: '/pool/b.png', url: '/u/b.png', type: 'image' },
		{ path: '/pool/c.png', url: '/u/c.png', type: 'image' }
	],
	reference_videos: [{ path: '/pool/v.mp4', type: 'video' }],
	reference_audios: [{ path: '/pool/t.wav', type: 'audio' }]
};

const chainCaps = {
	references: 'per_shot',
	referenceFields: ['references', 'reference_videos', 'reference_audios'],
	segmentRouting: true
} as unknown as DirectorCapabilities;

function segment(id: string, prompt_segments: Segment[]): ChainSegment {
	return {
		id,
		prompt: '',
		prompt_segments,
		duration: 5,
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

function chainDoc(segments: ChainSegment[], global: Segment[] = []): VideoDirectorValue {
	return {
		schema_version: 1,
		mode: 'director',
		global_prompt: '',
		global_prompt_segments: global,
		negative_prompt: '',
		negative_prompt_segments: [],
		simple: { duration: 5, fps: 24, start_image: null, first_frame: null, last_frame: null },
		timeline: { fps: 24, shots: [] },
		chain: { fps: 24, segments, continuation: { overlap_frames: 0, stitch: true }, keyframes: [], audio: [] }
	} as VideoDirectorValue;
}

describe('shotReferenceOverview', () => {
	it('lists cited items in film order with per-kind handles and use counts', () => {
		const doc = chainDoc([
			segment('s1', [
				{ id: 'p1', content: '@[references:/pool/c.png] hugs @[references:/pool/a.png] near @[references:/pool/c.png]' },
				{ id: 'p2', content: 'in @[reference_videos:/pool/v.mp4]' }
			])
		]);
		const { used, unused } = shotReferenceOverview(doc, chainCaps, 's1', formData, specs);
		expect(used.map((e) => [e.itemKey, e.handle, e.count])).toEqual([
			['/pool/a.png', 'Picture 1', 1],
			['/pool/c.png', 'Picture 2', 2],
			['/pool/v.mp4', 'Video 1', 1]
		]);
		expect(unused.map((e) => e.itemKey)).toEqual(['/pool/b.png', '/pool/t.wav']);
		expect(used[0].name).toBe('Alice');
		expect(unused[0].name).toBe('b.png');
	});

	it('ignores disabled prompt segments and other shots', () => {
		const doc = chainDoc([
			segment('s1', [{ id: 'p1', content: '@[references:/pool/b.png]', enabled: false }]),
			segment('s2', [{ id: 'p2', content: '@[references:/pool/a.png]' }])
		]);
		const { used } = shotReferenceOverview(doc, chainCaps, 's1', formData, specs);
		expect(used).toEqual([]);
	});

	it('counts markers in the global prompt, which every shot carries', () => {
		const doc = chainDoc([segment('s1', [])], [{ id: 'g', content: 'style of @[references:/pool/b.png]' }]);
		const { used } = shotReferenceOverview(doc, chainCaps, 's1', formData, specs);
		expect(used.map((e) => e.itemKey)).toEqual(['/pool/b.png']);
	});
});

describe('shotResourceNumbering', () => {
	it('numbers per-shot in film pool order, ahead of an uncited item in between', () => {
		const doc = chainDoc([
			segment('s1', [{ id: 'p1', content: '@[references:/pool/c.png] and @[references:/pool/a.png]' }])
		]);
		const numbering = shotResourceNumbering(doc, chainCaps, 's1', formData, specs);
		expect(numbering.positionFor('references', '/pool/a.png')).toBe(1);
		expect(numbering.positionFor('references', '/pool/c.png')).toBe(2);
		expect(numbering.positionFor('references', '/pool/b.png')).toBeNull();
	});

	it('renumbers once the in-between item is also cited', () => {
		const doc = chainDoc([
			segment('s1', [
				{ id: 'p1', content: '@[references:/pool/c.png] and @[references:/pool/a.png] and @[references:/pool/b.png]' }
			])
		]);
		const numbering = shotResourceNumbering(doc, chainCaps, 's1', formData, specs);
		expect(numbering.positionFor('references', '/pool/a.png')).toBe(1);
		expect(numbering.positionFor('references', '/pool/b.png')).toBe(2);
		expect(numbering.positionFor('references', '/pool/c.png')).toBe(3);
	});

	it('returns null for an item that no longer exists in the field', () => {
		const doc = chainDoc([segment('s1', [{ id: 'p1', content: '@[references:/pool/missing.png]' }])]);
		const numbering = shotResourceNumbering(doc, chainCaps, 's1', formData, specs);
		expect(numbering.positionFor('references', '/pool/missing.png')).toBeNull();
	});
});

describe('withMarkerAppendedToShot', () => {
	it('appends the marker to the first enabled segment of the shot and recompiles its prompt', () => {
		const doc = chainDoc([
			segment('s1', [
				{ id: 'off', content: 'skip', enabled: false },
				{ id: 'on', content: 'a woman ' }
			]),
			segment('s2', [{ id: 'x', content: 'other' }])
		]);
		const next = withMarkerAppendedToShot(doc, chainCaps, 's1', '@[references:/pool/b.png]');
		const shot = next.chain.segments[0];
		expect(shot.prompt_segments[0].content).toBe('skip');
		expect(shot.prompt_segments[1].content).toBe('a woman @[references:/pool/b.png]');
		expect(Object.values(shot.prompt_segments[1].resources ?? {})).toEqual([{ field: 'references', item_key: '/pool/b.png' }]);
		expect(shot.prompt).toBe('a woman @[references:/pool/b.png]');
		expect(next.chain.segments[1]).toBe(doc.chain.segments[1]);
	});

	it('creates a segment when the shot has none', () => {
		const next = withMarkerAppendedToShot(chainDoc([segment('s1', [])]), chainCaps, 's1', '@[references:/pool/a.png]');
		expect(next.chain.segments[0].prompt_segments.map((s) => s.content)).toEqual(['@[references:/pool/a.png]']);
	});

	it('writes onto the first beat of a timeline shot', () => {
		const caps = { ...chainCaps, segmentRouting: false } as DirectorCapabilities;
		const doc = chainDoc([]);
		doc.timeline = {
			fps: 24,
			shots: [
				{
					id: 'shot-1',
					duration: 5,
					continue_from_previous: false,
					segments: [{ id: 'b1', start: 0, end: 5, text: 'x', prompt_segments: [{ id: 'p', content: 'x' }] }],
					keyframes: [],
					audio: [],
					ic_lora: []
				}
			]
		};
		const next = withMarkerAppendedToShot(doc, caps, 'shot-1', '@[references:/pool/a.png]');
		expect(next.timeline.shots[0].segments[0].text).toBe('x @[references:/pool/a.png]');
		expect(shotReferenceOverview(next, caps, 'shot-1', formData, specs).used.map((e) => e.itemKey)).toEqual(['/pool/a.png']);
	});
});
