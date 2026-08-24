import { describe, expect, it } from 'vitest';
import { collectFormImages } from './formMedia';
import type { Tab } from '$lib/types/tabs';

function baseTab(overrides: Partial<Tab> = {}): Tab {
	return {
		id: 't1',
		name: 'Tab 1',
		selectedPreset: null,
		selectedMode: null,
		prompt: '',
		negativePrompt: '',
		formData: {},
		...overrides
	} as Tab;
}

describe('collectFormImages', () => {
	it('returns an empty list for a null/empty tab', () => {
		expect(collectFormImages(null)).toEqual([]);
		expect(collectFormImages(baseTab())).toEqual([]);
	});

	it('includes a form_data image object, humanizing its key', () => {
		const tab = baseTab({
			formData: {
				start_image: {
					path: 'generations/2026-01-01/gen123/0.png',
					type: 'image',
					name: '0.png'
				}
			}
		});
		const entries = collectFormImages(tab);
		expect(entries).toHaveLength(1);
		expect(entries[0].label).toBe('Start image');
		expect(entries[0].url).toBe('/api/media/generations/gen123/0.png');
	});

	it('excludes a video-typed media object', () => {
		const tab = baseTab({
			formData: {
				input_video: { path: 'generations/2026-01-01/gen123/0.mp4', type: 'video' }
			}
		});
		expect(collectFormImages(tab)).toEqual([]);
	});

	it('accepts a legacy bare-string path with an image extension', () => {
		const tab = baseTab({
			formData: { reference: 'generations/2026-01-01/gen999/frame.jpg' }
		});
		const entries = collectFormImages(tab);
		expect(entries).toHaveLength(1);
		expect(entries[0].url).toBe('/api/media/generations/gen999/frame.jpg');
	});

	it('ignores a legacy bare-string path with a non-image extension', () => {
		const tab = baseTab({ formData: { audio_ref: 'uploads/track.mp3' } });
		expect(collectFormImages(tab)).toEqual([]);
	});

	it('ignores a blob: url and resolves from the durable path instead', () => {
		const tab = baseTab({
			formData: {
				start_image: {
					path: 'generations/2026-01-01/gen123/0.png',
					url: 'blob:http://localhost/abc-def',
					type: 'image'
				}
			}
		});
		const entries = collectFormImages(tab);
		expect(entries[0].url).toBe('/api/media/generations/gen123/0.png');
	});

	it('trusts an already-durable /api/ url verbatim', () => {
		const tab = baseTab({
			formData: {
				start_image: {
					path: 'generations/2026-01-01/gen123/0.png',
					url: '/api/media/generations/gen123/0.png?v=2',
					type: 'image'
				}
			}
		});
		expect(collectFormImages(tab)[0].url).toBe('/api/media/generations/gen123/0.png?v=2');
	});

	it('resolves an uploads-bucket path (absolute or uploads/-prefixed)', () => {
		const tab = baseTab({
			formData: {
				a: { path: '/srv/uploads/foo.png', type: 'image' },
				b: { path: 'uploads/bar.webp', type: 'image' }
			}
		});
		const urls = collectFormImages(tab).map((e) => e.url);
		expect(urls).toContain('/api/media/uploads/foo.png');
		expect(urls).toContain('/api/media/uploads/bar.webp');
	});

	it('resolves a tmp-bucket path', () => {
		const tab = baseTab({
			formData: { a: { path: '/tmp/uploads/foo.png', type: 'image' } }
		});
		expect(collectFormImages(tab)[0].url).toBe('/api/media/tmp/foo.png');
	});

	it('resolves a generations-bucket path using the second-to-last segment', () => {
		const tab = baseTab({
			formData: { a: { relative_path: 'generations/2026-01-01/genABC/2.png', type: 'image' } }
		});
		expect(collectFormImages(tab)[0].url).toBe('/api/media/generations/genABC/2.png');
	});

	it('walks all Video Director image slots', () => {
		const tab = baseTab({
			videoDirector: {
				schema_version: 1,
				mode: 'director',
				global_prompt: '',
				global_prompt_segments: [],
				negative_prompt: '',
				negative_prompt_segments: [],
				simple: {
					duration: 5,
					fps: 24,
					start_image: { path: 'generations/d/g1/start.png', type: 'image' },
					first_frame: { path: 'generations/d/g2/first.png', type: 'image' },
					last_frame: { path: 'generations/d/g3/last.png', type: 'image' }
				},
				timeline: {
					duration: 5,
					fps: 24,
					segments: [],
					keyframes: [
						{ id: 'k1', start: 0, role: 'first', strength: 1, media: { path: 'generations/d/g4/kf1.png', type: 'image' } },
						{ id: 'k2', start: 1, role: 'last', strength: 1, media: { path: 'generations/d/g5/kf2.png', type: 'image' } }
					],
					audio: [],
					ic_lora: [
						{ id: 'ic1', lora: null, ref_media: { path: 'generations/d/g6/ic.png', type: 'image' }, strength: 1 }
					]
				},
				chain: {
					fps: 24,
					segments: [
						{
							id: 'c1',
							prompt: '',
							prompt_segments: [],
							duration: 2,
							loras: null,
							keyframe: { path: 'generations/d/g7/chain1.png', type: 'image' },
							keyframe_strength: 1,
							last_keyframe: null,
							last_keyframe_strength: 1,
							sub_type_override: null
						}
					],
					continuation: { overlap_frames: 0, stitch: false },
					keyframes: [
						{ id: 'ck1', at: 1.5, strength: 1, media: { path: 'generations/d/g8/placed1.png', type: 'image' } }
					],
					audio: []
				}
			}
		});

		const entries = collectFormImages(tab);
		const labels = entries.map((e) => e.label);
		expect(labels).toEqual([
			'Director · Start image',
			'Director · First frame',
			'Director · Last frame',
			'Director · Keyframe 1',
			'Director · Keyframe 2',
			'Director · IC-LoRA reference',
			'Director · Chain keyframe 1',
			'Director · Placed keyframe 1'
		]);
	});

	it('excludes a non-image video-tagged keyframe media', () => {
		const tab = baseTab({
			videoDirector: {
				schema_version: 1,
				mode: 't2v',
				global_prompt: '',
				global_prompt_segments: [],
				negative_prompt: '',
				negative_prompt_segments: [],
				simple: { duration: 5, fps: 24, start_image: null, first_frame: null, last_frame: null },
				timeline: {
					duration: 5,
					fps: 24,
					segments: [],
					keyframes: [
						{ id: 'k1', start: 0, role: 'first', strength: 1, media: { path: 'generations/d/g1/kf.mp4', type: 'video' } }
					],
					audio: [],
					ic_lora: []
				},
				chain: { fps: 24, segments: [], continuation: { overlap_frames: 0, stitch: false }, keyframes: [], audio: [] }
			}
		});
		expect(collectFormImages(tab)).toEqual([]);
	});

	it('dedupes by durable path, first occurrence winning (form_data before director)', () => {
		const shared = { path: 'generations/d/gShared/same.png', type: 'image' };
		const tab = baseTab({
			formData: { start_image: shared },
			videoDirector: {
				schema_version: 1,
				mode: 'i2v',
				global_prompt: '',
				global_prompt_segments: [],
				negative_prompt: '',
				negative_prompt_segments: [],
				simple: { duration: 5, fps: 24, start_image: { ...shared }, first_frame: null, last_frame: null },
				timeline: { duration: 5, fps: 24, segments: [], keyframes: [], audio: [], ic_lora: [] },
				chain: { fps: 24, segments: [], continuation: { overlap_frames: 0, stitch: false }, keyframes: [], audio: [] }
			}
		});
		const entries = collectFormImages(tab);
		expect(entries).toHaveLength(1);
		expect(entries[0].key).toBe('form:start_image');
	});

	it('is a pure function: same input yields the same output on repeated calls', () => {
		const tab = baseTab({ formData: { start_image: { path: 'generations/d/g1/a.png', type: 'image' } } });
		expect(collectFormImages(tab)).toEqual(collectFormImages(tab));
	});

	describe('array-valued (multi-item) media fields', () => {
		it('adds one entry per array item, keyed by index', () => {
			const tab = baseTab({
				formData: {
					references: [
						{ path: 'generations/d/g1/a.png', type: 'image' },
						{ path: 'generations/d/g2/b.png', type: 'image' }
					]
				}
			});
			const entries = collectFormImages(tab);
			expect(entries).toHaveLength(2);
			expect(entries.map((e) => e.key)).toEqual(['form:references:0', 'form:references:1']);
		});

		it('uses a trimmed item label when present, falling back to a numbered field label', () => {
			const tab = baseTab({
				formData: {
					references: [
						{ path: 'generations/d/g1/a.png', type: 'image', label: '  Hero  ' },
						{ path: 'generations/d/g2/b.png', type: 'image' }
					]
				}
			});
			const entries = collectFormImages(tab);
			expect(entries[0].label).toBe('Hero');
			expect(entries[1].label).toBe('References 2');
		});

		it('excludes non-image items and legacy string paths without an image extension', () => {
			const tab = baseTab({
				formData: {
					references: [
						{ path: 'generations/d/g1/a.mp4', type: 'video' },
						'generations/d/g2/b.jpg',
						'uploads/track.mp3'
					]
				}
			});
			const entries = collectFormImages(tab);
			expect(entries).toHaveLength(1);
			expect(entries[0].url).toBe('/api/media/generations/g2/b.jpg');
		});

		it('dedupes an array item against a matching single-valued field, first occurrence winning', () => {
			const shared = { path: 'generations/d/gShared/same.png', type: 'image' };
			const tab = baseTab({
				formData: {
					start_image: shared,
					references: [{ ...shared }]
				}
			});
			const entries = collectFormImages(tab);
			expect(entries).toHaveLength(1);
			expect(entries[0].key).toBe('form:start_image');
		});
	});
});
