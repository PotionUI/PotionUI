import { describe, expect, it } from 'vitest';
import { convertLongVideo, longVideoParts } from './longVideoConversion';
import { buildDirectorSubmission, parseDirectorCapabilities, validateDirector } from './videoDirector';

const H3_CAPS = parseDirectorCapabilities({
	family: 'minimax_h3',
	preset_modes: ['video', 'refs'],
	segment_routing: true,
	modes: {
		t2v: {},
		i2v: {},
		flf: {},
		director: {
			keyframes: 'anywhere',
			audio: true,
			max_keyframes: 8,
			fps_locked: true,
			max_segments: 30,
			max_frames_per_segment: 345,
			continuation: { source: 'tail_frames', overlap_frames: 17, stitch: true },
			max_overlap_frames: 34
		}
	},
	limits: { default_duration: 5, default_fps: 24, max_duration: 15 }
})!;

const form = {
	total_seconds: 30,
	piece_seconds: 10,
	piece_prompts: 'rain starts\n\nthe boat reaches the drain',
	start_image: { path: '/storage/boat.png', type: 'image' },
	soundtrack: '/storage/rain.mp3',
	drift_correction: 0.5
};

describe('a converted Long video setup in the real Video Director', () => {
	it('is a document the editor accepts', () => {
		const { directorValue } = convertLongVideo({ prompt: 'a paper boat', formData: form }, { caps: H3_CAPS });
		expect(validateDirector(directorValue, H3_CAPS)).toEqual({ ok: true, reasons: [] });
	});

	it('goes on the wire as one routed film with the parts Long video would have run', () => {
		const { directorValue } = convertLongVideo({ prompt: 'a paper boat', formData: form }, { caps: H3_CAPS });
		const [wire] = buildDirectorSubmission(directorValue, H3_CAPS);

		expect(wire.mode).toBe('director');
		expect(wire.segments.map((segment) => segment.frames)).toEqual(longVideoParts(30, 10));
		expect(wire.segments.map((segment) => segment.prompt)).toEqual([
			'rain starts',
			'a paper boat',
			'the boat reaches the drain',
			'a paper boat'
		]);
		expect(wire.settings.continuation).toEqual({ source: 'tail_frames', overlap_frames: 17, stitch: true });
		expect(wire.media.map((entry) => [entry.role, entry.segment_id])).toEqual([['first', wire.segments[0].id]]);
		expect(wire.audio.map((entry) => [entry.role, (entry.media as { path: string }).path])).toEqual([['mux', '/storage/rain.mp3']]);
	});

	it('survives the longest setup Long video allowed', () => {
		const { directorValue, shots, notes } = convertLongVideo(
			{ prompt: 'x', formData: { total_seconds: 120, piece_seconds: 5 } },
			{ caps: H3_CAPS }
		);

		expect(shots).toBe(27);
		expect(notes.filter((note) => note.includes('left out'))).toEqual([]);
		expect(validateDirector(directorValue, H3_CAPS).ok).toBe(true);
	});

	it('is cut to the Director limit and says so when a preset allows fewer shots', () => {
		const tight = parseDirectorCapabilities({
			segment_routing: true,
			modes: { t2v: {}, director: { max_segments: 6, max_frames_per_segment: 345 } },
			limits: { default_duration: 5, default_fps: 24, max_duration: 15 }
		})!;
		const { shots, notes } = convertLongVideo({ prompt: 'x', formData: { total_seconds: 120, piece_seconds: 14 } }, { caps: tight });

		expect(shots).toBe(6);
		expect(notes[0]).toContain('allows up to 6 shots');
	});
});
