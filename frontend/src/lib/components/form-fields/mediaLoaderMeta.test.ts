import { describe, it, expect } from 'vitest';
import { durationBadge, formatContainer, formatDimensions, metaChips, metaLine } from './mediaLoaderMeta';

const IMAGE = { width: 1024, height: 1536, size: 2411724 };
const VIDEO = { width: 2560, height: 1440, duration_seconds: 8.4, fps: 24, size: 24117248 };
const AUDIO = { duration_seconds: 32.6, size: 6081740 };

describe('formatDimensions', () => {
	it('renders both axes with the multiplication sign', () => {
		expect(formatDimensions(IMAGE)).toBe('1024×1536');
	});

	it('renders nothing when either axis is missing', () => {
		expect(formatDimensions({ width: 1024 })).toBeNull();
		expect(formatDimensions(null)).toBeNull();
	});
});

describe('formatContainer', () => {
	it('reads the container from the filename', () => {
		expect(formatContainer('sdxl_portrait_0043.png')).toBe('PNG');
	});

	it('renders nothing for a name with no extension', () => {
		expect(formatContainer('Upload')).toBeNull();
		expect(formatContainer(null)).toBeNull();
	});
});

describe('metaChips', () => {
	it('describes an image with resolution, format and size', () => {
		const chips = metaChips(IMAGE, 'image', 'sdxl_portrait_0043.png');
		expect(chips.map((c) => c.text)).toEqual(['1024×1536', 'PNG', '2.3 MB']);
	});

	it('describes a video with resolution, duration, frame rate and size', () => {
		const chips = metaChips(VIDEO, 'video', 'wan22_i2v_00042.mp4');
		expect(chips.map((c) => c.text)).toEqual(['2560×1440', '8.4s', '24 fps', '23 MB']);
	});

	it('marks the duration as state rather than plain metadata', () => {
		expect(metaChips(VIDEO, 'video', 'a.mp4').find((c) => c.key === 'duration')?.tone).toBe('signal');
	});

	// A still that carries a stray duration (a probe on an animated source,
	// a copied metadata blob) must not grow a duration chip.
	it('never puts a duration on a still', () => {
		const chips = metaChips({ ...IMAGE, duration_seconds: 4 }, 'image', 'a.png');
		expect(chips.map((c) => c.key)).not.toContain('duration');
	});

	it('describes audio without a resolution or a frame rate', () => {
		expect(metaChips(AUDIO, 'audio', 'vo_take_03_clean.wav').map((c) => c.key)).toEqual(['duration', 'size']);
	});

	// A generation from before probing existed has no numbers at all; the row
	// must come out empty rather than carrying placeholders.
	it('renders no chips at all when nothing was measured', () => {
		expect(metaChips(null, 'image', null)).toEqual([]);
	});

	it('marks an edited resolution as a change the user made', () => {
		const chips = metaChips(IMAGE, 'image', 'a.png', { edited: true });
		expect(chips[0].tone).toBe('success');
	});
});

describe('metaLine', () => {
	it('joins what is known with a middle dot', () => {
		expect(metaLine(VIDEO, 'video')).toBe('2560×1440 · 8.4s · 24fps · 23 MB');
	});

	it('omits a duration on a still', () => {
		expect(metaLine({ ...IMAGE, duration_seconds: 4 }, 'image')).toBe('1024×1536 · 2.3 MB');
	});

	it('returns null when there is nothing to say', () => {
		expect(metaLine({}, 'image')).toBeNull();
	});
});

describe('durationBadge', () => {
	it('badges timed media only', () => {
		expect(durationBadge(VIDEO, 'video')).toBe('8.4s');
		expect(durationBadge(AUDIO, 'audio')).toBe('32.6s');
		expect(durationBadge({ ...IMAGE, duration_seconds: 3 }, 'image')).toBeNull();
	});

	it('renders nothing when the duration is unknown or zero', () => {
		expect(durationBadge({ duration_seconds: 0 }, 'video')).toBeNull();
		expect(durationBadge(null, 'video')).toBeNull();
	});
});
