import { describe, expect, it } from 'vitest';
import {
	deriveMediaSizeOptions,
	matchingSide,
	parseCustomSize,
	pixelShare,
	preferredMediaSize
} from './mediaSizeOptions';

const rows = (width: number, height: number) =>
	deriveMediaSizeOptions({ width, height }).map((opt) => [opt.label, opt.value, opt.tier]);

describe('deriveMediaSizeOptions', () => {
	it('offers the original, smaller common heights and scale steps at the source aspect, deduplicated', () => {
		expect(rows(1920, 1080)).toEqual([
			['Original', '1920x1080', 'Source'],
			['720p', '1280x720', 'Common sizes'],
			['540p', '960x540', 'Common sizes'],
			['480p', '854x480', 'Common sizes'],
			['360p', '640x360', 'Common sizes'],
			['75%', '1440x810', 'Scale'],
			['33%', '634x356', 'Scale'],
			['25%', '480x270', 'Scale']
		]);
	});

	it('carries the numeric sides alongside the value', () => {
		const option = deriveMediaSizeOptions({ width: 1920, height: 1080 })[1];
		expect([option.width, option.height]).toEqual([1280, 720]);
	});

	it('measures common heights on the short edge so portrait media stays portrait', () => {
		const options = deriveMediaSizeOptions({ width: 1080, height: 1920 });
		expect(options.find((opt) => opt.label === '720p')?.value).toBe('720x1280');
		expect(options.every((opt) => opt.height > opt.width)).toBe(true);
	});

	it('never offers a common height at or above the source', () => {
		expect(rows(640, 360).map(([label]) => label)).toEqual(['Original', '75%', '50%', '33%', '25%']);
	});

	it('keeps every side even and drops sizes below the 64px minimum', () => {
		expect(rows(641, 361)[0]).toEqual(['Original', '640x360', 'Source']);
		expect(deriveMediaSizeOptions({ width: 100, height: 80 }).map((opt) => opt.value)).toEqual(['100x80']);
		for (const opt of deriveMediaSizeOptions({ width: 1279, height: 721 })) {
			expect(opt.width % 2 + (opt.height % 2)).toBe(0);
		}
	});

	it('has nothing to offer without a known size', () => {
		expect(deriveMediaSizeOptions(null)).toEqual([]);
		expect(deriveMediaSizeOptions({ width: 0, height: 720 })).toEqual([]);
	});
});

describe('preferredMediaSize', () => {
	it('picks the largest common height below the source, else half size', () => {
		expect(preferredMediaSize(deriveMediaSizeOptions({ width: 3840, height: 2160 }))?.value).toBe('1920x1080');
		expect(preferredMediaSize(deriveMediaSizeOptions({ width: 640, height: 360 }))?.value).toBe('320x180');
		expect(preferredMediaSize([])).toBeNull();
	});
});

describe('matchingSide', () => {
	it('fills the other side from the source aspect, rounded to even', () => {
		expect(matchingSide({ width: 1920, height: 1080 }, 'width', '1000')).toBe('562');
		expect(matchingSide({ width: 1920, height: 1080 }, 'height', '720')).toBe('1280');
		expect(matchingSide({ width: 1920, height: 1080 }, 'width', '')).toBe('');
		expect(matchingSide({ width: 1920, height: 1080 }, 'width', 'abc')).toBe('');
	});
});

describe('parseCustomSize', () => {
	const source = { width: 1920, height: 1080 };

	it('accepts an even size inside the source', () => {
		expect(parseCustomSize(source, '854', '480')).toEqual({ ok: true, size: { width: 854, height: 480 } });
	});

	it('refuses anything larger than the source', () => {
		expect(parseCustomSize(source, '2560', '1440')).toEqual({
			ok: false,
			error: 'Larger than the original 1920×1080.'
		});
	});

	it('refuses odd sides for video but allows them for images', () => {
		expect(parseCustomSize(source, '855', '481').ok).toBe(false);
		expect(parseCustomSize(source, '855', '481', false).ok).toBe(true);
	});

	it('refuses empty, non-numeric and too-small sides', () => {
		expect(parseCustomSize(source, '', '480').ok).toBe(false);
		expect(parseCustomSize(source, '12a', '480').ok).toBe(false);
		expect(parseCustomSize(source, '62', '480').ok).toBe(false);
	});
});

describe('pixelShare', () => {
	it('is the share of the source pixels the result keeps', () => {
		expect(pixelShare({ width: 1920, height: 1080 }, { width: 960, height: 540 })).toBe(25);
	});
});
