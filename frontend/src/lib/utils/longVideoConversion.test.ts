import { describe, expect, it } from 'vitest';
import { convertLongVideo, isLongVideoForm, longVideoParts } from './longVideoConversion';

const base = {
	total_seconds: 30,
	piece_seconds: 10,
	piece_prompts: '',
	speed_profile: 'quality',
	seed: 7,
	resolution: '832x480',
	drift_correction: 0.5
};

function chain(conversion: ReturnType<typeof convertLongVideo>) {
	return (conversion.directorValue as unknown as { chain: { segments: any[]; audio: any[]; continuation: any } }).chain;
}

describe('longVideoParts', () => {
	it.each([
		[30, 10, [243, 243, 243, 56]],
		[10, 10, [243]],
		[10, 14, [243]],
		[12, 5, [124, 124, 90]],
		[120, 14, 9]
	])('cuts %s s into %s s parts like the backend filter did', (total, piece, expected) => {
		const parts = longVideoParts(total as number, piece as number);
		if (Array.isArray(expected)) expect(parts).toEqual(expected);
		else expect(parts).toHaveLength(expected as number);
	});

	it('needs 27 parts for the longest, shortest-part setup', () => {
		expect(longVideoParts(120, 5)).toHaveLength(27);
	});
});

describe('isLongVideoForm', () => {
	it('recognises a Long video setup by its own fields', () => {
		expect(isLongVideoForm(base)).toBe(true);
		expect(isLongVideoForm({ resolution: '832x480' })).toBe(false);
		expect(isLongVideoForm(null)).toBe(false);
	});
});

describe('convertLongVideo', () => {
	it('makes one shot per part with the main prompt on every blank line', () => {
		const result = convertLongVideo({ prompt: 'a paper boat', formData: base });
		expect(result.shots).toBe(4);
		expect(chain(result).segments.map((s) => s.prompt)).toEqual(Array(4).fill('a paper boat'));
		expect(chain(result).segments.map((s) => Math.round(s.duration * 24))).toEqual([243, 243, 243, 56]);
		expect(chain(result).continuation).toEqual({ overlap_frames: 17, stitch: true });
	});

	it('uses each Prompt per part line for its own part and falls back for blank ones', () => {
		const result = convertLongVideo({
			prompt: 'main',
			formData: { ...base, piece_prompts: 'first part\n\n  third part  ' }
		});
		expect(chain(result).segments.map((s) => s.prompt)).toEqual(['first part', 'main', 'third part', 'main']);
	});

	it('carries the total length as the sum of whole shots and says it is not exact', () => {
		const result = convertLongVideo({ prompt: 'x', formData: base });
		expect(result.finishedSeconds).toBeCloseTo(30.583, 2);
		expect(result.notes).toEqual([
			'Long video cut the end to exactly 30 s; Video Director keeps whole shots, so the film runs 30.6 s.'
		]);
	});

	it('puts the start picture on the first shot only', () => {
		const result = convertLongVideo({ prompt: 'x', formData: { ...base, start_image: '/storage/a.png' } });
		const segments = chain(result).segments;
		expect(segments[0].keyframe).toEqual({ path: '/storage/a.png', type: 'image' });
		expect(segments.slice(1).every((s) => s.keyframe === null)).toBe(true);
	});

	it('keeps a start picture that already is a reference object', () => {
		const ref = { path: '/storage/a.png', relative_path: 'a.png', name: 'a.png' };
		const result = convertLongVideo({ prompt: 'x', formData: { ...base, start_image: ref } });
		expect(chain(result).segments[0].keyframe).toEqual({ type: 'image', ...ref });
	});

	it('lays the soundtrack over the whole film as a mux clip', () => {
		const result = convertLongVideo({ prompt: 'x', formData: { ...base, soundtrack: '/storage/song.mp3' } });
		expect(chain(result).audio).toEqual([
			{
				id: 'audio-1',
				start: 0,
				trim_start: 0,
				length: result.finishedSeconds,
				media: { path: '/storage/song.mp3', type: 'audio' },
				role: 'mux'
			}
		]);
	});

	it('has no audio and no picture when the setup had none', () => {
		const result = convertLongVideo({ prompt: 'x', formData: base });
		expect(chain(result).audio).toEqual([]);
		expect(chain(result).segments[0].keyframe).toBeNull();
	});

	it('hands the speed profile, seed, resolution and colour hold to the Director form', () => {
		const result = convertLongVideo({
			prompt: 'x',
			formData: { ...base, start_image: '/a.png', soundtrack: '/s.mp3', piece_prompts: 'a', steps: 4 }
		});
		expect(result.formPatch).toEqual({
			speed_profile: 'quality',
			seed: 7,
			resolution: '832x480',
			drift_correction: 0.5,
			steps: 4
		});
	});

	it('lists a start picture or soundtrack it cannot read instead of dropping it silently', () => {
		const result = convertLongVideo({ prompt: 'x', formData: { ...base, start_image: { name: 'no path' }, soundtrack: 12 } });
		expect(result.notes).toContain('The start picture could not be read from the saved setup and was left out.');
		expect(result.notes).toContain('The soundtrack could not be read from the saved setup and was left out.');
	});

	it('lists Prompt per part lines that have no part', () => {
		const result = convertLongVideo({
			prompt: 'x',
			formData: { ...base, total_seconds: 10, piece_prompts: 'one\ntwo\n\nthree' }
		});
		expect(result.shots).toBe(1);
		expect(result.notes).toContain(
			'2 "Prompt per part" lines had no part to belong to and were left out: "two", "three".'
		);
	});

	it('converts only as many parts as the Director allows and names what it left out', () => {
		const result = convertLongVideo({ prompt: 'x', formData: { ...base, total_seconds: 120, piece_seconds: 5 } }, { maxShots: 24 });
		expect(result.shots).toBe(24);
		expect(result.notes[0]).toMatch(/^Video Director allows up to 24 shots, so the last 3 of 27 parts \(about 12\.\d s of the 120 s\) were left out\.$/);
		expect(result.notes.some((note) => note.startsWith('Long video cut the end'))).toBe(false);
	});

	it('converts every part when the Director allows enough shots', () => {
		const result = convertLongVideo({ prompt: 'x', formData: { ...base, total_seconds: 120, piece_seconds: 5 } }, { maxShots: 30 });
		expect(result.shots).toBe(27);
		expect(result.notes.filter((note) => note.includes('left out'))).toEqual([]);
	});

	it('falls back to Long video defaults for a setup that lacks its length fields', () => {
		const result = convertLongVideo({ prompt: 'x', formData: { piece_prompts: '' } });
		expect(result.shots).toBe(4);
	});

});
