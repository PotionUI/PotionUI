import { describe, expect, it } from 'vitest';
import {
	actionsForCount,
	bucketForCardWidth,
	formatBarResolution,
	mediaChipOwnsDuration,
	resolveCardResolution
} from './generationCardChrome';

describe('bucketForCardWidth', () => {
	it('honors the inclusive lower bound of every threshold', () => {
		expect(bucketForCardWidth(299).name).toBe('regular');
		expect(bucketForCardWidth(300).name).toBe('full');
		expect(bucketForCardWidth(209).name).toBe('compact');
		expect(bucketForCardWidth(210).name).toBe('regular');
		expect(bucketForCardWidth(139).name).toBe('micro');
		expect(bucketForCardWidth(140).name).toBe('compact');
		expect(bucketForCardWidth(109).name).toBe('nano');
		expect(bucketForCardWidth(110).name).toBe('micro');
	});

	it('falls back to nano below every threshold, including zero and negative widths', () => {
		expect(bucketForCardWidth(0).name).toBe('nano');
		expect(bucketForCardWidth(-40).name).toBe('nano');
	});

	it('only the compact bucket trades the star row for a rating chip', () => {
		expect(bucketForCardWidth(320)).toMatchObject({ showStars: true, showRatingChip: false });
		expect(bucketForCardWidth(220)).toMatchObject({ showStars: true, showRatingChip: false });
		expect(bucketForCardWidth(150)).toMatchObject({ showStars: false, showRatingChip: true });
		expect(bucketForCardWidth(120)).toMatchObject({ showStars: false, showRatingChip: false });
		expect(bucketForCardWidth(80)).toMatchObject({ showStars: false, showRatingChip: false });
	});

	it('only the full bucket keeps the bar-time lane and only nano shortens resolution', () => {
		expect(bucketForCardWidth(320).showBarTime).toBe(true);
		expect(bucketForCardWidth(220).showBarTime).toBe(false);
		expect(bucketForCardWidth(320).resolutionShort).toBe(false);
		expect(bucketForCardWidth(80).resolutionShort).toBe(true);
	});

	it('shrinks the checkbox and status style as the tile narrows', () => {
		expect(bucketForCardWidth(320)).toMatchObject({ checkboxSize: 20, statusStyle: 'label' });
		expect(bucketForCardWidth(220)).toMatchObject({ checkboxSize: 20, statusStyle: 'label' });
		expect(bucketForCardWidth(150)).toMatchObject({ checkboxSize: 18, statusStyle: 'dot' });
		expect(bucketForCardWidth(120)).toMatchObject({ checkboxSize: 16, statusStyle: 'dot' });
		expect(bucketForCardWidth(80)).toMatchObject({ checkboxSize: 14, statusStyle: 'dot' });
	});
});

describe('actionsForCount', () => {
	it('keeps favorite and delete at every count, adding view then download as room allows', () => {
		expect(actionsForCount(1)).toEqual(['delete']);
		expect(actionsForCount(2)).toEqual(['favorite', 'delete']);
		expect(actionsForCount(3)).toEqual(['favorite', 'view', 'delete']);
		expect(actionsForCount(4)).toEqual(['favorite', 'view', 'download', 'delete']);
	});

	it('treats counts above 4 the same as 4', () => {
		expect(actionsForCount(9)).toEqual(['favorite', 'view', 'download', 'delete']);
	});

	it('treats a count of zero the same as the smallest bucket', () => {
		expect(actionsForCount(0)).toEqual(['delete']);
	});
});

describe('formatBarResolution', () => {
	it('renders the full W×H pair when not shortened', () => {
		expect(formatBarResolution(1920, 1080, false)).toBe('1920×1080');
	});

	it('renders only the short side, video-convention style, when shortened', () => {
		expect(formatBarResolution(1920, 1080, true)).toBe('1080p');
		expect(formatBarResolution(1080, 1920, true)).toBe('1080p');
	});
});

describe('resolveCardResolution', () => {
	it('prefers the file dimensions over form_data when both are present', () => {
		expect(
			resolveCardResolution({ width: 640, height: 480 }, { width: 1920, height: 1080 })
		).toEqual({ width: 640, height: 480 });
	});

	it('falls back to form_data when the file has no dimensions', () => {
		expect(resolveCardResolution({}, { width: 1920, height: 1080 })).toEqual({
			width: 1920,
			height: 1080
		});
		expect(resolveCardResolution(null, { width: 1920, height: 1080 })).toEqual({
			width: 1920,
			height: 1080
		});
	});

	it('treats one dimension present without the other as absent, on either source', () => {
		expect(resolveCardResolution({ width: 640 }, { width: 1920, height: 1080 })).toEqual({
			width: 1920,
			height: 1080
		});
		expect(resolveCardResolution({ width: 640, height: 480 }, {})).toEqual({
			width: 640,
			height: 480
		});
		expect(resolveCardResolution({ width: 640 }, { width: 1920 })).toBeNull();
	});

	it('rejects non-numeric or non-positive form_data values', () => {
		expect(resolveCardResolution(null, { width: '1920', height: '1080' })).toBeNull();
		expect(resolveCardResolution(null, { width: 0, height: 1080 })).toBeNull();
		expect(resolveCardResolution(null, { width: -100, height: 1080 })).toBeNull();
		expect(resolveCardResolution(null, { width: NaN, height: 1080 })).toBeNull();
	});

	it('renders nothing when neither source has usable dimensions', () => {
		expect(resolveCardResolution(null, null)).toBeNull();
		expect(resolveCardResolution({}, {})).toBeNull();
		expect(resolveCardResolution({ width: 0, height: 0 }, undefined)).toBeNull();
	});
});

describe('mediaChipOwnsDuration', () => {
	it('is true only for a single video file, where the media chip shows duration', () => {
		expect(mediaChipOwnsDuration(1, true)).toBe(true);
		expect(mediaChipOwnsDuration(0, true)).toBe(true);
	});

	it('is false for a multi-file video, where the media chip shows a counter instead', () => {
		expect(mediaChipOwnsDuration(2, true)).toBe(false);
	});

	it('is false for non-video media regardless of file count', () => {
		expect(mediaChipOwnsDuration(1, false)).toBe(false);
	});
});
